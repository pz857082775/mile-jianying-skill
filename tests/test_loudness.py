# ruff: noqa: E402

import json
import math
import os
import subprocess
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

from mile.analysis import capability_status, measure_loudness
from mile.decide import decide_timeline
from mile.errors import MileError


def _ffmpeg_available() -> bool:
    from utils.media_probe import find_ffmpeg

    return bool(find_ffmpeg())


def _tone_wav(path: Path, amplitude: float, seconds: float = 2.0, rate: int = 22050) -> Path:
    frames = array("h")
    total = int(rate * seconds)
    for index in range(total):
        frames.append(int(amplitude * 32767 * math.sin(2 * math.pi * 440 * index / rate)))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames.tobytes())
    return path


class MeasureLoudnessTests(unittest.TestCase):
    def test_tone_returns_stable_lufs_evidence(self):
        if not _ffmpeg_available():
            self.skipTest("ffmpeg 不可用，跳过响度实测")
        with tempfile.TemporaryDirectory(prefix="mile_loud_") as tmp:
            tone = _tone_wav(Path(tmp) / "tone.wav", 0.5)
            result = measure_loudness(tone)
            self.assertEqual(result["method"], "ffmpeg-ebur128")
            self.assertTrue(result["available"])
            self.assertIsNotNone(result["integrated_lufs"])
            self.assertTrue(-70 < result["integrated_lufs"] < 0)
            self.assertEqual(result["target_lufs"], -16.0)
            self.assertAlmostEqual(result["deviation_lu"], round(result["integrated_lufs"] - -16.0, 1))
            self.assertIn("不定位到具体片段", result["note"])
            self.assertFalse(result["silent"])

    def test_quiet_tone_is_quieter_than_loud_tone(self):
        if not _ffmpeg_available():
            self.skipTest("ffmpeg 不可用，跳过响度实测")
        with tempfile.TemporaryDirectory(prefix="mile_loud_") as tmp:
            quiet = measure_loudness(_tone_wav(Path(tmp) / "quiet.wav", 0.05))
            loud = measure_loudness(_tone_wav(Path(tmp) / "loud.wav", 0.8))
            self.assertLess(quiet["integrated_lufs"], loud["integrated_lufs"])
            self.assertGreater(quiet["suggested_volume"], 1.0)
            self.assertLess(loud["suggested_volume"], 1.0)

    def test_silence_is_flagged_not_guessed(self):
        if not _ffmpeg_available():
            self.skipTest("ffmpeg 不可用，跳过静音检测")
        with tempfile.TemporaryDirectory(prefix="mile_loud_") as tmp:
            silence = _tone_wav(Path(tmp) / "silence.wav", 0.0)
            result = measure_loudness(silence)
            self.assertTrue(result["silent"])
            self.assertIsNone(result["integrated_lufs"])
            self.assertNotIn("suggested_volume", result)

    def test_missing_file_and_bad_target(self):
        with self.assertRaises(MileError) as ctx:
            measure_loudness("Z:/definitely/missing.wav")
        self.assertEqual(ctx.exception.code, "FILE_NOT_FOUND")
        with tempfile.TemporaryDirectory(prefix="mile_loud_") as tmp:
            tone = _tone_wav(Path(tmp) / "tone.wav", 0.5)
            with self.assertRaises(MileError) as ctx:
                measure_loudness(tone, target_lufs="很响")
            self.assertEqual(ctx.exception.code, "BAD_RANGE")

    def test_ffmpeg_missing_returns_stable_code(self):
        with tempfile.TemporaryDirectory(prefix="mile_loud_") as tmp:
            tone = _tone_wav(Path(tmp) / "tone.wav", 0.5)
            import utils.media_probe as media_probe

            original = media_probe.find_ffmpeg
            media_probe.find_ffmpeg = lambda: None
            try:
                with self.assertRaises(MileError) as ctx:
                    measure_loudness(tone)
                self.assertEqual(ctx.exception.code, "FFMPEG_MISSING")
            finally:
                media_probe.find_ffmpeg = original

    def test_registry_reports_loudness(self):
        status = capability_status()
        self.assertIn("loudness", status)
        self.assertEqual(status["loudness"]["tier"], "T1")
        self.assertEqual(status["loudness"]["available"], _ffmpeg_available())


class DecideLoudnessTests(unittest.TestCase):
    def _audio(self, **loudness):
        base = {"available": True, "method": "ffmpeg-ebur128", "source": "voice.wav", "target_lufs": -16.0}
        base.update(loudness)
        return {"bpm": None, "loudness": base}

    def _decide(self, audio):
        return decide_timeline({"fps": 30, "segments": [], "duration": 0}, intent="讲解", audio=audio)

    def test_quiet_material_gets_review_decision(self):
        result = self._decide(self._audio(integrated_lufs=-27.8, deviation_lu=-11.8, suggested_gain_db=11.8, suggested_volume=3.9))
        review = [item for item in result["decisions"] if item["decision"] == "review-loudness"]
        self.assertEqual(len(review), 1)
        self.assertTrue(review[0]["manual_review"])
        self.assertEqual(review[0]["plan_ops"], [])
        self.assertIn("-27.8", review[0]["why"])

    def test_on_target_material_gets_no_loudness_decision(self):
        result = self._decide(self._audio(integrated_lufs=-16.5, deviation_lu=-0.5, suggested_gain_db=0.5, suggested_volume=1.06))
        self.assertFalse([item for item in result["decisions"] if item["decision"] == "review-loudness"])

    def test_silent_material_flags_check(self):
        result = self._decide(self._audio(integrated_lufs=None, silent=True))
        review = [item for item in result["decisions"] if item["decision"] == "review-loudness"]
        self.assertEqual(len(review), 1)
        self.assertIn("静音", review[0]["why"])

    def test_no_loudness_evidence_no_decision(self):
        result = self._decide({"bpm": 100, "loudness": {"available": False, "code": "FFMPEG_MISSING"}})
        self.assertFalse([item for item in result["decisions"] if item["decision"] == "review-loudness"])


class CliLoudnessTests(unittest.TestCase):
    def test_cli_decide_audio_attaches_loudness(self):
        if not _ffmpeg_available():
            self.skipTest("ffmpeg 不可用，跳过 CLI 响度冒烟")
        sys.path.insert(0, str(CURRENT_DIR))
        from test_mile import make_draft

        with tempfile.TemporaryDirectory(prefix="mile_loud_cli_") as tmp:
            root = Path(tmp) / "drafts"
            root.mkdir()
            make_draft(root)
            tone = _tone_wav(Path(tmp) / "voice.wav", 0.05)
            env = {
                **os.environ,
                "MILE_DRAFT_ROOT": str(root),
                "PYTHONIOENCODING": "utf-8",
                # 子进程 numpy/OpenBLAS 单线程，避免全量跑时线程池内存分配失败（OpenBLAS error）
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
            }
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "mile_cli.py"), "decide", "MileTestDraft", "--audio", str(tone), "--intent", "口播讲解"],
                capture_output=True,
                env=env,
                timeout=300,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
            payload = json.loads(completed.stdout.decode("utf-8"))
            self.assertTrue(payload["ok"])
            loudness = payload["data"]["evidence"]["audio"]["loudness"]
            self.assertEqual(loudness["method"], "ffmpeg-ebur128")
            decisions = [item["decision"] for item in payload["data"]["decisions"]]
            self.assertIn("review-loudness", decisions)


if __name__ == "__main__":
    unittest.main()
