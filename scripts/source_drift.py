"""Compare checked-in source pins with signed public distribution indexes."""

import argparse
import base64
import json
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
    subprocess.run([
        "docker", "run", "--rm", "--platform", profile["build"]["containerPlatform"],
        profile["baseImage"], "sh", "-eu", "-c", """
        rm -f /etc/apt/sources.list.d/*
        printf '%s' "$1" | base64 -d > /etc/apt/sources.list
        apt-get -o APT::Update::Error-Mode=any update
        versions="$(apt-cache showsrc --only-source "$2" | sed -n 's/^Version: //p')"
        test -n "$versions"
        newest="$3"
        for version in $versions; do
          if dpkg --compare-versions "$version" gt "$newest"; then newest="$version"; fi
        done
        if [ "$newest" != "$3" ]; then
          printf '::warning title=Source drift::%s pins %s; signed public indexes contain %s. Review distro fixes before updating.\n' "$2" "$3" "$newest"
        fi
        if ! apt-get -s build-dep "$2=$3"; then
          printf '::warning title=Build dependency drift::The current public index could not resolve build dependencies for %s=%s.\n' "$2" "$3"
        fi
        """, "drift", sources, package, version,
    ], check=True)


if __name__ == "__main__":
    main()
