"""成片质量检查与安全修复。

检查重点放在“导出后才发现的坑”：零时长片段、素材丢失、轨道重叠、
字幕过短/为空、音量异常、画布尺寸、总时长不一致等。
"""

from __future__ import annotations

import os
from pathlib import Path

from .draft import refresh_duration
from .timeline import build_timeline

__all__ = ["run_checks", "autofix"]

_SEVERITIES = ("error", "warning", "info")


def _issue(code: str, severity: str, message: str, **extra) -> dict:
    payload = {"code": code, "severity": severity, "message": message, "fixable": False}
    payload.update(extra)
    return payload


def _path_missing(path: str, folder: Path) -> bool:
    text = str(path or "").strip()
    if not text:
        return False
    if text.startswith(("http://", "https://")):
        return False
    if "##_draftpath_placeholder" in text:
        return False
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = folder / candidate
    return not candidate.exists()


def run_checks(data: dict, *, folder: Optional[str | os.PathLike] = None, max_issues: int = 300) -> dict:
    """执行质量检查，返回问题列表与统计。"""
    timeline = build_timeline(data)
    segments = timeline.get("segments") or []
    tracks = timeline.get("tracks") or []
    materials = data.get("materials") or {}
    material_ids = set()
    if isinstance(materials, dict):
        for items in materials.values():
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("id"):
                        material_ids.add(str(item["id"]))

    draft_folder = Path(folder) if folder else Path(".")
    issues: list[dict] = []

    def add(issue: dict):
        if len(issues) < max_issues:
            issues.append(issue)

    fps = float(timeline.get("fps") or 0)
    if fps <= 0:
        add(_issue("FPS_INVALID", "error", "草稿帧率无效（<=0）", fixable=False))

    canvas = timeline.get("canvas") or {}
    for key in ("width", "height"):
        value = int(canvas.get(key) or 0)
        if value and value % 2:
            add(
                _issue(
                    "CANVAS_ODD",
                    "warning",
                    f"画布{key}为奇数 {value}，部分编码器会报错",
                    fixable=True,
                    axis=key,
                )
            )

    declared_duration = int(data.get("duration") or 0)
    max_end = 0
    path_cache: dict[str, bool] = {}

    for seg in segments:
        target = seg.get("start", 0)
        duration = seg.get("duration", 0)
        end = target + duration
        max_end = max(max_end, end)

        if target < 0:
            add(_issue("SEGMENT_NEGATIVE_START", "error", "片段开始时间为负", ref=seg["ref"], track_index=seg["track_index"], fixable=True))
        if duration <= 0:
            add(
                _issue(
                    "SEGMENT_ZERO_DURATION",
                    "error",
                    "片段时长为 0 或负数，导出时可能报错",
                    ref=seg["ref"],
                    track_index=seg["track_index"],
                    fixable=True,
                )
            )
        if seg.get("material_id") and seg["material_id"] not in material_ids:
            add(_issue("MATERIAL_MISSING", "error", "片段引用的素材不存在", ref=seg["ref"], track_index=seg["track_index"]))

        if seg["track_type"] == "text":
            text = (seg.get("text") or "").strip()
            if not text:
                add(_issue("TEXT_EMPTY", "warning", "文字片段内容为空", ref=seg["ref"], track_index=seg["track_index"]))
            elif duration < 300_000:
                add(
                    _issue(
                        "TEXT_TOO_SHORT",
                        "warning",
                        f"字幕仅 {duration / 1_000_000:.2f}s，观众可能看不清",
                        ref=seg["ref"],
                        track_index=seg["track_index"],
                        fixable=False,
                    )
                )
        elif seg["track_type"] in {"video", "audio"}:
            try:
                volume = float(seg.get("volume", 1.0))
            except Exception:
                volume = 1.0
            if volume > 4:
                add(_issue("AUDIO_VOLUME_HIGH", "error", f"音量 {volume} 超出安全范围（>4）", ref=seg["ref"], track_index=seg["track_index"], fixable=True))
            try:
                speed = float(seg.get("speed", 1.0))
            except Exception:
                speed = 1.0
            if speed < 0.1 or speed > 10:
                add(_issue("SPEED_OUT_OF_RANGE", "warning", f"变速 {speed} 超出常用范围（0.1-10）", ref=seg["ref"], track_index=seg["track_index"]))
            path = seg.get("path") or ""
            if path:
                if path not in path_cache:
                    path_cache[path] = _path_missing(path, draft_folder)
                if path_cache[path]:
                    add(
                        _issue(
                            "MEDIA_FILE_MISSING",
                            "warning",
                            f"本地素材文件不存在: {path}",
                            ref=seg["ref"],
                            track_index=seg["track_index"],
                            path=path,
                        )
                    )

    for track_index, track in enumerate(data.get("tracks") or []):
        for seg in track.get("segments") or []:
            for ref_id in seg.get("extra_material_refs") or []:
                if str(ref_id) not in material_ids:
                    add(
                        _issue(
                            "EXTRA_MATERIAL_MISSING",
                            "warning",
                            f"片段附加素材引用失效: {str(ref_id)[:8]}",
                            ref=_ref(seg),
                            track_index=track_index,
                        )
                    )
                    break

    # 轨道内重叠 / 空隙
    by_track: dict[int, list[dict]] = {}
    for seg in segments:
        by_track.setdefault(seg["track_index"], []).append(seg)
    for track in tracks:
        segs = sorted(by_track.get(track["index"], []), key=lambda s: (s["start"], s["end"]))
        for prev, curr in zip(segs, segs[1:]):
            if curr["start"] < prev["end"] - 20_000:
                add(
                    _issue(
                        "TRACK_OVERLAP",
                        "warning",
                        f"{track['label']} 中 {prev['ref']} 与 {curr['ref']} 时间重叠",
                        track_index=track["index"],
                        ref=curr["ref"],
                        other_ref=prev["ref"],
                    )
                )
            elif track["type"] == "text" and curr["start"] - prev["end"] > 1_000_000:
                add(
                    _issue(
                        "SUBTITLE_GAP",
                        "info",
                        f"{track['label']} 字幕之间有 {((curr['start'] - prev['end']) / 1_000_000):.1f}s 空档",
                        track_index=track["index"],
                        ref=prev["ref"],
                    )
                )
            if track["type"] == "text" and prev["text"] and prev["text"] == curr["text"]:
                add(_issue("DUPLICATE_TEXT", "info", f"连续字幕内容重复: {prev['text'][:20]}", track_index=track["index"], ref=curr["ref"]))

    if declared_duration and max_end and abs(declared_duration - max_end) > 100_000:
        add(
            _issue(
                "DRAFT_DURATION_MISMATCH",
                "warning",
                f"草稿总时长 {declared_duration / 1_000_000:.2f}s 与实际内容 {max_end / 1_000_000:.2f}s 不一致",
                fixable=True,
            )
        )

    main_track = next((track for track in tracks if track.get("is_main")), None)
    if main_track:
        main_segs = sorted(by_track.get(main_track["index"], []), key=lambda s: s["start"])
        for prev, curr in zip(main_segs, main_segs[1:]):
            gap = curr["start"] - prev["end"]
            if gap > 500_000:
                add(
                    _issue(
                        "MAIN_TRACK_GAP",
                        "warning",
                        f"主轨在 {prev['end_label']} 之后有 {gap / 1_000_000:.1f}s 空档",
                        track_index=main_track["index"],
                        ref=prev["ref"],
                    )
                )
    else:
        add(_issue("NO_MAIN_TRACK", "warning", "没有可识别的主视频轨道"))

    summary = {"error": 0, "warning": 0, "info": 0}
    for issue in issues:
        summary[issue["severity"]] = summary.get(issue["severity"], 0) + 1
    return {
        "issues": issues,
        "summary": summary,
        "checked_segments": len(segments),
        "checked_tracks": len(tracks),
        "declared_duration": declared_duration,
        "actual_duration": max_end,
        "ok": summary["error"] == 0,
    }


def autofix(data: dict, issues: list[dict]) -> dict:
    """只做确定安全的修复；结构性问题会保留在报告中。"""
    fixed = []
    remaining = []
    removable = set()
    clamp_volume = set()
    clamp_start = set()
    fix_canvas = False
    fix_duration = False

    for issue in issues:
        code = issue.get("code")
        if code == "SEGMENT_ZERO_DURATION":
            ref = issue.get("ref")
            if ref:
                removable.add(ref)
                fixed.append({"code": code, "ref": ref, "action": "删除零时长片段"})
            else:
                remaining.append(issue)
        elif code == "AUDIO_VOLUME_HIGH":
            clamp_volume.add(issue.get("ref"))
            fixed.append({"code": code, "ref": issue.get("ref"), "action": "音量收敛到 4"})
        elif code == "SEGMENT_NEGATIVE_START":
            clamp_start.add(issue.get("ref"))
            fixed.append({"code": code, "ref": issue.get("ref"), "action": "开始时间修正为 0"})
        elif code == "CANVAS_ODD":
            fix_canvas = True
            fixed.append({"code": code, "action": "画布宽高向下取偶数"})
        elif code == "DRAFT_DURATION_MISMATCH":
            fix_duration = True
            fixed.append({"code": code, "action": "按实际内容刷新总时长"})
        else:
            remaining.append(issue)

    for track in data.get("tracks") or []:
        kept = []
        for seg in track.get("segments") or []:
            ref = _ref(seg)
            if ref in removable:
                continue
            target = seg.get("target_timerange") or {}
            if ref in clamp_start:
                target["start"] = 0
            if ref in clamp_volume:
                seg["volume"] = 4.0
            kept.append(seg)
        track["segments"] = kept

    if fix_canvas:
        canvas = data.get("canvas_config") or {}
        width = int(canvas.get("width") or 0)
        height = int(canvas.get("height") or 0)
        if width:
            canvas["width"] = width - (width % 2)
        if height:
            canvas["height"] = height - (height % 2)
        data["canvas_config"] = canvas
    if fix_duration:
        refresh_duration(data)

    return {"fixed": fixed, "remaining": remaining}


def _ref(seg: dict) -> str:
    import re

    return re.sub(r"[^0-9A-Za-z]", "", str(seg.get("id") or ""))[:8].upper()
