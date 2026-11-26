"""Fetch checksum-pinned test data before entering the offline build container.

Archives are deliberately never extracted on the runner host.
"""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse
import urllib.request


ORIGINS = {"download.gnome.org", "gitlab.freedesktop.org", "www.w3.org"}
MAX_BYTES = 512 * 1024 * 1024


def validate_url(url):
    if not isinstance(url, str):
        raise ValueError("Test asset URL must be a string")
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in ORIGINS
            or parsed.username is not None or parsed.password is not None
            or parsed.port not in (None, 443) or parsed.fragment):
        raise ValueError("Test assets must use an approved HTTPS origin")
    return url


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        validate_url(url)
        return super().redirect_request(request, response, code, message, headers, url)


def load_assets(path):
    config = json.loads(path.read_text())
    if not isinstance(config, dict):
        raise ValueError("Expected test asset configuration object")
    assets = config.get("assets")
    if (config.get("schemaVersion") != 1 or not isinstance(assets, list)
            or not 1 <= len(assets) <= 8):
        raise ValueError("Expected schemaVersion 1 and 1–8 test assets")
    names = set()
    total = 0
    for asset in assets:
        if not isinstance(asset, dict):
            raise ValueError("Expected test asset object")
        name = asset.get("name")
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name)
                or name in names):
            raise ValueError("Test asset names must be unique plain filenames")
        names.add(name)
        if not re.fullmatch(r"[0-9a-f]{64}", asset.get("sha256", "")):
            raise ValueError("Test assets require a SHA-256 digest")
        size = asset.get("bytes")
        if type(size) is not int or not 1 <= size <= MAX_BYTES:
            raise ValueError("Invalid test asset byte count")
        total += size
        validate_url(asset.get("url", ""))
    if total > MAX_BYTES:
        raise ValueError("Combined test assets exceed 512 MiB")
    return assets


def download_assets(assets, destination):
    destination.mkdir()
    for asset in assets:
        path = destination / asset["name"]
        try:
            run_worker([sys.executable, str(Path(__file__).resolve()), json.dumps(asset), str(path)], 300)
        except Exception:
            path.unlink(missing_ok=True)
            raise


def run_worker(command, timeout):
    # A process timeout covers DNS, connect, redirect and slow-drip reads.
    subprocess.run(command, check=True, timeout=timeout)


def download_asset(asset, path):
    opener = urllib.request.build_opener(SafeRedirect())
    digest = hashlib.sha256()
    count = 0
    try:
        validate_url(asset["url"])
        request = urllib.request.Request(asset["url"], headers={"User-Agent": "getpatched-package-ci"})
        with opener.open(request, timeout=120) as response, path.open("xb") as output:
            while chunk := response.read(min(1024 * 1024, asset["bytes"] - count + 1)):
                count += len(chunk)
                if count > asset["bytes"]:
                    raise ValueError(f"Test asset exceeds pinned size: {asset['name']}")
                digest.update(chunk)
                output.write(chunk)
        if count != asset["bytes"] or digest.hexdigest() != asset["sha256"]:
            raise ValueError(f"Test asset checksum/size mismatch: {asset['name']}")
    except Exception:
        path.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    download_asset(json.loads(sys.argv[1]), Path(sys.argv[2]))
