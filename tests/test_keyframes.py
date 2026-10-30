# ruff: noqa: E402

import sys
import unittest
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS = CURRENT_DIR.parent / "scripts"
for entry in (str(SCRIPTS), str(CURRENT_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_mile import MileBase

from mile import ops, quality
from mile.draft import list_backups, load_draft
from mile.errors import MileError
from mile.intent import parse_intent
from mile.plan import apply_plan, compile_plan
from mile.timeline import build_timeline


def _kf_segment(data, seg_id):
    for track in data.get("tracks") or []:
        for segment in track.get("segments") or []:
            if segment.get("id") == seg_id:
                return segment
    raise AssertionError(f"segment not found: {seg_id}")


class KeyframeOpsTests(MileBase):
    def test_uniform_scale_writes_5_9_schema(self):
        result = ops.add_keyframes(
            self.draft.data, ["segv1"], "scale", [{"at": "0s", "value": 1.0}, {"at": "2s", "value": 1.5}]
        )
        self.assertEqual(result["changed"][0]["property"], "KFTypeScaleX")
        self.assertEqual(result["changed"][0]["total"], 2)
        seg = _kf_segment(self.draft.data, "segv1")
        self.assertTrue(seg["uniform_scale"]["on"])
        kf_list = seg["common_keyframes"][0]
        self.assertEqual(kf_list["property_type"], "KFTypeScaleX")
        self.assertEqual(kf_list["keyframe_list"][0]["curveType"], "Line")
        self.assertEqual(kf_list["keyframe_list"][1]["time_offset"], 2_000_000)
        self.assertEqual(kf_list["keyframe_list"][1]["values"], [1.5])

    def test_merge_sorts_and_keeps_single_list_per_property(self):
        ops.add_keyframes(self.draft.data, ["segv1"], "scale", [{"at": "2s", "value": 1.5}])
        ops.add_keyframes(self.draft.data, ["segv1"], "scale", [{"at": "0.5s", "value": 1.0}])
        seg = _kf_segment(self.draft.data, "segv1")
        self.assertEqual(len(seg["common_keyframes"]), 1)
        offsets = [item["time_offset"] for item in seg["common_keyframes"][0]["keyframe_list"]]
        self.assertEqual(offsets, sorted(offsets))

    def test_axis_scale_turns_uniform_off(self):
        ops.add_keyframes(self.draft.data, ["segv1"], "scale_x", [{"at": "1s", "value": 1.2}])
        seg = _kf_segment(self.draft.data, "segv1")
        self.assertFalse(seg["uniform_scale"]["on"])

    def test_time_beyond_segment_clamped_with_warning(self):
        result = ops.add_keyframes(self.draft.data, ["segv1"], "scale", [{"at": "9s", "value": 2.0}])
        self.assertIn("收敛", result["changed"][0]["warning"])
        seg = _kf_segment(self.draft.data, "segv1")
        self.assertEqual(seg["common_keyframes"][0]["keyframe_list"][0]["time_offset"], 3_000_000)

    def test_property_validation(self):
        with self.assertRaises(MileError) as ctx:
            ops.add_keyframes(self.draft.data, ["segv1"], "模糊度", [{"at": "1s", "value": 1}])
        self.assertEqual(ctx.exception.code, "BAD_ARGS")
        with self.assertRaises(MileError) as ctx:
            ops.add_keyframes(self.draft.data, ["segv1"], "scale", [])
        self.assertEqual(ctx.exception.code, "BAD_ARGS")
        with self.assertRaises(MileError) as ctx:
            ops.add_keyframes(self.draft.data, ["segv1"], "scale", [{"at": "1s", "value": "很大"}])
        self.assertEqual(ctx.exception.code, "BAD_RANGE")

    def test_alpha_rejects_audio_target(self):
        with self.assertRaises(MileError) as ctx:
            ops.add_keyframes(self.draft.data, ["sega1"], "alpha", [{"at": "1s", "value": 0.5}])
        self.assertEqual(ctx.exception.code, "BAD_TARGET")

    def test_remove_keyframes_all_and_by_property(self):
        ops.add_keyframes(self.draft.data, ["segv1"], "scale", [{"at": "1s", "value": 1.5}])
        ops.add_keyframes(self.draft.data, ["segv1"], "rotation", [{"at": "1s", "value": 10}])
        ops.remove_keyframes(self.draft.data, ["segv1"], "rotation")
        seg = _kf_segment(self.draft.data, "segv1")
        self.assertEqual([item["property_type"] for item in seg["common_keyframes"]], ["KFTypeScaleX"])
        ops.remove_keyframes(self.draft.data, ["segv1"])
        self.assertEqual(seg["common_keyframes"], [])

    def test_volume_keyframe_allowed_on_audio(self):
        result = ops.add_keyframes(self.draft.data, ["sega1"], "volume", [{"at": "0s", "value": 1.0}, {"at": "2s", "value": 0.2}])
        self.assertEqual(result["changed"][0]["property"], "KFTypeVolume")


class KeyframePlanTests(MileBase):
    def _plan(self):
        return {
            "intent": "局部放大",
            "ops": [
                {
                    "op": "add_keyframes",
                    "target": {"ref": "SEGV1"},
                    "property": "scale",
                    "frames": [{"at": 0, "value": 1.0}, {"at": 2, "value": 1.5}],
                }
            ],
        }

    def test_dry_run_does_not_touch_draft(self):
        result = apply_plan(self.draft, self._plan(), dry_run=True)
        self.assertTrue(result["dry_run"])
        seg = _kf_segment(self.draft.data, "segv1")
        self.assertNotIn("common_keyframes", seg)
        self.assertEqual(list_backups(self.folder), [])

    def test_apply_persist_and_backup(self):
        result = apply_plan(self.draft, self._plan())
        self.assertEqual(len(result["operations"]), 1)
        self.assertEqual(len(list_backups(self.folder)), 1)
        fresh = load_draft(self.folder)
        seg = _kf_segment(fresh.data, "segv1")
        self.assertEqual(seg["common_keyframes"][0]["property_type"], "KFTypeScaleX")
        report = quality.run_checks(fresh.data)
        self.assertTrue(report["ok"])

    def test_missing_args_validation(self):
        with self.assertRaises(MileError) as ctx:
            compile_plan(self.draft.data, {"ops": [{"op": "add_keyframes", "target": {"ref": "SEGV1"}}]})
        self.assertEqual(ctx.exception.code, "MISSING_ARGS")

    def test_rollback_on_late_failure(self):
        plan = {
            "ops": [
                self._plan()["ops"][0],
                {"op": "add_keyframes", "target": {"ref": "SEGV1"}, "property": "模糊度", "frames": [{"at": 1, "value": 1}]},
            ]
        }
        with self.assertRaises(MileError) as ctx:
            apply_plan(self.draft, plan)
        self.assertEqual(ctx.exception.code, "BAD_ARGS")
        seg = _kf_segment(self.draft.data, "segv1")
        self.assertNotIn("common_keyframes", seg)
        self.assertEqual(list_backups(self.folder), [])


class KeyframeIntentTests(MileBase):
    def parse(self, text):
        return parse_intent(text, timeline=build_timeline(self.draft.data))

    def test_range_phrase_becomes_two_frames(self):
        parsed = self.parse("从第2秒到第5秒把画面放大到1.5倍")
        self.assertEqual(parsed["clarify"], [], parsed["clarify"])
        operation = parsed["plan"]["ops"][0]
        self.assertEqual(operation["op"], "add_keyframes")
        self.assertEqual(operation["property"], "scale")
        self.assertEqual([frame["at"] for frame in operation["frames"]], [2.0, 5.0])
        self.assertEqual([frame["value"] for frame in operation["frames"]], [1.0, 1.5])

    def test_point_phrase_builds_from_zero(self):
        parsed = self.parse("在第4秒把画面放大到2倍")
        operation = parsed["plan"]["ops"][0]
        self.assertEqual([frame["at"] for frame in operation["frames"]], [0.0, 4.0])

    def test_missing_time_clarifies(self):
        parsed = self.parse("把画面放大到1.5倍")
        self.assertIsNone(parsed["plan"])
        self.assertTrue(any("时间点" in item for item in parsed["clarify"]))

    def test_keyframe_no_longer_unsupported(self):
        parsed = self.parse("从第1秒到第2秒把画面放大到1.2倍")
        self.assertFalse([warning for warning in parsed["warnings"] if "暂不覆盖" in warning and "关键帧" in warning])

    def test_plan_from_intent_applies(self):
        parsed = self.parse("从第1秒到第2秒把第1个视频片段放大到1.5倍")
        result = apply_plan(self.draft, parsed["plan"], dry_run=True)
        self.assertEqual(result["operations"][0]["op"], "add_keyframes")


if __name__ == "__main__":
    unittest.main()
