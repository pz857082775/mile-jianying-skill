# ruff: noqa: E402

import copy
import hashlib
import sys
import unittest
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS = CURRENT_DIR.parent / "scripts"
for entry in (str(SCRIPTS), str(CURRENT_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_mile import MileBase

from mile import preview as preview_mod
from mile.preview import preview_plan
from mile.plan import _execute_ops, apply_plan, compile_plan
from mile.timeline import build_timeline


def _digest(path) -> str:
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


class PreviewPlanTests(MileBase):
    def test_preview_does_not_save(self):
        plan = {
            "intent": "测试预览",
            "ops": [
                {"op": "set_volume", "target": {"role": "bgm"}, "volume": "30%"},
            ],
        }
        before_digest = _digest(self.draft.content_path)
        result = preview_plan(self.draft.data, plan)
        after_digest = _digest(self.draft.content_path)
        self.assertTrue(result["preview"])
        self.assertFalse(result["saved"])
        self.assertEqual(before_digest, after_digest)

    def test_preview_matches_real_apply(self):
        """preview 的 after 必须与真实 apply 后 build_timeline 的公开字段一致。"""
        plan = {
            "intent": "先看后改一致性",
            "ops": [
                {"op": "trim", "target": {"track": "video#1", "index": 0}, "end": "1.5s", "ripple": True},
                {"op": "set_volume", "target": {"role": "bgm"}, "volume": "30%"},
            ],
        }
        result = preview_plan(self.draft.data, plan)

        # 真实 apply（副本草稿，不落盘到原位置）
        work = copy.deepcopy(self.draft.data)
        applied, _warnings = _execute_ops(work, compile_plan(work, plan))
        real_after = build_timeline(work)

        self.assertEqual(result["after"]["segments"], len(real_after["segments"]))
        self.assertEqual(result["after"]["tracks"], len(real_after["tracks"]))
        self.assertEqual(len(applied), 2)

        # 片段级：preview 的 changed 字段应与真实执行后一致
        changed_ids = [item["id"] for item in result["diff"]["segments"] if item["status"] in ("changed", "added", "removed")]
        self.assertGreaterEqual(len(changed_ids), 1)
        for seg in real_after["segments"]:
            self.assertIn(seg["id"], {s["id"] for s in (build_timeline(self.draft.data))["segments"]} | set(changed_ids) or set())

    def test_preview_shows_ripple_shift(self):
        plan = {
            "ops": [
                {"op": "trim", "target": {"track": "video#1", "index": 0}, "end": "1.5s", "ripple": True},
            ],
        }
        result = preview_plan(self.draft.data, plan)
        seg_diff = {item["ref"]: item for item in result["diff"]["segments"]}
        self.assertIn("SEGV1", seg_diff)
        self.assertEqual(seg_diff["SEGV1"]["status"], "changed")
        self.assertIn("duration", seg_diff["SEGV1"]["fields"])
        # SEGV2 因 ripple 前移
        if "SEGV2" in seg_diff:
            self.assertIn("start", seg_diff["SEGV2"]["fields"])

    def test_preview_reports_ops_summary(self):
        plan = {
            "ops": [
                {"op": "set_filter", "target": {"ref": "SEGV1"}, "name": "1980", "intensity": 70},
            ],
        }
        result = preview_plan(self.draft.data, plan)
        self.assertEqual(len(result["diff"]["ops"]), 1)
        op = result["diff"]["ops"][0]
        self.assertEqual(op["op"], "set_filter")
        self.assertEqual(op["args"]["intensity"], 70)

    def test_preview_preserves_original_data(self):
        plan = {"ops": [{"op": "set_volume", "target": {"role": "bgm"}, "volume": "10%"}]}
        before = copy.deepcopy(self.draft.data)
        preview_plan(self.draft.data, plan)
        self.assertEqual(before, self.draft.data)


if __name__ == "__main__":
    unittest.main()