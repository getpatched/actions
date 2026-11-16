"""Compare passive OS vulnerability scans of two installed package inventories."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import uuid

TRIVY = "aquasec/trivy:0.70.0@sha256:85e87be1a96459c38a4eea47dc64eb2d342bb14cd4b4cef96adcf6ff03378b7c"


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def findings(report, packages):
    return {
        (entry["PkgName"], entry["VulnerabilityID"])
        for section in report.get("Results", [])
        for entry in section.get("Vulnerabilities", [])
        if entry["PkgName"] in packages
    }


def installed_packages(report, expected_versions):
    # Trivy splits Debian versions into Version, Epoch and Release, but
    # preserves the unmodified dpkg version in the package ID.
    package_ids = {
        entry["ID"] for section in report.get("Results", []) if section.get("Class") == "os-pkgs"
        for entry in section.get("Packages", [])
    }
    expected = {f"{name}@{version}" for name, version in expected_versions.items()}
    missing = sorted(expected - package_ids)
    if missing:
        raise ValueError(f"Scanner did not identify expected package versions: {', '.join(missing)}")


def run(*args, **kwargs):
    return subprocess.run(("docker", *args), check=True, **kwargs)


def compare(before, after, reports, policy):
    cache = f"package-scan-{uuid.uuid4().hex}"
    hardened = ("--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--pids-limit=128")
    mount_cache = ("--mount", f"type=volume,src={cache},dst=/cache")
    run("volume", "create", cache, stdout=subprocess.DEVNULL)
    try:
        run("run", "--rm", *hardened, *mount_cache, TRIVY, "image",
            "--cache-dir", "/cache", "--download-db-only", "--skip-version-check")
        database = run("run", "--rm", "--network=none", *hardened, *mount_cache,
                       "--entrypoint", "/bin/busybox", TRIVY, "cat", "/cache/db/metadata.json",
                       stdout=subprocess.PIPE).stdout
        database_hash = run("run", "--rm", "--network=none", *hardened, *mount_cache,
                            "--entrypoint", "/bin/busybox", TRIVY, "sha256sum", "/cache/db/trivy.db",
                            stdout=subprocess.PIPE, text=True).stdout.split()[0]
        scans = {}
        for name, path, versions in (
            ("baseline", before, policy["baselineBinaryVersions"]),
            ("candidate", after, dict.fromkeys(policy["binaryPackages"], policy["version"])),
        ):
            output = reports / f"vulnerability-{name}.json"
            with output.open("wb") as stream:
                run("run", "--rm", "--network=none", *hardened, *mount_cache,
                    "--mount", f"type=bind,src={path},dst=/inventory,readonly", TRIVY,
                    "rootfs", "--cache-dir", "/cache", "--offline-scan", "--skip-db-update",
                    "--skip-java-db-update", "--skip-check-update", "--skip-version-check",
                    "--pkg-types", "os", "--scanners", "vuln", "--list-all-pkgs",
                    "--format", "json", "/inventory", stdout=stream)
            report = json.loads(output.read_text())
            installed_packages(report, versions)
            scans[name] = {
                "digest": digest(output),
                "inventorySha256": digest(path / "var/lib/dpkg/status"),
                "findings": findings(report, policy["binaryPackages"]),
            }
        baseline, candidate = (scans[key]["findings"] for key in ("baseline", "candidate"))
        for scan in scans.values():
            scan["findings"] = sorted(scan["findings"])
        summary = {
            "target": policy["targetId"],
            "scanner": TRIVY,
            "databaseSha256": database_hash,
            "databaseMetadata": json.loads(database),
            "baselineVersion": policy["baseVersion"],
            "baselineBinaryVersions": policy["baselineBinaryVersions"],
            "candidateVersion": policy["version"],
            "scans": scans,
            "newFindings": sorted(candidate - baseline),
            "absentFromCandidateScan": sorted(baseline - candidate),
        }
        (reports / "vulnerability-comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
        print(f"Paired OS scans found {len(candidate - baseline)} new and {len(baseline - candidate)} absent findings")
    finally:
        run("volume", "rm", cache, stdout=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("before", "after", "reports", "policy"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    compare(args.before.resolve(), args.after.resolve(), args.reports,
            json.loads(args.policy.read_text()))


if __name__ == "__main__":
    main()
