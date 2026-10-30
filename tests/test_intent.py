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

from mile import ops
from mile.intent import parse_intent
from mile.plan import apply_plan
from mile.timeline import build_timeline


class IntentTests(MileBase):
    def parse(self, text):
        return parse_intent(text, timeline=build_timeline(self.draft.data))

    def single_op(self, text):
        parsed = self.parse(text)
        self.assertEqual(parsed["clarify"], [], parsed["clarify"])
        self.assertIsNotNone(parsed["plan"])
        return parsed["plan"]["ops"][0]

    def test_volume_percentage_targets_bgm(self):
        parsed = self.parse("把BGM音量调到30%")
        operation = parsed["plan"]["ops"][0]
        self.assertEqual(operation["op"], "set_volume")
        self.assertEqual(operation["target"], {"role": "bgm"})
        self.assertAlmostEqual(operation["volume"], 0.3)
        self.assertIn("音乐", parsed["tags"])

    def test_multi_clause_returns_two_operations(self):
        parsed = self.parse("把BGM音量调到30%，再把字幕颜色改成红色")
        self.assertEqual(parsed["clarify"], [])
        self.assertEqual([op["op"] for op in parsed["plan"]["ops"]], ["set_volume", "set_text"])

    def test_head_trim_ripples_following_segments(self):
        parsed = self.parse("开头删掉1秒")
        first, second = parsed["plan"]["ops"]
        self.assertEqual(first["op"], "trim")
        self.assertEqual(first["target"], {"ref": "SEGV1"})
        self.assertEqual(first["start"], 1.0)
        self.assertEqual(second["op"], "move")
        self.assertEqual(second["delta"], -1.0)

    def test_tail_trim_uses_last_segment(self):
        parsed = self.parse("结尾裁剪2秒")
        operation = parsed["plan"]["ops"][0]
        self.assertEqual(operation["target"], {"ref": "SEGV2"})
        self.assertEqual(operation["end"], 4.0)
        self.assertTrue(parsed["warnings"])

    def test_unquoted_text_rewrite(self):
        operation = self.single_op("把第2个字幕改成你好")
        self.assertEqual(operation["op"], "set_text")
        self.assertEqual(operation["target"], {"role": "caption", "index": 1})
        self.assertEqual(operation["text"], "你好")

    def test_style_without_quotes_is_not_text(self):
        operation = self.single_op("把字幕字号改成20")
        self.assertEqual(operation["font_size"], 20.0)
        self.assertNotIn("text", operation)

    def test_add_transition_without_keyword(self):
        operation = self.single_op("给第1个视频片段加0.5秒叠化")
        self.assertEqual(operation["op"], "add_transition")
        self.assertEqual(operation["type"], "叠化")
        self.assertEqual(operation["duration"], 0.5)

    def test_remove_transition_clarifies_when_absent(self):
        parsed = self.parse("删除所有转场")
        self.assertIsNone(parsed["plan"])
        self.assertTrue(parsed["clarify"])

    def test_remove_transition_targets_existing(self):
        ops.add_transition(self.draft.data, "segv1", "叠化", 0.5)
        parsed = self.parse("删除所有转场")
        self.assertEqual(parsed["clarify"], [])
        operation = parsed["plan"]["ops"][0]
        self.assertEqual(operation["op"], "remove_transition")
        self.assertEqual(operation["target"], {"ref": "SEGV1"})
        result = apply_plan(self.draft, parsed["plan"], dry_run=True)
        self.assertEqual(result["operations"][0]["op"], "remove_transition")

    def test_remove_transition_with_ordinal(self):
        ops.add_transition(self.draft.data, "segv1", "叠化", 0.5)
        operation = self.single_op("删除第1个视频片段的转场")
        self.assertEqual(operation["op"], "remove_transition")
        self.assertEqual(operation["target"], {"ref": "SEGV1"})

    def test_bgm_replacement_extracts_path(self):
        parsed = self.parse("把BGM换成D:/music/a.mp3")
        add_audio = parsed["plan"]["ops"][-1]
        self.assertEqual(add_audio["op"], "add_audio")
        self.assertEqual(add_audio["path"], "D:/music/a.mp3")
        self.assertEqual(add_audio["name"], "a")

    def test_bgm_request_without_path_recommends(self):
        parsed = self.parse("配个音乐")
        self.assertIsNone(parsed["plan"])
        self.assertEqual(parsed["actions"][0]["op"], "recommend")
        self.assertEqual(parsed["actions"][0]["kind"], "bgm")

    def test_unsupported_feature_mentions_limitation(self):
        parsed = self.parse("视频调色")
        self.assertIsNone(parsed["plan"])
        self.assertTrue(parsed["clarify"])
        self.assertTrue(any("调色" in warning for warning in parsed["warnings"]))

    def test_asr_action_is_not_editable(self):
        parsed = self.parse("生成字幕")
        self.assertIsNone(parsed["plan"])
        self.assertEqual(parsed["actions"][0]["op"], "transcribe")
        self.assertFalse(parsed["actions"][0]["editable"])

    def test_filter_name_intensity_and_ordinal_target(self):
        parsed = self.parse("给第1个视频片段加1980滤镜，强度70")
        self.assertEqual(parsed["clarify"], [])
        operation = parsed["plan"]["ops"][0]
        self.assertEqual(operation["op"], "set_filter")
        self.assertEqual(operation["target"], {"role": "main", "index": 0})
        self.assertEqual(operation["name"], "1980")
        self.assertEqual(operation["intensity"], 70.0)

    def test_filter_preserves_display_name_spaces(self):
        operation = self.single_op("把所有视频换成VHS III滤镜")
        self.assertEqual(operation["op"], "set_filter")
        self.assertEqual(operation["name"], "VHS III")

    def test_mask_and_effect_natural_language(self):
        mask = self.single_op("给第1个视频片段加线性蒙版")
        self.assertEqual(mask["op"], "set_mask")
        self.assertEqual(mask["name"], "线性")
        effect = self.single_op("给第1个视频片段加故障特效")
        self.assertEqual(effect["op"], "add_effect")
        self.assertEqual(effect["name"], "故障")
        self.assertTrue(effect["keep_old"])

    def test_animation_extracts_kind_and_name(self):
        operation = self.single_op("给第1个视频片段加Kira游动入场动画")
        self.assertEqual(operation["op"], "add_animation")
        self.assertEqual(operation["name"], "Kira游动")
        self.assertEqual(operation["kind"], "in")

    def test_remove_feature_generates_remove_material(self):
        for text, material_type in (
            ("删除所有滤镜", "filter"),
            ("移除所有特效", "effect"),
            ("去掉所有蒙版", "mask"),
            ("取消所有动画", "animation"),
        ):
            operation = self.single_op(text)
            self.assertEqual(operation["op"], "remove_material")
            self.assertEqual(operation["type"], material_type)

    def test_text_animation_parses_to_add_text_animation(self):
        operation = self.single_op("给第1个字幕加卡拉OK入场动画")
        self.assertEqual(operation["op"], "add_text_animation")
        self.assertEqual(operation["name"], "卡拉OK")
        self.assertEqual(operation["kind"], "in")
        self.assertEqual(operation["target"], {"role": "caption", "index": 0})

    def test_text_animation_defaults_to_caption_scope(self):
        parsed = self.parse("加个字幕动画")
        self.assertIsNone(parsed["plan"])
        self.assertTrue(any("名称" in item for item in parsed["clarify"]))

    def test_text_animation_loop_kind(self):
        operation = self.single_op("给所有字幕加吹泡泡循环动画")
        self.assertEqual(operation["op"], "add_text_animation")
        self.assertEqual(operation["kind"], "loop")

    def test_text_animation_rejected_on_video_target(self):
        parsed = self.parse("给第1个视频片段加字幕动画")
        self.assertIsNone(parsed["plan"])
        self.assertTrue(any("文字片段" in item for item in parsed["clarify"]))

    def test_remove_text_animation_uses_remove_material(self):
        operation = self.single_op("去掉字幕动画")
        self.assertEqual(operation["op"], "remove_material")
        self.assertEqual(operation["type"], "animation")

    def test_feature_on_text_target_clarifies(self):
        parsed = self.parse("给第1个字幕加线性蒙版")
        self.assertIsNone(parsed["plan"])
        self.assertTrue(any("视频片段" in item for item in parsed["clarify"]))


if __name__ == "__main__":
    unittest.main()
