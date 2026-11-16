#!/usr/bin/env bash
set -euo pipefail

case "$1" in
  ubuntu-*) release_file=/etc/lsb-release ;;
  debian-*) release_file=/etc/debian_version ;;
  *) echo "Unsupported scan target: $1" >&2; exit 1 ;;
esac

mkdir -p /reports
cp --dereference "$release_file" /reports/distro-release
cp /var/lib/dpkg/status /reports/dpkg-status
