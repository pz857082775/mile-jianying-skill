# ruff: noqa: E402

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
SCRIPTS = SCRIPT_DIR.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from mile import memory as memory_mod
from mile import ops, quality
from mile.draft import list_backups, list_drafts, load_draft, undo_draft
from mile.errors import MileError
from mile.plan import apply_plan, compile_plan
from mile.timecode import format_srt, format_time, parse_range, parse_time
from mile.timeline import build_timeline, select_segments


def make_draft(root: Path, name: str = "MileTestDraft") -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)

    def text_material(material_id, content):
        return {
            "id": material_id,
            "type": "text",
            "content": json.dumps(
                {
                    "text": content,
                    "styles": [
                        {
                            "fill": {"content": {"solid": {"color": [1.0, 1.0, 1.0]}}},
                            "range": [0, len(content)],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            "font_size": 8.0,
            "text_color": "#ffffff",
            "alignment": 1,
            "border_width": 0,
            "has_shadow": False,
        }

    clip = {
        "alpha": 1.0,
        "flip": {"horizontal": False, "vertical": False},
        "rotation": 0.0,
        "scale": {"x": 1.0, "y": 1.0},
        "transform": {"x": 0.0, "y": 0.0},
    }
    data = {
        "id": "draft-" + name,
        "fps": 30,
        "canvas_config": {"width": 1920, "height": 1080, "ratio": "16:9"},
        "duration": 6_000_000,
        "materials": {
            "videos": [
                {
                    "id": "v1",
                    "path": str(root / "a.mp4"),
                    "material_name": "a.mp4",
                    "type": "video",
                    "duration": 6_000_000,
                    "width": 1920,
                    "height": 1080,
                }
            ],
            "texts": [text_material("t1", "欢迎光临"), text_material("t2", "今天介绍新品")],
            "audios": [
                {
                    "id": "a1",
                    "path": str(root / "bgm.mp3"),
                    "name": "bgm.mp3",
                    "type": "audio",
                    "duration": 6_000_000,
                }
            ],
            "speeds": [{"id": "sp1", "type": "speed", "speed": 1.0, "mode": 0, "curve_speed": None}],
            "audio_fades": [{"id": "af1", "type": "audio_fade", "fade_in_duration": 0, "fade_out_duration": 0}],
            "transitions": [],
            "canvases": [],
        },
        "tracks": [
            {
                "id": "tr-v",
                "type": "video",
                "flag": 0,
                "attribute": 0,
                "name": "主轨",
                "segments": [
                    {
                        "id": "segv1",
                        "material_id": "v1",
                        "target_timerange": {"start": 0, "duration": 3_000_000},
                        "source_timerange": {"start": 0, "duration": 3_000_000},
                        "speed": 1.0,
                        "volume": 1.0,
                        "extra_material_refs": ["sp1", "af1"],
                        "visible": True,
                        "clip": dict(clip),
                    },
                    {
                        "id": "segv2",
                        "material_id": "v1",
                        "target_timerange": {"start": 3_000_000, "duration": 3_000_000},
                        "source_timerange": {"start": 3_000_000, "duration": 3_000_000},
                        "speed": 1.0,
                        "volume": 1.0,
                        "extra_material_refs": ["sp1", "af1"],
                        "visible": True,
                        "clip": dict(clip),
                    },
                ],
            },
            {
                "id": "tr-t",
                "type": "text",
                "flag": 1,
                "attribute": 0,
                "name": "",
                "segments": [
                    {
                        "id": "segt1",
                        "material_id": "t1",
                        "target_timerange": {"start": 0, "duration": 2_000_000},
                        "source_timerange": {"start": 0, "duration": 2_000_000},
                        "extra_material_refs": [],
                    },
                    {
                        "id": "segt2",
                        "material_id": "t2",
                        "target_timerange": {"start": 3_000_000, "duration": 2_000_000},
                        "source_timerange": {"start": 0, "duration": 2_000_000},
                        "extra_material_refs": [],
                    },
                ],
            },
            {
                "id": "tr-a",
                "type": "audio",
                "flag": 0,
                "attribute": 0,
                "name": "BGM",
                "segments": [
                    {
                        "id": "sega1",
                        "material_id": "a1",
                        "target_timerange": {"start": 0, "duration": 6_000_000},
                        "source_timerange": {"start": 0, "duration": 6_000_000},
                        "volume": 0.3,
                        "extra_material_refs": ["af1"],
                    }
                ],
            },
        ],
    }
    (folder / "draft_content.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    (folder / "draft_meta_info.json").write_text(json.dumps({"draft_name": name}, ensure_ascii=False), encoding="utf-8")
    return folder


class MileBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mile_test_")
        base = Path(self.tmp.name)
        self.root = base / "drafts"
        self.mem = base / "memory"
        self.root.mkdir()
        self.mem.mkdir()
        self._env = {key: os.environ.get(key) for key in ("MILE_DRAFT_ROOT", "MILE_MEMORY_DIR")}
        os.environ["MILE_DRAFT_ROOT"] = str(self.root)
        os.environ["MILE_MEMORY_DIR"] = str(self.mem)
        self.folder = make_draft(self.root)
        self.draft = load_draft(self.folder)

    def tearDown(self):
        for key, value in self._env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self.tmp.cleanup()

    def seg(self, data, seg_id):
        for track in data.get("tracks") or []:
            for segment in track.get("segments") or []:
                if segment.get("id") == seg_id:
                    return track, segment
        raise AssertionError(f"segment not found: {seg_id}")

    def text_of(self, data, material_id):
        for material in data["materials"]["texts"]:
            if material["id"] == material_id:
                return json.loads(material["content"])["text"]
        raise AssertionError(f"text material not found: {material_id}")


class TimecodeTests(unittest.TestCase):
    def test_parse_units(self):
        self.assertEqual(parse_time(12), 12_000_000)
        self.assertEqual(parse_time("12.5s"), 12_500_000)
        self.assertEqual(parse_time("500ms"), 500_000)
        self.assertEqual(parse_time("1m2s"), 62_000_000)
        self.assertEqual(parse_time("1h2m3s"), 3_723_000_000)
        self.assertEqual(parse_time("00:01:02.500"), 62_500_000)
        self.assertEqual(parse_time("1:02"), 62_000_000)
        self.assertEqual(parse_time("3分2秒"), 182_000_000)
        self.assertEqual(parse_time("-2s"), -2_000_000)

    def test_parse_range(self):
        self.assertEqual(parse_range("12s-18s"), (12_000_000, 6_000_000))
        self.assertEqual(parse_range("12s+6s"), (12_000_000, 6_000_000))
        self.assertEqual(parse_range("12s~18s"), (12_000_000, 6_000_000))
        self.assertEqual(parse_range({"start": 12, "end": "18s"}), (12_000_000, 6_000_000))

    def test_format(self):
        self.assertEqual(format_time(12_500_000, "clock"), "00:00:12.500")
        self.assertEqual(format_time(12_500_000, "s"), "12.5s")
        self.assertEqual(format_srt(62_500_000), "00:01:02,500")


class DraftTests(MileBase):
    def test_save_backup_and_undo(self):
        original = json.loads(self.draft.content_path.read_text(encoding="utf-8"))
        ops.set_text(self.draft.data, ["segt1"], text="改过的字幕")
        saved = self.draft.save()
        self.assertTrue(saved.exists())
        backups = list_backups(self.folder)
        self.assertEqual(len(backups), 1)

        undo_draft(self.folder)
        restored = json.loads(self.draft.content_path.read_text(encoding="utf-8"))
        self.assertEqual(restored, original)

    def test_list_drafts(self):
        items = list_drafts(self.root, detail=True)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "MileTestDraft")
        self.assertEqual(items[0]["duration"], 6_000_000)
        self.assertEqual(items[0]["canvas"], "1920x1080")

    def test_save_syncs_meta(self):
        folder = make_draft(self.root, "MetaCopy")
        meta_path = folder / "draft_meta_info.json"
        meta_path.write_text(
            json.dumps(
                {"draft_name": "OldName", "tm_duration": 1, "draft_cover": "cover.jpg"},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        draft = load_draft(folder)
        ops.set_text(draft.data, ["segt1"], text="同步测试")
        draft.save()

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["draft_name"], "MetaCopy")
        self.assertEqual(meta["tm_duration"], 6_000_000)
        self.assertEqual(Path(meta["draft_fold_path"]), folder.resolve())
        self.assertEqual(meta["draft_cover"], "cover.jpg")


class TimelineTests(MileBase):
    def test_select(self):
        timeline = build_timeline(self.draft.data)
        self.assertEqual(len(timeline["segments"]), 5)
        self.assertEqual([s["ref"] for s in select_segments(timeline, {"text_contains": "欢迎"})], ["SEGT1"])
        self.assertEqual(len(select_segments(timeline, {"role": "caption"})), 2)
        self.assertEqual(len(select_segments(timeline, {"role": "bgm"})), 1)
        refs = {s["ref"] for s in select_segments(timeline, {"range": "2s-4s"})}
        self.assertIn("SEGV2", refs)
        self.assertIn("SEGT2", refs)
        last = select_segments(timeline, {"track": "video", "index": "last"})
        self.assertEqual([s["ref"] for s in last], ["SEGV2"])
        first = select_segments(timeline, {"track": "video", "index": 0})
        self.assertEqual([s["ref"] for s in first], ["SEGV1"])
        self.assertEqual(first[0]["index_in_track"], 0)

    def test_roles(self):
        timeline = build_timeline(self.draft.data)
        roles = {s["ref"]: s["role"] for s in timeline["segments"]}
        self.assertEqual(roles["SEGV1"], "main")
        self.assertEqual(roles["SEGT1"], "caption")
        self.assertEqual(roles["SEGA1"], "bgm")

    def test_sticker_and_pip_roles(self):
        data = json.loads(json.dumps(self.draft.data))
        clip = {
            "alpha": 1.0,
            "flip": {"horizontal": False, "vertical": False},
            "rotation": 0.0,
            "scale": {"x": 1.0, "y": 1.0},
            "transform": {"x": 0.0, "y": 0.0},
        }
        data["materials"]["videos"].extend(
            [
                {
                    "id": "sticker1",
                    "path": str(self.root / "click_marker.png"),
                    "material_name": "click_marker.png",
                    "type": "photo",
                    "duration": 1_000_000,
                    "width": 128,
                    "height": 128,
                },
                {
                    "id": "pip1",
                    "path": str(self.root / "pip.mp4"),
                    "material_name": "pip.mp4",
                    "type": "video",
                    "duration": 2_000_000,
                    "width": 1920,
                    "height": 1080,
                },
            ]
        )
        data["tracks"].extend(
            [
                {
                    "id": "tr-sticker",
                    "type": "video",
                    "flag": 2,
                    "name": "StickerTrack",
                    "segments": [
                        {
                            "id": "segsticker",
                            "material_id": "sticker1",
                            "target_timerange": {"start": 0, "duration": 1_000_000},
                            "clip": dict(clip),
                        }
                    ],
                },
                {
                    "id": "tr-pip",
                    "type": "video",
                    "flag": 2,
                    "name": "PIP",
                    "segments": [
                        {
                            "id": "segpip",
                            "material_id": "pip1",
                            "target_timerange": {"start": 0, "duration": 2_000_000},
                            "clip": dict(clip),
                        }
                    ],
                },
            ]
        )
        timeline = build_timeline(data)
        self.assertEqual(len(select_segments(timeline, {"role": "sticker"})), 1)
        self.assertEqual(len(select_segments(timeline, {"role": "pip"})), 1)


class OpsTests(MileBase):
    def test_trim_end_and_ripple(self):
        result = ops.trim_segment(self.draft.data, "segv1", end="2s", ripple=True)
        _, seg1 = self.seg(self.draft.data, "segv1")
        _, seg2 = self.seg(self.draft.data, "segv2")
        self.assertEqual(seg1["target_timerange"], {"start": 0, "duration": 2_000_000})
        self.assertEqual(seg1["source_timerange"], {"start": 0, "duration": 2_000_000})
        self.assertEqual(seg2["target_timerange"]["start"], 2_000_000)
        self.assertEqual(result["new"]["duration"], 2_000_000)

    def test_trim_start_moves_source(self):
        ops.trim_segment(self.draft.data, "segv1", start="1s")
        _, seg1 = self.seg(self.draft.data, "segv1")
        self.assertEqual(seg1["target_timerange"], {"start": 1_000_000, "duration": 2_000_000})
        self.assertEqual(seg1["source_timerange"], {"start": 1_000_000, "duration": 2_000_000})

    def test_trim_material_end_shrinks(self):
        result = ops.trim_segment(self.draft.data, "segv2", end="9s")
        _, seg2 = self.seg(self.draft.data, "segv2")
        # segv2 源起点 3s，素材总长 6s，最多再播 3s
        self.assertEqual(seg2["target_timerange"]["duration"], 3_000_000)
        self.assertIsNotNone(result["warning"])

    def test_trim_head_out_of_range(self):
        ops.shift_segments(self.draft.data, ["segv2"], "7s")
        with self.assertRaises(MileError) as ctx:
            ops.trim_segment(self.draft.data, "segv2", start=0)
        self.assertEqual(ctx.exception.code, "SOURCE_OUT_OF_RANGE")

    def test_split(self):
        result = ops.split_segment(self.draft.data, "segv1", "1.5s")
        track, _ = self.seg(self.draft.data, "segv1")
        self.assertEqual(len(track["segments"]), 3)
        first = track["segments"][0]
        second = track["segments"][1]
        self.assertEqual(first["target_timerange"], {"start": 0, "duration": 1_500_000})
        self.assertEqual(second["target_timerange"], {"start": 1_500_000, "duration": 1_500_000})
        self.assertEqual(first["source_timerange"], {"start": 0, "duration": 1_500_000})
        self.assertEqual(second["source_timerange"], {"start": 1_500_000, "duration": 1_500_000})
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(result["second"]["ref"], second["id"][:8].upper())

    def test_delete_ripple(self):
        ops.delete_segments(self.draft.data, ["segv1"], ripple=True)
        _, seg2 = self.seg(self.draft.data, "segv2")
        self.assertEqual(seg2["target_timerange"]["start"], 0)

    def test_set_text(self):
        ops.set_text(self.draft.data, ["segt1"], text="欢迎来到米乐", color="#ffcc33", font_size=12)
        self.assertEqual(self.text_of(self.draft.data, "t1"), "欢迎来到米乐")
        material = [m for m in self.draft.data["materials"]["texts"] if m["id"] == "t1"][0]
        self.assertEqual(material["text_color"].lower(), "#ffcc33")
        style = json.loads(material["content"])["styles"][0]
        self.assertAlmostEqual(style["fill"]["content"]["solid"]["color"][0], 1.0)
        self.assertEqual(style["range"], [0, 6])

    def test_set_volume_and_fade(self):
        ops.set_volume(self.draft.data, ["sega1"], 0.8, fade_in="0.5s", fade_out="0.5s")
        _, seg = self.seg(self.draft.data, "sega1")
        self.assertEqual(seg["volume"], 0.8)
        fade = [m for m in self.draft.data["materials"]["audio_fades"] if m["id"] == "af1"][0]
        self.assertEqual(fade["fade_in_duration"], 500_000)
        self.assertEqual(fade["fade_out_duration"], 500_000)
        with self.assertRaises(MileError):
            ops.set_volume(self.draft.data, ["sega1"], 9)

    def test_set_speed(self):
        ops.set_speed(self.draft.data, ["segv2"], 2.0)
        _, seg = self.seg(self.draft.data, "segv2")
        self.assertEqual(seg["speed"], 2.0)
        self.assertEqual(seg["target_timerange"]["duration"], 1_500_000)
        self.assertEqual(seg["source_timerange"]["duration"], 3_000_000)
        speed_material = [m for m in self.draft.data["materials"]["speeds"] if m["id"] == "sp1"][0]
        self.assertEqual(speed_material["speed"], 2.0)

    def test_transition_add_and_remove(self):
        result = ops.add_transition(self.draft.data, "segv1", "叠化", duration="0.5s")
        track, seg = self.seg(self.draft.data, "segv1")
        refs = seg["extra_material_refs"]
        transitions = [m for m in self.draft.data["materials"]["transitions"] if m["type"] == "transition"]
        self.assertEqual(len(transitions), 1)
        self.assertIn(transitions[0]["id"], refs)
        self.assertEqual(transitions[0]["duration"], 500_000)
        self.assertEqual(result["next_ref"], "SEGV2")

        ops.add_transition(self.draft.data, "segv1", "上移", duration="0.3s")
        transitions = [m for m in self.draft.data["materials"]["transitions"] if m["type"] == "transition"]
        self.assertEqual(len(transitions), 1)
        self.assertEqual(transitions[0]["name"], "上移")

        ops.remove_transition(self.draft.data, "segv1")
        self.assertEqual(self.draft.data["materials"]["transitions"], [])

        with self.assertRaises(MileError) as ctx:
            ops.add_transition(self.draft.data, "segv2", "叠化")
        self.assertEqual(ctx.exception.code, "NOT_ADJACENT")

    def test_clone_text(self):
        result = ops.clone_text_segment(self.draft.data, "segt1", text="克隆字幕", start="4s")
        timeline = build_timeline(self.draft.data)
        cloned = [s for s in timeline["segments"] if s.get("text") == "克隆字幕"]
        self.assertEqual(len(cloned), 1)
        self.assertEqual(cloned[0]["start"], 4_000_000)
        self.assertNotEqual(result["id"], "segt1")

    def test_add_text_and_audio(self):
        media = self.root / "voice.mp3"
        media.write_bytes(b"fake")
        created = ops.add_text(self.draft.data, text="新字幕", start="1s", duration="2s", font_size=9)
        self.assertTrue(created.get("id"))
        timeline = build_timeline(self.draft.data)
        self.assertTrue(any(s.get("text") == "新字幕" for s in timeline["segments"]))
        audio = ops.add_audio(self.draft.data, media, start=0, duration="3s", name="配音", volume=1.0)
        self.assertEqual(len(audio["added"]), 1)
        self.assertEqual(audio["added"][0]["duration"], 3_000_000)
        self.assertEqual(audio["material_name"], "配音")


class PlanTests(MileBase):
    def test_compile_and_dry_run(self):
        plan = {
            "intent": "改字幕",
            "ops": [
                {"op": "set_text", "target": {"text_contains": "欢迎"}, "text": "欢迎来到米乐"},
                {"op": "trim", "target": {"ref": "SEGV1"}, "end": "2s", "ripple": True},
            ],
        }
        compiled = compile_plan(self.draft.data, plan)
        self.assertEqual([op["op"] for op in compiled["operations"]], ["set_text", "trim"])
        result = apply_plan(self.draft, plan, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(len(result["preview"]), 2)
        self.assertEqual(self.text_of(self.draft.data, "t1"), "欢迎光临")

    def test_apply_and_rollback(self):
        plan = {
            "ops": [
                {"op": "set_text", "target": {"ref": "SEGT1"}, "text": "会回滚"},
                {"op": "trim", "target": {"ref": "SEGV1"}, "start": "5s", "end": "6s"},
            ],
        }
        with self.assertRaises(MileError) as ctx:
            apply_plan(self.draft, plan)
        self.assertEqual(ctx.exception.code, "BAD_RANGE")
        self.assertEqual(self.text_of(self.draft.data, "t1"), "欢迎光临")
        fresh = load_draft(self.folder)
        self.assertEqual(self.text_of(fresh.data, "t1"), "欢迎光临")
        self.assertEqual(list_backups(self.folder), [])

    def test_validation(self):
        with self.assertRaises(MileError) as ctx:
            compile_plan(self.draft.data, {"ops": [{"op": "set_canvas", "width": 1280}]})
        self.assertEqual(ctx.exception.code, "MISSING_ARGS")
        with self.assertRaises(MileError) as ctx:
            compile_plan(self.draft.data, {"ops": [{"op": "delete", "target": {"ref": "NOPE"}}]})
        self.assertEqual(ctx.exception.code, "NO_TARGET")

    def test_alias_and_percent_volume(self):
        plan = {"ops": [{"op": "mute", "target": {"role": "bgm"}}]}
        compiled = compile_plan(self.draft.data, plan)
        self.assertEqual(compiled["operations"][0]["args"]["volume"], 0.0)
        plan = {"ops": [{"op": "音量", "target": {"role": "bgm"}, "volume": "50%"}]}
        compiled = compile_plan(self.draft.data, plan)
        self.assertEqual(compiled["operations"][0]["args"]["volume"], 0.5)


class MemoryTests(MileBase):
    def test_record_recall_feedback_forget(self):
        record = memory_mod.record_session(
            project=self.draft.name,
            context={"intent": "口播字幕，节奏快", "tags": ["口播", "字幕"]},
            outcome="applied",
            rating=4,
            draft=self.draft,
        )
        self.assertTrue(record["id"].startswith("m-"))
        records = memory_mod.list_memory()
        self.assertEqual(len(records), 1)

        again = memory_mod.record_session(
            project=self.draft.name,
            context={"intent": "口播字幕，节奏快", "tags": ["口播"]},
            draft=self.draft,
        )
        self.assertEqual(again["id"], record["id"])
        self.assertEqual(len(memory_mod.list_memory()), 1)

        recalled = memory_mod.recommend_style(context={"intent": "口播字幕", "tags": ["口播"]}, k=3)
        self.assertGreaterEqual(recalled["match_count"], 1)
        self.assertEqual(recalled["style"]["caption"]["color"], "#ffffff")

        updated = memory_mod.update_feedback(record["id"], rating=5, outcome="liked", notes="效果不错")
        self.assertEqual(updated["rating"], 5.0)
        self.assertEqual(memory_mod.stats()["count"], 1)

        memory_mod.forget(record["id"])
        self.assertEqual(memory_mod.list_memory(), [])

    def test_auto_record_respects_env(self):
        os.environ["MILE_MEMORY_AUTO"] = "0"
        try:
            result = memory_mod.auto_record_from_draft(project=self.draft.name, draft_path=str(self.folder))
            self.assertIsNone(result)
        finally:
            os.environ.pop("MILE_MEMORY_AUTO", None)
        result = memory_mod.auto_record_from_draft(project=self.draft.name, draft_path=str(self.folder))
        self.assertIsNotNone(result)
        self.assertEqual(memory_mod.stats()["count"], 1)


class QualityTests(MileBase):
    def test_checks_and_autofix(self):
        data = self.draft.data
        data["canvas_config"]["width"] = 1921
        data["canvas_config"]["height"] = 1081
        _, seg2 = self.seg(data, "segv2")
        seg2["target_timerange"]["duration"] = 0
        _, seg1 = self.seg(data, "segv1")
        seg1["volume"] = 10.0
        data["duration"] = 99_000_000

        report = quality.run_checks(data, folder=self.folder)
        codes = {issue["code"] for issue in report["issues"]}
        for expected in ("CANVAS_ODD", "SEGMENT_ZERO_DURATION", "AUDIO_VOLUME_HIGH", "DRAFT_DURATION_MISMATCH"):
            self.assertIn(expected, codes)

        result = quality.autofix(data, report["issues"])
        self.assertTrue(result["fixed"])
        after = quality.run_checks(data, folder=self.folder)
        remaining = {issue["code"] for issue in after["issues"]}
        for fixed_code in ("CANVAS_ODD", "SEGMENT_ZERO_DURATION", "AUDIO_VOLUME_HIGH", "DRAFT_DURATION_MISMATCH"):
            self.assertNotIn(fixed_code, remaining)
        self.assertEqual(data["canvas_config"]["width"], 1920)
        self.assertEqual(data["canvas_config"]["height"], 1080)
        self.assertEqual(seg1["volume"], 4.0)


class CliTests(MileBase):
    def run_cli(self, argv):
        import contextlib
        import io

        import mile_cli

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = mile_cli.main(argv + ["--root", str(self.root)])
        payload = json.loads(buffer.getvalue())
        return code, payload

    def test_timeline_and_error_envelope(self):
        code, payload = self.run_cli(["timeline", "MileTestDraft"])
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["data"]["summary"]["segment_count"], 5)

        code, payload = self.run_cli(["timeline", "不存在的草稿"])
        self.assertEqual(code, 2)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "DRAFT_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
