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
config="/recipe/ci/$1/upstream-tests.json"
hook="/recipe/ci/$1/upstream.sh"
if [[ -f "$config" && -f "$hook" ]]; then
  echo "Configure either upstream-tests.json or upstream.sh, not both" >&2
  exit 1
fi
if [[ -f "$config" || -f "$hook" ]]; then
  # dpkg-buildpackage may unapply quilt patches during its after-build hook.
  echo "patch" > /reports/stage
  dpkg-source --before-build .
  echo "additional upstream tests" > /reports/stage
  if [[ -f "$config" ]]; then
    python3 /upstream_tests.py --config "$config" --report /reports/upstream-junit.xml
  else
    bash "$hook"
  fi
else
  echo "No additional upstream suite configured"
fi
mkdir -p /reports/debs
cp /build/*.deb /reports/debs/
echo "built" > /reports/stage
