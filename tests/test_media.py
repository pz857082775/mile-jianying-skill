# ruff: noqa: E402

import sys
import tempfile
import unittest
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS = CURRENT_DIR.parent / "scripts"
for entry in (str(SCRIPTS), str(CURRENT_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_mile import MileBase, make_draft

from mile import ops, quality
from mile.draft import list_backups, load_draft
from mile.errors import MileError
from mile.intent import parse_intent
from mile.plan import apply_plan, compile_plan
from mile.timeline import build_timeline

ASSETS = SCRIPTS.parent / "assets"
VIDEO = ASSETS / "video.mp4"
PNG = ASSETS / "click_marker.png"


def _require_assets():
    if not VIDEO.exists() or not PNG.exists():
        raise unittest.SkipTest("技能 assets 缺失，跳过素材入轨实测")


def _segment(data, seg_id):
    for track in data.get("tracks") or []:
        for segment in track.get("segments") or []:
            if segment.get("id") == seg_id:
                return track, segment
    raise AssertionError(f"segment not found: {seg_id}")


class AddMediaOpsTests(MileBase):
    def setUp(self):
        _require_assets()
        super().setUp()

    def test_append_video_to_main_track(self):
        result = ops.add_media(self.draft.data, str(VIDEO))
        self.assertEqual(result["kind"], "video")
        self.assertEqual(result["track_index"], 0)
        self.assertEqual(result["start"], 6_000_000)  # 测试草稿主轨现有 6s
        self.assertGreater(result["duration"], 0)
        track, seg = _segment(self.draft.data, result["id"])
        self.assertEqual(int(track.get("flag") or 0), 0)
        self.assertEqual(seg["uniform_scale"], {"on": True, "value": 1.0})
        self.assertTrue(seg["source_timerange"]["duration"] > 0)
        speed_ids = [str(m["id"]) for m in self.draft.data["materials"]["speeds"]]
        self.assertTrue(set(seg["extra_material_refs"]) <= set(speed_ids))
        material = ops._material(self.draft.data, seg["material_id"])
        self.assertEqual(material["type"], "video")
        self.assertGreater(int(material["width"]), 0)

    def test_photo_defaults_and_sticker_track(self):
        result = ops.add_media(self.draft.data, str(PNG), track="贴纸")
        self.assertEqual(result["kind"], "photo")
        self.assertEqual(result["duration"], 3_000_000)
        self.assertIn("3 秒", result["warning"])
        material = ops._material(self.draft.data, result["material_id"])
        self.assertEqual(material["type"], "photo")
        self.assertEqual(int(material["duration"]), 10_800_000_000)  # 剪映图片占位 3h
        track, _ = _segment(self.draft.data, result["id"])
        self.assertEqual(int(track["flag"]), 2)
        self.assertEqual(track["name"], "StickerTrack")
        timeline = build_timeline(self.draft.data)
        self.assertIn("sticker", {s["role"] for s in timeline["segments"]})

    def test_pip_track_created_once_and_reused(self):
        first = ops.add_media(self.draft.data, str(VIDEO), start="1s", duration="2s", track="pip")
        second = ops.add_media(self.draft.data, str(VIDEO), start="4s", duration="1s", track="画中画")
        self.assertEqual(first["track_index"], second["track_index"])
        overlay_tracks = [
            t for t in self.draft.data["tracks"] if _track_is_video_overlay(t)
        ]
        self.assertEqual(len(overlay_tracks), 1)

    def test_source_start_clamped_with_warning(self):
        probe = ops.probe_duration(str(VIDEO)) or 5_000_000
        source_start_s = round(probe * 0.5 / 1e6, 3)
        over_duration_s = round(probe * 1.5 / 1e6, 3)
        result = ops.add_media(
            self.draft.data, str(VIDEO), track="pip", source_start=f"{source_start_s}s", duration=f"{over_duration_s}s"
        )
        expected = probe - int(source_start_s * 1e6)
        self.assertEqual(result["duration"], expected)
        self.assertLess(result["duration"], int(over_duration_s * 1e6))
        self.assertIn("收敛", result["warning"])

    def test_validation_errors(self):
        with self.assertRaises(MileError) as ctx:
            ops.add_media(self.draft.data, "Z:/missing.mp4")
        self.assertEqual(ctx.exception.code, "MEDIA_NOT_FOUND")
        with self.assertRaises(MileError) as ctx:
            ops.add_media(self.draft.data, str(ASSETS / "audio.mp3"))
        self.assertEqual(ctx.exception.code, "BAD_ARGS")
        with self.assertRaises(MileError) as ctx:
            ops.add_media(self.draft.data, str(VIDEO), volume=9)
        self.assertEqual(ctx.exception.code, "BAD_RANGE")

    def test_qa_ok_after_media(self):
        ops.add_media(self.draft.data, str(VIDEO))
        ops.add_media(self.draft.data, str(PNG), track="pip")
        report = quality.run_checks(self.draft.data)
        self.assertTrue(report["ok"])
        self.assertFalse([i for i in report.get("issues", []) if i.get("severity") == "error"])


def _track_is_video_overlay(track):
    return str(track.get("type") or "") == "video" and int(track.get("flag") or 0) == 2


class AddMediaPlanTests(MileBase):
    def setUp(self):
        _require_assets()
        super().setUp()

    def _plan(self):
        return {
            "intent": "画中画补一段",
            "ops": [{"op": "加素材", "path": str(VIDEO), "start": "1s", "duration": "2s", "track": "pip"}],
        }

    def test_compile_and_dry_run(self):
        compiled = compile_plan(self.draft.data, self._plan())
        self.assertEqual(compiled["operations"][0]["op"], "add_media")
        result = apply_plan(self.draft, self._plan(), dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(len(self.draft.data["materials"]["videos"]), 1)
        self.assertEqual(list_backups(self.folder), [])

    def test_apply_backup_rollback(self):
        result = apply_plan(self.draft, self._plan())
        self.assertTrue(result["saved"])
        self.assertEqual(len(list_backups(self.folder)), 1)
        fresh = load_draft(self.folder)
        self.assertEqual(len(fresh.data["materials"]["videos"]), 2)

        plan = {
            "ops": [
                {"op": "add_media", "path": str(VIDEO), "track": "pip"},
                {"op": "set_filter", "target": {"ref": "SEGT1"}, "name": "1980"},  # 文字片段 → NOT_VIDEO
            ]
        }
        with self.assertRaises(MileError) as ctx:
            apply_plan(self.draft, plan)
        self.assertEqual(ctx.exception.code, "NOT_VIDEO")
        self.assertEqual(len(self.draft.data["materials"]["videos"]), 2)
        self.assertEqual(len(list_backups(self.folder)), 1)

    def test_missing_path_validation(self):
        with self.assertRaises(MileError) as ctx:
            compile_plan(self.draft.data, {"ops": [{"op": "add_media"}]})
        self.assertEqual(ctx.exception.code, "MISSING_ARGS")


class AddMediaIntentTests(MileBase):
    def setUp(self):
        _require_assets()
        super().setUp()

    def parse(self, text):
        return parse_intent(text, timeline=build_timeline(self.draft.data))

    def test_relative_path_and_point(self):
        parsed = self.parse(f"把 {VIDEO.as_posix()} 加到第3秒")
        operation = parsed["plan"]["ops"][0]
        self.assertEqual(operation["op"], "add_media")
        self.assertEqual(operation["start"], 3.0)

    def test_sticker_and_pip_targets(self):
        sticker = self.parse(f"加个贴纸 {PNG.as_posix()}")["plan"]["ops"][0]
        self.assertEqual(sticker["track"], "sticker")
        pip = self.parse(f"画中画插入 {VIDEO.as_posix()} 从2秒到6秒")["plan"]["ops"][0]
        self.assertEqual(pip["track"], "pip")
        self.assertEqual((pip["start"], pip["duration"]), (2.0, 4.0))

    def test_append_without_time_warns(self):
        parsed = self.parse(f"把 {VIDEO.as_posix()} 加进来")
        self.assertEqual(parsed["plan"]["ops"][0]["op"], "add_media")
        self.assertTrue(any("末尾" in warning for warning in parsed["warnings"]))

    def test_audio_path_still_add_audio(self):
        parsed = self.parse("把BGM换成D:/music/a.mp3")
        self.assertEqual([op["op"] for op in parsed["plan"]["ops"]][-1], "add_audio")


if __name__ == "__main__":
    unittest.main()
