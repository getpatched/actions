#!/usr/bin/env bash
set -euo pipefail

mkdir -p /reports
cp --dereference /etc/os-release /reports/os-release
cp /var/lib/dpkg/status /reports/dpkg-status
