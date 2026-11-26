import hashlib
import io
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch, Mock

from test_assets import download_asset, download_assets, load_assets, run_worker, SafeRedirect, validate_url


class TestAssets(unittest.TestCase):
    def asset(self, data=b"corpus"):
        return {"name": "corpus.tar.xz", "url": "https://download.gnome.org/corpus.tar.xz",
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

    def test_schema_rejects_untrusted_or_unbounded_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "assets.json"
            invalid = [{"name": "../escape"}, {"bytes": True}, {"bytes": 536870913},
                       {"url": "https://127.0.0.1/data"}, {"sha256": "bad"}]
            for change in invalid:
                path.write_text(json.dumps({"schemaVersion": 1, "assets": [self.asset() | change]}))
                with self.subTest(change=change), self.assertRaises(ValueError):
                    load_assets(path)
            path.write_text(json.dumps({"schemaVersion": 1, "assets": [self.asset(), self.asset()]}))
            with self.assertRaises(ValueError):
                load_assets(path)

    def test_redirect_cannot_escape_approved_origins(self):
        for url in ("http://download.gnome.org/data", "https://download.gnome.org.evil/data",
                    "https://user:pass@download.gnome.org/data", "https://download.gnome.org:444/data"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)
        with self.assertRaises(ValueError):
            SafeRedirect().redirect_request(None, None, 302, "", {}, "https://localhost/data")

    def download(self, asset, response, folder):
        opener = Mock()
        opener.open.return_value = io.BytesIO(response)
        folder.mkdir()
        with patch("test_assets.urllib.request.build_opener", return_value=opener):
            download_asset(asset, folder / asset["name"])

    def test_stalled_worker_is_killed_and_partial_file_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "assets"
            asset = self.asset()
            partial = root / asset["name"]

            def stalled(command, timeout):
                # Simulate a worker stuck within a blocking read after writing data.
                partial.write_bytes(b"partial")
                run_worker([sys.executable, "-c", "import time; time.sleep(30)"], 0.1)

            with patch("test_assets.run_worker", side_effect=stalled):
                with self.assertRaises(subprocess.TimeoutExpired):
                    download_assets([asset], root)
            self.assertFalse(partial.exists())

    def test_exact_download_retained_without_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "assets"
            self.download(self.asset(), b"corpus", root)
            self.assertEqual((root / "corpus.tar.xz").read_bytes(), b"corpus")
            self.assertEqual(len(list(root.iterdir())), 1)

    def test_mismatch_short_and_oversize_downloads_removed(self):
        for response in (b"wrong!", b"short", b"corpus with trailing data"):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as folder:
                root = Path(folder) / "assets"
                with self.assertRaises(ValueError):
                    self.download(self.asset(), response, root)
                self.assertFalse((root / "corpus.tar.xz").exists())


if __name__ == "__main__":
    unittest.main()
