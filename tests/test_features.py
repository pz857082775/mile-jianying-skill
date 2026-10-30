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

from mile import ops, quality  # noqa: F401
from mile.draft import list_backups, load_draft
from mile.errors import MileError
from mile.plan import apply_plan, compile_plan
from mile.timeline import build_timeline

from test_mile import MileBase, make_draft


def _first_member_name(enum_cls) -> str:
    return list(enum_cls)[0].name


def _enum_member(enum_cls, index=0):
    return list(enum_cls)[index]


def _load_vendor_metadata():
    from mile import ensure_vendor_path

    ensure_vendor_path()
    from pyJianYingDraft import metadata

    return metadata


def _refs_of(data, seg_id) -> list[str]:
    for track in data.get("tracks") or []:
        for segment in track.get("segments") or []:
            if segment.get("id") == seg_id:
                return list(segment.get("extra_material_refs") or [])
    raise AssertionError(f"segment not found: {seg_id}")


def _materials_of_type(data, list_name, type_name) -> list[dict]:
    return [m for m in data["materials"].get(list_name) or [] if m.get("type") == type_name]


class FilterTests(MileBase):
    def test_set_filter_adds_5_9_material(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.FilterType)
        result = ops.set_filter(self.draft.data, ["segv1"], name, intensity=80.0)
        self.assertEqual(len(result["changed"]), 1)
        self.assertIn(name, result["changed"][0]["filter"])
        filters = _materials_of_type(self.draft.data, "effects", "filter")
        self.assertEqual(len(filters), 1)
        self.assertEqual(filters[0]["value"], 0.8)
        self.assertIn(filters[0]["id"], _refs_of(self.draft.data, "segv1"))

    def test_set_filter_replaces_old_by_default(self):
        metadata = _load_vendor_metadata()
        first = _first_member_name(metadata.FilterType)
        second = _enum_member(metadata.FilterType, 1).name
        ops.set_filter(self.draft.data, ["segv1"], first)
        ops.set_filter(self.draft.data, ["segv1"], second)
        filters = _materials_of_type(self.draft.data, "effects", "filter")
        self.assertEqual(len(filters), 1)
        self.assertEqual(len(_refs_of(self.draft.data, "segv1")), 3)  # sp1/af1 保留，滤镜替换

    def test_set_filter_keep_old_appends(self):
        metadata = _load_vendor_metadata()
        first = _first_member_name(metadata.FilterType)
        second = _enum_member(metadata.FilterType, 1).name
        ops.set_filter(self.draft.data, ["segv1"], first)
        ops.set_filter(self.draft.data, ["segv1"], second, keep_old=True)
        filters = _materials_of_type(self.draft.data, "effects", "filter")
        self.assertEqual(len(filters), 2)

    def test_set_filter_rejects_bad_intensity(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.FilterType)
        with self.assertRaises(MileError) as ctx:
            ops.set_filter(self.draft.data, ["segv1"], name, intensity=101)
        self.assertEqual(ctx.exception.code, "BAD_RANGE")

    def test_set_filter_unknown_name(self):
        with self.assertRaises(MileError) as ctx:
            ops.set_filter(self.draft.data, ["segv1"], "不存在的滤镜")
        self.assertEqual(ctx.exception.code, "FILTER_NOT_FOUND")

    def test_set_filter_not_video(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.FilterType)
        with self.assertRaises(MileError) as ctx:
            ops.set_filter(self.draft.data, ["segt1"], name)
        self.assertEqual(ctx.exception.code, "NOT_VIDEO")


class EffectTests(MileBase):
    def test_add_effect_multi_target(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.VideoSceneEffectType)
        result = ops.add_effect(self.draft.data, ["segv1", "segv2"], name, params=[60])
        self.assertEqual(len(result["changed"]), 2)
        effects = _materials_of_type(self.draft.data, "video_effects", "video_effect")
        self.assertEqual(len(effects), 2)
        for seg_id in ("segv1", "segv2"):
            self.assertTrue(any(m["id"] in _refs_of(self.draft.data, seg_id) for m in effects))
        self.assertEqual(effects[0]["adjust_params"][0]["value"], 0.6)

    def test_add_effect_keep_old_and_remove(self):
        metadata = _load_vendor_metadata()
        first = _first_member_name(metadata.VideoSceneEffectType)
        second = _enum_member(metadata.VideoSceneEffectType, 1).name
        ops.add_effect(self.draft.data, ["segv1"], first)
        ops.add_effect(self.draft.data, ["segv1"], second)  # keep_old=True 默认
        effects = _materials_of_type(self.draft.data, "video_effects", "video_effect")
        self.assertEqual(len(effects), 2)
        ops.add_effect(self.draft.data, ["segv1"], first, keep_old=False)
        effects = _materials_of_type(self.draft.data, "video_effects", "video_effect")
        self.assertEqual(len(effects), 1)

    def test_add_effect_unknown_name(self):
        with self.assertRaises(MileError) as ctx:
            ops.add_effect(self.draft.data, ["segv1"], "不存在的特效")
        self.assertEqual(ctx.exception.code, "EFFECT_NOT_FOUND")

    def test_add_effect_not_video(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.VideoSceneEffectType)
        with self.assertRaises(MileError) as ctx:
            ops.add_effect(self.draft.data, ["segt1"], name)
        self.assertEqual(ctx.exception.code, "NOT_VIDEO")


class MaskTests(MileBase):
    def test_set_mask_uses_normalized_coords(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.MaskType)
        result = ops.set_mask(
            self.draft.data,
            ["segv1"],
            name,
            center_x=480,
            center_y=270,
            size=0.4,
            feather=20,
            invert=False,
        )
        self.assertEqual(len(result["changed"]), 1)
        masks = _materials_of_type(self.draft.data, "masks", "mask")
        self.assertEqual(len(masks), 1)
        self.assertIn(masks[0]["id"], _refs_of(self.draft.data, "segv1"))

    def test_set_mask_replaces_old(self):
        metadata = _load_vendor_metadata()
        first = _first_member_name(metadata.MaskType)
        second = _enum_member(metadata.MaskType, 1).name
        ops.set_mask(self.draft.data, ["segv1"], first)
        ops.set_mask(self.draft.data, ["segv1"], second)
        masks = _materials_of_type(self.draft.data, "masks", "mask")
        self.assertEqual(len(masks), 1)

    def test_set_mask_unknown_name(self):
        with self.assertRaises(MileError) as ctx:
            ops.set_mask(self.draft.data, ["segv1"], "不存在的蒙版")
        self.assertEqual(ctx.exception.code, "MASK_NOT_FOUND")

    def test_set_mask_not_video(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.MaskType)
        with self.assertRaises(MileError) as ctx:
            ops.set_mask(self.draft.data, ["segt1"], name)
        self.assertEqual(ctx.exception.code, "NOT_VIDEO")


class AnimationTests(MileBase):
    def test_add_intro_animation(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.IntroType)
        result = ops.add_animation(self.draft.data, ["segv1"], name, kind="in", duration="0.6s")
        self.assertEqual(result["changed"][0]["kind"], "intro")
        holders = _materials_of_type(self.draft.data, "material_animations", "sticker_animation")
        self.assertEqual(len(holders), 1)
        self.assertEqual(len(holders[0]["animations"]), 1)
        self.assertIn(holders[0]["id"], _refs_of(self.draft.data, "segv1"))

    def test_add_animation_kinds_share_holder(self):
        metadata = _load_vendor_metadata()
        intro = _first_member_name(metadata.IntroType)
        outro = _first_member_name(metadata.OutroType)
        ops.add_animation(self.draft.data, ["segv1"], intro, kind="in", duration="0.5s")
        ops.add_animation(self.draft.data, ["segv1"], outro, kind="out", duration="0.5s")
        holders = _materials_of_type(self.draft.data, "material_animations", "sticker_animation")
        self.assertEqual(len(holders), 1)
        kinds = {a.get("type") for a in holders[0]["animations"]}
        self.assertEqual(kinds, {"in", "out"})

    def test_add_animation_same_kind_replaces(self):
        metadata = _load_vendor_metadata()
        first = _first_member_name(metadata.IntroType)
        second = _enum_member(metadata.IntroType, 1).name
        ops.add_animation(self.draft.data, ["segv1"], first, kind="in")
        ops.add_animation(self.draft.data, ["segv1"], second, kind="in")
        holders = _materials_of_type(self.draft.data, "material_animations", "sticker_animation")
        self.assertEqual(len(holders[0]["animations"]), 1)

    def test_add_group_animation_duration_bounds(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.GroupAnimationType)
        with self.assertRaises(MileError) as ctx:
            ops.add_animation(self.draft.data, ["segv1"], name, kind="group", duration="10s")
        self.assertEqual(ctx.exception.code, "BAD_RANGE")

    def test_add_animation_bad_kind(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.IntroType)
        with self.assertRaises(MileError) as ctx:
            ops.add_animation(self.draft.data, ["segv1"], name, kind="sideways")
        self.assertEqual(ctx.exception.code, "BAD_ARGS")

    def test_add_animation_not_video(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.IntroType)
        with self.assertRaises(MileError) as ctx:
            ops.add_animation(self.draft.data, ["segt1"], name, kind="in")
        self.assertEqual(ctx.exception.code, "NOT_VIDEO")


class TextAnimationTests(MileBase):
    def test_add_intro_writes_sticker_animation_entry(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.TextIntro)
        result = ops.add_text_animation(self.draft.data, ["segt1"], name, kind="in", duration="0.5s")
        self.assertEqual(result["changed"][0]["kind"], "in")
        self.assertEqual(result["changed"][0]["start"], 0)
        holders = _materials_of_type(self.draft.data, "material_animations", "sticker_animation")
        self.assertEqual(len(holders), 1)
        entry = holders[0]["animations"][0]
        self.assertEqual(entry["type"], "in")
        self.assertEqual(entry["material_type"], "sticker")  # 与真实 5.9 草稿字段一致
        self.assertEqual(entry["panel"], "")
        self.assertIn(holders[0]["id"], _refs_of(self.draft.data, "segt1"))

    def test_loop_defaults_to_full_segment(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.TextLoopAnim)
        result = ops.add_text_animation(self.draft.data, ["segt1"], name, kind="loop")
        changed = result["changed"][0]
        self.assertEqual(changed["start"], 0)
        self.assertEqual(changed["duration"], 2_000_000)  # 测试草稿字幕长 2s

    def test_outro_starts_at_segment_end_minus_duration(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.TextOutro)
        result = ops.add_text_animation(self.draft.data, ["segt1"], name, kind="out", duration="0.8s")
        self.assertEqual(result["changed"][0]["start"], 1_200_000)

    def test_same_kind_replaces_other_kind_kept(self):
        metadata = _load_vendor_metadata()
        intro = _first_member_name(metadata.TextIntro)
        intro2 = _enum_member(metadata.TextIntro, 1).name
        outro = _first_member_name(metadata.TextOutro)
        ops.add_text_animation(self.draft.data, ["segt1"], intro, kind="in")
        ops.add_text_animation(self.draft.data, ["segt1"], outro, kind="out")
        holders = _materials_of_type(self.draft.data, "material_animations", "sticker_animation")
        self.assertEqual({item["type"] for item in holders[0]["animations"]}, {"in", "out"})
        ops.add_text_animation(self.draft.data, ["segt1"], intro2, kind="in")
        holders = _materials_of_type(self.draft.data, "material_animations", "sticker_animation")
        self.assertEqual({item["type"] for item in holders[0]["animations"]}, {"in", "out"})
        self.assertEqual(len(holders[0]["animations"]), 2)

    def test_video_target_rejected(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.TextIntro)
        with self.assertRaises(MileError) as ctx:
            ops.add_text_animation(self.draft.data, ["segv1"], name)
        self.assertEqual(ctx.exception.code, "NOT_TEXT")

    def test_unknown_name_and_kind(self):
        with self.assertRaises(MileError) as ctx:
            ops.add_text_animation(self.draft.data, ["segt1"], "不存在的字幕动画")
        self.assertEqual(ctx.exception.code, "TEXT_ANIMATION_NOT_FOUND")
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.TextIntro)
        with self.assertRaises(MileError) as ctx:
            ops.add_text_animation(self.draft.data, ["segt1"], name, kind="group")
        self.assertEqual(ctx.exception.code, "BAD_ARGS")

    def test_plan_apply_and_remove(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.TextIntro)
        plan = {
            "ops": [
                {"op": "add_text_animation", "target": {"role": "caption"}, "name": name, "kind": "in"},
                {"op": "remove_material", "target": {"ref": "SEGT1"}, "type": "animation"},
            ]
        }
        result = apply_plan(self.draft, plan)
        self.assertEqual([item["op"] for item in result["operations"]], ["add_text_animation", "remove_material"])
        holders = _materials_of_type(self.draft.data, "material_animations", "sticker_animation")
        self.assertEqual(len(holders), 1)  # SEGT2 的动画保留，SEGT1 的被摘除并回收
        self.assertEqual(_refs_of(self.draft.data, "segt1"), [])
        self.assertTrue(all(holder["id"] in _refs_of(self.draft.data, "segt2") for holder in holders))
        self.assertEqual(len(list_backups(self.folder)), 1)
        report = quality.run_checks(self.draft.data)
        self.assertTrue(report["ok"])

    def test_plan_validation_requires_name(self):
        with self.assertRaises(MileError) as ctx:
            compile_plan(self.draft.data, {"ops": [{"op": "字幕动画", "target": {"ref": "SEGT1"}}]})
        self.assertEqual(ctx.exception.code, "MISSING_ARGS")


class RemoveMaterialTests(MileBase):
    def setUp(self):
        super().setUp()
        metadata = _load_vendor_metadata()
        self.filter_name = _first_member_name(metadata.FilterType)
        self.effect_name = _first_member_name(metadata.VideoSceneEffectType)
        self.mask_name = _first_member_name(metadata.MaskType)
        ops.set_filter(self.draft.data, ["segv1"], self.filter_name)
        ops.add_effect(self.draft.data, ["segv1"], self.effect_name)
        ops.set_mask(self.draft.data, ["segv1"], self.mask_name)

    def test_remove_filter(self):
        filters_before = _materials_of_type(self.draft.data, "effects", "filter")
        self.assertEqual(len(filters_before), 1)
        result = ops.remove_material(self.draft.data, ["segv1"], "filter")
        self.assertEqual(len(result["changed"][0]["removed"]), 1)
        self.assertEqual(_materials_of_type(self.draft.data, "effects", "filter"), [])
        self.assertEqual(len(_refs_of(self.draft.data, "segv1")), 4)  # 保留 sp1/af1、特效、蒙版

    def test_remove_with_aliases(self):
        ops.remove_material(self.draft.data, ["segv1"], "特效")
        ops.remove_material(self.draft.data, ["segv1"], "蒙版")
        self.assertEqual(_materials_of_type(self.draft.data, "video_effects", "video_effect"), [])
        self.assertEqual(_materials_of_type(self.draft.data, "masks", "mask"), [])

    def test_remove_unknown_type(self):
        with self.assertRaises(MileError) as ctx:
            ops.remove_material(self.draft.data, ["segv1"], "粒子")
        self.assertEqual(ctx.exception.code, "BAD_ARGS")

    def test_remove_keeps_material_used_elsewhere(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.FilterType)
        ops.set_filter(self.draft.data, ["segv2"], name, keep_old=True)
        all_filters = _materials_of_type(self.draft.data, "effects", "filter")
        shared = [f for f in all_filters if any(f["id"] in _refs_of(self.draft.data, s) for s in ("segv1", "segv2"))]
        self.assertGreaterEqual(len(shared), 1)
        ops.remove_material(self.draft.data, ["segv1"], "filter")
        leftovers = _materials_of_type(self.draft.data, "effects", "filter")
        self.assertEqual(len(leftovers), 1)  # segv2 的滤镜还在
        self.assertIn(leftovers[0]["id"], _refs_of(self.draft.data, "segv2"))


class FeaturePlanTests(MileBase):
    def test_compile_requires_name_and_type(self):
        with self.assertRaises(MileError) as ctx:
            compile_plan(self.draft.data, {"ops": [{"op": "set_filter", "target": {"ref": "SEGV1"}}]})
        self.assertEqual(ctx.exception.code, "MISSING_ARGS")
        with self.assertRaises(MileError) as ctx:
            compile_plan(self.draft.data, {"ops": [{"op": "remove_material", "target": {"ref": "SEGV1"}}]})
        self.assertEqual(ctx.exception.code, "MISSING_ARGS")

    def test_dry_run_does_not_touch_draft(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.FilterType)
        plan = {
            "ops": [
                {"op": "set_filter", "target": {"ref": "SEGV1"}, "name": name, "intensity": 70},
                {"op": "add_effect", "target": {"ref": "SEGV1"}, "name": _first_member_name(metadata.VideoSceneEffectType)},
            ]
        }
        compiled = compile_plan(self.draft.data, plan)
        self.assertIn("set_filter", [op["op"] for op in compiled["operations"]])
        result = apply_plan(self.draft, plan, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(len(result["preview"]), 2)
        self.assertEqual(_materials_of_type(self.draft.data, "effects", "filter"), [])
        self.assertEqual(list_backups(self.folder), [])

    def test_apply_saves_backup_and_schema(self):
        metadata = _load_vendor_metadata()
        plan = {
            "intent": "科技感滤镜与动画",
            "ops": [
                {"op": "set_filter", "target": {"ref": "SEGV1"}, "name": _first_member_name(metadata.FilterType), "intensity": 90},
                {"op": "add_animation", "target": {"ref": "SEGV1"}, "name": _first_member_name(metadata.IntroType), "kind": "in"},
                {"op": "set_mask", "target": {"ref": "SEGV1"}, "name": _first_member_name(metadata.MaskType), "size": 0.6},
                {"op": "remove_material", "target": {"ref": "SEGV1"}, "type": "mask"},
            ],
        }
        result = apply_plan(self.draft, plan)
        self.assertFalse(result["dry_run"])
        self.assertEqual(len(result["operations"]), 4)
        self.assertTrue(result["saved"])
        self.assertEqual(len(list_backups(self.folder)), 1)
        self.assertEqual(len(_materials_of_type(self.draft.data, "effects", "filter")), 1)
        self.assertEqual(len(_materials_of_type(self.draft.data, "masks", "mask")), 0)
        self.assertEqual(len(_materials_of_type(self.draft.data, "material_animations", "sticker_animation")), 1)
        report = quality.run_checks(self.draft.data)
        self.assertTrue(report["ok"])

    def test_apply_rolls_back_on_late_failure(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.FilterType)
        plan = {
            "ops": [
                {"op": "set_filter", "target": {"ref": "SEGV1"}, "name": name},
                {"op": "set_filter", "target": {"ref": "SEGT1"}, "name": name},  # 文字片段 -> NOT_VIDEO
            ]
        }
        with self.assertRaises(MileError) as ctx:
            apply_plan(self.draft, plan)
        self.assertEqual(ctx.exception.code, "NOT_VIDEO")
        self.assertEqual(_materials_of_type(self.draft.data, "effects", "filter"), [])
        self.assertEqual(list_backups(self.folder), [])
        fresh = load_draft(self.folder)
        self.assertEqual(_materials_of_type(fresh.data, "effects", "filter"), [])

    def test_apply_rippleable_effects_on_main_video(self):
        metadata = _load_vendor_metadata()
        name = _first_member_name(metadata.FilterType)
        plan = {"ops": [{"op": "滤镜", "target": {"role": "main"}, "filter": name}]}
        result = apply_plan(self.draft, plan)
        self.assertEqual(len(result["operations"]), 1)
        filters = _materials_of_type(self.draft.data, "effects", "filter")
        self.assertEqual(len(filters), 2)  # segv1 + segv2 主线段各一个

    def test_timeline_with_feature_materials(self):
        metadata = _load_vendor_metadata()
        plan = {
            "ops": [
                {"op": "set_filter", "target": {"ref": "SEGV1"}, "name": _first_member_name(metadata.FilterType)},
            ]
        }
        apply_plan(self.draft, plan)
        timeline = build_timeline(self.draft.data)
        main = [s for s in timeline["segments"] if s["ref"] == "SEGV1"][0]
        self.assertEqual(len(_refs_of(self.draft.data, "segv1")), 3)  # sp1/af1/filter