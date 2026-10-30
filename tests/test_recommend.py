# ruff: noqa: E402

import array
import math
import sys
import tempfile
import unittest
import wave
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS = CURRENT_DIR.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mile.errors import MileError
from mile.recommend import (
    analyze_audio,
    load_music_library,
    rank_bgm,
    recommend_transitions,
)

try:
    import numpy  # noqa: F401

    HAS_NUMPY = True
except Exception:
    HAS_NUMPY = False


def write_click_track(path: Path, *, bpm: float = 120, seconds: float = 8.0, rate: int = 22050) -> Path:
    interval = 60.0 / bpm
    samples = []
    for index in range(int(seconds * rate)):
        moment = index / rate
        phase = moment % interval
        value = 0.0
        if phase < 0.04:
            value = 0.6 * math.sin(2 * math.pi * 1000 * phase) * math.exp(-phase * 80)
        samples.append(int(max(-1.0, min(1.0, value)) * 32767))
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(array.array("h", samples).tobytes())
    return path


def segment(track_index, index_in_track, start_s, duration_s, material_id, ref):
    start = int(start_s * 1_000_000)
    duration = int(duration_s * 1_000_000)
    return {
        "ref": ref,
        "id": ref.lower(),
        "role": "main",
        "track_type": "video",
        "track_index": track_index,
        "index_in_track": index_in_track,
        "start": start,
        "duration": duration,
        "end": start + duration,
        "material_id": material_id,
    }


class AudioAnalysisTests(unittest.TestCase):
    @unittest.skipUnless(HAS_NUMPY, "音频分析需要 numpy")
    def test_detects_tempo_and_features(self):
        with tempfile.TemporaryDirectory(prefix="mile_audio_") as folder:
            audio = write_click_track(Path(folder) / "click120.wav", bpm=120, seconds=8)
            result = analyze_audio(audio)
        self.assertAlmostEqual(result["duration_s"], 8.0, places=1)
        self.assertLess(abs(result["bpm"] - 120), 3)
        self.assertGreater(result["bpm_confidence"], 0.6)
        self.assertEqual(result["bpm_method"], "onset-autocorrelation")
        self.assertEqual(result["mood"]["tempo"], "中")
        self.assertEqual(result["mood"]["energy"], "低")
        self.assertIn("中节奏", result["mood"]["tags"])

    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(MileError) as context:
                analyze_audio(Path(folder) / "missing.wav")
            self.assertEqual(context.exception.code, "FILE_NOT_FOUND")


class MusicLibraryTests(unittest.TestCase):
    def setUp(self):
        self.library = [
            {
                "music_id": "1",
                "title": "轻快 Vlog 日常",
                "duration_s": 60.0,
                "categories": ["VLOG"],
                "url": "",
            },
            {
                "music_id": "2",
                "title": "史诗 电影 氛围",
                "duration_s": 10.0,
                "categories": ["电影"],
                "url": "",
            },
            {
                "music_id": "3",
                "title": "轻快 Vlog 短版",
                "duration_s": 5.0,
                "categories": ["VLOG"],
                "url": "",
            },
        ]

    def test_load_csv_with_comments_and_pipes(self):
        content = (
            "# 米乐曲库\n"
            "music_id,title,duration_s,categories,url\n"
            "101,轻快 Vlog,60,VLOG|日常,https://example.com/a\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "library.csv"
            path.write_text(content, encoding="utf-8")
            items = load_music_library(path)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["categories"], ["VLOG", "日常"])
        self.assertEqual(items[0]["duration_s"], 60.0)

    def test_rank_prefers_tag_and_duration_match(self):
        result = rank_bgm(self.library, duration_s=20, tags=["Vlog"], limit=3)
        self.assertEqual(result["method"], "metadata-rules")
        self.assertEqual(result["items"][0]["music_id"], "1")
        self.assertGreater(result["items"][0]["score"], result["items"][1]["score"])
        self.assertTrue(any("标签命中" in reason for reason in result["items"][0]["reasons"]))
        short = next(item for item in result["items"] if item["music_id"] == "3")
        self.assertTrue(any("时长不足" in reason for reason in short["reasons"]))

    def test_rank_defaults_to_vlog(self):
        result = rank_bgm(self.library, limit=1)
        self.assertEqual(result["desired"], ["VLOG"])


class TransitionRecommendationTests(unittest.TestCase):
    def test_same_material_uses_short_dissolve(self):
        timeline = {"segments": [segment(0, 0, 0, 3, "m1", "A"), segment(0, 1, 3, 3, "m1", "B")]}
        result = recommend_transitions(timeline)
        self.assertEqual(result["method"], "duration-rules")
        item = result["items"][0]
        self.assertEqual(item["transition"], "叠化")
        self.assertEqual(item["duration"], 0.25)
        self.assertIn("同一素材", item["reason"])

    def test_gap_recommends_no_transition(self):
        timeline = {"segments": [segment(0, 0, 0, 3, "m1", "A"), segment(0, 1, 4, 3, "m2", "B")]}
        item = recommend_transitions(timeline)["items"][0]
        self.assertEqual(item["transition"], "无")
        self.assertIn("空隙", item["reason"])

    def test_short_shot_recommends_hard_cut(self):
        timeline = {"segments": [segment(0, 0, 0, 1, "m1", "A"), segment(0, 1, 1, 1, "m2", "B")]}
        item = recommend_transitions(timeline)["items"][0]
        self.assertEqual(item["transition"], "无")
        self.assertIn("不足 1.2s", item["reason"])

    def test_bpm_sets_half_beat_duration(self):
        timeline = {"segments": [segment(0, 0, 0, 3, "m1", "A"), segment(0, 1, 3, 3, "m2", "B")]}
        item = recommend_transitions(timeline, bpm=120)["items"][0]
        self.assertEqual(item["transition"], "叠化")
        self.assertEqual(item["duration"], 0.25)
        self.assertIn("120 BPM", item["reason"])

    def test_single_segment_has_no_boundary(self):
        timeline = {"segments": [segment(0, 0, 0, 3, "m1", "A")]}
        self.assertEqual(recommend_transitions(timeline)["count"], 0)


if __name__ == "__main__":
    unittest.main()
