"""Compare checked-in source pins with signed public distribution indexes."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def public_sources(profile):
    release = profile["codename"]
    arch = profile["architecture"]
    if profile["distribution"] == "Ubuntu":
        archive = "http://archive.ubuntu.com/ubuntu"
        security = "http://security.ubuntu.com/ubuntu"
        if arch == "arm64":
            archive = security = "http://ports.ubuntu.com/ubuntu-ports"
        components = "main restricted universe multiverse"
        archives = [(archive, release), (archive, release + "-updates"),
                    (security, release + "-security")]
    else:
        components = "main contrib non-free non-free-firmware"
        archives = [("http://deb.debian.org/debian", release),
                    ("http://deb.debian.org/debian", release + "-updates"),
                    ("http://deb.debian.org/debian-security", release + "-security")]
    return "\n".join(f"{kind} [arch={arch}] {url} {suite} {components}"
                     for url, suite in archives for kind in ("deb", "deb-src"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.platform.resolve() / "scripts"))
    from platform_policy import target_profile

    policy = json.loads(args.policy.read_text())
    profile = target_profile(json.loads((args.platform / "packages.json").read_text()),
                             policy["targetId"])
    package = policy["source"]
    version = policy["baseVersion"]
    if not re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", package):
        raise ValueError("Invalid source package name")
    sources = base64.b64encode(public_sources(profile).encode()).decode()
    scan = subprocess.run([
        "docker", "run", "--rm", "--platform", profile["build"]["containerPlatform"],
        profile["baseImage"], "sh", "-eu", "-c", """
        rm -f /etc/apt/sources.list.d/*
        printf '%s' "$1" | base64 -d > /etc/apt/sources.list
        apt-get -o APT::Update::Error-Mode=any update >&2
        records="$(apt-cache showsrc --only-source "$2")"
        versions="$(printf '%s\n' "$records" | sed -n 's/^Version: //p')"
        newest=''
        pinned_available=no
        for version in $versions; do
          if [ -z "$newest" ] || dpkg --compare-versions "$version" gt "$newest"; then
            newest="$version"
          fi
          if [ "$version" = "$3" ]; then pinned_available=yes; fi
        done
        dependencies=unavailable
        if [ "$pinned_available" = yes ]; then
          if apt-get -s build-dep "$2=$3" >&2; then
            dependencies=resolved
          else
            dependencies=unresolved
          fi
        fi
        if [ -z "$newest" ]; then newest=none; fi
        printf '%s\n' "$newest" "$pinned_available" "$dependencies"
        """, "drift", sources, package, version,
    ], check=True, stdout=subprocess.PIPE, text=True)
    newest, pinned_available, dependencies = scan.stdout.splitlines()
    if newest != "none" and not re.fullmatch(r"(?:[0-9]+:)?[0-9][A-Za-z0-9.+~:-]*", newest):
        raise ValueError("Invalid source version from signed archive")
    source_page = (f"https://launchpad.net/ubuntu/+source/{package}"
                   if profile["distribution"] == "Ubuntu"
                   else f"https://tracker.debian.org/pkg/{package}")
    result = (f"{package} / {policy['targetId']}: pinned {version}, current public index "
              f"{newest}, pin visible {pinned_available}, dependencies {dependencies}.")
    print(result)
    if newest != version or dependencies != "resolved":
        print(f"::warning title=Source or build dependency drift::{result}")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as output:
            print(f"{result} [Distro source and updates]({source_page})", file=output)


if __name__ == "__main__":
    main()
