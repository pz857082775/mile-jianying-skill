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

from test_mile import MileBase

from mile import ops
from mile.draft import list_backups, load_draft
from mile.errors import MileError
from mile.subtitles import (
    asr_available,
    build_captions,
    correct_text,
    correct_timeline,
    layout_caption,
    parse_srt,
    polish_text,
    split_caption,
    to_srt,
    transcribe,
)
from mile.timeline import build_timeline


class SubtitleTextTests(unittest.TestCase):
    def test_typo_correction(self):
        self.assertEqual(correct_text("这个视频做为开场"), "这个视频作为开场")
        self.assertEqual(correct_text("请大家多多关健支持"), "请大家多多关键支持")

    def test_custom_replace_and_protect(self):
        self.assertEqual(
            correct_text("欢迎使用米乐剪辑", glossary={"replace": {"米乐剪辑": "米乐自动剪辑"}}),
            "欢迎使用米乐自动剪辑",
        )
        self.assertEqual(
            correct_text("这个视频做为开场", glossary={"protect": ["做为"]}),
            "这个视频做为开场",
        )

    def test_numbers_and_urls_are_protected(self):
        self.assertEqual(
            correct_text("价格是 1,234 元, 马上购买!"),
            "价格是 1,234 元，马上购买！",
        )
        self.assertEqual(
            correct_text("请访问https://example.com/a,b 或者发邮件到 a@b.com"),
            "请访问https://example.com/a,b 或者发邮件到 a@b.com",
        )
        self.assertEqual(correct_text("AI 和 Python 3.14"), "AI 和 Python 3.14")

    def test_polish_reports_changes(self):
        report = polish_text("这个视频做为开场,我们马上开始!")
        self.assertTrue(report["changed"])
        kinds = {change["kind"] for change in report["changes"]}
        self.assertIn("typo", kinds)
        self.assertIn("punctuation", kinds)
        self.assertEqual(report["corrected"], "这个视频作为开场，我们马上开始！")

    def test_split_respects_limit(self):
        text = "今天天气不错，我们一起去公园散步吧。"
        parts = split_caption(text, max_chars=8)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 8 for part in parts), parts)
        self.assertEqual("".join(parts), text)

    def test_split_keeps_short_text_whole(self):
        self.assertEqual(split_caption("欢迎光临", max_chars=8), ["欢迎光临"])

    def test_layout_wraps_and_truncates(self):
        output = layout_caption("今天天气不错我们一起去公园散步吧", line_chars=6, max_lines=2)
        lines = output.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(len(line) <= 6 for line in lines), lines)
        self.assertTrue(output.endswith("…"))

    def test_build_captions_allocates_durations(self):
        captions = build_captions(
            [{"text": "这个视频做为开场", "start": 0.0, "duration": 3.0}],
            max_chars=6,
        )
        self.assertEqual([item["text"] for item in captions], ["这个视频", "作为开场"])
        self.assertEqual([item["start"] for item in captions], [0.0, 1.5])
        self.assertAlmostEqual(sum(item["duration"] for item in captions), 3.0)
        self.assertEqual(captions[0]["original"], "这个视频做为开场")

    def test_build_captions_merges_when_time_is_short(self):
        captions = build_captions(
            [{"text": "今天天气不错", "start": 0.0, "duration": 0.8}],
            max_chars=3,
            min_duration=0.5,
        )
        self.assertEqual(len(captions), 1)
        self.assertAlmostEqual(captions[0]["duration"], 0.8)

    def test_srt_round_trip(self):
        captions = build_captions(
            [{"text": "欢迎光临", "start": 0.0, "duration": 2.0}],
            max_chars=8,
        )
        text = to_srt(captions)
        parsed = parse_srt(text)
        self.assertEqual(parsed[0]["text"], "欢迎光临")
        self.assertAlmostEqual(parsed[0]["start"], 0.0)
        self.assertAlmostEqual(parsed[0]["duration"], 2.0)


class SubtitleDraftTests(MileBase):
    def setUp(self):
        super().setUp()
        ops.set_text(self.draft.data, ["segt1"], text="这个视频做为开场")
        ops.set_text(self.draft.data, ["segt2"], text="请大家多多关健支持")

    def test_correct_timeline_reports_changes(self):
        report = correct_timeline(build_timeline(self.draft.data))
        self.assertEqual(report["checked"], 2)
        self.assertEqual(report["changed"], 2)
        by_ref = {item["ref"]: item for item in report["items"]}
        self.assertEqual(by_ref["SEGT1"]["corrected"], "这个视频作为开场")
        self.assertEqual(by_ref["SEGT1"]["role"], "caption")
        self.assertTrue(by_ref["SEGT2"]["changes"])

    def test_correct_timeline_respects_role_filter(self):
        report = correct_timeline(build_timeline(self.draft.data), roles=("title",))
        self.assertEqual(report["checked"], 0)

    def test_apply_split_creates_independent_segments(self):
        from mile.smart import run_subtitles

        result = run_subtitles(self.draft, apply=True, split_max=8)
        self.assertTrue(result["apply"])
        self.assertEqual(len(result["applied"]["changed"]), 1)
        self.assertEqual(result["applied"]["changed"][0]["corrected"], "这个视频作为开场")
        self.assertEqual(len(result["applied"]["split"]), 1)
        self.assertEqual(result["applied"]["split"][0]["parts"], ["请大家多多", "关键支持"])
        self.assertTrue(result["qa"]["ok"])

        saved = load_draft(self.folder)
        timeline = build_timeline(saved.data)
        captions = sorted(
            [seg for seg in timeline["segments"] if seg["role"] == "caption"],
            key=lambda seg: seg["start"],
        )
        texts = [seg["text"] for seg in captions]
        self.assertIn("请大家多多", texts)
        self.assertIn("关键支持", texts)
        self.assertEqual(len(captions), 3)
        materials = {seg["material_id"] for seg in captions}
        self.assertEqual(len(materials), 3)
        self.assertEqual(len(list_backups(self.folder)), 1)

    def test_preview_does_not_write(self):
        from mile.smart import run_subtitles

        before = self.draft.content_path.read_bytes()
        result = run_subtitles(self.draft, apply=False, split_max=8)
        self.assertEqual(result["changed"], 2)
        self.assertEqual(self.draft.content_path.read_bytes(), before)
        self.assertEqual(list_backups(self.folder), [])

    def test_shared_material_is_skipped(self):
        from mile.smart import apply_subtitle_fixes

        self.draft.data["tracks"][1]["segments"][1]["material_id"] = "t1"
        report = correct_timeline(build_timeline(self.draft.data))
        applied = apply_subtitle_fixes(self.draft.data, report["items"])
        self.assertEqual(applied["changed"], [])
        self.assertEqual(len(applied["skipped"]), 2)
        material = next(m for m in self.draft.data["materials"]["texts"] if m["id"] == "t1")
        self.assertIn("做为", material["content"])


class TranscribeTests(unittest.TestCase):
    def test_missing_file(self):
        with tempfile.TemporaryDirectory() as folder:
            missing = Path(folder) / "missing.mp3"
            with self.assertRaises(MileError) as context:
                transcribe(missing)
            self.assertEqual(context.exception.code, "FILE_NOT_FOUND")

    @unittest.skipIf(asr_available(), "本机已安装 faster-whisper，跳过缺少 ASR 的用例")
    def test_asr_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(folder) / "voice.mp3"
            audio.write_bytes(b"not audio")
            with self.assertRaises(MileError) as context:
                transcribe(audio)
            self.assertEqual(context.exception.code, "ASR_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
