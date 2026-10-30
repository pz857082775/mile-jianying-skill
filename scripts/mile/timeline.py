"""把草稿 JSON 建模为可检索的时间轴。

自然语言里的“第 12 秒那句字幕”“第三个镜头”“背景音乐”都要先落到
具体片段上，编辑才可能是精准的。本模块只做只读解析，不修改草稿。
"""

from __future__ import annotations

import json
import re
from typing import Iterable, Optional

from .errors import MileError
from .timecode import format_time, parse_range, parse_time

__all__ = [
    "material_map",
    "build_timeline",
    "select_segments",
    "segment_public",
    "timeline_summary",
    "TEXT_TYPE_NAMES",
]

TYPE_NAMES = {
    "video": "视频",
    "audio": "音频",
    "text": "文本",
    "sticker": "贴纸",
    "effect": "特效",
    "filter": "滤镜",
    "adjust": "调节",
}
TEXT_TYPE_NAMES = TYPE_NAMES

_BGM_HINTS = ("bgm", "music", "背景音乐", "配乐", "音乐")
_VOICE_HINTS = ("配音", "旁白", "口播", "voice", "tone", "textreading")
_STICKER_HINTS = ("sticker", "贴纸", "marker", "标记", "click", "icon")
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif")

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


def _safe_json(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return json.loads(value)
        except Exception:
            return None
    return None


def material_map(data: dict) -> dict:
    result = {}
    materials = data.get("materials") or {}
    if not isinstance(materials, dict):
        return result
    for items in materials.values():
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("id"):
                    result[str(item["id"])] = item
    return result


def text_material_info(material: dict) -> dict:
    content = _safe_json(material.get("content")) or {}
    text = str(content.get("text") or material.get("base_content") or "")
    styles = content.get("styles") or []
    primary = styles[0] if isinstance(styles, list) and styles else {}
    fill = (((primary.get("fill") or {}).get("content") or {}).get("solid") or {}) if isinstance(primary, dict) else {}
    font = (primary.get("font") or {}) if isinstance(primary, dict) else {}
    try:
        size = float(primary.get("size") or material.get("font_size") or 0)
    except Exception:
        size = 0.0
    return {
        "text": text,
        "font_size": size or None,
        "color": fill.get("color"),
        "color_hex": material.get("text_color") if isinstance(material.get("text_color"), str) else None,
        "font_path": font.get("path"),
        "font_id": font.get("id"),
        "alignment": material.get("alignment"),
        "style_count": len(styles) if isinstance(styles, list) else 0,
        "bold": bool(primary.get("bold")) if isinstance(primary, dict) else None,
        "border_width": material.get("border_width"),
        "has_shadow": material.get("has_shadow"),
    }


def _looks_like_sticker(track: dict, material: dict) -> bool:
    """区分覆盖层里的图片贴纸和真正的画中画视频。"""
    path = str(material.get("path") or "").lower()
    mat_type = str(material.get("type") or "").lower()
    is_image = mat_type in {"photo", "image"} or path.endswith(_IMAGE_SUFFIXES)
    if not is_image:
        return False
    name = str(material.get("material_name") or material.get("name") or "")
    haystack = f"{track.get('name') or ''} {name} {path}".lower()
    return any(hint in haystack for hint in _STICKER_HINTS)


def _role_for(track_type: str, track: dict, seg: dict, material: dict, main_video_track: int) -> str:
    if track_type == "video":
        if track.get("flag") == 2:
            return "sticker" if _looks_like_sticker(track, material) else "pip"
        return "main" if track.get("index") == main_video_track else "broll"
    if track_type == "text":
        track_name = str(track.get("name") or "").lower()
        if track.get("flag") == 1 or any(h in track_name for h in ("subtitle", "caption", "字幕")):
            return "caption"
        return "title"
    if track_type == "audio":
        name = str(material.get("name") or material.get("material_name") or "")
        path = str(material.get("path") or "")
        mat_type = str(material.get("type") or "")
        haystack = f"{name} {path} {track.get('name') or ''}".lower()
        if mat_type == "text_to_audio" or any(h in haystack for h in _VOICE_HINTS):
            return "voice"
        if mat_type in {"music", "extract_music"} or any(h in haystack for h in _BGM_HINTS):
            return "bgm"
        return "audio"
    if track_type == "sticker":
        return "sticker"
    if track_type == "effect":
        return "effect"
    if track_type == "filter":
        return "filter"
    return track_type or "unknown"


def _track_label(track_type: str, index_in_type: int, name: str) -> str:
    base = TYPE_NAMES.get(track_type, track_type)
    if name:
        return f"{base}·{name}"
    return f"{base}{index_in_type}"


def build_timeline(data: dict) -> dict:
    """构建完整时间轴模型。"""
    mats = material_map(data)
    tracks_raw = data.get("tracks") or []

    video_durations = {}
    for track in tracks_raw:
        if track.get("type") == "video":
            total = sum(int((seg.get("target_timerange") or {}).get("duration") or 0) for seg in track.get("segments") or [])
            video_durations[id(track)] = total
    main_video_track = None
    if video_durations:
        main_track_obj = max(
            (t for t in tracks_raw if t.get("type") == "video"),
            key=lambda t: video_durations.get(id(t), 0),
            default=None,
        )
        if main_track_obj is not None:
            main_video_track = tracks_raw.index(main_track_obj)

    tracks = []
    segments = []
    type_counters: dict[str, int] = {}
    used_refs: set[str] = set()

    for ti, track in enumerate(tracks_raw):
        ttype = str(track.get("type") or "unknown")
        type_counters[ttype] = type_counters.get(ttype, 0) + 1
        label = _track_label(ttype, type_counters[ttype], str(track.get("name") or ""))
        segs = track.get("segments") or []
        track_entry = {
            "index": ti,
            "id": track.get("id"),
            "type": ttype,
            "name": track.get("name") or "",
            "label": label,
            "flag": track.get("flag"),
            "attribute": track.get("attribute"),
            "is_main": ti == main_video_track,
            "segment_count": len(segs),
            "segments": [str(seg.get("id") or "") for seg in segs],
        }
        tracks.append(track_entry)

        track_meta = {"index": ti, "type": ttype, "flag": track.get("flag"), "name": track.get("name") or ""}
        for si, seg in enumerate(segs):
            mid = str(seg.get("material_id") or "")
            mat = mats.get(mid) or {}
            target = seg.get("target_timerange") or {}
            source = seg.get("source_timerange") or {}
            try:
                start = int(target.get("start") or 0)
                duration = int(target.get("duration") or 0)
            except Exception:
                start, duration = 0, 0
            try:
                source_start = int(source.get("start") or 0)
                source_duration = int(source.get("duration") or 0)
            except Exception:
                source_start, source_duration = 0, 0
            name = str(mat.get("material_name") or mat.get("name") or "")
            path = str(mat.get("path") or mat.get("media_path") or "")
            text_info = text_material_info(mat) if ttype == "text" else {}
            clip = seg.get("clip") or {}
            transform = clip.get("transform") or {}
            scale = clip.get("scale") or {}
            try:
                volume = float(seg.get("volume", 1.0))
            except Exception:
                volume = 1.0
            try:
                speed = float(seg.get("speed", 1.0))
            except Exception:
                speed = 1.0
            transition = None
            for ref in seg.get("extra_material_refs") or []:
                extra = mats.get(str(ref)) or {}
                if str(extra.get("type") or "") == "transition":
                    transition = extra.get("name") or extra.get("resource_name") or "转场"
                    break

            raw_id = str(seg.get("id") or "")
            ref = re.sub(r"[^0-9A-Za-z]", "", raw_id)[:8].upper() or f"T{ti}S{si}"
            while ref.lower() in used_refs:
                ref = (ref + f"{len(ref)}")[:12]
            used_refs.add(ref.lower())

            segments.append(
                {
                    "ref": ref,
                    "id": raw_id,
                    "track_index": ti,
                    "track_type": ttype,
                    "track_label": label,
                    "track_name": track.get("name") or "",
                    "index_in_track": si,
                    "start": start,
                    "duration": duration,
                    "end": start + duration,
                    "start_label": format_time(start, "s"),
                    "end_label": format_time(start + duration, "s"),
                    "source_start": source_start,
                    "source_duration": source_duration,
                    "material_id": mid,
                    "material_type": str(mat.get("type") or ""),
                    "name": name,
                    "path": path,
                    "text": text_info.get("text", ""),
                    "text_meta": text_info or None,
                    "volume": volume,
                    "speed": speed,
                    "transition": transition,
                    "visible": bool(seg.get("visible", True)),
                    "role": _role_for(ttype, track_meta, seg, mat, main_video_track),
                    "flag": track.get("flag"),
                    "render_index": seg.get("render_index"),
                    "transform": transform,
                    "scale": scale,
                    "rotation": clip.get("rotation"),
                    "alpha": clip.get("alpha"),
                    "has_audio": mat.get("has_audio"),
                }
            )

    canvas = data.get("canvas_config") or {}
    timeline = {
        "name": data.get("name") or "",
        "canvas": {
            "width": int(canvas.get("width") or 0),
            "height": int(canvas.get("height") or 0),
            "ratio": canvas.get("ratio"),
        },
        "fps": float(data.get("fps") or 0),
        "duration": int(data.get("duration") or 0),
        "duration_label": format_time(int(data.get("duration") or 0), "clock"),
        "tracks": tracks,
        "segments": segments,
        "summary": _summary(segments, tracks),
    }
    return timeline


def _summary(segments: list[dict], tracks: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for seg in segments:
        counts[seg["track_type"]] = counts.get(seg["track_type"], 0) + 1
    captions = [s for s in segments if s["role"] == "caption"]
    gaps = 0
    overlaps = 0
    by_track: dict[int, list[dict]] = {}
    for seg in segments:
        by_track.setdefault(seg["track_index"], []).append(seg)
    for segs in by_track.values():
        ordered = sorted(segs, key=lambda s: (s["start"], s["end"]))
        for prev, curr in zip(ordered, ordered[1:]):
            if curr["start"] > prev["end"] + 20_000:
                gaps += 1
            elif curr["start"] < prev["end"] - 20_000:
                overlaps += 1
    return {
        "track_count": len(tracks),
        "segment_count": len(segments),
        "type_counts": counts,
        "caption_count": len(captions),
        "caption_chars": sum(len(s["text"] or "") for s in captions),
        "gap_count": gaps,
        "overlap_count": overlaps,
        "main_duration": max((s["end"] for s in segments if s["role"] == "main"), default=0),
    }


def _match_track(spec, track: dict, tracks: list[dict]) -> bool:
    if isinstance(spec, int):
        return track["index"] == spec
    if not isinstance(spec, str):
        return True
    text = spec.strip()
    if not text:
        return True
    if text.isdigit():
        return track["index"] == int(text)

    nth = None
    match = re.match(r"^(.+?)\s*[#:]\s*(\d+)$", text)
    if match:
        text, nth = match.group(1).strip(), int(match.group(2))

    normalized = text.lower()
    same_type = [
        t
        for t in tracks
        if t["type"].lower() == normalized
        or TYPE_NAMES.get(t["type"], "").lower() == normalized
        or normalized in t["label"].lower()
        or (t["name"] and normalized in t["name"].lower())
    ]
    if nth is not None:
        return track in same_type and same_type.index(track) == nth - 1
    return track in same_type


def _id_set(spec) -> set[str]:
    if spec is None:
        return set()
    if isinstance(spec, str):
        return {spec.strip().lower()}
    if isinstance(spec, (list, tuple, set)):
        return {str(item).strip().lower() for item in spec if str(item).strip()}
    return {str(spec).strip().lower()}


def _match_ref(seg: dict, wanted: set[str]) -> bool:
    if not wanted:
        return False
    full = (seg["id"] or "").lower()
    plain = re.sub(r"[^0-9a-z]", "", full)
    ref = seg["ref"].lower()
    for item in wanted:
        item_plain = re.sub(r"[^0-9a-z]", "", item)
        if len(item_plain) >= 4 and (plain.startswith(item_plain) or item_plain.startswith(plain)):
            return True
        if item == ref or item == full:
            return True
    return False


def select_segments(timeline: dict, spec) -> list[dict]:
    """按语义选择片段。

    spec 支持：
    - ``"A1B2C3D4"``：片段 ref / id 前缀
    - ``{"ref": ...}``、``{"track": "video#2"}``、``{"index": 3}``
    - ``{"range": "12s-18s"}``、``{"at": "12.5s"}``
    - ``{"text_contains": "欢迎"}``、``{"name_contains": "片头"}``
    - ``{"role": "caption"}``、``{"material_type": "photo"}``
    """
    if isinstance(spec, str):
        spec = {"ref": spec}
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise MileError("BAD_TARGET", "片段选择器必须是字符串或对象")

    unknown = set(spec.keys()) - set(_SELECT_KEYS) - {"first", "all", "limit"}
    if unknown:
        raise MileError("BAD_TARGET", f"不支持的定位条件: {sorted(unknown)}", supported=list(_SELECT_KEYS))

    results = timeline.get("segments") or []
    wanted_refs = _id_set(spec.get("ref")) | _id_set(spec.get("ids"))
    if wanted_refs:
        results = [seg for seg in results if _match_ref(seg, wanted_refs)]

    tracks = timeline.get("tracks") or []
    if spec.get("track") is not None:
        valid_track_indexes = {t["index"] for t in tracks if _match_track(spec["track"], t, tracks)}
        if not valid_track_indexes:
            raise MileError("TRACK_NOT_FOUND", f"未找到轨道: {spec['track']}")
        results = [seg for seg in results if seg["track_index"] in valid_track_indexes]

    if spec.get("index") is not None:
        index = spec["index"]
        if isinstance(index, str):
            text = index.strip().lower()
            if text in {"last", "末", "最后一个"}:
                max_index = max((seg["index_in_track"] for seg in results), default=0)
                results = [seg for seg in results if seg["index_in_track"] == max_index]
            else:
                try:
                    index = int(text)
                except ValueError as exc:
                    raise MileError("BAD_TARGET", f"无效的序号: {index}") from exc
        if isinstance(index, int):
            results = [seg for seg in results if seg["index_in_track"] == index]

    if spec.get("range") is not None:
        start, duration = parse_range(spec["range"])
        if duration is None:
            results = [seg for seg in results if seg["start"] <= start < seg["end"]]
        else:
            end = start + duration
            results = [seg for seg in results if seg["start"] < end and seg["end"] > start]

    if spec.get("at") is not None:
        point = parse_time(spec["at"])
        results = [seg for seg in results if seg["start"] <= point < seg["end"]]

    if spec.get("text_contains"):
        needle = str(spec["text_contains"]).casefold()
        results = [seg for seg in results if needle in (seg.get("text") or "").casefold()]

    if spec.get("name_contains"):
        needle = str(spec["name_contains"]).casefold()
        results = [
            seg
            for seg in results
            if needle in (seg.get("name") or "").casefold() or needle in (seg.get("path") or "").casefold()
        ]

    if spec.get("role") is not None:
        roles = spec["role"]
        role_set = {roles} if isinstance(roles, str) else set(roles)
        results = [seg for seg in results if seg.get("role") in role_set]

    if spec.get("material_type") is not None:
        mt = spec["material_type"]
        types = {mt} if isinstance(mt, str) else set(mt)
        results = [seg for seg in results if seg.get("material_type") in types]

    if spec.get("min_duration") is not None:
        minimum = parse_time(spec["min_duration"])
        results = [seg for seg in results if seg["duration"] >= minimum]
    if spec.get("max_duration") is not None:
        maximum = parse_time(spec["max_duration"])
        results = [seg for seg in results if seg["duration"] <= maximum]

    results = sorted(results, key=lambda s: (s["track_index"], s["start"], s["index_in_track"]))
    limit = spec.get("limit")
    if limit:
        results = results[: int(limit)]
    return results


def segment_public(seg: dict) -> dict:
    """给 CLI / Agent 输出的精简片段信息。"""
    keys = (
        "ref",
        "id",
        "track_index",
        "track_type",
        "track_label",
        "index_in_track",
        "start",
        "end",
        "duration",
        "start_label",
        "end_label",
        "role",
        "name",
        "text",
        "material_type",
        "volume",
        "speed",
        "visible",
    )
    return {key: seg.get(key) for key in keys if key in seg}


def timeline_summary(timeline: dict) -> dict:
    return {
        "name": timeline.get("name"),
        "canvas": timeline.get("canvas"),
        "fps": timeline.get("fps"),
        "duration": timeline.get("duration"),
        "duration_label": timeline.get("duration_label"),
        "tracks": [
            {
                "index": t["index"],
                "type": t["type"],
                "label": t["label"],
                "flag": t["flag"],
                "is_main": t["is_main"],
                "segment_count": t["segment_count"],
            }
            for t in timeline.get("tracks") or []
        ],
        "summary": timeline.get("summary"),
    }


def iter_text_segments(timeline: dict) -> Iterable[dict]:
    for seg in timeline.get("segments") or []:
        if seg["track_type"] == "text" and seg.get("text"):
            yield seg
