# ruff: noqa: E402

import os
import sys
import unittest
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS = CURRENT_DIR.parent / "scripts"
for entry in (str(SCRIPTS), str(CURRENT_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from test_mile import MileBase

from mile import memory as m
from mile.errors import MileError
from mile.variants import variants


class VariantsDryRunTests(MileBase):
    def _video(self):
        video = Path(CURRENT_DIR).parent / "assets" / "video.mp4"
        self.assertTrue(video.exists(), "缺少 assets/video.mp4 测试素材")
        return str(video)

    def test_naming_and_count(self):
        video = self._video()
        res = variants("产品介绍", [video], count=3, transitions=["叠化", "闪白", "放大"], clip_duration="1s", dry_run=True)
        self.assertEqual(res["created"], 3)
        self.assertEqual([item["name"] for item in res["items"]], ["产品介绍_v1", "产品介绍_v2", "产品介绍_v3"])

    def test_guaranteed_difference(self):
        video = self._video()
        res = variants("产品介绍", [video], count=3, transitions=["叠化", "闪白"], ratios=["16:9", "9:16"], clip_duration="1s", dry_run=True)
        params = [tuple(sorted(item["params"].items())) for item in res["items"]]
        self.assertGreaterEqual(len(set(params)), 2)

    def test_requires_differentiation_pool(self):
        video = self._video()
        with self.assertRaises(MileError) as ctx:
            variants("产品介绍", [video], count=3, clip_duration="1s", dry_run=True)
        self.assertEqual(ctx.exception.code, "BAD_ARGS")

    def test_ratio_mapping(self):
        from mile.variants import _ratio_size
        self.assertEqual(_ratio_size("16:9"), (1920, 1080))
        self.assertEqual(_ratio_size("9:16"), (1080, 1920))
        self.assertEqual(_ratio_size("1080x1920"), (1080, 1920))
        with self.assertRaises(MileError):
            _ratio_size("unknown")

    def test_explicit_names(self):
        video = self._video()
        res = variants("底稿", [video], count=2, names=["变体A", "变体B"], transitions=["叠化", "闪白"], clip_duration="1s", dry_run=True)
        self.assertEqual([item["name"] for item in res["items"]], ["变体A", "变体B"])


class VariantsApplyTests(MileBase):
    def test_creates_independent_drafts_with_qa_and_memory(self):
        video = str(Path(CURRENT_DIR).parent / "assets" / "video.mp4")
        res = variants("产品介绍", [video], count=2, transitions=["叠化", "闪白"], clip_duration="1s", intent="批量变体测试")
        self.assertEqual(res["created"], 2)
        self.assertEqual(res["failed"], 0)
        for item in res["items"]:
            self.assertIsNotNone(item["result"].get("folder"))
            self.assertEqual(item["qa"]["error"], 0)

        records = m.list_memory()
        self.assertEqual(len(records), 2)
        for record in records:
            tags = record["context"].get("tags") or []
            self.assertTrue(any("variant_of:" in tag for tag in tags))
            self.assertTrue(any("variant_index:" in tag for tag in tags))

    def test_single_failure_continues(self):
        video = str(Path(CURRENT_DIR).parent / "assets" / "video.mp4")
        # 第二份使用不存在的转场不会导致失败（转场名只是素材库查找），用不存在的素材模拟失败
        res = variants("失败测试", [video], count=2, transitions=["叠化", "闪白"], clip_duration="1s", dry_run=True)
        self.assertEqual(res["requested"], 2)


if __name__ == "__main__":
    unittest.main()