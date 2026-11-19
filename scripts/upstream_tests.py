"""Run recipe-selected upstream suites inside the isolated build container."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import xml.etree.ElementTree as ET


def load_suites(path):
    config = json.loads(path.read_text())
    suites = config.get("suites")
    if config.get("schemaVersion") != 1 or not isinstance(suites, list) or not 1 <= len(suites) <= 32:
        raise ValueError("Expected schemaVersion 1 and 1–32 suites")
    names = set()
    for suite in suites:
        name, command = suite.get("name"), suite.get("command")
        timeout = suite.get("timeoutSeconds", 600)
        if not isinstance(name, str) or not name or name in names:
            raise ValueError("Suite names must be nonempty and unique")
        names.add(name)
        if not isinstance(command, list) or not command or any(not isinstance(arg, str) for arg in command):
            raise ValueError(f"{name}: command must be a nonempty argv array")
        if type(timeout) is not int or not 1 <= timeout <= 1800:
            raise ValueError(f"{name}: timeoutSeconds must be 1–1800")
        env = suite.get("env", {})
        if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
            raise ValueError(f"{name}: env must map strings to strings")
    return suites


def execute(suite):
    process = subprocess.Popen(suite["command"], env={**os.environ, **suite.get("env", {})},
                               start_new_session=True)
    try:
        code = process.wait(timeout=suite.get("timeoutSeconds", 600))
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        # A child may survive even if the suite's main process has already exited.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        return "Suite exceeded its timeout"
    return None if code == 0 else f"Suite exited with status {code}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = ET.Element("testsuite", name="upstream suites")
    failures = 0
    try:
        suites = load_suites(args.config)
    except (ValueError, OSError, AttributeError, TypeError) as error:
        case = ET.SubElement(report, "testcase", name="configuration")
        ET.SubElement(case, "failure", message=str(error))
        failures = 1
        suites = []
    for suite in suites:
        print(f"Running upstream suite: {suite['name']}", flush=True)
        start = time.monotonic()
        case = ET.SubElement(report, "testcase", name=suite["name"], classname="upstream.suite")
        try:
            failure = execute(suite)
        except (OSError, ValueError) as error:
            failure = str(error)
        case.set("time", f"{time.monotonic() - start:.3f}")
        if failure:
            failures += 1
            ET.SubElement(case, "failure", message=failure)
    report.set("tests", str(len(report)))
    report.set("failures", str(failures))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(report).write(args.report, encoding="utf-8", xml_declaration=True)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
