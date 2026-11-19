import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

RUNNER = Path(__file__).with_name("upstream_tests.py")


class UpstreamTests(unittest.TestCase):
    def run_suites(self, config):
        with tempfile.TemporaryDirectory() as folder:
            config_path, report = Path(folder) / "config.json", Path(folder) / "result.xml"
            config_path.write_text(json.dumps(config))
            result = subprocess.run([sys.executable, str(RUNNER), "--config", str(config_path),
                                     "--report", str(report)], capture_output=True, text=True)
            return result.returncode, ET.parse(report).getroot()

    def test_all_suites_run_and_failure_is_retained(self):
        code, report = self.run_suites({"schemaVersion": 1, "suites": [
            {"name": "fails", "command": [sys.executable, "-c", "raise SystemExit(7)"]},
            {"name": "passes", "command": [sys.executable, "-c", "pass"]}]})
        self.assertEqual(code, 1)
        self.assertEqual(report.get("tests"), "2")
        self.assertEqual(report.get("failures"), "1")
        self.assertIn("7", report.find("testcase/failure").get("message"))

    def test_environment_and_success(self):
        code, report = self.run_suites({"schemaVersion": 1, "suites": [{
            "name": "environment", "env": {"PACKAGE_TEST_VALUE": "works"},
            "command": [sys.executable, "-c", "import os; assert os.environ['PACKAGE_TEST_VALUE']=='works'"]}]})
        self.assertEqual(code, 0)
        self.assertEqual(report.get("failures"), "0")

    def test_timeout_fails_with_report(self):
        code, report = self.run_suites({"schemaVersion": 1, "suites": [{
            "name": "times out", "timeoutSeconds": 1,
            "command": [sys.executable, "-c", "import time; time.sleep(30)"]}]})
        self.assertEqual(code, 1)
        self.assertIn("timeout", report.find("testcase/failure").get("message"))

    def test_missing_command_fails_with_report(self):
        code, report = self.run_suites({"schemaVersion": 1, "suites": [{
            "name": "missing", "command": ["/missing-package-test-command"]}]})
        self.assertEqual(code, 1)
        self.assertEqual(report.get("failures"), "1")

    def test_timeout_stops_child_that_ignores_termination(self):
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "survived"
            child = ("import signal,time; from pathlib import Path; "
                     "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                     f"time.sleep(3); Path({str(marker)!r}).write_text('survived')")
            parent = ("import subprocess,sys,time; "
                      f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)")
            code, _ = self.run_suites({"schemaVersion": 1, "suites": [{
                "name": "child timeout", "timeoutSeconds": 1,
                "command": [sys.executable, "-c", parent]}]})
            self.assertEqual(code, 1)
            import time
            time.sleep(3)
            self.assertFalse(marker.exists())

    def test_empty_and_invalid_configs_fail(self):
        for config in ({"schemaVersion": 1, "suites": []}, [],
                       {"schemaVersion": 1, "suites": [{"name": "bad", "command": "true"}]},
                       {"schemaVersion": 1, "suites": [{"name": "bad", "command": ["true"], "timeoutSeconds": True}]}):
            with self.subTest(config=config):
                code, report = self.run_suites(config)
                self.assertEqual(code, 1)
                self.assertEqual(report.find("testcase").get("name"), "configuration")


if __name__ == "__main__":
    unittest.main()
