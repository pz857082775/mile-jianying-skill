# ruff: noqa: E402

import json
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
from mile.draft import list_backups
from mile.errors import MileError
from mile.intent import parse_intent
from mile.plan import apply_plan
from mile.timeline import build_timeline


def _material(data, material_id):
    for item in data["materials"]["texts"]:
        if item["id"] == material_id:
            return item
    raise AssertionError(f"text material not found: {material_id}")


def _style(data, material_id):
    content = json.loads(_material(data, material_id)["content"])
    return content, content["styles"][0]


class TextDecorationOpsTests(MileBase):
    def test_border_matches_5_9_stroke_schema(self):
        ops.set_text(self.draft.data, ["segt1"], border={"color": "#ffffff", "width": 30, "alpha": 1.0})
        _content, style = _style(self.draft.data, "t1")
        stroke = style["strokes"][0]
        self.assertEqual(stroke["content"]["solid"]["color"], [1.0, 1.0, 1.0])
        self.assertAlmostEqual(stroke["content"]["solid"]["alpha"], 1.0)
        self.assertAlmostEqual(stroke["width"], 0.06)  # 剪映显示值 30 -> raw 0.06（与真实草稿一致）
        material = _material(self.draft.data, "t1")
        self.assertAlmostEqual(material["border_width"], 0.06)
        self.assertEqual(material["border_color"], "#ffffff")

    def test_shadow_uses_feather_key_and_material_mirror(self):
        ops.set_text(
            self.draft.data,
            ["segt1"],
            shadow={"color": "#b71c1c", "alpha": 0.8, "distance": 8, "angle": -45, "feather": 0.055},
        )
        _content, style = _style(self.draft.data, "t1")
        entry = style["shadows"][0]
        self.assertEqual(entry["distance"], 8.0)
        self.assertEqual(entry["angle"], -45.0)
        self.assertIn("feather", entry)
        self.assertNotIn("diffuse", entry)
        self.assertAlmostEqual(entry["content"]["solid"]["color"][0], 183 / 255.0, places=6)
        material = _material(self.draft.data, "t1")
        self.assertTrue(material["has_shadow"])
        self.assertEqual(material["shadow_color"], "#b71c1c")

    def test_background_offsets_and_style(self):
        ops.set_text(
            self.draft.data,
            ["segt1"],
            background={"color": "#1a1a1a", "style": 2, "alpha": 0.6, "height": 0.2, "width": 0.2},
        )
        material = _material(self.draft.data, "t1")
        self.assertEqual(material["background_style"], 2)
        self.assertEqual(material["background_color"], "#1a1a1a")
        self.assertAlmostEqual(material["background_alpha"], 0.6)
        self.assertAlmostEqual(material["background_height"], 0.2)
        self.assertAlmostEqual(material["background_horizontal_offset"], 0.0)  # 默认 0.5 -> 0

    def test_removal_semantics(self):
        ops.set_text(self.draft.data, ["segt1"], border={"color": "#ff0000"}, shadow={"color": "#000000"})
        ops.set_text(self.draft.data, ["segt1"], border=False, shadow=False)
        _content, style = _style(self.draft.data, "t1")
        self.assertNotIn("strokes", style)
        self.assertNotIn("shadows", style)
        material = _material(self.draft.data, "t1")
        self.assertEqual(material["border_width"], 0.0)
        self.assertFalse(material["has_shadow"])

    def test_text_and_other_styles_preserved(self):
        ops.set_text(self.draft.data, ["segt1"], text="欢迎光临", font_size=12, border={"color": "#ffffff"})
        content, style = _style(self.draft.data, "t1")
        self.assertEqual(content["text"], "欢迎光临")
        self.assertEqual(style["size"], 12.0)
        self.assertTrue(style["strokes"])

    def test_range_and_shape_validation(self):
        with self.assertRaises(MileError) as ctx:
            ops.set_text(self.draft.data, ["segt1"], border={"width": 130})
        self.assertEqual(ctx.exception.code, "BAD_RANGE")
        with self.assertRaises(MileError) as ctx:
            ops.set_text(self.draft.data, ["segt1"], shadow={"angle": 200})
        self.assertEqual(ctx.exception.code, "BAD_RANGE")
        with self.assertRaises(MileError) as ctx:
            ops.set_text(self.draft.data, ["segt1"], background={"style": 5})
        self.assertEqual(ctx.exception.code, "BAD_ARGS")
        with self.assertRaises(MileError) as ctx:
            ops.set_text(self.draft.data, ["segt1"], border="不是颜色")
        self.assertIn(ctx.exception.code, ("BAD_ARGS", "BAD_COLOR"))

    def test_qa_ok_after_decoration(self):
        ops.set_text(
            self.draft.data,
            ["segt1", "segt2"],
            border={"color": "#ffffff", "width": 20},
            background={"color": "#000000"},
        )
        report = quality.run_checks(self.draft.data)
        self.assertTrue(report["ok"])


class TextDecorationPlanTests(MileBase):
    def test_plan_apply_dry_run_and_rollback(self):
        plan = {
            "ops": [
                {"op": "set_text", "target": {"ref": "SEGT1"}, "border": {"color": "#ffffff", "width": 25}},
            ]
        }
        dry = apply_plan(self.draft, plan, dry_run=True)
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["operations"][0]["args"]["border"]["width"], 25)
        _content, style = _style(self.draft.data, "t1")
        self.assertNotIn("strokes", style)

        result = apply_plan(self.draft, plan)
        self.assertEqual(len(list_backups(self.folder)), 1)
        _content, style = _style(self.draft.data, "t1")
        self.assertAlmostEqual(style["strokes"][0]["width"], 0.05)

        bad = {
            "ops": [
                {"op": "set_text", "target": {"ref": "SEGT1"}, "shadow": {"color": "#000000"}},
                {"op": "set_text", "target": {"ref": "SEGV1"}, "border": {"color": "#ffffff"}},  # NOT_TEXT
            ]
        }
        with self.assertRaises(MileError) as ctx:
            apply_plan(self.draft, bad)
        self.assertEqual(ctx.exception.code, "NOT_TEXT")
        _content, style = _style(self.draft.data, "t1")
        self.assertNotIn("shadows", style)
        self.assertEqual(len(list_backups(self.folder)), 1)

    def test_shared_material_not_double_written(self):
        # 两条字幕共享同一素材时，set_text 仍按素材写入一次；此处仅断言不报错且样式一致
        self.draft.data["tracks"][1]["segments"][1]["material_id"] = "t1"
        ops.set_text(self.draft.data, ["segt1", "segt2"], border={"color": "#ffffff", "width": 10})
        _content, style = _style(self.draft.data, "t1")
        self.assertEqual(len(style["strokes"]), 1)


class TextDecorationIntentTests(MileBase):
    def parse(self, text):
        return parse_intent(text, timeline=build_timeline(self.draft.data))

    def test_white_border_with_width_clause_attaches(self):
        parsed = self.parse("给第1个字幕加白色描边，宽度20")
        self.assertEqual(parsed["clarify"], [], parsed["clarify"])
        operation = parsed["plan"]["ops"][0]
        self.assertEqual(operation["op"], "set_text")
        self.assertEqual(operation["border"]["color"], "#ffffff")
        self.assertEqual(operation["border"]["width"], 20.0)

    def test_removal_phrasings(self):
        for text, key in (
            ("去掉所有描边", "border"),
            ("取消字幕阴影", "shadow"),
            ("字幕背景去掉", "background"),
        ):
            operation = self.parse(text)["plan"]["ops"][0]
            self.assertIs(operation[key], False, text)

    def test_shadow_distance_and_angle(self):
        operation = self.parse("字幕加阴影，距离8，角度-45")["plan"]["ops"][0]
        self.assertEqual(operation["shadow"]["distance"], 8.0)
        self.assertEqual(operation["shadow"]["angle"], -45.0)

    def test_video_target_clarifies(self):
        parsed = self.parse("给第1个视频片段加白色描边")
        self.assertIsNone(parsed["plan"])
        self.assertTrue(any("文字片段" in item for item in parsed["clarify"]))

    def test_no_longer_listed_as_unsupported(self):
        parsed = self.parse("给字幕加描边")
        self.assertFalse([w for w in parsed["warnings"] if "暂不覆盖" in w and ("描边" in w or "阴影" in w)])

    def test_plain_text_edit_still_works(self):
        operation = self.parse("把第2个字幕改成你好")["plan"]["ops"][0]
        self.assertEqual(operation["text"], "你好")
        self.assertNotIn("border", operation)


if __name__ == "__main__":
    unittest.main()
