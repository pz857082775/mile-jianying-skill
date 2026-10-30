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

from mile import ops
from mile.draft import list_backups, load_draft
from mile.smart import run_smart


class SmartFlowTests(MileBase):
    def setUp(self):
        super().setUp()
        ops.set_text(self.draft.data, ["segt1"], text="这个视频做为开场")
        ops.set_text(self.draft.data, ["segt2"], text="请大家多多关健支持")

    def bgm_volume(self, draft):
        for track in draft.data["tracks"]:
            if track.get("type") == "audio":
                return track["segments"][0].get("volume")
        raise AssertionError("audio track not found")

    def caption_texts(self, draft):
        result = []
        for track in draft.data["tracks"]:
            if track.get("type") != "text":
                continue
            for segment in track["segments"]:
                material = next(
                    item
                    for item in draft.data["materials"]["texts"]
                    if item["id"] == segment["material_id"]
                )
                result.append(json.loads(material["content"])["text"])
        return result

    def test_preview_returns_plan_without_writing(self):
        before = self.draft.content_path.read_bytes()
        result = run_smart(self.draft, "把BGM音量调到30%", remember=False)
        self.assertFalse(result["apply"])
        self.assertTrue(result["plan_result"]["dry_run"])
        self.assertEqual(result["qa"]["stage"], "current")
        self.assertEqual(self.draft.content_path.read_bytes(), before)
        self.assertEqual(list_backups(self.folder), [])

    def test_apply_combines_subtitles_and_plan_in_one_save(self):
        result = run_smart(
            self.draft,
            "把BGM音量调到30%",
            polish=True,
            apply=True,
            remember=False,
        )
        self.assertFalse(result["plan_result"]["dry_run"])
        self.assertTrue(result["qa"]["ok"])

        saved = load_draft(self.folder)
        self.assertAlmostEqual(self.bgm_volume(saved), 0.3)
        texts = self.caption_texts(saved)
        self.assertIn("这个视频作为开场", texts)
        self.assertIn("请大家多多关键支持", texts)
        self.assertEqual(len(list_backups(self.folder)), 1)

    def test_apply_polish_only_still_saves(self):
        result = run_smart(self.draft, apply=True, polish=True, remember=False)
        self.assertIsNone(result["plan"])
        self.assertTrue(result["subtitles"]["applied"]["changed"])
        saved = load_draft(self.folder)
        self.assertIn("这个视频作为开场", self.caption_texts(saved))
        self.assertEqual(len(list_backups(self.folder)), 1)

    def test_clarify_returns_before_apply(self):
        before = self.draft.content_path.read_bytes()
        result = run_smart(self.draft, "视频调色", apply=True, remember=False)
        self.assertTrue(result["clarify"])
        self.assertIsNone(result["plan"])
        self.assertEqual(self.draft.content_path.read_bytes(), before)
        self.assertEqual(list_backups(self.folder), [])

    def test_recommend_action_returns_candidates(self):
        result = run_smart(self.draft, "推荐一个BGM", remember=False)
        self.assertIsNone(result["plan"])
        self.assertEqual(result["recommend"]["bgm"]["count"], 5)
        self.assertEqual(result["recommend"]["bgm"]["method"], "metadata-rules")

    def test_transcribe_without_audio_warns(self):
        result = run_smart(self.draft, "生成字幕", remember=False)
        self.assertIsNone(result["asr"])
        self.assertTrue(any("--audio" in warning for warning in result["warnings"]))

    def test_missing_arguments(self):
        from mile.errors import MileError

        with self.assertRaises(MileError) as context:
            run_smart(self.draft, apply=False, remember=False)
        self.assertEqual(context.exception.code, "MISSING_ARGS")


class SmartCliTests(MileBase):
    def setUp(self):
        super().setUp()
        ops.set_text(self.draft.data, ["segt1"], text="这个视频做为开场")
        self.draft.save()

    def run_cli(self, argv):
        import contextlib
        import io

        import mile_cli

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = mile_cli.main(argv + ["--root", str(self.root)])
        return code, json.loads(buffer.getvalue())

    def test_smart_preview(self):
        code, payload = self.run_cli(["smart", "MileTestDraft", "把BGM音量调到30%", "--no-remember"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["data"]["apply"])
        self.assertEqual(payload["data"]["plan"]["ops"][0]["op"], "set_volume")

    def test_smart_apply(self):
        code, payload = self.run_cli(
            [
                "smart",
                "MileTestDraft",
                "把BGM音量调到30%",
                "--polish",
                "--apply",
                "--no-remember",
            ]
        )
        self.assertEqual(code, 0)
        saved = load_draft(self.folder)
        volume = next(
            segment["volume"]
            for track in saved.data["tracks"]
            if track.get("type") == "audio"
            for segment in track["segments"]
        )
        self.assertAlmostEqual(volume, 0.3)
        content = saved.data["materials"]["texts"][0]["content"]
        self.assertIn("作为", json.loads(content)["text"])

    def test_subtitles_and_recommend(self):
        code, payload = self.run_cli(["subtitles", "MileTestDraft", "--split-max", "8"])
        self.assertEqual(code, 0)
        self.assertGreaterEqual(payload["data"]["changed"], 1)

        code, payload = self.run_cli(["recommend", "MileTestDraft", "--transitions"])
        self.assertEqual(code, 0)
        self.assertEqual(payload["data"]["transitions"]["method"], "duration-rules")

    def test_smart_without_instruction_fails(self):
        code, payload = self.run_cli(["smart", "MileTestDraft"])
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "MISSING_ARGS")


if __name__ == "__main__":
    unittest.main()
