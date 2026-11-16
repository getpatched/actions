#!/usr/bin/env bash
set -euo pipefail

mkdir /repository
cd /repository
for archive in /candidate/*.deb; do
  ln -s "$archive" "${archive##*/}"
done
umask 022
export GNUPGHOME
GNUPGHOME=$(mktemp -d)
trap 'gpgconf --kill all; rm -rf "$GNUPGHOME"' EXIT

dpkg-scanpackages . /dev/null > Packages
test -s Packages
apt-ftparchive release . > "$GNUPGHOME/Release"
cp "$GNUPGHOME/Release" Release
gpg --batch --passphrase '' --quick-generate-key 'GetPatched CI test repository' rsa2048 sign 1d
gpg --batch --export > archive-keyring.gpg
test -s archive-keyring.gpg
gpg --batch --yes --clearsign --output InRelease Release
{
  gpg --batch --with-colons --fingerprint | awk -F: '$1 == "fpr" {print "Test signing fingerprint: " $10; exit}'
  sha256sum InRelease Packages ./*.deb
} > signed-apt.txt
