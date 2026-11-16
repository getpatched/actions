#!/usr/bin/env bash
set -euo pipefail
target=$1
version=$2
read -r -a dependencies <<< "$3"
shift 3
echo "::group::Install candidate packages"
# Let APT resolve absolute paths for local archives. The container has no network.
apt-get -o Acquire::Retries=0 -y install --no-install-recommends /candidate/*.deb "${dependencies[@]}"
for package in "$@"; do
  test "$(dpkg-query -W -f='${Version}' "$package")" = "$version"
  test "$(dpkg-query -W -f='${db:Status-Status}' "$package")" = installed
done
test -z "$(dpkg --audit)"
echo "::endgroup::"
if [[ -f /recipe/ci/$target/smoke.sh ]]; then
  echo "::group::Run installed package smoke tests"
  bash "/recipe/ci/$target/smoke.sh"
  echo "::endgroup::"
else
  echo "::warning::No ordinary-use smoke hook configured for $target; exact installation verified"
fi
