# ruff: noqa: E402

import json
import sys
import tempfile
import unittest
import wave
from array import array
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS = CURRENT_DIR.parent / "scripts"
for entry in (str(SCRIPTS), str(CURRENT_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_mile import MileBase

from mile.analysis import analyze_media, analyze_timeline_signals, capability_status, list_capabilities
from mile.analysis.signal import _parse_kv_event
from mile.errors import MileError
from mile.timeline import build_timeline


def write_wav(path: Path, seconds: float = 2.0, rate: int = 22050) -> Path:
    frames = array("h")
    for index in range(int(rate * seconds)):
        frames.append(6000 if (index // (rate // 4)) % 2 == 0 else -6000)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames.tobytes())
    return path


class RegistryTests(unittest.TestCase):
    def test_signal_capability_available(self):
        status = capability_status()
        self.assertTrue(status["signal"]["available"])
        self.assertEqual(status["signal"]["tier"], "T0/T1")

    def test_optional_capabilities_report_missing(self):
        status = capability_status()
        for name in ("pyscenedetect", "sam2"):
            self.assertIn("available", status[name])
            self.assertIsInstance(status[name]["missing"], list)

    def test_list_capabilities_shape(self):
        items = list_capabilities()
        self.assertTrue(items)
        self.assertTrue(all("name" in item and "tier" in item for item in items))


class TimelineSignalTests(MileBase):
    def test_summary_counts_and_min_duration(self):
        timeline = build_timeline(self.draft.data)
        result = analyze_timeline_signals(timeline)
        self.assertEqual(result["method"], "timeline-signal-rules")
        self.assertEqual(result["captions"]["count"], 2)
        self.assertEqual(len(result["main"]), 2)
        self.assertAlmostEqual(result["min_main_duration_s"], 3.0, places=2)
        self.assertEqual(result["bgm"]["count"], 1)
        self.assertFalse(result["bgm"]["volume_high"])

    def test_short_and_long_flags(self):
        timeline = build_timeline(self.draft.data)
        main = [seg for seg in timeline["segments"] if seg["role"] == "main"]
        main[0]["duration"] = 300_000
        result = analyze_timeline_signals(timeline)
        self.assertIn("too_short", result["main"][0]["issues"])


class MediaSignalTests(unittest.TestCase):
    def test_missing_file_raises(self):
        with self.assertRaises(MileError) as ctx:
            analyze_media("definitely-missing-file.mp4")
        self.assertEqual(ctx.exception.code, "FILE_NOT_FOUND")

    def test_wav_audio_analysis(self):
        with tempfile.TemporaryDirectory() as temp:
            path = write_wav(Path(temp) / "tone.wav", 2.0)
            result = analyze_media(path)
            self.assertTrue(result["available"])
            self.assertIsNotNone(result["audio"])
            self.assertAlmostEqual(result["duration_s"], 2.0, places=1)
            self.assertIn("bpm", result["audio"])

    def test_parse_kv_event(self):
        line = "[blackdetect @ 0000] black_start:1.5 black_end:2.25 black_duration:0.75"
        parsed = _parse_kv_event(line, ["black_start", "black_end", "black_duration"])
        self.assertAlmostEqual(parsed["black_start"], 1.5)
        self.assertAlmostEqual(parsed["black_duration"], 0.75)


class AnalyzeCliTests(MileBase):
    def _run(self, argv):
        import io

        import mile_cli

        buffer = io.StringIO()
        import contextlib

        with contextlib.redirect_stdout(buffer):
            code = mile_cli.main(argv + ["--root", str(self.root)])
        return code, json.loads(buffer.getvalue())

    def test_analyze_draft_is_read_only(self):
        before = self.draft.content_path.read_bytes()
        code, payload = self._run(["analyze", self.draft.name])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["timeline"]["method"], "timeline-signal-rules")
        self.assertIn("signal", payload["data"]["capabilities"])
        self.assertEqual(self.draft.content_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
