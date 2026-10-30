"""结构化剪辑计划：自然语言需求与草稿之间的中间层。

一份计划是若干操作的数组，例如::

    {
      "intent": "把开场字幕改短一点",
      "ops": [
        {"op": "set_text", "target": {"text_contains": "欢迎"}, "text": "欢迎来到米乐"},
        {"op": "trim", "target": {"ref": "A1B2C3D4"}, "end": "18s", "ripple": true}
      ]
    }

``compile_plan`` 只解析和校验，不修改草稿；``apply_plan`` 在全部操作
成功后才保存，失败时回滚内存中的修改，保证不会留下半套编辑。
"""

from __future__ import annotations

import copy
import re
from typing import Optional

from . import ops
from .draft import Draft, refresh_duration
from .errors import MileError
from .timeline import build_timeline, segment_public, select_segments

__all__ = ["compile_plan", "apply_plan", "SUPPORTED_OPS"]

SUPPORTED_OPS = {
    "delete",
    "trim",
    "move",
    "shift",
    "split",
    "set_text",
    "set_volume",
    "set_speed",
    "set_transform",
    "set_visible",
    "set_canvas",
    "add_transition",
    "remove_transition",
    "set_filter",
    "add_effect",
    "set_mask",
    "add_animation",
    "add_text_animation",
    "add_keyframes",
    "remove_keyframes",
    "remove_material",
    "clone_text",
    "add_text",
    "add_audio",
    "add_media",
}

_ALIASES = {
    "remove": "delete",
    "del": "delete",
    "cut": "trim",
    "resize": "trim",
    "text": "set_text",
    "replace_text": "set_text",
    "volume": "set_volume",
    "mute": "set_volume",
    "speed": "set_speed",
    "transform": "set_transform",
    "visibility": "set_visible",
    "transition": "add_transition",
    "duplicate_text": "clone_text",
    "caption": "add_text",
    "audio": "add_audio",
    "media": "add_media",
    "add_video": "add_media",
    "add_photo": "add_media",
    "canvas": "set_canvas",
    "滤镜": "set_filter",
    "filter": "set_filter",
    "特效": "add_effect",
    "effect": "add_effect",
    "蒙版": "set_mask",
    "mask": "set_mask",
    "动画": "add_animation",
    "animation": "add_animation",
    "字幕动画": "add_text_animation",
    "文字动画": "add_text_animation",
    "text_animation": "add_text_animation",
    "关键帧": "add_keyframes",
    "keyframe": "add_keyframes",
    "keyframes": "add_keyframes",
    "移除关键帧": "remove_keyframes",
    "remove_keyframe": "remove_keyframes",
    "移除素材": "remove_material",
    "删除": "delete",
    "裁剪": "trim",
    "分割": "split",
    "移动": "move",
    "改文字": "set_text",
    "改文案": "set_text",
    "音量": "set_volume",
    "变速": "set_speed",
    "画面": "set_transform",
    "隐藏": "set_visible",
    "转场": "add_transition",
    "克隆字幕": "clone_text",
    "加字幕": "add_text",
    "加音乐": "add_audio",
    "加素材": "add_media",
    "入轨": "add_media",
    "加贴纸": "add_media",
    "画布": "set_canvas",
}

_SELECT_KEYS = (
    "ref",
    "ids",
    "track",
    "index",
    "range",
    "at",
    "text_contains",
    "name_contains",
    "role",
    "material_type",
    "min_duration",
    "max_duration",
)

_CONTROL_KEYS = {"op", "target", "targets", "args", "first", "all", "limit", "note", "description", "desc", "id"}

_SINGLE_TARGET_OPS = {"trim", "split", "add_transition", "remove_transition", "clone_text"}
_NO_TARGET_OPS = {"add_text", "add_audio", "add_media", "set_canvas"}

_TEXT_STYLE_KEYS = {
    "text",
    "font_size",
    "color",
    "bold",
    "italic",
    "underline",
    "align",
    "alpha",
    "letter_spacing",
    "line_spacing",
    "font_path",
    "font_name",
    "border",
    "shadow",
    "background",
}

_TRANSFORM_KEYS = {
    "x",
    "y",
    "scale",
    "scale_x",
    "scale_y",
    "rotation",
    "alpha",
    "flip_horizontal",
    "flip_vertical",
}


def _normalize_op_name(name: str) -> str:
    text = str(name or "").strip()
    text = re.sub(r"[\s\-]+", "_", text)
    lowered = text.lower()
    resolved = _ALIASES.get(text) or _ALIASES.get(lowered) or lowered
    if resolved not in SUPPORTED_OPS:
        raise MileError(
            "UNSUPPORTED_OP",
            f"不支持的操作: {name}",
            supported=sorted(SUPPORTED_OPS),
        )
    return resolved


def _extract_selector(op: dict, op_name: str) -> dict:
    selector: dict = {}
    target = op.get("target", op.get("targets"))
    if isinstance(target, str):
        selector["ref"] = target
    elif isinstance(target, dict):
        selector.update(target)
    elif isinstance(target, list) and target and all(isinstance(item, str) for item in target):
        selector["ids"] = target
    elif isinstance(target, list) and target and all(isinstance(item, dict) for item in target):
        raise MileError("BAD_TARGET", "v1 暂不支持一个操作使用多个不同的定位条件")

    for key in _SELECT_KEYS:
        if key in op and key not in selector:
            if key == "at" and op_name == "split":
                continue
            if key == "track" and op_name == "clone_text" and op.get("to_track") is not None:
                continue
            selector[key] = op[key]
    return selector


def _extract_args(op: dict, op_name: str) -> dict:
    args = dict(op.get("args") or {})
    for key, value in op.items():
        # split 的 at 同时也是定位字段，必须先于 _SELECT_KEYS 过滤处理
        if key == "at" and op_name == "split":
            args.setdefault("at", value)
            continue
        if key in _CONTROL_KEYS or key in _SELECT_KEYS:
            continue
        args.setdefault(key, value)
    if op_name == "move" and op.get("to") is not None:
        args.setdefault("start", op.get("to"))
    if op_name in _NO_TARGET_OPS and op.get("track") is not None:
        args.setdefault("track", op.get("track"))
    return args


def _require_targets(op_name: str, matches: list[dict], op: dict) -> list[dict]:
    if op_name in _NO_TARGET_OPS:
        return []
    if not matches:
        raise MileError(
            "NO_TARGET",
            "没有找到符合条件的片段",
            op=op_name,
            selector=op.get("target") or {key: op.get(key) for key in _SELECT_KEYS if op.get(key) is not None},
        )
    if op_name in _SINGLE_TARGET_OPS and len(matches) > 1 and not op.get("first"):
        raise MileError(
            "TARGET_AMBIGUOUS",
            f"该操作一次只能作用于一个片段，当前匹配到 {len(matches)} 个；请缩小条件或加 first: true",
            op=op_name,
            matches=[segment_public(seg) for seg in matches[:10]],
        )
    if op_name in _SINGLE_TARGET_OPS:
        return matches[:1]
    return matches


def _validate_args(op_name: str, args: dict) -> None:
    def need(*keys):
        missing = [key for key in keys if args.get(key) is None]
        if missing:
            raise MileError("MISSING_ARGS", f"{op_name} 缺少参数: {' / '.join(missing)}", op=op_name)

    def need_any(*keys):
        if all(args.get(key) is None for key in keys):
            raise MileError("MISSING_ARGS", f"{op_name} 缺少参数: {' / '.join(keys)}", op=op_name)

    if op_name == "trim":
        need_any("start", "end", "duration")
    elif op_name == "move":
        if args.get("start") is None and args.get("delta") is None and args.get("to") is None:
            raise MileError("MISSING_ARGS", "move 需要 start / delta / to", op=op_name)
    elif op_name == "shift":
        need("delta")
    elif op_name == "split":
        need("at")
    elif op_name == "set_text":
        if not any(args.get(key) is not None for key in _TEXT_STYLE_KEYS):
            raise MileError("MISSING_ARGS", "set_text 至少需要一个文字/样式参数", op=op_name)
    elif op_name == "set_volume":
        need("volume")
    elif op_name == "set_speed":
        need("speed")
    elif op_name == "set_transform":
        if not any(args.get(key) is not None for key in _TRANSFORM_KEYS):
            raise MileError("MISSING_ARGS", "set_transform 至少需要一个画面参数", op=op_name)
    elif op_name == "set_visible":
        need("visible")
    elif op_name == "set_canvas":
        need("width", "height")
    elif op_name == "add_text":
        need("text", "duration")
    elif op_name == "add_audio":
        need("path")
    elif op_name == "add_media":
        need("path")
    elif op_name == "set_filter":
        need_any("name", "filter")
    elif op_name == "add_effect":
        need_any("name", "effect")
        if args.get("params") is not None and not isinstance(args.get("params"), list):
            raise MileError("BAD_ARGS", "add_effect 的 params 必须是 0-100 数值数组", op=op_name)
    elif op_name == "set_mask":
        need_any("name", "mask")
    elif op_name == "add_animation":
        need_any("name", "animation")
    elif op_name == "add_text_animation":
        need_any("name", "animation")
    elif op_name == "add_keyframes":
        need("property", "frames")
    elif op_name == "remove_keyframes":
        pass
    elif op_name == "remove_material":
        need("type")


def _plan_ops(plan) -> list[dict]:
    if isinstance(plan, list):
        return plan
    if isinstance(plan, dict) and "op" in plan:
        return [plan]
    if isinstance(plan, dict):
        raw = plan.get("ops")
        if raw is None:
            raw = plan.get("operations")
        if raw is None:
            raise MileError("BAD_PLAN", "计划中缺少 ops 数组")
        if not isinstance(raw, list):
            raise MileError("BAD_PLAN", "ops 必须是数组")
        return raw
    raise MileError("BAD_PLAN", "计划必须是对象或操作数组")


def compile_plan(data: dict, plan) -> dict:
    """解析并校验计划；不修改草稿。"""
    raw_ops = _plan_ops(plan)
    if not raw_ops:
        raise MileError("EMPTY_PLAN", "计划中没有任何操作")

    timeline = build_timeline(data)
    compiled = []
    warnings = []
    for index, raw in enumerate(raw_ops):
        if not isinstance(raw, dict):
            raise MileError("BAD_PLAN", f"第 {index + 1} 个操作不是对象")
        op_name = _normalize_op_name(raw.get("op"))
        selector = {} if op_name in _NO_TARGET_OPS else _extract_selector(raw, op_name)
        args = _extract_args(raw, op_name)
        if op_name == "set_volume":
            if str(raw.get("op", "")).strip().lower() == "mute" or args.get("mute"):
                args["volume"] = 0.0
            elif isinstance(args.get("volume"), str) and args["volume"].strip().endswith("%"):
                args["volume"] = float(args["volume"].strip().rstrip("%")) / 100.0
        _validate_args(op_name, args)

        matches = select_segments(timeline, selector) if selector else []
        targets = _require_targets(op_name, matches, raw)

        note = raw.get("note") or raw.get("description") or raw.get("desc")
        entry = {
            "index": index,
            "op": op_name,
            "note": note,
            "targets": [seg["id"] for seg in targets],
            "refs": [seg["ref"] for seg in targets],
            "target_detail": [segment_public(seg) for seg in targets],
            "args": args,
        }
        if args.get("at") is not None and op_name == "split":
            entry["at"] = args["at"]
        compiled.append(entry)

    return {
        "draft": data.get("name") or "",
        "intent": plan.get("intent") if isinstance(plan, dict) else None,
        "tags": plan.get("tags") if isinstance(plan, dict) else None,
        "notes": plan.get("notes") if isinstance(plan, dict) else None,
        "operations": compiled,
        "warnings": warnings,
    }


def _apply_one(data: dict, entry: dict):
    op_name = entry["op"]
    args = dict(entry["args"])
    ids = entry["targets"]

    if op_name == "delete":
        return ops.delete_segments(data, ids, ripple=bool(args.get("ripple")))
    if op_name == "trim":
        return ops.trim_segment(
            data,
            ids[0],
            start=args.get("start"),
            end=args.get("end"),
            duration=args.get("duration"),
            ripple=bool(args.get("ripple")),
        )
    if op_name == "move":
        return ops.move_segments(
            data,
            ids,
            start=args.get("start"),
            delta=args.get("delta"),
            track=args.get("to_track") if args.get("to_track") is not None else None,
            overlap=str(args.get("overlap") or "reject"),
        )
    if op_name == "shift":
        return ops.shift_segments(data, ids, args.get("delta"))
    if op_name == "split":
        return ops.split_segment(data, ids[0], args.get("at"))
    if op_name == "set_text":
        style = {key: args.get(key) for key in _TEXT_STYLE_KEYS if args.get(key) is not None}
        return ops.set_text(data, ids, **style)
    if op_name == "set_volume":
        return ops.set_volume(
            data,
            ids,
            args.get("volume"),
            fade_in=args.get("fade_in"),
            fade_out=args.get("fade_out"),
        )
    if op_name == "set_speed":
        return ops.set_speed(data, ids, args.get("speed"), keep_duration=bool(args.get("keep_duration")))
    if op_name == "set_transform":
        transform = {key: args.get(key) for key in _TRANSFORM_KEYS if args.get(key) is not None}
        return ops.set_transform(data, ids, **transform)
    if op_name == "set_visible":
        return ops.set_visible(data, ids, bool(args.get("visible")))
    if op_name == "set_canvas":
        return ops.set_canvas(data, args.get("width"), args.get("height"))
    if op_name == "add_transition":
        return ops.add_transition(
            data,
            ids[0],
            args.get("type") or args.get("transition") or "叠化",
            duration=args.get("duration"),
        )
    if op_name == "remove_transition":
        return ops.remove_transition(data, ids[0])
    if op_name == "set_filter":
        return ops.set_filter(
            data,
            ids,
            args.get("name") or args.get("filter"),
            intensity=args.get("intensity", 100.0),
            keep_old=bool(args.get("keep_old", False)),
        )
    if op_name == "add_effect":
        params = args.get("params")
        return ops.add_effect(
            data,
            ids,
            args.get("name") or args.get("effect"),
            params=list(params) if isinstance(params, list) else None,
            keep_old=bool(args.get("keep_old", True)),
        )
    if op_name == "set_mask":
        mask_args = {
            key: args.get(key)
            for key in ("center_x", "center_y", "size", "rotation", "feather", "invert", "rect_width", "round_corner")
            if args.get(key) is not None
        }
        return ops.set_mask(data, ids, args.get("name") or args.get("mask"), **mask_args)
    if op_name == "add_animation":
        return ops.add_animation(
            data,
            ids,
            args.get("name") or args.get("animation"),
            kind=args.get("kind", "in"),
            duration=args.get("duration"),
        )
    if op_name == "add_text_animation":
        return ops.add_text_animation(
            data,
            ids,
            args.get("name") or args.get("animation"),
            kind=args.get("kind", "in"),
            duration=args.get("duration"),
        )
    if op_name == "add_keyframes":
        return ops.add_keyframes(data, ids, args.get("property"), args.get("frames"))
    if op_name == "remove_keyframes":
        return ops.remove_keyframes(data, ids, args.get("property"))
    if op_name == "remove_material":
        return ops.remove_material(data, ids, args.get("type"))
    if op_name == "clone_text":
        template_id = ids[0]
        template = entry["target_detail"][0]
        return ops.clone_text_segment(
            data,
            template_id,
            text=args.get("text"),
            start=args.get("start"),
            duration=args.get("duration"),
            track=args.get("to_track"),
        )
    if op_name == "add_text":
        return ops.add_text(
            data,
            text=args.get("text"),
            start=args.get("start", 0),
            duration=args.get("duration"),
            track=args.get("to_track") if args.get("to_track") is not None else args.get("track"),
            font_size=args.get("font_size", 8.0),
            color=args.get("color", "#ffffff"),
            align=args.get("align", 1),
            bold=bool(args.get("bold", False)),
            italic=bool(args.get("italic", False)),
            underline=bool(args.get("underline", False)),
            alpha=args.get("alpha", 1.0),
            transform_y=args.get("transform_y"),
            x=args.get("x"),
            scale=args.get("scale"),
        )
    if op_name == "add_audio":
        return ops.add_audio(
            data,
            args.get("path"),
            start=args.get("start", 0),
            duration=args.get("duration"),
            name=args.get("name"),
            volume=args.get("volume", 1.0),
            track=args.get("to_track") if args.get("to_track") is not None else args.get("track"),
            loop=bool(args.get("loop", False)),
        )
    if op_name == "add_media":
        return ops.add_media(
            data,
            args.get("path"),
            start=args.get("start"),
            duration=args.get("duration"),
            source_start=args.get("source_start"),
            track=args.get("to_track") if args.get("to_track") is not None else args.get("track"),
            volume=args.get("volume", 1.0),
            scale=args.get("scale"),
            x=args.get("x"),
            y=args.get("y"),
            rotation=args.get("rotation"),
            alpha=args.get("alpha"),
            name=args.get("name"),
        )
    raise MileError("UNSUPPORTED_OP", f"未实现的操作: {op_name}")


def _execute_ops(data: dict, compiled: dict) -> tuple[list[dict], list[str]]:
    """在内存 dict ``data`` 上按顺序执行编译后的操作；任一失败整体回滚数据并抛错。

    与 ``apply_plan`` 共用同一执行路径：preview / dry-run 与真实落盘的结果
    保证字段级一致。不写磁盘、不触发备份。
    """
    snapshot = copy.deepcopy(data)
    applied = []
    warnings = list(compiled.get("warnings") or [])

    for entry in compiled["operations"]:
        try:
            result = _apply_one(data, entry)
        except Exception as exc:
            data.clear()
            data.update(snapshot)
            if isinstance(exc, MileError):
                raise MileError(
                    exc.code,
                    f"第 {entry['index'] + 1} 步（{entry['op']}）失败：{exc.reason}",
                    **exc.details,
                    op_index=entry["index"],
                    op=entry["op"],
                ) from exc
            raise MileError(
                "PLAN_FAILED",
                f"第 {entry['index'] + 1} 步（{entry['op']}）失败：{exc}",
                op_index=entry["index"],
                op=entry["op"],
            ) from exc
        entry["status"] = "applied"
        entry["result"] = result
        applied.append(entry)
        for item in (result or {}).get("changed", []) if isinstance(result, dict) else []:
            if isinstance(item, dict) and item.get("warning"):
                warnings.append(f"{entry['op']}#{entry['index'] + 1}: {item['warning']}")
        if isinstance(result, dict) and result.get("warning"):
            warnings.append(f"{entry['op']}#{entry['index'] + 1}: {result['warning']}")

    refresh_duration(data)
    return applied, warnings


def apply_plan(draft: Draft, plan, *, dry_run: bool = False, backup: bool = True) -> dict:
    """校验并应用计划。失败时回滚内存修改，磁盘上不会留下半套结果。"""
    compiled = compile_plan(draft.data, plan)
    if dry_run:
        compiled["dry_run"] = True
        compiled["preview"] = [
            {"op": entry["op"], "targets": entry["target_detail"], "args": entry["args"]}
            for entry in compiled["operations"]
        ]
        return compiled

    timeline = build_timeline(draft.data)
    before = {
        "duration": int(draft.data.get("duration") or 0),
        "segments": len(timeline.get("segments") or []),
        "tracks": len(timeline.get("tracks") or []),
    }

    applied, warnings = _execute_ops(draft.data, compiled)

    timeline_after = build_timeline(draft.data)
    saved_path = draft.save(backup=backup)
    after = {
        "duration": int(draft.data.get("duration") or 0),
        "segments": len(timeline_after.get("segments") or []),
        "tracks": len(timeline_after.get("tracks") or []),
    }
    return {
        "draft": draft.name,
        "folder": str(draft.folder),
        "dry_run": False,
        "intent": compiled.get("intent"),
        "tags": compiled.get("tags"),
        "notes": compiled.get("notes"),
        "operations": applied,
        "warnings": warnings,
        "before": before,
        "after": after,
        "saved": str(saved_path),
    }
