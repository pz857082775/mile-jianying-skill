"""剪辑记忆。

每次保存/应用剪辑后记录一条结构化记忆（项目、意图、标签、草稿结构、风格），
下次剪辑同类视频时按“文字意图 + 结构 + 风格 + 时效”召回，给出可复用的
风格档案与建议。

记忆默认存放在 ``~/.mile-jianying/memory/sessions.jsonl``，可用
``MILE_MEMORY_DIR`` 覆盖；是纯文本 JSONL，随时可读、可改、可删。
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import statistics
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .draft import Draft, content_fingerprint, load_draft, structure_signature
from .errors import MileError
from .timeline import build_timeline

__all__ = [
    "memory_dir",
    "record_session",
    "auto_record_from_draft",
    "recommend_style",
    "extract_style",
    "list_memory",
    "get_memory",
    "forget",
    "update_feedback",
    "stats",
]

_LOCK = threading.Lock()
_MAX_RECORDS = 5000
_DEDUPE_SECONDS = 1800


def memory_dir() -> Path:
    env = os.environ.get("MILE_MEMORY_DIR")
    path = Path(env).expanduser() if env else Path.home() / ".mile-jianying" / "memory"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _records_path() -> Path:
    return memory_dir() / "sessions.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_time(text: str) -> datetime:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


def _load_records() -> list[dict]:
    path = _records_path()
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict) and item.get("id"):
            records.append(item)
    return records


def _write_records(records: list[dict]) -> None:
    path = _records_path()
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in records)
    tmp.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
    os.replace(tmp, path)


def _coerce_json(value):
    if isinstance(value, dict):
        return {str(k): _coerce_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_coerce_json(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _normalize_context(context: Optional[dict]) -> dict:
    if not context:
        return {}
    result = _coerce_json(dict(context))
    tags = result.get("tags")
    if isinstance(tags, str):
        result["tags"] = [item.strip() for item in re.split(r"[,，;；|]", tags) if item.strip()]
    elif isinstance(tags, list):
        result["tags"] = [str(item).strip() for item in tags if str(item).strip()]
    return result


def _merge_context(base: dict, extra: dict) -> dict:
    merged = dict(base or {})
    for key, value in (extra or {}).items():
        if key == "tags" and value:
            old = merged.get("tags") or []
            merged["tags"] = list(dict.fromkeys([*(old if isinstance(old, list) else [old]), *value]))
        elif value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _mode(values, default=None):
    values = [v for v in values if v is not None and v != ""]
    if not values:
        return default
    return Counter(values).most_common(1)[0][0]


def _top(values, n=5) -> list:
    return [item for item, _ in Counter(v for v in values if v).most_common(n)]


def _median(values, default=None):
    values = [float(v) for v in values if v is not None]
    return round(statistics.median(values), 3) if values else default


def _hex_from_rgb(rgb) -> Optional[str]:
    if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
        try:
            return "#" + "".join(f"{max(0, min(255, int(round(float(v) * 255)))):02x}" for v in rgb[:3])
        except Exception:
            return None
    return None


def extract_style(data: dict) -> dict:
    """从草稿中抽取可复用的风格画像。"""
    signature = structure_signature(data)
    timeline = build_timeline(data)
    segments = timeline.get("segments") or []

    colors = []
    font_sizes = []
    fonts = []
    transforms_y = []
    bold_flags = []
    border_flags = []
    shadow_flags = []
    alignments = []
    bgm = []
    voice = []
    volumes = []
    speeds = []
    shot_lengths = []
    caption_count = 0

    for seg in segments:
        if seg["track_type"] == "text":
            meta = seg.get("text_meta") or {}
            if seg.get("role") == "caption" and seg.get("text"):
                caption_count += 1
            if meta.get("color"):
                hex_color = _hex_from_rgb(meta["color"]) or meta.get("color_hex")
                if hex_color:
                    colors.append(hex_color)
            elif meta.get("color_hex"):
                colors.append(str(meta["color_hex"]).lower())
            if meta.get("font_size"):
                font_sizes.append(float(meta["font_size"]))
            font_path = meta.get("font_path") or ""
            if font_path:
                fonts.append(Path(str(font_path)).stem)
            transform_y = (seg.get("transform") or {}).get("y")
            if transform_y is not None:
                transforms_y.append(round(float(transform_y), 3))
            if meta.get("bold") is not None:
                bold_flags.append(bool(meta.get("bold")))
            try:
                border_flags.append(float(meta.get("border_width") or 0) > 0)
            except Exception:
                pass
            shadow_flags.append(bool(meta.get("has_shadow")))
            if meta.get("alignment") is not None:
                alignments.append(int(meta.get("alignment")))
        elif seg["track_type"] == "audio":
            if seg.get("role") == "bgm" and seg.get("name"):
                bgm.append(str(seg["name"]))
            if seg.get("role") == "voice" and seg.get("name"):
                voice.append(str(seg["name"]))
            try:
                volumes.append(round(float(seg.get("volume", 1.0)), 3))
            except Exception:
                pass
        if seg["track_type"] in {"video", "audio"}:
            try:
                speeds.append(round(float(seg.get("speed", 1.0)), 2))
            except Exception:
                pass
        if seg.get("role") == "main" and seg.get("duration"):
            shot_lengths.append(int(seg["duration"]))

    if not shot_lengths:
        shot_lengths = [int(v * 1000) for v in signature.get("shot_lengths_ms") or []]
    duration = int(timeline.get("duration") or 0)
    cuts_per_min = round(len(shot_lengths) / (duration / 60_000_000), 2) if duration > 0 and shot_lengths else None

    filters = []
    effects = []
    for key, bucket in (("filters", filters), ("effects", effects)):
        for item in (data.get("materials") or {}).get(key, []) or []:
            if isinstance(item, dict) and item.get("name"):
                bucket.append(str(item["name"]))

    caption = {
        "font_size": _median(font_sizes),
        "color": _mode(colors),
        "alignment": _mode(alignments),
        "transform_y": _median(transforms_y),
        "bold_ratio": round(sum(bold_flags) / len(bold_flags), 2) if bold_flags else None,
        "border_ratio": round(sum(border_flags) / len(border_flags), 2) if border_flags else None,
        "shadow_ratio": round(sum(shadow_flags) / len(shadow_flags), 2) if shadow_flags else None,
    }
    return {
        "canvas": signature.get("canvas"),
        "fps": timeline.get("fps"),
        "duration_ms": signature.get("duration_ms"),
        "caption": caption,
        "font_sizes": sorted({round(v, 1) for v in font_sizes})[:10],
        "colors": _top(colors, 6),
        "fonts": _top(fonts, 5),
        "bgm": _top(bgm, 5),
        "voice": _top(voice, 3),
        "transitions": _top(signature.get("transition_names") or [], 5),
        "filters": _top(filters, 5),
        "effects": _top(effects, 5),
        "volume": _median(volumes, 1.0),
        "speed": _mode(speeds, 1.0),
        "avg_shot_ms": int(statistics.mean(shot_lengths)) if shot_lengths else None,
        "median_shot_ms": int(statistics.median(shot_lengths)) if shot_lengths else None,
        "cuts_per_min": cuts_per_min,
        "caption_count": caption_count,
        "shot_count": len(shot_lengths),
    }


def _style_summary(style: dict) -> dict:
    caption = style.get("caption") or {}
    return {
        "caption": caption,
        "font_size": caption.get("font_size"),
        "color": caption.get("color"),
        "bgm": style.get("bgm"),
        "transitions": style.get("transitions"),
        "avg_shot_ms": style.get("avg_shot_ms"),
        "cuts_per_min": style.get("cuts_per_min"),
    }


def record_session(
    *,
    project: str,
    draft_path: Optional[str] = None,
    context: Optional[dict] = None,
    outcome: Optional[str] = None,
    rating: Optional[float] = None,
    notes: Optional[str] = None,
    kind: str = "session",
    draft: Optional[Draft] = None,
    dedupe_window: int = _DEDUPE_SECONDS,
) -> dict:
    """记录一次剪辑会话；相同草稿指纹在短时间内会合并，避免重复刷屏。"""
    context = _normalize_context(context)
    if notes and "notes" not in context:
        context["notes"] = notes
    if draft is None and draft_path:
        try:
            draft = load_draft(draft_path)
        except MileError:
            draft = None
    if draft is not None:
        project = project or draft.name
        draft_path = str(draft.content_path)
        data = draft.data
    else:
        data = None

    fingerprint = content_fingerprint(data) if data else None
    structure = structure_signature(data) if data else None
    style = extract_style(data) if data else None
    now = _now()
    record_id = f"m-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{random.getrandbits(16):04x}"

    with _LOCK:
        records = _load_records()
        existing = None
        if dedupe_window > 0 and fingerprint:
            for candidate in reversed(records):
                if (
                    candidate.get("kind") == kind
                    and candidate.get("fingerprint") == fingerprint
                    and candidate.get("project") == project
                ):
                    age = (datetime.now(timezone.utc) - _parse_time(candidate.get("created_at", ""))).total_seconds()
                    if age <= dedupe_window:
                        existing = candidate
                    break

        if existing is not None:
            existing["updated_at"] = now
            existing["context"] = _merge_context(existing.get("context") or {}, context)
            existing["style"] = style or existing.get("style")
            existing["structure"] = structure or existing.get("structure")
            existing["draft_path"] = draft_path or existing.get("draft_path")
            if outcome:
                existing["outcome"] = outcome
            if rating is not None:
                existing["rating"] = float(rating)
            if notes:
                existing["notes"] = notes
            _write_records(records)
            return existing

        record = {
            "id": record_id,
            "kind": kind,
            "created_at": now,
            "updated_at": now,
            "project": project or "",
            "draft_path": draft_path,
            "fingerprint": fingerprint,
            "structure": structure,
            "context": context,
            "outcome": outcome,
            "rating": float(rating) if rating is not None else None,
            "notes": notes,
            "style": style,
        }
        records.append(record)
        if len(records) > _MAX_RECORDS:
            records = records[-_MAX_RECORDS:]
        _write_records(records)
        return record


def auto_record_from_draft(*, project: str, draft_path: str, context: Optional[dict] = None, outcome: str = "draft_saved") -> Optional[dict]:
    """供 JyProject.save() 调用的自动记录钩子；任何异常都不影响保存。"""
    if os.environ.get("MILE_MEMORY_AUTO", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    try:
        return record_session(project=project, draft_path=draft_path, context=context, outcome=outcome)
    except Exception:
        return None


def _tokens(text: str) -> set[str]:
    text = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text))
    cjk = re.findall(r"[\u4e00-\u9fff]+", text)
    bigrams = set()
    for run in cjk:
        for i in range(len(run) - 1):
            bigrams.add(run[i : i + 2])
    return words | bigrams


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _record_text(record: dict) -> str:
    context = record.get("context") or {}
    tags = context.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    parts = [
        str(context.get("intent") or ""),
        " ".join(str(t) for t in tags),
        str(context.get("notes") or ""),
        str(record.get("project") or ""),
        str(record.get("notes") or ""),
    ]
    structure = record.get("structure") or {}
    texts = structure.get("texts") or []
    parts.extend(str(t) for t in texts[:5])
    return " ".join(part for part in parts if part)


def _structure_similarity(query_sig: Optional[dict], record_sig: Optional[dict]) -> float:
    if not query_sig or not record_sig:
        return 0.0
    score = 0.0
    q_canvas = query_sig.get("canvas") or [0, 0]
    r_canvas = record_sig.get("canvas") or [0, 0]
    if q_canvas == r_canvas:
        score += 0.3
    elif q_canvas[0] and r_canvas[0]:
        q_ratio = q_canvas[0] / max(q_canvas[1], 1)
        r_ratio = r_canvas[0] / max(r_canvas[1], 1)
        if abs(q_ratio - r_ratio) < 0.05:
            score += 0.2

    q_duration = int(query_sig.get("duration_ms") or 0)
    r_duration = int(record_sig.get("duration_ms") or 0)
    if q_duration > 0 and r_duration > 0:
        score += 0.2 * (min(q_duration, r_duration) / max(q_duration, r_duration))

    q_counts = query_sig.get("track_segments") or {}
    r_counts = record_sig.get("track_segments") or {}
    keys = set(q_counts) | set(r_counts)
    if keys:
        dot = sum(q_counts.get(k, 0) * r_counts.get(k, 0) for k in keys)
        norm_q = math.sqrt(sum(v * v for v in q_counts.values())) or 1
        norm_r = math.sqrt(sum(v * v for v in r_counts.values())) or 1
        score += 0.4 * (dot / (norm_q * norm_r))

    q_text = int(query_sig.get("text_count") or 0)
    r_text = int(record_sig.get("text_count") or 0)
    if q_text and r_text:
        score += 0.1 * (min(q_text, r_text) / max(q_text, r_text))
    return min(1.0, score)


def _style_similarity(query_style: Optional[dict], record_style: Optional[dict]) -> float:
    if not query_style or not record_style:
        return 0.0
    score = 0.0
    q_caption = query_style.get("caption") or {}
    r_caption = record_style.get("caption") or {}
    if q_caption.get("color") and q_caption.get("color") == r_caption.get("color"):
        score += 0.3
    q_size = q_caption.get("font_size")
    r_size = r_caption.get("font_size")
    if q_size and r_size:
        score += 0.2 * (min(q_size, r_size) / max(q_size, r_size))
    q_bgm = set(query_style.get("bgm") or [])
    r_bgm = set(record_style.get("bgm") or [])
    if q_bgm & r_bgm:
        score += 0.25
    q_trans = set(query_style.get("transitions") or [])
    r_trans = set(record_style.get("transitions") or [])
    if q_trans & r_trans:
        score += 0.15
    q_shot = query_style.get("avg_shot_ms")
    r_shot = record_style.get("avg_shot_ms")
    if q_shot and r_shot:
        score += 0.1 * (min(q_shot, r_shot) / max(q_shot, r_shot))
    return min(1.0, score)


def _recency(record: dict) -> float:
    created = _parse_time(record.get("created_at", ""))
    days = max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 86400)
    return math.exp(-days / 120.0)


def _advice(style: dict, profiles: list[dict]) -> list[str]:
    result = []
    caption = style.get("caption") or {}
    if caption.get("font_size"):
        result.append(f"字幕字号可先用 {caption['font_size']}（记忆中的常用值）")
    if caption.get("color"):
        result.append(f"字幕主色常用 {caption['color']}，可沿用保持品牌一致")
    if caption.get("transform_y") is not None:
        position = "画面下方" if caption["transform_y"] < -0.4 else ("画面上方" if caption["transform_y"] > 0.4 else "画面中部")
        result.append(f"字幕位置多位于{position}（transform_y≈{caption['transform_y']}）")
    if style.get("transitions"):
        result.append(f"常用转场：{'、'.join(style['transitions'][:3])}，建议只在段落切换处使用")
    if style.get("bgm"):
        result.append(f"常用 BGM：{'、'.join(style['bgm'][:3])}；发布前请确认商用授权")
    if style.get("avg_shot_ms"):
        seconds = style["avg_shot_ms"] / 1_000_000
        pace = "偏快" if seconds < 2.5 else ("偏慢" if seconds > 6 else "中等")
        result.append(f"平均镜头 {seconds:.1f}s，节奏{pace}；可据此控制单段素材长度")
    if style.get("volume") is not None:
        result.append(f"人声/音乐音量常在 {style['volume']} 左右，注意 BGM 不要压过人声")
    if profiles:
        result.append(f"本次参考了 {len(profiles)} 条历史记忆，最高相似度 {profiles[0]['score']:.2f}")
    if not result:
        result.append("暂无足够的历史风格数据；本次完成后会自动沉淀为新的记忆")
    return result


def _merge_styles(profiles: list[dict]) -> dict:
    styles = [item["record"]["style"] for item in profiles if item.get("record", {}).get("style")]
    if not styles:
        return {}

    font_sizes = []
    colors = []
    fonts = []
    bgm = []
    transitions = []
    filters = []
    effects = []
    transforms = []
    alignments = []
    bold = []
    border = []
    avg_shots = []
    med_shots = []
    volumes = []
    speeds = []
    canvases = []
    fps_values = []
    captions = []

    for style in styles:
        caption = style.get("caption") or {}
        caption and captions.append(caption)
        if caption.get("font_size"):
            font_sizes.append(caption["font_size"])
        colors.extend(style.get("colors") or [])
        fonts.extend(style.get("fonts") or [])
        bgm.extend(style.get("bgm") or [])
        transitions.extend(style.get("transitions") or [])
        filters.extend(style.get("filters") or [])
        effects.extend(style.get("effects") or [])
        if caption.get("transform_y") is not None:
            transforms.append(caption["transform_y"])
        if caption.get("alignment") is not None:
            alignments.append(caption["alignment"])
        if caption.get("bold_ratio") is not None:
            bold.append(caption["bold_ratio"])
        if caption.get("border_ratio") is not None:
            border.append(caption["border_ratio"])
        if style.get("avg_shot_ms"):
            avg_shots.append(style["avg_shot_ms"])
        if style.get("median_shot_ms"):
            med_shots.append(style["median_shot_ms"])
        if style.get("volume") is not None:
            volumes.append(style["volume"])
        if style.get("speed") is not None:
            speeds.append(style["speed"])
        if style.get("canvas"):
            canvases.append(tuple(style["canvas"]))
        if style.get("fps"):
            fps_values.append(style["fps"])

    merged_caption = {
        "font_size": _median(font_sizes),
        "color": _mode(colors),
        "alignment": _mode(alignments),
        "transform_y": _median(transforms),
        "bold_ratio": round(statistics.mean(bold), 2) if bold else None,
        "border_ratio": round(statistics.mean(border), 2) if border else None,
    }
    return {
        "canvas": list(_mode(canvases)) if canvases else None,
        "fps": _mode(fps_values),
        "caption": {key: value for key, value in merged_caption.items() if value is not None},
        "font_sizes": sorted({round(float(v), 1) for v in font_sizes})[:10],
        "colors": _top(colors, 6),
        "fonts": _top(fonts, 5),
        "bgm": _top(bgm, 5),
        "transitions": _top(transitions, 5),
        "filters": _top(filters, 5),
        "effects": _top(effects, 5),
        "volume": _median(volumes, 1.0),
        "speed": _mode(speeds, 1.0),
        "avg_shot_ms": int(statistics.mean(avg_shots)) if avg_shots else None,
        "median_shot_ms": int(statistics.median(med_shots)) if med_shots else None,
        "memory_count": len(styles),
    }


def recommend_style(
    *,
    draft_path: Optional[str] = None,
    context: Optional[dict] = None,
    k: int = 3,
    tags: Optional[list[str]] = None,
) -> dict:
    """按相似度召回历史记忆，合并出一份可直接使用的风格档案。"""
    context = _normalize_context(context)
    if tags:
        context["tags"] = list(dict.fromkeys([*(context.get("tags") or []), *tags])) if isinstance(context.get("tags"), list) else tags

    records = [item for item in _load_records() if item.get("kind") == "session"]
    if not records:
        return {
            "match_count": 0,
            "profiles": [],
            "style": {},
            "advice": ["暂无历史记忆；本次剪辑完成后会自动生成第一条记忆"],
        }

    draft = None
    query_sig = None
    query_style = None
    if draft_path:
        try:
            draft = load_draft(draft_path)
            query_sig = structure_signature(draft.data)
            query_style = extract_style(draft.data)
        except MileError:
            draft = None

    query_text = " ".join(
        str(part)
        for part in (
            context.get("intent"),
            " ".join(context.get("tags") or []) if isinstance(context.get("tags"), list) else context.get("tags"),
            context.get("notes"),
        )
        if part
    )
    query_tokens = _tokens(query_text)
    has_text_query = bool(query_tokens)

    scored = []
    for record in records:
        text_score = _jaccard(query_tokens, _tokens(_record_text(record))) if has_text_query else 0.0
        if has_text_query:
            project_tokens = _tokens(record.get("project") or "")
            if project_tokens & query_tokens:
                text_score = min(1.0, text_score + 0.1)
        struct_score = _structure_similarity(query_sig, record.get("structure")) if query_sig else 0.0
        style_score = _style_similarity(query_style, record.get("style")) if query_style else 0.0
        recency = _recency(record)
        if has_text_query:
            score = 0.4 * text_score + 0.25 * struct_score + 0.15 * style_score + 0.2 * recency
        else:
            score = 0.45 * struct_score + 0.25 * style_score + 0.3 * recency
        rating = record.get("rating")
        if isinstance(rating, (int, float)):
            score += max(-0.05, min(0.05, (float(rating) - 3) * 0.02))
        scored.append((score, text_score, struct_score, style_score, recency, record))

    scored.sort(key=lambda item: item[0], reverse=True)
    top = scored[: max(1, int(k))]
    profiles = [
        {
            "id": record.get("id"),
            "project": record.get("project"),
            "created_at": record.get("created_at"),
            "score": round(score, 3),
            "signals": {
                "text": round(text_score, 3),
                "structure": round(struct_score, 3),
                "style": round(style_score, 3),
                "recency": round(recency, 3),
            },
            "context": record.get("context") or {},
            "style": _style_summary(record.get("style") or {}),
            "record": record,
        }
        for score, text_score, struct_score, style_score, recency, record in top
    ]
    merged = _merge_styles(profiles)
    return {
        "match_count": len(records),
        "profiles": [{key: value for key, value in item.items() if key != "record"} for item in profiles],
        "style": merged,
        "advice": _advice(merged, profiles),
        "query": {"text": query_text, "draft": str(draft.folder) if draft else draft_path},
    }


def list_memory(
    *,
    query: Optional[str] = None,
    tags: Optional[list[str]] = None,
    project: Optional[str] = None,
    limit: int = 20,
) -> list[dict]:
    records = _load_records()
    if project:
        records = [item for item in records if project.lower() in str(item.get("project") or "").lower()]
    if tags:
        wanted = {str(tag).lower() for tag in tags}
        filtered = []
        for item in records:
            record_tags = {str(tag).lower() for tag in (item.get("context") or {}).get("tags") or []}
            if wanted & record_tags:
                filtered.append(item)
        records = filtered
    if query:
        needle = str(query).lower()
        records = [
            item
            for item in records
            if needle in _record_text(item).lower() or needle in str(item.get("project") or "").lower()
        ]
    records = sorted(records, key=lambda item: item.get("created_at") or "", reverse=True)[: int(limit)]
    return [
        {
            "id": item.get("id"),
            "project": item.get("project"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "context": item.get("context") or {},
            "outcome": item.get("outcome"),
            "rating": item.get("rating"),
            "style": _style_summary(item.get("style") or {}),
            "draft_path": item.get("draft_path"),
        }
        for item in records
    ]


def get_memory(memory_id: str) -> dict:
    for item in _load_records():
        if item.get("id") == memory_id:
            return item
    raise MileError("MEMORY_NOT_FOUND", f"未找到记忆: {memory_id}")


def forget(memory_id: str) -> dict:
    with _LOCK:
        records = _load_records()
        remaining = [item for item in records if item.get("id") != memory_id]
        if len(remaining) == len(records):
            raise MileError("MEMORY_NOT_FOUND", f"未找到记忆: {memory_id}")
        _write_records(remaining)
    return {"forgotten": memory_id, "remaining": len(remaining)}


def update_feedback(
    memory_id: str,
    *,
    rating: Optional[float] = None,
    outcome: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    with _LOCK:
        records = _load_records()
        for item in records:
            if item.get("id") == memory_id:
                if rating is not None:
                    item["rating"] = float(rating)
                if outcome is not None:
                    item["outcome"] = outcome
                if notes is not None:
                    item["notes"] = notes
                item["updated_at"] = _now()
                _write_records(records)
                return item
    raise MileError("MEMORY_NOT_FOUND", f"未找到记忆: {memory_id}")


def stats() -> dict:
    records = _load_records()
    projects = Counter(str(item.get("project") or "") for item in records)
    tags = Counter()
    ratings = []
    for item in records:
        for tag in (item.get("context") or {}).get("tags") or []:
            tags[str(tag)] += 1
        if isinstance(item.get("rating"), (int, float)):
            ratings.append(float(item["rating"]))
    last = max((item.get("updated_at") or item.get("created_at") or "" for item in records), default=None)
    return {
        "count": len(records),
        "projects": len([name for name in projects if name]),
        "top_projects": projects.most_common(8),
        "top_tags": tags.most_common(12),
        "avg_rating": round(statistics.mean(ratings), 2) if ratings else None,
        "last_at": last,
        "dir": str(memory_dir()),
        "size_bytes": _records_path().stat().st_size if _records_path().exists() else 0,
    }
