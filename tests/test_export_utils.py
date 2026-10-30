# ruff: noqa: E402

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from utils.export_utils import (
    discover_exported_file,
    normalize_export_path,
    validate_exported_file,
)


class ExportUtilsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mile_export_")
        self.root = Path(self.tmp.name)
        self.now = time.time()

    def tearDown(self):
        self.tmp.cleanup()

    def _file(self, name: str, payload: bytes = b"x" * 16, age: float = 0.0) -> Path:
        path = self.root / name
        path.write_bytes(payload)
        stamp = self.now - age
        os.utime(path, (stamp, stamp))
        return path

    def test_normalize_quoted_path(self):
        path = normalize_export_path('  "C:\\temp\\out.mp4"  ')
        self.assertIsNotNone(path)
        self.assertEqual(path.name, "out.mp4")
        self.assertIsNone(normalize_export_path("  "))

    def test_discover_prefers_name_match_and_newest(self):
        self._file("Draft.mp4", age=3600)
        newest = self._file("Draft(1).mp4", age=1)
        self._file("Other.mp4", age=1)
        self._file("Draft(1).WAV", age=1)

        found = discover_exported_file(
            "Draft", self.now - 5, candidate=self.root / "Draft.mp4", search_dirs=[self.root]
        )
        self.assertEqual(found, newest)

    def test_discover_honors_new_candidate(self):
        newest = self._file("Draft.mp4", age=1)
        self.assertEqual(
            discover_exported_file(
                "Draft", self.now - 5, candidate=newest, search_dirs=[self.root]
            ),
            newest,
        )

    def test_discover_ignores_stale_files(self):
        self._file("Draft.mp4", age=7200)
        found = discover_exported_file(
            "Draft", self.now - 5, candidate=self.root / "Draft.mp4", search_dirs=[self.root]
        )
        self.assertIsNone(found)

    def test_discover_inside_same_name_folder(self):
        folder = self.root / "Draft(2)"
        folder.mkdir()
        exported = folder / "Draft(2).mp4"
        exported.write_bytes(b"video")
        stamp = self.now - 1
        os.utime(exported, (stamp, stamp))

        found = discover_exported_file(
            "Draft",
            self.now - 5,
            candidate=self.root / "Draft(2).mp4",
            search_dirs=[self.root],
        )
        self.assertEqual(found, exported)

    def test_validate_exported_file(self):
        valid = self._file("ok.mp4", payload=b"not-a-real-video")
        check = validate_exported_file(valid, probe=False)
        self.assertTrue(check["ok"])
        self.assertEqual(check["size"], len(b"not-a-real-video"))

        empty = self._file("empty.mp4", payload=b"")
        self.assertFalse(validate_exported_file(empty, probe=False)["ok"])

        missing = self.root / "missing.mp4"
        self.assertFalse(validate_exported_file(missing, probe=False)["ok"])

    def test_validate_uses_ffmpeg_metadata_without_ffprobe(self):
        target = self._file("ok.mp4", payload=b"x" * 32)
        fake = {
            "ok": True,
            "method": "ffmpeg",
            "duration": 4.71,
            "width": 2046,
            "height": 1080,
            "codec": "h264",
            "fps": 30.0,
        }
        with mock.patch("utils.export_utils.find_ffprobe", return_value=None), mock.patch(
            "utils.export_utils.probe_with_ffmpeg", return_value=fake
        ):
            check = validate_exported_file(target)
        self.assertTrue(check["ok"])
        self.assertEqual(check["method"], "ffmpeg")
        self.assertEqual(check["width"], 2046)
        self.assertAlmostEqual(check["duration"], 4.71)

    def test_validate_reports_ffmpeg_failure(self):
        target = self._file("bad.mp4", payload=b"x" * 32)
        with mock.patch("utils.export_utils.find_ffprobe", return_value=None), mock.patch(
            "utils.export_utils.probe_with_ffmpeg",
            return_value={"ok": False, "method": "ffmpeg", "error": "decode failed"},
        ):
            check = validate_exported_file(target)
        self.assertFalse(check["ok"])
        self.assertEqual(check["probe"], "failed")
        self.assertIn("decode failed", check["error"])


if __name__ == "__main__":
    unittest.main()
