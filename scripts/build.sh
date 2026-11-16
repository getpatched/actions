#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C.UTF-8
export DEB_BUILD_OPTIONS='check parallel=1'
mkdir -p /build /reports
echo "patch" > /reports/stage
descriptors=(/originals/*.dsc)
test "${#descriptors[@]}" -eq 1
dpkg-source -x "${descriptors[0]}" /build/source
cp /originals/*.orig.tar.* /build/
cd /build/source
python3 /materialize-package.py --recipe /recipe --source /build/source --target "$1" \
  --policy /policy.json --provenance /policy.json --source-provenance /reports/source.json
dpkg-source --before-build .
echo "build and distro tests" > /reports/stage
dpkg-buildpackage -b -us -uc -j1
cp /build/*.buildinfo /reports/
if [[ -f /recipe/ci/$1/upstream.sh ]]; then
  echo "additional upstream tests" > /reports/stage
  bash "/recipe/ci/$1/upstream.sh"
else
  echo "No additional upstream suite configured"
fi
mkdir -p /reports/debs
cp /build/*.deb /reports/debs/
echo "built" > /reports/stage
