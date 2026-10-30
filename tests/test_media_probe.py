# ruff: noqa: E402

import os
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from utils.media_probe import (
    check_assets,
    find_ffmpeg,
    image_size,
    parse_ffmpeg_metadata,
    probe_media_duration,
    probe_media_file,
    validate_image_file,
)
from utils.sizing import scale_for_pixel_size


def make_png(width: int = 1, height: int = 1) -> bytes:
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class MediaProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mile_media_")
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_png(self):
        path = self.root / "ok.png"
        path.write_bytes(make_png(2, 3))
        info = validate_image_file(path)
        self.assertTrue(info["ok"])
        self.assertEqual((info["width"], info["height"]), (2, 3))
        self.assertEqual(image_size(path), (2, 3))

    def test_corrupted_png(self):
        path = self.root / "broken.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage" * 8)
        self.assertFalse(validate_image_file(path)["ok"])
        self.assertIsNone(image_size(path))

    def test_check_assets_reports_bad_file(self):
        (self.root / "ok.png").write_bytes(make_png())
        (self.root / "broken.png").write_bytes(b"\x89PNG\r\n\x1a\nbroken")
        (self.root / "notes.txt").write_text("skip", encoding="utf-8")

        result = check_assets(self.root)
        self.assertFalse(result["ok"])
        self.assertEqual(result["checked"], 2)
        self.assertEqual(len(result["bad"]), 1)
        self.assertTrue(result["bad"][0]["path"].endswith("broken.png"))

    def test_find_ffmpeg_in_jianying_home(self):
        install = self.root / "JianyingPro" / "5.9.0.11632"
        install.mkdir(parents=True)
        ffmpeg = install / "ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")

        env = {
            "JIANYING_HOME": str(self.root / "JianyingPro"),
            "LOCALAPPDATA": str(self.root / "empty"),
            "PROGRAMFILES": str(self.root / "empty"),
            "PROGRAMFILES(X86)": str(self.root / "empty"),
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch(
            "utils.media_probe.shutil.which", return_value=None
        ):
            for key in ("MILE_FFMPEG", "JY_FFMPEG"):
                os.environ.pop(key, None)
            self.assertEqual(find_ffmpeg(), str(ffmpeg))

    def test_find_ffmpeg_from_running_jianying(self):
        install = self.root / "JianyingPro" / "5.9.0.11632"
        install.mkdir(parents=True)
        ffmpeg = install / "ffmpeg.exe"
        ffmpeg.write_bytes(b"MZ")

        env = {
            "JIANYING_HOME": "",
            "LOCALAPPDATA": str(self.root / "empty"),
            "PROGRAMFILES": str(self.root / "empty"),
            "PROGRAMFILES(X86)": str(self.root / "empty"),
        }
        with mock.patch.dict(os.environ, env, clear=False), mock.patch(
            "utils.media_probe.shutil.which", return_value=None
        ), mock.patch(
            "utils.media_probe._running_process_path",
            return_value=install / "JianyingPro.exe",
        ):
            for key in ("MILE_FFMPEG", "JY_FFMPEG"):
                os.environ.pop(key, None)
            self.assertEqual(find_ffmpeg(), str(ffmpeg))

    def test_parse_ffmpeg_metadata(self):
        text = """
  Duration: 00:00:04.71, start: 0.000000, bitrate: 18748 kb/s
  Stream #0:0[0x1](und): Video: h264 (Main) (avc1 / 0x31637661), yuv420p, 2046x1080 [SAR 1:1], 18598 kb/s, 30 fps, 30 tbr
  Stream #0:1[0x2](und): Audio: aac (LC) (mp4a / 0x6134706D), 44100 Hz, stereo
"""
        info = parse_ffmpeg_metadata(text)
        self.assertAlmostEqual(info["duration"], 4.71)
        self.assertEqual((info["width"], info["height"]), (2046, 1080))
        self.assertEqual(info["codec"], "h264")
        self.assertEqual(info["fps"], 30.0)

    def test_probe_media_file_falls_back_to_ffmpeg(self):
        target = self.root / "clip.mp4"
        target.write_bytes(b"x")
        with mock.patch("utils.media_probe._ffprobe_info", return_value=None), mock.patch(
            "utils.media_probe._ffmpeg_info",
            return_value={"ok": True, "method": "ffmpeg", "duration": 2.5, "width": 640, "height": 360},
        ):
            info = probe_media_file(target)
        self.assertTrue(info["ok"])
        self.assertEqual(info["method"], "ffmpeg")
        self.assertEqual(info["width"], 640)

    def test_probe_media_duration_uses_ffmpeg(self):
        target = self.root / "clip.mp4"
        target.write_bytes(b"x")
        with mock.patch("utils.media_probe._ffprobe_info", return_value=None), mock.patch(
            "utils.media_probe._ffmpeg_info",
            return_value={"ok": True, "method": "ffmpeg", "duration": 3.25},
        ):
            self.assertAlmostEqual(probe_media_duration(target), 3.25)

    def test_probe_media_file_keeps_ffprobe_failure(self):
        target = self.root / "broken.mp4"
        target.write_bytes(b"x")
        with mock.patch(
            "utils.media_probe._ffprobe_info",
            return_value={"ok": False, "error": "invalid data"},
        ), mock.patch(
            "utils.media_probe._ffmpeg_info",
            return_value={"ok": True, "method": "ffmpeg", "duration": 1.0},
        ):
            info = probe_media_file(target)
        self.assertFalse(info["ok"])
        self.assertEqual(info["error"], "invalid data")


class SizingTests(unittest.TestCase):
    def test_height_pixel_conversion(self):
        scale = scale_for_pixel_size(2046, 1080, 128, 128, height_px=108)
        self.assertAlmostEqual(scale, 0.1)

    def test_width_pixel_conversion(self):
        scale = scale_for_pixel_size(1920, 1080, 200, 100, width_px=192)
        self.assertAlmostEqual(scale, 192 / 1920)

    def test_requires_exactly_one_target(self):
        with self.assertRaises(ValueError):
            scale_for_pixel_size(1920, 1080, 100, 100)
        with self.assertRaises(ValueError):
            scale_for_pixel_size(1920, 1080, 100, 100, width_px=10, height_px=10)
        with self.assertRaises(ValueError):
            scale_for_pixel_size(1920, 1080, 100, 100, width_px=0)


if __name__ == "__main__":
    unittest.main()
