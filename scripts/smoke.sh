#!/usr/bin/env bash
set -euo pipefail
target=$1
version=$2
read -r -a dependencies <<< "$3"
shift 3
mkdir -p /reports
echo "clean install" > /reports/stage
echo "::group::Install candidate packages"
# Fetch the candidate through signed metadata in this offline, clean client.
printf '%s\n' 'deb [signed-by=/candidate/archive-keyring.gpg] file:/candidate ./' \
  > /etc/apt/sources.list.d/getpatched-test.list
apt-get -o Dir::Etc::sourcelist=sources.list.d/getpatched-test.list \
  -o Dir::Etc::sourceparts=- -o APT::Get::List-Cleanup=false \
  -o Acquire::AllowInsecureRepositories=false update
candidate_requests=()
for package in "$@"; do
  candidate_requests+=("$package=$version")
done
apt-get -o Acquire::Retries=0 -y install --no-install-recommends \
  "${candidate_requests[@]}" "${dependencies[@]}"
verify_install() {
  local audit
  for package in "$@"; do
    test "$(dpkg-query -W -f='${Version}' "$package")" = "$version"
    test "$(dpkg-query -W -f='${db:Status-Status}' "$package")" = installed
  done
  audit=$(dpkg --audit)
  test -z "$audit"
}
verify_install "$@"
echo "::endgroup::"
if [[ -f /recipe/ci/$target/smoke.sh ]]; then
  echo "installed package smoke test" > /reports/stage
  echo "::group::Run installed package smoke tests"
  bash "/recipe/ci/$target/smoke.sh"
  verify_install "$@"
  echo "::endgroup::"
else
  echo "::warning::No ordinary-use smoke hook configured for $target; exact installation verified"
fi
if [[ -f /recipe/ci/$target/autopkgtest.sh ]]; then
  echo "installed distro tests" > /reports/stage
  echo "::group::Run installed distro tests"
  bash "/recipe/ci/$target/autopkgtest.sh"
  verify_install "$@"
  echo "::endgroup::"
else
  echo "No installed distro test suite configured for $target"
fi
