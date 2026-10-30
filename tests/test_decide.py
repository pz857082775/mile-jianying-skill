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

from mile.analysis import analyze_timeline_signals
from mile.decide import decide_timeline
from mile.timeline import build_timeline


class DecideStyleTests(unittest.TestCase):
    def test_short_and_long_shot_are_not_auto_deleted(self):
        timeline = {"fps": 30, "segments": [], "duration": 0}
        result = decide_timeline(timeline, intent="科技产品 30 秒竖屏")
        self.assertEqual(result["style"]["key"], "科技")
        self.assertEqual(result["plan_ops"], [])

    def test_confidence_stays_below_certainty(self):
        timeline = {"fps": 30, "segments": [], "duration": 0}
        result = decide_timeline(timeline, intent="任意意图")
        self.assertLess(result["confidence"], 1.0)
        self.assertGreater(result["confidence"], 0.0)

    def test_unknown_intent_falls_back_to_explainer_style(self):
        timeline = {"fps": 30, "segments": [], "duration": 0}
        result = decide_timeline(timeline, intent="")
        self.assertEqual(result["style"]["key"], "讲解")


class DecideTimelineTests(MileBase):
    def timeline(self):
        return build_timeline(self.draft.data)

    def test_long_shot_requires_manual_review(self):
        timeline = self.timeline()
        for seg in timeline["segments"]:
            if seg["role"] == "main":
                seg["duration"] = 20_000_000
        result = decide_timeline(timeline, intent="vlog 日常")
        decisions = [item["decision"] for item in result["decisions"]]
        self.assertIn("review-long-shot", decisions)
        self.assertTrue(result["manual_review"])
        self.assertEqual(result["plan_ops"], [])

    def test_captions_are_preserved(self):
        result = decide_timeline(self.timeline(), intent="讲解视频")
        decisions = [item["decision"] for item in result["decisions"]]
        self.assertIn("preserve-caption-timing", decisions)

    def test_voice_and_bgm_protect_dialogue(self):
        timeline = self.timeline()
        voice = dict(next(seg for seg in timeline["segments"] if seg["role"] == "bgm"))
        voice["role"] = "voice"
        voice["ref"] = "VOICE1"
        timeline["segments"].append(voice)
        result = decide_timeline(timeline, intent="讲解视频")
        decisions = [item["decision"] for item in result["decisions"]]
        self.assertIn("protect-voice-over-bgm", decisions)

    def test_short_main_tracks_prefer_no_transition(self):
        timeline = self.timeline()
        for seg in timeline["segments"]:
            if seg["role"] == "main":
                seg["duration"] = 500_000
        result = decide_timeline(timeline, intent="vlog 日常")
        transitions = [
            item
            for item in result["decisions"]
            if item["decision"] in {"suggest-transition", "prefer-hard-cut"}
        ]
        self.assertTrue(transitions)
        self.assertTrue(all(item["decision"] == "prefer-hard-cut" for item in transitions))
        self.assertEqual(result["plan_ops"], [])

    def test_decide_is_read_only_and_reports_evidence(self):
        before = self.draft.content_path.read_bytes()
        timeline = self.timeline()
        result = decide_timeline(
            timeline,
            intent="科技产品",
            timeline_signals=analyze_timeline_signals(timeline),
        )
        self.assertEqual(result["method"], "explainable-style-rules")
        self.assertEqual(result["evidence"]["main_count"], 2)
        self.assertEqual(result["evidence"]["caption_count"], 2)
        self.assertIn("risk", result)
        self.assertEqual(self.draft.content_path.read_bytes(), before)


class DecideCliTests(MileBase):
    def _run(self, argv):
        import contextlib
        import io

        import mile_cli

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = mile_cli.main(argv + ["--root", str(self.root)])
        return code, json.loads(buffer.getvalue())

    def test_decide_returns_explainable_payload(self):
        code, payload = self._run(["decide", self.draft.name, "--intent", "vlog 日常"])
        self.assertEqual(code, 0)
        data = payload["data"]
        self.assertEqual(data["method"], "explainable-style-rules")
        self.assertEqual(data["style"]["key"], "vlog")
        self.assertTrue(data["decisions"])

    def test_decide_plan_out_writes_empty_plan_for_safe_case(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "plan.json"
            code, payload = self._run(
                ["decide", self.draft.name, "--intent", "讲解", "--plan-out", str(target)]
            )
            self.assertEqual(code, 0)
            self.assertTrue(target.exists())
            plan = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(plan["ops"], [])
            self.assertEqual(payload["data"]["plan_file"], str(target))


if __name__ == "__main__":
    unittest.main()
