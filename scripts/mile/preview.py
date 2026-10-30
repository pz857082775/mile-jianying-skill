"""计划差异预览：在内存副本上执行计划，输出修改前后的结构化对比。

这是「先看后改」的落地点：``plan --preview`` / ``apply --preview`` / ``smart --preview``
都通过本模块产出差异。执行路径与真实落盘共用 ``plan._execute_ops``，因此
preview 的 ``after`` 与实际 apply 后的结果字段级一致；全程不写磁盘、不改草稿、
不写入记忆。
"""

from __future__ import annotations

import copy

from . import quality
from .plan import compile_plan, _execute_ops
from .timeline import build_timeline


def _to_sec(us) -> float | None:
    """微秒 -> 秒（对外契约）；无法转换返回 None。"""
    try:
        return round(int(us) / 1_000_000, 3)
    except (TypeError, ValueError):
        return None


_SEGMENT_KEYS = (
    "text", "name", "start", "duration", "end", "source_start", "source_duration",
    "volume", "speed", "transition", "visible", "role", "track_label", "track_type",
    "material_type", "path",
)


def _snapshot(seg: dict) -> dict:
    """取片段的可比较公开字段（时间统一转秒）。"""
    out = {}
    for key in _SEGMENT_KEYS:
        if key not in seg or seg[key] is None:
            continue
        value = seg[key]
        if key in ("start", "duration", "end", "source_start", "source_duration"):
            value = _to_sec(value)
        out[key] = value
    return out


def _meta(timeline: dict) -> dict:
    segs = timeline.get("segments") or []
    summary = timeline.get("summary") or {}
    return {
        "duration": _to_sec(timeline.get("duration")),
        "duration_label": timeline.get("duration_label"),
        "segments": len(segs),
        "tracks": len(timeline.get("tracks") or []),
        "captions": summary.get("caption_count", 0),
        "canvas": timeline.get("canvas"),
        "fps": timeline.get("fps"),
    }


def _segments_diff(before_tl: dict, after_tl: dict) -> list[dict]:
    """按片段 id 对齐，输出 added / removed / changed 三态差异。"""
    bmap = {seg["id"]: seg for seg in before_tl.get("segments") or []}
    amap = {seg["id"]: seg for seg in after_tl.get("segments") or []}
    ids = list(dict.fromkeys([*bmap.keys(), *amap.keys()]))
    items = []
    for sid in ids:
        b, a = bmap.get(sid), amap.get(sid)
        if b is None:
            items.append({"id": sid, "ref": (a or {}).get("ref"), "status": "added", "after": _snapshot(a)})
        elif a is None:
            items.append({"id": sid, "ref": b.get("ref"), "status": "removed", "before": _snapshot(b)})
        else:
            sb, sa = _snapshot(b), _snapshot(a)
            changed = [key for key in sb if sb[key] != sa.get(key)]
            if changed:
                items.append(
                    {
                        "id": sid,
                        "ref": b.get("ref"),
                        "status": "changed",
                        "fields": changed,
                        "before": {key: sb[key] for key in changed},
                        "after": {key: sa[key] for key in changed},
                    }
                )
    return items


def _ops_summary(compiled: dict) -> list[dict]:
    return [
        {
            "op": entry["op"],
            "refs": entry.get("refs") or [],
            "note": entry.get("note"),
            "args": entry.get("args"),
        }
        for entry in compiled.get("operations") or []
    ]


def _risks(work: dict, max_items: int = 5) -> list[dict]:
    """对预览后的内存草稿做轻量质检，只取 error 级问题作为风险提示。"""
    try:
        checks = quality.run_checks(work, folder=None, max_issues=20)
    except Exception:
        return []
    issues = [item for item in checks.get("issues") or [] if item.get("severity") == "error"]
    return issues[:max_items]


def preview_plan(data: dict, plan) -> dict:
    """在内存副本上执行计划并返回差异；不修改 ``data``、不写磁盘。"""
    work = copy.deepcopy(data)
    compiled = compile_plan(work, plan)
    before_tl = build_timeline(data)
    _applied, warnings = _execute_ops(work, compiled)
    after_tl = build_timeline(work)

    before_meta = _meta(before_tl)
    after_meta = _meta(after_tl)
    delta: dict = {}
    for key in ("duration", "segments", "tracks", "captions"):
        bv, av = before_meta.get(key), after_meta.get(key)
        if bv != av:
            try:
                delta[key] = round(float(av) - float(bv), 3) if key == "duration" else int(av) - int(bv)
            except (TypeError, ValueError):
                delta[key] = None

    return {
        "draft": data.get("name") or "",
        "preview": True,
        "saved": False,
        "intent": compiled.get("intent"),
        "tags": compiled.get("tags"),
        "before": before_meta,
        "after": after_meta,
        "diff": {
            "meta": {"delta": delta},
            "segments": _segments_diff(before_tl, after_tl),
            "ops": _ops_summary(compiled),
        },
        "risks": _risks(work),
        "warnings": warnings,
    }