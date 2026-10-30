"""片段级精准操作。

所有函数直接修改传入的草稿 dict（不做保存），并返回结构化结果；
保存、备份、撤销由 ``mile.draft`` / ``mile.plan`` 负责。

设计原则：
- 只触碰目标片段及其必要关联素材，其它轨道保持不变；
- 时长、源区间与变速保持自洽；
- 无法安全执行时抛出带稳定 code 的 ``MileError``，而不是“尽量猜”。
"""

from __future__ import annotations

import copy
import json
import os
import re
import uuid
from pathlib import Path
from typing import Iterable, Optional

from .draft import refresh_duration
from .errors import MileError
from .timecode import US_PER_SEC, parse_time, round_to_frame

__all__ = [
    "delete_segments",
    "trim_segment",
    "move_segments",
    "shift_segments",
    "split_segment",
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
    "clone_text_segment",
    "add_text",
    "add_audio",
    "add_media",
    "resolve_transition_type",
    "list_transition_types",
    "parse_color",
]

_DEFAULT_CLIP = {
    "alpha": 1.0,
    "flip": {"horizontal": False, "vertical": False},
    "rotation": 0.0,
    "scale": {"x": 1.0, "y": 1.0},
    "transform": {"x": 0.0, "y": 0.0},
}

_DUPLICABLE_TYPES = {
    "speed",
    "canvas_color",
    "canvas_blur",
    "audio_fade",
    "sticker_animation",
    "material_animation",
    "chroma",
    "text_shape",
    "text_effect",
}

_FADE_TYPE = "audio_fade"

_TRANSITION_ALIASES = {
    "dissolve": "叠化",
    "fade": "淡入淡出",
    "wipe": "擦除",
    "slide": "滑动",
    "zoom": "缩放",
    "flash": "闪白",
    "black": "黑场",
}


def _new_id() -> str:
    return uuid.uuid4().hex


def _ref(seg: dict) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", str(seg.get("id") or ""))[:8].upper()


def _materials(data: dict) -> dict:
    materials = data.setdefault("materials", {})
    if not isinstance(materials, dict):
        raise MileError("DRAFT_INVALID", "草稿 materials 字段格式异常")
    return materials


def _material_location(data: dict, material_id: str):
    materials = _materials(data)
    for list_name, items in materials.items():
        if isinstance(items, list):
            for index, item in enumerate(items):
                if isinstance(item, dict) and str(item.get("id")) == str(material_id):
                    return list_name, items, index, item
    return None, None, None, None


def _material(data: dict, material_id: str) -> dict:
    _, _, _, item = _material_location(data, material_id)
    return item or {}


def _add_material(data: dict, list_name: str, material: dict) -> dict:
    materials = _materials(data)
    items = materials.setdefault(list_name, [])
    if not isinstance(items, list):
        items = materials[list_name] = []
    items.append(material)
    return material


def _tracks(data: dict) -> list[dict]:
    tracks = data.setdefault("tracks", [])
    if not isinstance(tracks, list):
        raise MileError("DRAFT_INVALID", "草稿 tracks 字段格式异常")
    return tracks


def _iter_segments(data: dict) -> Iterable[tuple[dict, dict, int]]:
    for track in _tracks(data):
        for index, seg in enumerate(track.get("segments") or []):
            yield track, seg, index


def _find_segment(data: dict, seg_id: str):
    seg_id = str(seg_id or "").strip()
    if not seg_id:
        raise MileError("SEGMENT_REQUIRED", "缺少片段 id")
    exact = []
    prefix = []
    needle = re.sub(r"[^0-9A-Za-z]", "", seg_id).lower()
    for track, seg, index in _iter_segments(data):
        full = str(seg.get("id") or "")
        if full == seg_id:
            exact.append((track, seg, index))
            continue
        plain = re.sub(r"[^0-9A-Za-z]", "", full).lower()
        if len(needle) >= 4 and (plain.startswith(needle) or needle.startswith(plain)):
            prefix.append((track, seg, index))
    matches = exact or prefix
    if not matches:
        raise MileError("SEGMENT_NOT_FOUND", f"未找到片段: {seg_id}")
    if len({id(item[1]) for item in matches}) > 1:
        raise MileError("SEGMENT_AMBIGUOUS", f"片段 id 前缀匹配到多个片段: {seg_id}")
    return matches[0]


def _target_range(seg: dict) -> dict:
    target = seg.setdefault("target_timerange", {"start": 0, "duration": 0})
    target["start"] = int(target.get("start") or 0)
    target["duration"] = int(target.get("duration") or 0)
    return target


def _segment_end(seg: dict) -> int:
    target = _target_range(seg)
    return target["start"] + target["duration"]


def _track_type(track: dict) -> str:
    return str(track.get("type") or "")


def _compatible(track_type: str, seg_type: str) -> bool:
    return track_type == seg_type


def _insert_sorted(track: dict, seg: dict) -> int:
    segs = track.setdefault("segments", [])
    start = _target_range(seg)["start"]
    index = len(segs)
    for i, existing in enumerate(segs):
        if int((existing.get("target_timerange") or {}).get("start") or 0) > start:
            index = i
            break
    segs.insert(index, seg)
    return index


def _resolve_track(data: dict, spec, *, seg_type: Optional[str] = None, create: bool = False) -> dict:
    tracks = _tracks(data)
    if isinstance(spec, int):
        if 0 <= spec < len(tracks):
            return tracks[spec]
        raise MileError("TRACK_NOT_FOUND", f"轨道序号越界: {spec}")
    if spec is not None and not isinstance(spec, str):
        raise MileError("BAD_TRACK", "轨道选择器必须是序号或名称")

    text = str(spec or "").strip()
    if text:
        if text.isdigit():
            index = int(text)
            if 0 <= index < len(tracks):
                return tracks[index]
            raise MileError("TRACK_NOT_FOUND", f"轨道序号越界: {index}")
        nth = None
        match = re.match(r"^(.+?)\s*[#:]\s*(\d+)$", text)
        if match:
            text, nth = match.group(1).strip(), int(match.group(2))
        type_names = {"视频": "video", "音频": "audio", "文本": "text", "字幕": "text", "贴纸": "sticker", "特效": "effect", "滤镜": "filter"}
        label_match = re.match(r"^(视频|音频|文本|字幕|贴纸|特效|滤镜)\s*(\d+)$", text)
        if label_match and nth is None:
            text, nth = label_match.group(1), int(label_match.group(2))
        lowered = text.lower()
        wanted_type = type_names.get(text, lowered)
        matches = [
            track
            for track in tracks
            if _track_type(track).lower() == wanted_type
            or lowered in str(track.get("name") or "").lower()
        ]
        if nth is not None:
            if 1 <= nth <= len(matches):
                return matches[nth - 1]
            raise MileError("TRACK_NOT_FOUND", f"未找到第 {nth} 条 {text} 轨道")
        if matches:
            return matches[0]
        if seg_type and seg_type != wanted_type:
            raise MileError("TRACK_NOT_FOUND", f"未找到轨道: {spec}")

    if seg_type and create:
        if not text:
            for track in tracks:
                if _track_type(track) == seg_type:
                    return track
        track = {
            "attribute": 0,
            "flag": 0,
            "id": _new_id(),
            "is_default_name": True,
            "name": "",
            "segments": [],
            "type": seg_type,
        }
        tracks.append(track)
        return track
    if spec:
        raise MileError("TRACK_NOT_FOUND", f"未找到轨道: {spec}")
    if seg_type:
        for track in tracks:
            if _track_type(track) == seg_type:
                return track
        if create:
            return _resolve_track(data, None, seg_type=seg_type, create=True)
    raise MileError("TRACK_NOT_FOUND", "无法确定目标轨道")


def _min_duration(data: dict) -> int:
    try:
        fps = float(data.get("fps") or 30)
    except Exception:
        fps = 30.0
    return max(1, int(round(US_PER_SEC / max(fps, 1.0))))


def _align(data: dict, us: int, mode: str = "nearest") -> int:
    try:
        fps = float(data.get("fps") or 0)
    except Exception:
        fps = 0.0
    return round_to_frame(us, fps, mode) if fps else int(us)


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _conflicts(track: dict, start: int, end: int, ignore: set[str]) -> list[dict]:
    result = []
    for seg in track.get("segments") or []:
        if str(seg.get("id")) in ignore:
            continue
        s = int((seg.get("target_timerange") or {}).get("start") or 0)
        d = int((seg.get("target_timerange") or {}).get("duration") or 0)
        if d <= 0:
            continue
        if _overlaps(start, end, s, s + d):
            result.append({"ref": _ref(seg), "id": seg.get("id"), "start": s, "end": s + d})
    return result


def parse_color(value) -> tuple[tuple[float, float, float], str]:
    """把颜色解析为 ``((r,g,b), "#rrggbb")``；支持 0-1 浮点、0-255 整数与十六进制。"""
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("#"):
            text = text[1:]
        if len(text) in (3, 4):
            text = "".join(ch * 2 for ch in text[:3])
        elif len(text) in (6, 8):
            text = text[:6]
        if len(text) == 6 and re.fullmatch(r"[0-9a-fA-F]{6}", text):
            rgb = tuple(int(text[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
            return tuple(round(v, 6) for v in rgb), f"#{text.lower()}"
        raise MileError("BAD_COLOR", f"无法解析颜色: {value}")
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            numbers = [float(v) for v in value]
        except Exception as exc:
            raise MileError("BAD_COLOR", f"无法解析颜色: {value}") from exc
        if any(v > 1.0 for v in numbers):
            numbers = [v / 255.0 for v in numbers]
        if any(v < 0 or v > 1 for v in numbers):
            raise MileError("BAD_COLOR", f"颜色分量应在 0-1 或 0-255: {value}")
        rgb = tuple(round(v, 6) for v in numbers)
        hex_value = "#" + "".join(f"{int(round(v * 255)):02x}" for v in rgb)
        return rgb, hex_value
    raise MileError("BAD_COLOR", f"无法解析颜色: {value}")


def _ensure_speed_material(data: dict, seg: dict) -> dict:
    refs = seg.setdefault("extra_material_refs", [])
    for ref in list(refs):
        material = _material(data, ref)
        if material.get("type") == "speed":
            speed_material = material
            break
    else:
        speed_material = _add_material(
            data,
            "speeds",
            {"curve_speed": None, "id": _new_id(), "mode": 0, "speed": float(seg.get("speed") or 1.0), "type": "speed"},
        )
        refs.append(speed_material["id"])
    speed_material["speed"] = float(seg.get("speed") or 1.0)
    return speed_material


def _material_duration(seg: dict, material: dict) -> int:
    try:
        return int(material.get("duration") or 0)
    except Exception:
        return 0


def _replace_segment_refs_for_split(data: dict, seg: dict, offset: int) -> dict:
    """为新分割出的后半段复制必要素材引用，返回新片段。"""
    second = copy.deepcopy(seg)
    second["id"] = _new_id()
    second["keyframe_refs"] = []

    keyframes = []
    for keyframe_list in seg.get("common_keyframes") or []:
        remaining = []
        for keyframe in keyframe_list.get("keyframe_list") or []:
            current = int(keyframe.get("time_offset") or 0)
            if current >= offset:
                copied = copy.deepcopy(keyframe)
                copied["id"] = _new_id()
                copied["time_offset"] = current - offset
                remaining.append(copied)
        if remaining:
            copied_list = copy.deepcopy(keyframe_list)
            copied_list["id"] = _new_id()
            copied_list["keyframe_list"] = remaining
            keyframes.append(copied_list)
    seg["common_keyframes"] = [
        keyframe_list
        for keyframe_list in (seg.get("common_keyframes") or [])
        if any(int(k.get("time_offset") or 0) < offset for k in keyframe_list.get("keyframe_list") or [])
    ]
    second["common_keyframes"] = keyframes

    new_refs = []
    for ref in seg.get("extra_material_refs") or []:
        list_name, items, _, material = _material_location(data, ref)
        if not material:
            continue
        mat_type = str(material.get("type") or "")
        if mat_type == "transition":
            continue
        if mat_type in _DUPLICABLE_TYPES and list_name:
            cloned = copy.deepcopy(material)
            cloned["id"] = _new_id()
            items.append(cloned)
            new_refs.append(cloned["id"])
        else:
            new_refs.append(ref)
    second["extra_material_refs"] = new_refs
    return second


def _apply_head_trim(seg: dict, material: dict, speed: float, delta: int):
    source = seg.get("source_timerange")
    if isinstance(source, dict):
        source["start"] = int(source.get("start") or 0) + int(round(delta * speed))


def delete_segments(data: dict, seg_ids: list[str], *, ripple: bool = False) -> dict:
    """删除片段；``ripple=True`` 时同轨道后续片段左移补位。"""
    if not seg_ids:
        raise MileError("SEGMENT_REQUIRED", "缺少要删除的片段")
    found = []
    for seg_id in seg_ids:
        track, seg, index = _find_segment(data, seg_id)
        if all(seg is not item[1] for item in found):
            found.append((track, seg, index))

    removed = []
    by_track: dict[int, tuple[dict, list[dict]]] = {}
    for track, seg, _ in found:
        removed.append(
            {
                "ref": _ref(seg),
                "id": seg.get("id"),
                "track_index": _tracks(data).index(track),
                "start": _target_range(seg)["start"],
                "duration": _target_range(seg)["duration"],
                "name": _material(data, seg.get("material_id")).get("material_name")
                or _material(data, seg.get("material_id")).get("name")
                or "",
            }
        )
        by_track.setdefault(id(track), (track, []))[1].append(seg)

    for track, segs in by_track.values():
        track_segs = track.get("segments") or []
        removed_ids = {str(seg.get("id")) for seg in segs}
        track_segs[:] = [seg for seg in track_segs if str(seg.get("id")) not in removed_ids]
        if ripple:
            for seg in track_segs:
                shift = sum(
                    _target_range(removed_seg)["duration"]
                    for removed_seg in segs
                    if _target_range(removed_seg)["start"] < _target_range(seg)["start"]
                )
                if shift:
                    _target_range(seg)["start"] = max(0, _target_range(seg)["start"] - shift)

    refresh_duration(data)
    return {"removed": removed, "ripple": bool(ripple), "remaining_duration": int(data.get("duration") or 0)}


def trim_segment(
    data: dict,
    seg_id: str,
    *,
    start=None,
    end=None,
    duration=None,
    ripple: bool = False,
) -> dict:
    """调整片段的入点/出点（时间轴上的绝对时间）。"""
    track, seg, _ = _find_segment(data, seg_id)
    target = _target_range(seg)
    old_start, old_end = target["start"], target["start"] + target["duration"]
    material = _material(data, seg.get("material_id"))
    speed = float(seg.get("speed") or 1.0)

    new_start = parse_time(start) if start is not None else old_start
    if duration is not None:
        new_end = new_start + parse_time(duration)
    elif end is not None:
        new_end = parse_time(end)
    else:
        new_end = old_end
    if start is None and end is None and duration is None:
        raise MileError("BAD_ARGS", "裁剪需要提供 start / end / duration 之一")

    new_start = max(0, new_start)
    minimum = _min_duration(data)
    if new_end - new_start < minimum:
        raise MileError("SEGMENT_TOO_SHORT", f"裁剪后时长不足一帧（{minimum}µs）")
    if new_start >= old_end or new_end <= old_start:
        if not (new_start <= old_start and new_end >= old_end):
            raise MileError("BAD_RANGE", "裁剪范围与片段没有交集")

    warning = None
    source = seg.get("source_timerange")
    if isinstance(source, dict):
        source_start = int(source.get("start") or 0)
        source_duration = int(source.get("duration") or 0)
        head_delta = new_start - old_start
        new_source_start = source_start + int(round(head_delta * speed))
        new_source_duration = int(round((new_end - new_start) * speed))
        if new_source_start < 0:
            raise MileError(
                "SOURCE_OUT_OF_RANGE",
                "向前的入点超出了素材开头",
                material=material.get("material_name") or material.get("name"),
                source_start=source_start,
            )
        material_duration = _material_duration(seg, material)
        if material_duration > 0 and new_source_start + new_source_duration > material_duration:
            allowed = material_duration - new_source_start
            if allowed < int(round(minimum * speed)):
                raise MileError(
                    "SOURCE_OUT_OF_RANGE",
                    "裁剪超出素材可用长度",
                    material=material.get("material_name") or material.get("name"),
                    material_duration=material_duration,
                )
            new_source_duration = allowed
            new_end = new_start + int(round(allowed / speed))
            warning = "已按素材实际长度收缩出点"
        source["start"] = new_source_start
        source["duration"] = new_source_duration

    target["start"] = new_start
    target["duration"] = new_end - new_start

    if ripple:
        delta = (old_end - new_end) if new_end < old_end else (new_start - old_start)
        if delta:
            for other in track.get("segments") or []:
                if other is seg:
                    continue
                other_target = _target_range(other)
                if other_target["start"] >= old_end:
                    other_target["start"] = max(0, other_target["start"] - delta)

    refresh_duration(data)
    return {
        "ref": _ref(seg),
        "id": seg.get("id"),
        "old": {"start": old_start, "end": old_end},
        "new": {"start": new_start, "end": new_end, "duration": new_end - new_start},
        "source": seg.get("source_timerange"),
        "warning": warning,
    }


def move_segments(
    data: dict,
    seg_ids: list[str],
    *,
    start=None,
    delta=None,
    track=None,
    overlap: str = "reject",
) -> dict:
    """移动一个或多个片段（按组保持相对位置），可跨同类型轨道。"""
    if not seg_ids:
        raise MileError("SEGMENT_REQUIRED", "缺少要移动的片段")
    if overlap not in {"reject", "allow"}:
        raise MileError("BAD_ARGS", "overlap 只能是 reject 或 allow")
    if start is None and delta is None:
        raise MileError("BAD_ARGS", "移动需要提供 start 或 delta")

    found = []
    for seg_id in seg_ids:
        item = _find_segment(data, seg_id)
        if all(item[1] is not entry[1] for entry in found):
            found.append(item)

    types = {_track_type(track_obj) for track_obj, _, _ in found}
    if len(types) > 1:
        raise MileError("BAD_ARGS", "一次移动的片段必须属于同一类轨道")
    seg_type = types.pop()

    base_start = min(_target_range(seg)["start"] for _, seg, _ in found)
    if start is not None:
        new_base = parse_time(start)
    else:
        new_base = base_start + parse_time(delta)
    if new_base < 0:
        raise MileError("BAD_RANGE", "移动后的开始时间不能为负")

    destination = None
    if track is not None:
        destination = _resolve_track(data, track, seg_type=seg_type)
        if not _compatible(_track_type(destination), seg_type):
            raise MileError(
                "TRACK_TYPE_MISMATCH",
                f"{seg_type} 片段不能放入 {_track_type(destination)} 轨道",
            )
    else:
        destination = found[0][0]

    ignore = {str(seg.get("id")) for _, seg, _ in found}
    for _, seg, _ in found:
        offset = _target_range(seg)["start"] - base_start
        seg_start = new_base + offset
        seg_end = seg_start + _target_range(seg)["duration"]
        conflicts = _conflicts(destination, seg_start, seg_end, ignore)
        if conflicts and overlap == "reject":
            raise MileError(
                "OVERLAP",
                "移动后与目标轨道已有片段重叠",
                conflicts=conflicts,
                target_start=seg_start,
                target_end=seg_end,
            )

    for track_obj, seg, _ in found:
        if track_obj is not destination:
            track_obj["segments"].remove(seg)
    for _, seg, _ in found:
        offset = _target_range(seg)["start"] - base_start
        _target_range(seg)["start"] = new_base + offset
        if seg not in (destination.get("segments") or []):
            _insert_sorted(destination, seg)

    refresh_duration(data)
    return {
        "moved": [{"ref": _ref(seg), "id": seg.get("id"), "start": _target_range(seg)["start"]} for _, seg, _ in found],
        "track_index": _tracks(data).index(destination),
        "overlap": overlap,
    }


def shift_segments(data: dict, seg_ids: list[str], delta) -> dict:
    return move_segments(data, seg_ids, delta=delta, overlap="allow")


def split_segment(data: dict, seg_id: str, at) -> dict:
    """在时间轴 ``at`` 处把片段一分为二。"""
    track, seg, index = _find_segment(data, seg_id)
    target = _target_range(seg)
    point = parse_time(at)
    minimum = _min_duration(data)
    if point < target["start"] + minimum or point > target["start"] + target["duration"] - minimum:
        raise MileError(
            "BAD_RANGE",
            "分割点必须位于片段内部（且两段都不短于一帧）",
            segment={"start": target["start"], "end": target["start"] + target["duration"]},
            at=point,
        )

    old_duration = target["duration"]
    first_duration = point - target["start"]
    second_duration = old_duration - first_duration
    speed = float(seg.get("speed") or 1.0)

    source = seg.get("source_timerange")
    if isinstance(source, dict):
        source_start = int(source.get("start") or 0)
        first_source_duration = int(round(first_duration * speed))
        seg["source_timerange"] = {"start": source_start, "duration": first_source_duration}

    second = _replace_segment_refs_for_split(data, seg, first_duration)
    if isinstance(source, dict):
        first_source_duration = int(seg["source_timerange"]["duration"])
        second["source_timerange"] = {
            "start": int(source.get("start") or 0) + first_source_duration,
            "duration": int(round(second_duration * speed)),
        }

    target["duration"] = first_duration
    second_target = second.setdefault("target_timerange", {})
    second_target["start"] = point
    second_target["duration"] = second_duration
    second["speed"] = speed

    # 保证第二段有独立的变速素材，并同步数值
    speed_ref = None
    for ref in second.get("extra_material_refs") or []:
        material = _material(data, ref)
        if material.get("type") == "speed":
            speed_ref = material
            break
    if speed_ref is None:
        speed_ref = _add_material(
            data,
            "speeds",
            {"curve_speed": None, "id": _new_id(), "mode": 0, "speed": speed, "type": "speed"},
        )
        second.setdefault("extra_material_refs", []).append(speed_ref["id"])
    speed_ref["speed"] = speed
    seg_speed_material = _ensure_speed_material(data, seg)
    seg_speed_material["speed"] = speed

    track.setdefault("segments", []).insert(index + 1, second)
    refresh_duration(data)
    return {
        "first": {"ref": _ref(seg), "id": seg.get("id"), "start": target["start"], "duration": first_duration},
        "second": {"ref": _ref(second), "id": second.get("id"), "start": point, "duration": second_duration},
        "track_index": _tracks(data).index(track),
    }


def _apply_text_material(
    material: dict,
    *,
    text: Optional[str] = None,
    font_size: Optional[float] = None,
    color=None,
    bold: Optional[bool] = None,
    italic: Optional[bool] = None,
    underline: Optional[bool] = None,
    align=None,
    alpha: Optional[float] = None,
    letter_spacing: Optional[float] = None,
    line_spacing: Optional[float] = None,
    font_path: Optional[str] = None,
    font_name: Optional[str] = None,
    border=None,
    shadow=None,
    background=None,
) -> dict:
    """把样式写入文字素材；``border``/``shadow``/``background`` 传 ``None`` 表示不动、
    ``False`` 表示移除、dict 表示写入（字段与剪映 5.9 实测 schema 对齐）。
    """
    content = material.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            content = None
    if not isinstance(content, dict):
        content = {"text": str(material.get("base_content") or ""), "styles": [{}]}
    styles = content.get("styles")
    if not isinstance(styles, list) or not styles:
        styles = [{}]
        content["styles"] = styles

    if text is not None:
        content["text"] = str(text)
        primary = styles[0] if isinstance(styles[0], dict) else {}
        primary["range"] = [0, len(str(text))]
        content["styles"] = [primary]
        styles = content["styles"]

    rgb = hex_color = None
    if color is not None:
        rgb, hex_color = parse_color(color)

    for style in styles:
        if not isinstance(style, dict):
            continue
        if font_size is not None:
            style["size"] = float(font_size)
        if color is not None:
            fill = style.setdefault("fill", {})
            if not isinstance(fill, dict):
                fill = style["fill"] = {}
            fill.setdefault("alpha", 1.0)
            fill_content = fill.setdefault("content", {})
            if not isinstance(fill_content, dict):
                fill_content = fill["content"] = {}
            fill_content["render_type"] = "solid"
            solid = fill_content.setdefault("solid", {})
            if not isinstance(solid, dict):
                solid = fill_content["solid"] = {}
            solid["color"] = list(rgb)
            solid.setdefault("alpha", 1.0)
        if bold is not None:
            style["bold"] = bool(bold)
        if italic is not None:
            style["italic"] = bool(italic)
        if underline is not None:
            style["underline"] = bool(underline)
        if font_path is not None or font_name is not None:
            font = style.setdefault("font", {})
            if not isinstance(font, dict):
                font = style["font"] = {}
            if font_path is not None:
                font["path"] = font_path
            if font_name is not None:
                font.setdefault("id", "")
            if "id" not in font:
                font["id"] = ""

    _apply_text_decoration(material, styles, border=border, shadow=shadow, background=background)

    material["content"] = json.dumps(content, ensure_ascii=False)
    material["base_content"] = content.get("text") if text is not None else material.get("base_content", content.get("text"))
    if font_size is not None:
        material["font_size"] = float(font_size)
        material["text_size"] = float(font_size) * 2
    if hex_color is not None:
        material["text_color"] = hex_color
    if align is not None:
        material["alignment"] = int(align)
    if bold is not None:
        material["bold_width"] = 0.084 if bold else 0.0
    if italic is not None:
        material["italic_degree"] = 1 if italic else 0
    if underline is not None:
        material["underline"] = bool(underline)
    if alpha is not None:
        if not 0 <= float(alpha) <= 1:
            raise MileError("BAD_RANGE", "文字不透明度应在 0-1")
        material["global_alpha"] = float(alpha)
    if letter_spacing is not None:
        material["letter_spacing"] = float(letter_spacing) * 0.05
    if line_spacing is not None:
        material["line_spacing"] = 0.02 + float(line_spacing) * 0.05
    if font_path is not None:
        material["font_path"] = font_path
    if font_name is not None:
        material["font_name"] = font_name
    material.setdefault("name", str(content.get("text") or "")[:20])
    return material


def _apply_text_decoration(material: dict, styles: list, *, border=None, shadow=None, background=None) -> None:
    """写入描边/阴影/背景；键名与剪映 5.9 真实草稿一致（style 层为渲染权威，素材层做镜像）。"""
    def _spec(value, label):
        if value is None or value is False:
            return None
        if isinstance(value, str):
            return {"color": value}
        if not isinstance(value, dict):
            raise MileError("BAD_ARGS", f"{label}参数必须是对象或颜色字符串", value=value)
        return value

    def _range(name, value, low, high):
        number = float(value)
        if not low <= number <= high:
            raise MileError("BAD_RANGE", f"{name}应在 {low}-{high} 之间", value=number)
        return number

    border_spec = _spec(border, "描边")
    shadow_spec = _spec(shadow, "阴影")
    background_spec = _spec(background, "背景")

    for style in styles:
        if not isinstance(style, dict):
            continue
        if border_spec is not None:
            rgb, _hex = parse_color(border_spec.get("color", "#000000"))
            width_display = _range("描边宽度", border_spec.get("width", 40.0), 0, 100)
            style["strokes"] = [
                {
                    "content": {
                        "solid": {
                            "alpha": _range("描边不透明度", border_spec.get("alpha", 1.0), 0, 1),
                            "color": list(rgb),
                        }
                    },
                    "width": round(width_display / 100.0 * 0.2, 6),
                }
            ]
        elif border is False:
            style.pop("strokes", None)

        if shadow_spec is not None:
            rgb, _hex = parse_color(shadow_spec.get("color", "#000000"))
            style["shadows"] = [
                {
                    "alpha": _range("阴影不透明度", shadow_spec.get("alpha", 1.0), 0, 1),
                    "distance": _range("阴影距离", shadow_spec.get("distance", 5.0), 0, 100),
                    "content": {"solid": {"color": list(rgb)}},
                    "feather": _range("阴影羽化", shadow_spec.get("feather", 0.055), 0, 1),
                    "angle": _range("阴影角度", shadow_spec.get("angle", -45.0), -180, 180),
                }
            ]
        elif shadow is False:
            style.pop("shadows", None)

    if border_spec is not None:
        stroke = styles[0].get("strokes", [{}])[0]
        solid = stroke.get("content", {}).get("solid", {})
        material["border_width"] = stroke.get("width", 0.0)
        material["border_alpha"] = solid.get("alpha", 1.0)
        _rgb, hex_color = parse_color(border_spec.get("color", "#000000"))
        material["border_color"] = hex_color if hex_color else ""
    elif border is False:
        material["border_width"] = 0.0
        material["border_color"] = ""
        material["border_alpha"] = 1.0

    if shadow_spec is not None:
        entry = styles[0].get("shadows", [{}])[0]
        _rgb, hex_color = parse_color(shadow_spec.get("color", "#000000"))
        material["has_shadow"] = True
        material["shadow_alpha"] = entry.get("alpha", 1.0)
        material["shadow_distance"] = entry.get("distance", 5.0)
        material["shadow_angle"] = entry.get("angle", -45.0)
        material["shadow_color"] = hex_color if hex_color else ""
    elif shadow is False:
        material["has_shadow"] = False
        material["shadow_color"] = ""

    if background_spec is not None:
        style_value = int(background_spec.get("style", 1))
        if style_value not in (1, 2):
            raise MileError("BAD_ARGS", "背景样式只支持 1 或 2", style=style_value)
        _rgb, hex_color = parse_color(background_spec.get("color", "#000000"))
        material["background_style"] = style_value
        material["background_color"] = hex_color if hex_color else ""
        material["background_alpha"] = _range("背景不透明度", background_spec.get("alpha", 1.0), 0, 1)
        material["background_round_radius"] = _range(
            "背景圆角", background_spec.get("round_radius", 0.0), 0, 1
        )
        material["background_height"] = _range("背景高度", background_spec.get("height", 0.14), 0, 1)
        material["background_width"] = _range("背景宽度", background_spec.get("width", 0.14), 0, 1)
        material["background_horizontal_offset"] = (
            _range("背景水平偏移", background_spec.get("horizontal_offset", 0.5), 0, 1) * 2 - 1
        )
        material["background_vertical_offset"] = (
            _range("背景竖直偏移", background_spec.get("vertical_offset", 0.5), 0, 1) * 2 - 1
        )
    elif background is False:
        material["background_style"] = 0
        material["background_color"] = ""
        material["background_alpha"] = 1.0


def set_text(data: dict, seg_ids: list[str], **style) -> dict:
    """修改文本片段内容与样式；所有目标片段必须属于文本轨道。"""
    if not seg_ids:
        raise MileError("SEGMENT_REQUIRED", "缺少要修改的文本片段")
    if all(style.get(key) is None for key in style):
        raise MileError("BAD_ARGS", "没有提供任何文本修改参数")

    changed = []
    shared = set()
    material_uses: dict[str, int] = {}
    for _, seg, _ in _iter_segments(data):
        mid = str(seg.get("material_id"))
        material_uses[mid] = material_uses.get(mid, 0) + 1

    for seg_id in seg_ids:
        track, seg, _ = _find_segment(data, seg_id)
        if _track_type(track) != "text":
            raise MileError("NOT_TEXT", f"片段 {_ref(seg)} 不在文本轨道上")
        material = _material(data, seg.get("material_id"))
        if not material:
            raise MileError("MATERIAL_MISSING", f"文本片段 {_ref(seg)} 的素材不存在")
        if material_uses.get(str(material.get("id")), 0) > 1:
            shared.add(_ref(seg))
        _apply_text_material(material, **style)
        material["name"] = str(material.get("base_content") or material.get("name") or "")[:20]
        changed.append({"ref": _ref(seg), "id": seg.get("id"), "text": json.loads(material["content"]).get("text")})
    return {"changed": changed, "shared_material_refs": sorted(shared)}


def set_volume(
    data: dict,
    seg_ids: list[str],
    volume: float,
    *,
    fade_in=None,
    fade_out=None,
) -> dict:
    """设置片段音量（0-4），可选淡入淡出时长。"""
    try:
        volume = float(volume)
    except Exception as exc:
        raise MileError("BAD_ARGS", f"音量必须是数字: {volume}") from exc
    if not 0 <= volume <= 4:
        raise MileError("BAD_RANGE", "音量应在 0-4 之间（1 为原始音量）")

    fade_in_us = parse_time(fade_in) if fade_in is not None else 0
    fade_out_us = parse_time(fade_out) if fade_out is not None else 0
    if fade_in_us < 0 or fade_out_us < 0:
        raise MileError("BAD_RANGE", "淡入淡出时长不能为负")

    changed = []
    for seg_id in seg_ids:
        track, seg, _ = _find_segment(data, seg_id)
        if _track_type(track) not in {"audio", "video"}:
            raise MileError("NOT_AUDIO", f"片段 {_ref(seg)} 不支持音量调整")
        duration = _target_range(seg)["duration"]
        if fade_in_us + fade_out_us > duration:
            raise MileError("BAD_RANGE", "淡入淡出总时长超过片段时长")
        seg["volume"] = volume
        if volume > 0:
            seg["last_nonzero_volume"] = volume

        if fade_in is not None or fade_out is not None:
            refs = seg.setdefault("extra_material_refs", [])
            existing = None
            for ref in list(refs):
                material = _material(data, ref)
                if material.get("type") == _FADE_TYPE:
                    existing = material
                    break
            if existing is None:
                existing = _add_material(
                    data,
                    "audio_fades",
                    {
                        "id": _new_id(),
                        "fade_in_duration": fade_in_us,
                        "fade_out_duration": fade_out_us,
                        "fade_type": 0,
                        "type": _FADE_TYPE,
                    },
                )
                refs.append(existing["id"])
            else:
                existing["fade_in_duration"] = fade_in_us
                existing["fade_out_duration"] = fade_out_us
            changed.append(
                {
                    "ref": _ref(seg),
                    "volume": volume,
                    "fade_in": fade_in_us,
                    "fade_out": fade_out_us,
                }
            )
        else:
            changed.append({"ref": _ref(seg), "volume": volume})
    return {"changed": changed}


def set_speed(data: dict, seg_ids: list[str], speed: float, *, keep_duration: bool = False) -> dict:
    """设置固定变速，并保持源区间/时间轴时长自洽。"""
    try:
        speed = float(speed)
    except Exception as exc:
        raise MileError("BAD_ARGS", f"速度必须是数字: {speed}") from exc
    if not 0.1 <= speed <= 10:
        raise MileError("BAD_RANGE", "速度应在 0.1-10 之间")

    changed = []
    for seg_id in seg_ids:
        track, seg, _ = _find_segment(data, seg_id)
        if _track_type(track) not in {"audio", "video"} or not isinstance(seg.get("source_timerange"), dict):
            raise MileError("NOT_MEDIA", f"片段 {_ref(seg)} 不支持变速")
        target = _target_range(seg)
        source = seg["source_timerange"]
        source_duration = int(source.get("duration") or 0)
        material = _material(data, seg.get("material_id"))
        material_duration = _material_duration(seg, material)
        warning = None

        if keep_duration:
            new_source_duration = int(round(target["duration"] * speed))
            if material_duration > 0 and int(source.get("start") or 0) + new_source_duration > material_duration:
                new_source_duration = material_duration - int(source.get("start") or 0)
                warning = "素材长度不足，已按可用长度收缩"
            if new_source_duration <= 0:
                raise MileError("SOURCE_OUT_OF_RANGE", "素材长度不足以保持当前时长")
            source["duration"] = new_source_duration
        else:
            new_duration = max(_min_duration(data), int(round(source_duration / speed)))
            target["duration"] = new_duration

        seg["speed"] = speed
        _ensure_speed_material(data, seg)["speed"] = speed
        changed.append(
            {
                "ref": _ref(seg),
                "speed": speed,
                "duration": target["duration"],
                "source_duration": source.get("duration"),
                "warning": warning,
            }
        )

    refresh_duration(data)
    return {"changed": changed}


def set_transform(
    data: dict,
    seg_ids: list[str],
    *,
    x=None,
    y=None,
    scale=None,
    scale_x=None,
    scale_y=None,
    rotation=None,
    alpha=None,
    flip_horizontal=None,
    flip_vertical=None,
) -> dict:
    """调整画面位置/缩放/旋转/不透明度。"""
    changed = []
    for seg_id in seg_ids:
        track, seg, _ = _find_segment(data, seg_id)
        if _track_type(track) not in {"video", "text", "sticker"}:
            raise MileError("NOT_VISUAL", f"片段 {_ref(seg)} 不是可视片段")
        clip = seg.setdefault("clip", copy.deepcopy(_DEFAULT_CLIP))
        if not isinstance(clip, dict):
            clip = seg["clip"] = copy.deepcopy(_DEFAULT_CLIP)
        transform = clip.setdefault("transform", {"x": 0.0, "y": 0.0})
        scale_obj = clip.setdefault("scale", {"x": 1.0, "y": 1.0})
        flip = clip.setdefault("flip", {"horizontal": False, "vertical": False})

        if x is not None:
            transform["x"] = float(x)
        if y is not None:
            transform["y"] = float(y)
        if scale is not None:
            value = float(scale)
            if value <= 0:
                raise MileError("BAD_RANGE", "缩放比例必须大于 0")
            scale_obj["x"] = scale_obj["y"] = value
        if scale_x is not None:
            scale_obj["x"] = float(scale_x)
        if scale_y is not None:
            scale_obj["y"] = float(scale_y)
        if scale_x is not None or scale_y is not None:
            if float(scale_obj.get("x", 1.0)) != float(scale_obj.get("y", 1.0)):
                seg["uniform_scale"] = False
        if rotation is not None:
            clip["rotation"] = float(rotation)
        if alpha is not None:
            if not 0 <= float(alpha) <= 1:
                raise MileError("BAD_RANGE", "不透明度应在 0-1")
            clip["alpha"] = float(alpha)
        if flip_horizontal is not None:
            flip["horizontal"] = bool(flip_horizontal)
        if flip_vertical is not None:
            flip["vertical"] = bool(flip_vertical)
        changed.append(
            {
                "ref": _ref(seg),
                "transform": dict(transform),
                "scale": dict(scale_obj),
                "rotation": clip.get("rotation"),
                "alpha": clip.get("alpha"),
            }
        )
    return {"changed": changed}


def set_visible(data: dict, seg_ids: list[str], visible: bool) -> dict:
    changed = []
    for seg_id in seg_ids:
        _, seg, _ = _find_segment(data, seg_id)
        seg["visible"] = bool(visible)
        changed.append({"ref": _ref(seg), "visible": bool(visible)})
    return {"changed": changed}


def set_canvas(data: dict, width: int, height: int) -> dict:
    try:
        width, height = int(width), int(height)
    except Exception as exc:
        raise MileError("BAD_ARGS", "画布尺寸必须是整数") from exc
    if width < 16 or height < 16:
        raise MileError("BAD_RANGE", "画布尺寸过小")
    old = data.get("canvas_config") or {}
    data["canvas_config"] = dict(old)
    data["canvas_config"]["width"] = width
    data["canvas_config"]["height"] = height
    data["canvas_config"].setdefault("ratio", "original")
    return {"old": old, "new": data["canvas_config"]}


def resolve_transition_type(name):
    from . import ensure_vendor_path

    ensure_vendor_path()
    try:
        from pyJianYingDraft.metadata import TransitionType
    except Exception as exc:
        raise MileError("VENDOR_MISSING", "无法加载转场元数据（pyJianYingDraft）", error=str(exc)) from exc

    if isinstance(name, TransitionType):
        return name
    text = str(name or "").strip()
    if not text:
        text = "叠化"
    lowered = text.lower()
    lowered = _TRANSITION_ALIASES.get(lowered, text)

    for member in TransitionType:
        meta = member.value
        if lowered in {member.name, meta.name} or str(member.name).lower() == lowered.lower():
            return member
    for member in TransitionType:
        if lowered.lower() in member.name.lower():
            return member
    suggestions = [member.name for member in list(TransitionType)[:30]]
    raise MileError("TRANSITION_NOT_FOUND", f"未找到转场: {name}", suggestions=suggestions)


def list_transition_types(query: Optional[str] = None, limit: int = 40) -> list[str]:
    from . import ensure_vendor_path

    ensure_vendor_path()
    try:
        from pyJianYingDraft.metadata import TransitionType
    except Exception as exc:
        raise MileError("VENDOR_MISSING", "无法加载转场元数据", error=str(exc)) from exc
    names = [member.name for member in TransitionType]
    if query:
        needle = str(query).lower()
        names = [name for name in names if needle in name.lower()]
    return names[: int(limit)]


def _gc_transition(data: dict, material_id: str) -> None:
    for _, seg, _ in _iter_segments(data):
        if material_id in (seg.get("extra_material_refs") or []):
            return
    list_name, items, index, _ = _material_location(data, material_id)
    if items and index is not None:
        items.pop(index)


def add_transition(data: dict, seg_id: str, type_name: str = "叠化", duration=None) -> dict:
    """在片段与后一个同轨片段之间添加转场。

    转场挂在**前一个**片段上（与剪映及 pyJianYingDraft 的约定一致），
    因此 ``seg_id`` 应指向交界处的第一个片段。
    """
    track, seg, index = _find_segment(data, seg_id)
    if _track_type(track) != "video":
        raise MileError("NOT_VIDEO", "转场只能添加在视频轨道上")
    segs = track.get("segments") or []
    if index >= len(segs) - 1:
        raise MileError(
            "NOT_ADJACENT",
            "该片段后面没有可用片段；请把 target 指向交界处的前一个片段",
        )

    following = segs[index + 1]
    seg_end = _segment_end(seg)
    next_start = _target_range(following)["start"]
    fps = float(data.get("fps") or 30)
    tolerance = max(50_000, int(2 * US_PER_SEC / max(fps, 1)))
    if abs(seg_end - next_start) > tolerance:
        raise MileError(
            "NOT_ADJACENT",
            "两个片段没有首尾相接，无法添加转场",
            previous_end=seg_end,
            current_start=next_start,
        )

    transition_type = resolve_transition_type(type_name)
    meta = transition_type.value
    max_duration = min(_target_range(seg)["duration"], _target_range(following)["duration"])
    requested = parse_time(duration) if duration is not None else int(meta.default_duration)
    warning = None
    if requested > max_duration:
        requested = max_duration
        warning = "转场时长超过相邻片段长度，已收缩"
    if requested <= 0:
        raise MileError("BAD_RANGE", "转场时长必须大于 0")

    refs = seg.setdefault("extra_material_refs", [])
    for ref in list(refs):
        material = _material(data, ref)
        if material.get("type") == "transition":
            refs.remove(ref)
            _gc_transition(data, ref)

    material = {
        "category_id": "",
        "category_name": "",
        "duration": requested,
        "effect_id": str(meta.effect_id),
        "id": _new_id(),
        "is_overlap": bool(meta.is_overlap),
        "name": meta.name,
        "platform": "all",
        "resource_id": str(meta.resource_id),
        "type": "transition",
    }
    _add_material(data, "transitions", material)
    refs.append(material["id"])
    return {
        "ref": _ref(seg),
        "next_ref": _ref(following),
        "transition": meta.name,
        "duration": requested,
        "material_id": material["id"],
        "warning": warning,
    }


def remove_transition(data: dict, seg_id: str) -> dict:
    """移除挂在片段上、作用于它与后一个片段交界处的转场。"""
    _, seg, _ = _find_segment(data, seg_id)
    refs = seg.get("extra_material_refs") or []
    removed = []
    for ref in list(refs):
        material = _material(data, ref)
        if material.get("type") == "transition":
            refs.remove(ref)
            removed.append(material.get("name"))
            _gc_transition(data, ref)
    if not removed:
        raise MileError("NO_TRANSITION", "该片段没有转场")
    return {"ref": _ref(seg), "removed": removed}


def _render_index(data: dict, seg_type: str) -> int:
    base = {"video": 0, "audio": 0, "text": 15000, "sticker": 14000, "effect": 10000, "filter": 11000}.get(seg_type, 0)
    maximum = base
    for _, seg, _ in _iter_segments(data):
        try:
            value = int(seg.get("render_index") or 0)
        except Exception:
            continue
        if seg_type == "text" and value >= 14000:
            maximum = max(maximum, value + 1)
        elif seg_type in {"video", "audio"} and 0 <= value < 10000:
            maximum = max(maximum, value)
    return maximum if seg_type in {"video", "audio"} else maximum + 1


def _new_segment_shell(seg_type: str, material_id: str, start: int, duration: int, render_index: int) -> dict:
    seg = {
        "cartoon": False,
        "clip": copy.deepcopy(_DEFAULT_CLIP) if seg_type in {"video", "text", "sticker"} else None,
        "common_keyframes": [],
        "enable_adjust": True,
        "enable_color_correct_adjust": False,
        "enable_color_curves": True,
        "enable_color_match_adjust": False,
        "enable_color_wheels": True,
        "enable_lut": True,
        "enable_smart_color_adjust": False,
        "extra_material_refs": [],
        "group_id": "",
        "hdr_settings": None,
        "id": _new_id(),
        "intensifies_audio": False,
        "is_placeholder": False,
        "is_tone_modify": False,
        "keyframe_refs": [],
        "last_nonzero_volume": 1.0,
        "material_id": material_id,
        "render_index": render_index,
        "responsive_layout": {"enable": False, "horizontal_pos_layout": 0, "size_layout": 0, "target_follow": "", "vertical_pos_layout": 0},
        "reverse": False,
        "speed": 1.0,
        "target_timerange": {"duration": int(duration), "start": int(start)},
        "track_attribute": 0,
        "track_render_index": 0,
        "uniform_scale": True,
        "visible": True,
        "volume": 1.0,
    }
    if seg_type in {"video", "audio"}:
        seg["source_timerange"] = {"duration": int(duration), "start": 0}
    return seg


def _build_text_material(text: str, *, font_size: float, color, align: int, bold: bool, italic: bool, underline: bool, alpha: float) -> dict:
    rgb, hex_color = parse_color(color)
    content = {
        "text": text,
        "styles": [
            {
                "fill": {
                    "alpha": 1.0,
                    "content": {"render_type": "solid", "solid": {"alpha": 1.0, "color": list(rgb)}},
                },
                "range": [0, len(text)],
                "size": float(font_size),
                "bold": bool(bold),
                "italic": bool(italic),
                "underline": bool(underline),
                "strokes": [],
            }
        ],
    }
    return {
        "add_type": 0,
        "alignment": int(align),
        "background_alpha": 1.0,
        "background_color": "",
        "background_height": 0.1,
        "background_horizontal_offset": 0.0,
        "background_round_radius": 0.0,
        "background_style": 0,
        "background_vertical_offset": 0.0,
        "background_width": 0.1,
        "base_content": text,
        "bold_width": 0.084 if bold else 0.0,
        "border_alpha": 1.0,
        "border_color": "",
        "border_width": 0.0,
        "check_flag": 7,
        "combo_info": {"text_templates": []},
        "content": json.dumps(content, ensure_ascii=False),
        "fixed_height": -1.0,
        "fixed_width": -1.0,
        "font_category_id": "",
        "font_category_name": "",
        "font_id": "",
        "font_name": "",
        "font_path": "",
        "font_size": float(font_size),
        "font_source_platform": 0,
        "force_apply_line_max_width": False,
        "global_alpha": float(alpha),
        "has_shadow": False,
        "id": _new_id(),
        "initial_scale": 1.0,
        "inner_padding": -1.0,
        "is_rich_text": False,
        "italic_degree": 1 if italic else 0,
        "language": "",
        "layer_weight": 1,
        "letter_spacing": 0.0,
        "line_feed": 1,
        "line_max_width": 0.82,
        "line_spacing": 0.02,
        "name": text[:20],
        "source_from": "",
        "style_name": "",
        "sub_type": 0,
        "text_alpha": 1.0,
        "text_color": hex_color,
        "text_size": float(font_size) * 2,
        "type": "text",
        "typesetting": 0,
        "underline": bool(underline),
        "use_effect_default_color": False,
        "words": [],
    }


def add_text(
    data: dict,
    *,
    text: str,
    start,
    duration,
    track=None,
    font_size: float = 8.0,
    color="#ffffff",
    align: int = 1,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    alpha: float = 1.0,
    transform_y: Optional[float] = None,
    x: Optional[float] = None,
    scale=None,
) -> dict:
    """在指定（或新建的）文本轨道上添加一条文字。"""
    text = str(text or "")
    if not text:
        raise MileError("BAD_ARGS", "文字内容不能为空")
    start_us = parse_time(start)
    duration_us = parse_time(duration)
    if start_us < 0 or duration_us < _min_duration(data):
        raise MileError("BAD_RANGE", "文字的开始时间/时长不合法")

    target_track = _resolve_track(data, track, seg_type="text", create=True)
    material = _build_text_material(
        text,
        font_size=font_size,
        color=color,
        align=align,
        bold=bold,
        italic=italic,
        underline=underline,
        alpha=alpha,
    )
    _add_material(data, "texts", material)
    seg = _new_segment_shell("text", material["id"], start_us, duration_us, _render_index(data, "text"))
    if transform_y is not None:
        seg["clip"]["transform"]["y"] = float(transform_y)
    if x is not None:
        seg["clip"]["transform"]["x"] = float(x)
    if scale is not None:
        seg["clip"]["scale"]["x"] = seg["clip"]["scale"]["y"] = float(scale)
    _insert_sorted(target_track, seg)
    refresh_duration(data)
    return {
        "ref": _ref(seg),
        "id": seg.get("id"),
        "material_id": material["id"],
        "track_index": _tracks(data).index(target_track),
        "start": start_us,
        "duration": duration_us,
    }


def _vendor_modules():
    from . import ensure_vendor_path

    ensure_vendor_path()
    try:
        from pyJianYingDraft import metadata as meta_module
        from pyJianYingDraft.video_segment import Filter, Mask, VideoEffect
        from pyJianYingDraft.animation import VideoAnimation
    except Exception as exc:  # pragma: no cover - 依赖环境
        raise MileError("VENDOR_MISSING", "无法加载 pyJianYingDraft 素材类", error=str(exc)) from exc
    return meta_module, Filter, Mask, VideoEffect, VideoAnimation


def _resolve_enum(enum_cls, name, code: str, label: str):
    if isinstance(name, enum_cls):
        return name
    text = str(name or "").strip()
    if not text:
        raise MileError(code, f"未指定{label}")
    for member in enum_cls:
        meta = member.value
        candidates = {str(member.name), str(getattr(meta, "name", "")), str(getattr(meta, "title", ""))}
        if text in candidates:
            return member
    lowered = text.lower()
    for member in enum_cls:
        if lowered in str(member.name).lower():
            return member
    raise MileError(code, f"未找到{label}: {name}", query=text, suggestions=[m.name for m in list(enum_cls)[:30]])


def _material_type(data: dict, material_id: str) -> str:
    return str(_material(data, material_id).get("type") or "")


def _detach_materials(data: dict, seg: dict, type_names: set[str]) -> list[dict]:
    """从片段上摘除指定类型的挂载素材，返回被摘除的素材（仍留在 materials 中）。"""
    removed = []
    kept = []
    for ref in seg.get("extra_material_refs") or []:
        if _material_type(data, ref) in type_names:
            removed.append(_material(data, ref))
        else:
            kept.append(ref)
    seg["extra_material_refs"] = kept
    return removed


def _gc_unused_material(data: dict, material_id: str) -> None:
    for _, seg, _ in _iter_segments(data):
        if material_id in (seg.get("extra_material_refs") or []):
            return
    list_name, items, index, _ = _material_location(data, material_id)
    if items and index is not None:
        items.pop(index)


def _require_targets(seg_ids) -> list[str]:
    ids = [str(item) for item in (seg_ids or []) if str(item).strip()]
    if not ids:
        raise MileError("TARGET_REQUIRED", "该操作至少需要一个目标片段")
    return ids


def _video_only(data: dict, seg_id: str, op_label: str) -> tuple[dict, dict]:
    track, seg, _ = _find_segment(data, seg_id)
    if _track_type(track) != "video":
        raise MileError("NOT_VIDEO", f"{op_label}只能作用于视频片段", ref=seg_id)
    return track, seg


def set_filter(data: dict, seg_ids, name, *, intensity=100.0, keep_old: bool = False) -> dict:
    """设置片段滤镜；默认替换已有滤镜。强度按剪映约定传入 0-100。"""
    meta_module, Filter, _Mask, _VideoEffect, _Animation = _vendor_modules()
    member = _resolve_enum(meta_module.FilterType, name, "FILTER_NOT_FOUND", "滤镜")
    try:
        intensity_value = float(intensity)
    except (TypeError, ValueError) as exc:
        raise MileError("BAD_RANGE", f"滤镜强度必须是数字: {intensity}") from exc
    if not 0 <= intensity_value <= 100:
        raise MileError("BAD_RANGE", "滤镜强度需在 0-100 之间", value=intensity_value)

    results = []
    for seg_id in _require_targets(seg_ids):
        _track, seg = _video_only(data, seg_id, "滤镜")
        removed = [] if keep_old else _detach_materials(data, seg, {"filter"})
        material = Filter(member.value, intensity_value / 100.0).export_json()
        _add_material(data, "effects", material)
        seg.setdefault("extra_material_refs", []).append(material["id"])
        for item in removed:
            _gc_unused_material(data, str(item.get("id") or ""))
        results.append({"ref": seg_id, "filter": member.name, "intensity": intensity_value, "material_id": material["id"]})
    return {"changed": results}


def add_effect(data: dict, seg_ids, name, *, params=None, keep_old: bool = True) -> dict:
    """为片段添加作用于整段的视频/人物特效，参数取值 0-100。"""
    meta_module, _Filter, _Mask, VideoEffect, _Animation = _vendor_modules()
    pools = [meta_module.VideoSceneEffectType, meta_module.VideoCharacterEffectType]
    member = None
    for pool in pools:
        try:
            member = _resolve_enum(pool, name, "EFFECT_NOT_FOUND", "特效")
            break
        except MileError:
            continue
    if member is None:
        raise MileError(
            "EFFECT_NOT_FOUND",
            f"未找到特效: {name}",
            suggestions=[item.name for item in list(pools[0])[:30]],
        )

    results = []
    for seg_id in _require_targets(seg_ids):
        _track, seg = _video_only(data, seg_id, "特效")
        removed = [] if keep_old else _detach_materials(data, seg, {"video_effect", "face_effect"})
        material = VideoEffect(member, params).export_json()
        _add_material(data, "video_effects", material)
        seg.setdefault("extra_material_refs", []).append(material["id"])
        for item in removed:
            _gc_unused_material(data, str(item.get("id") or ""))
        results.append({"ref": seg_id, "effect": member.name, "material_id": material["id"]})
    return {"changed": results}


def set_mask(
    data: dict,
    seg_ids,
    name,
    *,
    center_x: float = 0.0,
    center_y: float = 0.0,
    size: float = 0.5,
    rotation: float = 0.0,
    feather: float = 0.0,
    invert: bool = False,
    rect_width: Optional[float] = None,
    round_corner: float = 0.0,
) -> dict:
    """设置片段蒙版；默认替换已有蒙版。坐标以素材像素为单位，size 为占素材高度比例。"""
    meta_module, _Filter, Mask, _VideoEffect, _Animation = _vendor_modules()
    member = _resolve_enum(meta_module.MaskType, name, "MASK_NOT_FOUND", "蒙版")

    results = []
    for seg_id in _require_targets(seg_ids):
        track, seg = _video_only(data, seg_id, "蒙版")
        material = _material(data, str(seg.get("material_id") or ""))
        width = int(material.get("width") or 0)
        height = int(material.get("height") or 0)
        if width <= 0 or height <= 0:
            canvas = data.get("canvas_config") or {}
            width = int(canvas.get("width") or 1920)
            height = int(canvas.get("height") or 1080)

        removed = _detach_materials(data, seg, {"mask"})
        aspect = float(getattr(member.value, "default_aspect_ratio", 1.0) or 1.0)
        mask_width = rect_width if rect_width is not None else size * height * aspect / width
        mask = Mask(
            member.value,
            float(center_x) / (width / 2),
            float(center_y) / (height / 2),
            w=float(mask_width),
            h=float(size),
            ratio=aspect,
            rot=float(rotation),
            inv=bool(invert),
            feather=float(feather) / 100.0,
            round_corner=float(round_corner) / 100.0,
        ).export_json()
        _add_material(data, "masks", mask)
        seg.setdefault("extra_material_refs", []).append(mask["id"])
        for item in removed:
            _gc_unused_material(data, str(item.get("id") or ""))
        results.append({"ref": seg_id, "mask": member.name, "material_id": mask["id"], "track": track.get("name")})
    return {"changed": results}


_ANIMATION_KINDS = {"in": "intro", "out": "outro", "group": "group", "intro": "intro", "outro": "outro"}
_ANIMATION_EXPORT_TYPE = {"intro": "in", "outro": "out", "group": "group"}


def _attach_animation(data: dict, seg: dict, animation: dict, export_type: str) -> dict:
    """把动画条目挂到片段的 sticker_animation holder 上；同类型动画默认替换。"""
    holder = None
    for ref in seg.get("extra_material_refs") or []:
        candidate = _material(data, ref)
        if str(candidate.get("type") or "") == "sticker_animation":
            holder = candidate
            break
    if holder is None:
        holder = {
            "id": _new_id(),
            "type": "sticker_animation",
            "multi_language_current": "none",
            "animations": [],
        }
        _add_material(data, "material_animations", holder)
        seg.setdefault("extra_material_refs", []).append(holder["id"])
    holder_animations = holder.setdefault("animations", [])
    holder_animations[:] = [item for item in holder_animations if str(item.get("type")) != export_type]
    holder_animations.append(animation)
    return holder


def _vendor_text_animation():
    from . import ensure_vendor_path

    ensure_vendor_path()
    try:
        from pyJianYingDraft import metadata as meta_module
        from pyJianYingDraft.animation import Text_animation
    except Exception as exc:  # pragma: no cover - 依赖环境
        raise MileError("VENDOR_MISSING", "无法加载 pyJianYingDraft 文字动画类", error=str(exc)) from exc
    return meta_module, Text_animation


def _text_only(data: dict, seg_id: str, op_label: str) -> tuple[dict, dict]:
    track, seg, _ = _find_segment(data, seg_id)
    if _track_type(track) != "text":
        raise MileError("NOT_TEXT", f"{op_label}只能作用于文字片段", ref=seg_id)
    return track, seg


def add_animation(data: dict, seg_ids, name, *, kind: str = "in", duration=None) -> dict:
    """添加视频入场/出场/组合动画；同类动画默认替换。"""
    meta_module, _Filter, _Mask, _VideoEffect, VideoAnimation = _vendor_modules()
    kind_key = _ANIMATION_KINDS.get(str(kind or "in").strip().lower())
    if kind_key is None:
        raise MileError("BAD_ARGS", f"不支持的动画类型: {kind}", supported=sorted(_ANIMATION_KINDS))

    enum_cls = {
        "intro": meta_module.IntroType,
        "outro": meta_module.OutroType,
        "group": meta_module.GroupAnimationType,
    }[kind_key]
    member = _resolve_enum(enum_cls, name, "ANIMATION_NOT_FOUND", "动画")

    results = []
    for seg_id in _require_targets(seg_ids):
        _track, seg = _video_only(data, seg_id, "动画")
        target = _target_range(seg)
        seg_duration = int(target["duration"])
        anim_meta = member.value
        if kind_key == "outro":
            anim_duration = parse_time(duration) if duration is not None else int(getattr(anim_meta, "duration", 0) or 0)
            start = seg_duration - anim_duration
        elif kind_key == "group":
            anim_duration = parse_time(duration) if duration is not None else seg_duration
            start = 0
        else:
            anim_duration = parse_time(duration) if duration is not None else int(getattr(anim_meta, "duration", 0) or 0)
            start = 0
        if anim_duration <= 0:
            raise MileError("BAD_RANGE", "动画时长不合法", duration=anim_duration)
        if seg_duration and anim_duration > seg_duration:
            raise MileError("BAD_RANGE", "动画时长不能超过片段时长", duration=anim_duration, segment=seg_duration)

        animation = VideoAnimation(member, start, anim_duration).export_json()
        holder = _attach_animation(data, seg, animation, _ANIMATION_EXPORT_TYPE[kind_key])
        results.append(
            {
                "ref": seg_id,
                "animation": member.name,
                "kind": kind_key,
                "start": start,
                "duration": anim_duration,
                "material_id": holder["id"],
            }
        )
    return {"changed": results}


_TEXT_ANIMATION_KINDS = {"in": "in", "intro": "in", "out": "out", "outro": "out", "loop": "loop"}


def add_text_animation(data: dict, seg_ids, name, *, kind: str = "in", duration=None) -> dict:
    """给文字片段添加入场/出场/循环动画（字幕动画）；同类动画默认替换。"""
    meta_module, TextAnimation = _vendor_text_animation()
    kind_key = _TEXT_ANIMATION_KINDS.get(str(kind or "in").strip().lower())
    if kind_key is None:
        raise MileError("BAD_ARGS", f"不支持的文字动画类型: {kind}", supported=sorted(_TEXT_ANIMATION_KINDS))

    enum_cls = {
        "in": meta_module.TextIntro,
        "out": meta_module.TextOutro,
        "loop": meta_module.TextLoopAnim,
    }[kind_key]
    member = _resolve_enum(enum_cls, name, "TEXT_ANIMATION_NOT_FOUND", "文字动画")

    results = []
    for seg_id in _require_targets(seg_ids):
        track, seg = _text_only(data, seg_id, "文字动画")
        target = _target_range(seg)
        seg_duration = int(target["duration"])
        anim_meta = member.value
        if kind_key == "loop":
            anim_duration = parse_time(duration) if duration is not None else seg_duration
            start = 0
        elif kind_key == "out":
            anim_duration = parse_time(duration) if duration is not None else int(getattr(anim_meta, "duration", 0) or 0)
            start = seg_duration - anim_duration
        else:
            anim_duration = parse_time(duration) if duration is not None else int(getattr(anim_meta, "duration", 0) or 0)
            start = 0
        if anim_duration <= 0:
            raise MileError("BAD_RANGE", "文字动画时长不合法", duration=anim_duration)
        if seg_duration and anim_duration > seg_duration:
            raise MileError("BAD_RANGE", "文字动画时长不能超过片段时长", duration=anim_duration, segment=seg_duration)
        if start < 0:
            raise MileError("BAD_RANGE", "出场动画超出片段起点", start=start)

        animation = TextAnimation(member, start, anim_duration).export_json()
        holder = _attach_animation(data, seg, animation, kind_key)
        results.append(
            {
                "ref": seg_id,
                "animation": member.name,
                "kind": kind_key,
                "start": start,
                "duration": anim_duration,
                "material_id": holder["id"],
                "track": track.get("name"),
            }
        )
    return {"changed": results}


def remove_material(data: dict, seg_ids, type_name: str) -> dict:
    """摘除片段上的滤镜/特效/蒙版/动画素材（不删除仍在别处引用的素材）。"""
    aliases = {
        "filter": {"filter"},
        "effect": {"video_effect", "face_effect"},
        "mask": {"mask"},
        "animation": {"sticker_animation", "material_animation"},
        "滤镜": {"filter"},
        "特效": {"video_effect", "face_effect"},
        "蒙版": {"mask"},
        "动画": {"sticker_animation", "material_animation"},
        "转场": {"transition"},
        "transition": {"transition"},
    }
    types = aliases.get(str(type_name or "").strip().lower())
    if types is None:
        raise MileError("BAD_ARGS", f"不支持摘除的素材类型: {type_name}", supported=sorted(aliases))

    results = []
    for seg_id in _require_targets(seg_ids):
        _track, seg, _ = _find_segment(data, seg_id)
        removed = _detach_materials(data, seg, types)
        for item in removed:
            _gc_unused_material(data, str(item.get("id") or ""))
        results.append({"ref": seg_id, "removed": [str(item.get("id") or "") for item in removed]})
    return {"changed": results}


def _vendor_keyframe():
    from . import ensure_vendor_path

    ensure_vendor_path()
    try:
        from pyJianYingDraft.keyframe import Keyframe, KeyframeList, KeyframeProperty
    except Exception as exc:  # pragma: no cover - 依赖环境
        raise MileError("VENDOR_MISSING", "无法加载 pyJianYingDraft 关键帧类", error=str(exc)) from exc
    return Keyframe, KeyframeList, KeyframeProperty


# normalized 名称 -> (KeyframeProperty 成员名, 允许的轨道类型, uniform_scale 处理)
_KEYFRAME_PROPERTIES = {
    "position_x": ("position_x", {"video", "text"}, None),
    "x": ("position_x", {"video", "text"}, None),
    "position_y": ("position_y", {"video", "text"}, None),
    "y": ("position_y", {"video", "text"}, None),
    "rotation": ("rotation", {"video", "text"}, None),
    "scale": ("scale_x", {"video", "text"}, True),
    "uniform_scale": ("scale_x", {"video", "text"}, True),
    "scale_x": ("scale_x", {"video", "text"}, False),
    "scale_y": ("scale_y", {"video", "text"}, False),
    "alpha": ("alpha", {"video"}, None),
    "volume": ("volume", {"audio", "video"}, None),
}


def _normalize_keyframe_frames(frames) -> list[dict]:
    items = []
    for entry in frames or []:
        if isinstance(entry, dict):
            at, value = entry.get("at"), entry.get("value")
        elif isinstance(entry, (list, tuple)) and len(entry) == 2:
            at, value = entry
        else:
            raise MileError("BAD_ARGS", "关键帧条目必须是 {at, value} 或 (at, value)", entry=entry)
        if at is None or value is None:
            raise MileError("BAD_ARGS", "关键帧条目缺少 at 或 value", entry=entry)
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise MileError("BAD_RANGE", f"关键帧值必须是数字: {value}") from exc
        items.append({"at": at, "at_us": parse_time(at), "value": numeric})
    if not items:
        raise MileError("BAD_ARGS", "至少需要一个关键帧")
    return items


def add_keyframes(data: dict, seg_ids, property_name: str, frames) -> dict:
    """为片段属性添加关键帧；同一属性自动合并并按时间排序。

    ``frames`` 为 ``[{"at": 秒, "value": 数值}]`` 或 ``[(at, value)]``；``at`` 相对片段起点。
    位置单位与剪映显示一致（半个画布宽/高），缩放 1.0 为原始大小。
    """
    Keyframe, KeyframeList, KeyframeProperty = _vendor_keyframe()
    spec = _KEYFRAME_PROPERTIES.get(str(property_name or "").strip().lower())
    if spec is None:
        raise MileError(
            "BAD_ARGS",
            f"不支持的关键帧属性: {property_name}",
            supported=sorted(_KEYFRAME_PROPERTIES),
        )
    prop_name, allowed_tracks, uniform_on = spec
    normalized = _normalize_keyframe_frames(frames)

    results = []
    for seg_id in _require_targets(seg_ids):
        track, seg, _ = _find_segment(data, seg_id)
        track_type = _track_type(track)
        if track_type not in allowed_tracks:
            raise MileError(
                "BAD_TARGET",
                f"关键帧属性 {property_name} 不能作用于 {track_type or '未知'} 轨道片段",
                ref=seg_id,
                allowed=sorted(allowed_tracks),
            )
        duration = int(_target_range(seg)["duration"])
        prop_enum = getattr(KeyframeProperty, prop_name)
        lists = seg.setdefault("common_keyframes", [])
        if not isinstance(lists, list):
            lists = seg["common_keyframes"] = []
        target_list = None
        for item in lists:
            if str(item.get("property_type") or "") == prop_enum.value:
                target_list = item
                break
        if target_list is None:
            target_list = KeyframeList(prop_enum).export_json()
            lists.append(target_list)

        added = []
        warnings = []
        for frame in normalized:
            at = frame["at_us"]
            if at < 0:
                raise MileError("BAD_RANGE", "关键帧时间不能为负", at=frame["at"], segment=seg_id)
            if duration and at > duration:
                warnings.append({"ref": seg_id, "warning": f"关键帧 {frame['at']} 超出片段时长 {duration / 1_000_000:g}s，已收敛到结尾"})
                at = duration
            entry = Keyframe(at, frame["value"]).export_json()
            target_list.setdefault("keyframe_list", []).append(entry)
            added.append(entry)
        target_list["keyframe_list"] = sorted(
            target_list.get("keyframe_list") or [],
            key=lambda item: int(item.get("time_offset") or 0),
        )
        if uniform_on is not None:
            current_scale = ((seg.get("clip") or {}).get("scale") or {})
            seg["uniform_scale"] = {
                "on": bool(uniform_on),
                "value": float(current_scale.get("x", 1.0) or 1.0),
            }
        record = {
            "ref": seg_id,
            "property": prop_enum.value,
            "added": len(added),
            "total": len(target_list["keyframe_list"]),
        }
        if warnings:
            record["warning"] = warnings[0]["warning"]
        results.append(record)
    return {"changed": results}


def remove_keyframes(data: dict, seg_ids, property_name: Optional[str] = None) -> dict:
    """移除片段上的关键帧；不传属性名时清空全部关键帧列表。"""
    _Keyframe, _KeyframeList, KeyframeProperty = _vendor_keyframe()
    prop_value = None
    if property_name is not None:
        spec = _KEYFRAME_PROPERTIES.get(str(property_name).strip().lower())
        if spec is None:
            raise MileError("BAD_ARGS", f"不支持的关键帧属性: {property_name}", supported=sorted(_KEYFRAME_PROPERTIES))
        prop_value = getattr(KeyframeProperty, spec[0]).value

    results = []
    for seg_id in _require_targets(seg_ids):
        _track, seg, _ = _find_segment(data, seg_id)
        lists = seg.get("common_keyframes") or []
        if prop_value is None:
            kept = []
        else:
            kept = [item for item in lists if str(item.get("property_type") or "") != prop_value]
        removed_count = len(lists) - len(kept)
        seg["common_keyframes"] = kept
        results.append({"ref": seg_id, "removed_lists": removed_count})
    return {"changed": results}


def clone_text_segment(
    data: dict,
    template_id: str,
    *,
    text: Optional[str] = None,
    start=None,
    duration=None,
    track=None,
) -> dict:
    """以已有文字片段为模板克隆一条新字幕/花字，保留字体与动画。"""
    source_track, template, _ = _find_segment(data, template_id)
    if _track_type(source_track) != "text":
        raise MileError("NOT_TEXT", "模板片段必须来自文本轨道")
    template_material = _material(data, template.get("material_id"))
    if not template_material:
        raise MileError("MATERIAL_MISSING", "模板片段的文字素材不存在")

    template_target = _target_range(template)
    start_us = parse_time(start) if start is not None else template_target["start"]
    duration_us = parse_time(duration) if duration is not None else template_target["duration"]
    if duration_us < _min_duration(data) or start_us < 0:
        raise MileError("BAD_RANGE", "新文字的开始时间/时长不合法")

    target_track = _resolve_track(data, track, seg_type="text", create=True)
    material = copy.deepcopy(template_material)
    material["id"] = _new_id()
    _add_material(data, "texts", material)
    if text is not None:
        _apply_text_material(material, text=str(text))

    seg = copy.deepcopy(template)
    seg["id"] = _new_id()
    seg["material_id"] = material["id"]
    seg["target_timerange"] = {"start": start_us, "duration": duration_us}
    seg["keyframe_refs"] = []
    _insert_sorted(target_track, seg)
    refresh_duration(data)
    return {
        "ref": _ref(seg),
        "id": seg.get("id"),
        "material_id": material["id"],
        "from": _ref(template),
        "track_index": _tracks(data).index(target_track),
        "start": start_us,
        "duration": duration_us,
    }


def probe_duration(path: str | os.PathLike) -> Optional[int]:
    """读取媒体时长（微秒）；ffprobe 优先，缺失时回退 ffmpeg。"""
    try:
        from utils.media_probe import probe_media_duration

        duration = probe_media_duration(path)
    except Exception:
        return None
    return int(round(duration * US_PER_SEC)) if duration else None


def add_audio(
    data: dict,
    path: str | os.PathLike,
    *,
    start=0,
    duration=None,
    name: Optional[str] = None,
    volume: float = 1.0,
    track=None,
    loop: bool = False,
) -> dict:
    """把本地音频加入草稿；``loop=True`` 时会重复铺满指定时长。"""
    file_path = Path(path).expanduser()
    if not file_path.exists() or not file_path.is_file():
        raise MileError("MEDIA_NOT_FOUND", f"音频文件不存在: {file_path}")
    source_duration = probe_duration(file_path)
    total_duration = parse_time(duration) if duration is not None else source_duration
    if source_duration is None:
        if total_duration is None:
            raise MileError(
                "FFPROBE_REQUIRED",
                "无法读取音频时长，请安装 ffmpeg/ffprobe，或提供 duration 参数",
            )
        source_duration = total_duration
    start_us = parse_time(start)
    if start_us < 0 or total_duration <= 0:
        raise MileError("BAD_RANGE", "音频开始时间/时长不合法")
    if not 0 <= float(volume) <= 4:
        raise MileError("BAD_RANGE", "音量应在 0-4 之间")

    target_track = _resolve_track(data, track, seg_type="audio", create=True)
    base_name = str(name or file_path.stem)
    segments = []
    cursor = start_us
    remaining = total_duration
    index = 1
    while remaining > 0:
        chunk = min(source_duration, remaining)
        material = {
            "app_id": 0,
            "category_id": "",
            "category_name": "local",
            "check_flag": 3,
            "copyright_limit_type": "none",
            "duration": source_duration,
            "effect_id": "",
            "formula_id": "",
            "id": _new_id(),
            "local_material_id": "",
            "music_id": "",
            "name": f"{base_name}" if index == 1 else f"{base_name}·{index}",
            "path": str(file_path.resolve()),
            "source_platform": 0,
            "type": "extract_music",
            "wave_points": [],
        }
        material["local_material_id"] = material["id"]
        material["music_id"] = material["id"]
        _add_material(data, "audios", material)

        seg = _new_segment_shell("audio", material["id"], cursor, chunk, _render_index(data, "audio"))
        seg["volume"] = float(volume)
        seg["source_timerange"] = {"duration": int(chunk), "start": 0}
        speed_material = _add_material(
            data,
            "speeds",
            {"curve_speed": None, "id": _new_id(), "mode": 0, "speed": 1.0, "type": "speed"},
        )
        seg["extra_material_refs"] = [speed_material["id"]]
        _insert_sorted(target_track, seg)
        segments.append({"ref": _ref(seg), "id": seg.get("id"), "start": cursor, "duration": chunk})

        cursor += chunk
        remaining -= chunk
        index += 1
        if not loop:
            break
        if remaining < _min_duration(data):
            break

    refresh_duration(data)
    return {
        "added": segments,
        "material_name": base_name,
        "path": str(file_path.resolve()),
        "track_index": _tracks(data).index(target_track),
        "total_duration": sum(item["duration"] for item in segments),
    }


_PHOTO_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".heic", ".gif", ".tif", ".tiff"}
_AUDIO_ONLY_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma", ".opus"}
_DEFAULT_PHOTO_CLIP_DURATION_US = 3_000_000
_PHOTO_MATERIAL_DURATION_US = 10_800_000_000  # 剪映对图片素材写入的占位时长（3 小时）


def _ensure_overlay_track(data: dict, name: str) -> dict:
    """找到或创建画中画/贴纸轨道（``flag=2``，与真实 5.9 草稿一致）。"""
    wanted = str(name or "PIP").strip()
    for track in _tracks(data):
        if _track_type(track) == "video" and int(track.get("flag") or 0) == 2:
            existing = str(track.get("name") or "")
            if not existing or existing.lower() == wanted.lower():
                track["name"] = existing or wanted
                return track
    track = {
        "attribute": 0,
        "flag": 2,
        "id": _new_id(),
        "is_default_name": False,
        "name": wanted,
        "segments": [],
        "type": "video",
    }
    _tracks(data).append(track)
    return track


def add_media(
    data: dict,
    path: str | os.PathLike,
    *,
    start=None,
    duration=None,
    source_start=None,
    track=None,
    volume: float = 1.0,
    scale=None,
    x=None,
    y=None,
    rotation=None,
    alpha=None,
    name: Optional[str] = None,
) -> dict:
    """把本地视频/图片素材加入草稿（素材入轨）。

    ``track`` 支持 ``pip``/``画中画``/``sticker``/``贴纸`` 以放入 ``flag=2`` 覆盖轨；
    缺省放入主视频轨尾部或指定 ``start``。图片素材默认入片 3 秒，视频素材优先探测时长。
    """
    file_path = Path(path).expanduser()
    if not file_path.exists() or not file_path.is_file():
        raise MileError("MEDIA_NOT_FOUND", f"素材文件不存在: {file_path}")
    suffix = file_path.suffix.lower()
    if suffix in _AUDIO_ONLY_SUFFIXES:
        raise MileError("BAD_ARGS", "音频素材请使用 add_audio 加入", suffix=suffix)

    info: dict = {}
    try:
        from utils.media_probe import probe_media_file

        probed = probe_media_file(file_path) or {}
        if probed.get("ok"):
            info = probed
    except Exception:
        info = {}
    is_photo = suffix in _PHOTO_SUFFIXES
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    if is_photo and (width <= 0 or height <= 0):
        try:
            from utils.media_probe import image_size

            size = image_size(file_path)
            if size:
                width, height = int(size[0]), int(size[1])
        except Exception:
            pass
    canvas = data.get("canvas_config") or {}
    if width <= 0:
        width = int(canvas.get("width") or 1920)
    if height <= 0:
        height = int(canvas.get("height") or 1080)

    warnings: list[str] = []
    probed_duration_us = None
    if info.get("duration"):
        try:
            probed_duration_us = int(round(float(info["duration"]) * US_PER_SEC))
        except (TypeError, ValueError):
            probed_duration_us = None
    if probed_duration_us is None and not is_photo:
        probed_duration_us = probe_duration(file_path)

    clip_duration_us = parse_time(duration) if duration is not None else None
    if clip_duration_us is None:
        if is_photo:
            clip_duration_us = _DEFAULT_PHOTO_CLIP_DURATION_US
            warnings.append("未给出图片入片时长，默认 3 秒")
        elif probed_duration_us:
            clip_duration_us = probed_duration_us
        else:
            raise MileError(
                "DURATION_UNKNOWN",
                f"无法确定素材时长: {file_path.name}，请安装 ffmpeg 或提供 duration 参数",
            )
    if clip_duration_us < _min_duration(data) or float(volume) < 0 or float(volume) > 4:
        raise MileError("BAD_RANGE", "素材入片时长或音量不合法", duration=clip_duration_us, volume=volume)

    material_duration_us = _PHOTO_MATERIAL_DURATION_US if is_photo else (probed_duration_us or clip_duration_us)
    material = {
        "audio_fade": None,
        "category_id": "",
        "category_name": "local",
        "check_flag": 63487,
        "crop": {
            "lower_left_x": 0.0,
            "lower_left_y": 1.0,
            "lower_right_x": 1.0,
            "lower_right_y": 1.0,
            "upper_left_x": 0.0,
            "upper_left_y": 0.0,
            "upper_right_x": 1.0,
            "upper_right_y": 0.0,
        },
        "crop_ratio": "free",
        "crop_scale": 1.0,
        "duration": int(material_duration_us),
        "height": height,
        "id": _new_id(),
        "local_material_id": "",
        "material_id": "",
        "material_name": str(name or file_path.name),
        "media_path": "",
        "path": str(file_path.resolve()),
        "type": "photo" if is_photo else "video",
        "width": width,
    }
    material["material_id"] = material["id"]
    material["local_material_id"] = material["id"]
    _add_material(data, "videos", material)

    track_spec = str(track or "").strip().lower() if isinstance(track, str) else track
    overlay_name = None
    if isinstance(track_spec, str):
        if track_spec in {"pip", "画中画", "overlay"}:
            overlay_name = "PIP"
        elif track_spec in {"sticker", "贴纸"}:
            overlay_name = "StickerTrack"
    if overlay_name:
        target_track = _ensure_overlay_track(data, overlay_name)
    else:
        target_track = _resolve_track(data, track_spec, seg_type="video", create=True)

    end_us = max((int((seg.get("target_timerange") or {}).get("start", 0) or 0) + int((seg.get("target_timerange") or {}).get("duration", 0) or 0)) for seg in target_track.get("segments") or []) if target_track.get("segments") else 0
    start_us = parse_time(start) if start is not None else end_us
    if start_us < 0:
        raise MileError("BAD_RANGE", "开始时间不能为负")

    source_start_us = parse_time(source_start) if source_start is not None else 0
    if source_start_us < 0:
        raise MileError("BAD_RANGE", "源起点不能为负")
    if not is_photo and material_duration_us:
        if source_start_us + clip_duration_us > material_duration_us:
            available = max(0, material_duration_us - source_start_us)
            if available >= _min_duration(data):
                warnings.append(
                    {"warning": f"源区间超出素材长度，已收敛为 {available / US_PER_SEC:g}s"}
                )
                clip_duration_us = available
            else:
                raise MileError("SOURCE_OUT_OF_RANGE", "源起点已超出素材末尾")

    seg = _new_segment_shell("video", material["id"], start_us, clip_duration_us, _render_index(data, "video"))
    seg["uniform_scale"] = {"on": True, "value": 1.0}
    seg["volume"] = float(volume)
    seg["source_timerange"] = {"duration": int(clip_duration_us), "start": int(source_start_us)}
    clip = seg.setdefault("clip", copy.deepcopy(_DEFAULT_CLIP))
    if scale is not None:
        clip.setdefault("scale", {})["x"] = float(scale)
        clip["scale"]["y"] = float(scale)
    if x is not None:
        clip.setdefault("transform", {})["x"] = float(x)
    if y is not None:
        clip["transform"]["y"] = float(y)
    if rotation is not None:
        clip["rotation"] = float(rotation)
    if alpha is not None:
        clip["alpha"] = float(alpha)
    speed_material = _add_material(
        data,
        "speeds",
        {"curve_speed": None, "id": _new_id(), "mode": 0, "speed": 1.0, "type": "speed"},
    )
    seg["extra_material_refs"] = [speed_material["id"]]
    _insert_sorted(target_track, seg)
    refresh_duration(data)
    result = {
        "ref": _ref(seg),
        "id": seg["id"],
        "material_id": material["id"],
        "material_type": material["type"],
        "kind": "photo" if is_photo else "video",
        "track_index": _tracks(data).index(target_track),
        "start": start_us,
        "duration": clip_duration_us,
        "source_start": source_start_us,
        "width": width,
        "height": height,
    }
    if warnings:
        result["warning"] = warnings[-1] if isinstance(warnings[-1], str) else warnings[-1]["warning"]
    return result
