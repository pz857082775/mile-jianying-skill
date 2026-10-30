"""按素材与记忆风格组装新草稿。

适用场景：已有镜头素材 + 文案/字幕，希望沿用历史项目的字号、颜色、
字幕位置、BGM 音量和转场习惯，快速生成一份可继续手动精修的剪映草稿。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Optional

from . import ensure_vendor_path
from .draft import drafts_root, load_draft
from .errors import MileError
from .memory import record_session
from .ops import parse_color, probe_duration, resolve_transition_type
from .timecode import US_PER_SEC, format_time

__all__ = ["assemble", "load_captions"]

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
DEFAULT_IMAGE_DURATION = 3 * US_PER_SEC


def _require_vendor():
    ensure_vendor_path()
    try:
        import pyJianYingDraft as draft  # noqa: F401
    except Exception as exc:
        raise MileError("VENDOR_MISSING", "无法加载 pyJianYingDraft", error=str(exc)) from exc
    return draft


def _parse_srt(text: str) -> list[dict]:
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip().lstrip("\ufeff"))
    captions = []
    time_re = re.compile(
        r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
    )
    from .timecode import parse_time

    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        match = None
        content_start = 0
        for index, line in enumerate(lines[:3]):
            match = time_re.search(line)
            if match:
                content_start = index + 1
                break
        if not match:
            continue
        caption_text = "\n".join(lines[content_start:]).strip()
        start = parse_time(match.group("start").replace(",", "."))
        end = parse_time(match.group("end").replace(",", "."))
        if caption_text and end > start:
            captions.append({"text": caption_text, "start": start / US_PER_SEC, "end": end / US_PER_SEC})
    return captions


def load_captions(source) -> list[dict]:
    """读取字幕：支持 SRT、JSON、TXT，``-`` 表示从标准输入读取。"""
    if isinstance(source, list):
        return source
    if str(source) == "-":
        text = sys.stdin.read().lstrip("\ufeff")
        if "-->" in text:
            return _parse_srt(text)
        return [{"text": line.strip()} for line in text.splitlines() if line.strip()]
    path = Path(str(source)).expanduser()
    if not path.exists():
        raise MileError("FILE_NOT_FOUND", f"字幕文件不存在: {path}")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".srt":
        return _parse_srt(text)
    if path.suffix.lower() in {".json", ".txt"}:
        if path.suffix.lower() == ".txt":
            return [{"text": line.strip()} for line in text.splitlines() if line.strip()]
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = payload.get("captions") or payload.get("subtitles") or payload.get("items")
        if not isinstance(payload, list):
            raise MileError("BAD_CAPTIONS", "字幕 JSON 必须是数组，或包含 captions/subtitles/items 数组")
        return payload
    raise MileError("BAD_CAPTIONS", f"不支持的字幕格式: {path.suffix}")


def _caption_timeline(captions: list[dict], total_duration: int) -> list[dict]:
    if not captions:
        return []
    items = []
    for caption in captions:
        text = str(caption.get("text") or caption.get("content") or "").strip()
        if not text:
            continue
        start = caption.get("start")
        end = caption.get("end")
        duration = caption.get("duration")
        items.append({"text": text, "start": start, "end": end, "duration": duration})

    if not items:
        return []
    auto = all(item["start"] is None for item in items)
    if auto:
        weights = [max(4, len(item["text"])) for item in items]
        total_weight = sum(weights)
        cursor = 0
        result = []
        for item, weight in zip(items, weights):
            duration = max(500_000, int(total_duration * weight / total_weight))
            result.append({"text": item["text"], "start": cursor, "duration": duration})
            cursor += duration
        if result:
            overflow = result[-1]["start"] + result[-1]["duration"] - total_duration
            if overflow > 0:
                result[-1]["duration"] = max(300_000, result[-1]["duration"] - overflow)
        return result

    from .timecode import parse_time

    result = []
    for index, item in enumerate(items):
        start = parse_time(item["start"]) if item["start"] is not None else 0
        if item["duration"] is not None:
            duration = parse_time(item["duration"])
        elif item["end"] is not None:
            duration = parse_time(item["end"]) - start
        elif index + 1 < len(items) and items[index + 1]["start"] is not None:
            duration = parse_time(items[index + 1]["start"]) - start
        else:
            duration = 2 * US_PER_SEC
        duration = max(300_000, duration)
        if total_duration and start >= total_duration:
            continue
        if total_duration:
            duration = min(duration, total_duration - start)
        result.append({"text": item["text"], "start": start, "duration": duration})
    return result


def _normalize_clips(clips) -> list[dict]:
    if isinstance(clips, (str, Path)):
        clips = [clips]
    if not isinstance(clips, (list, tuple)) or not clips:
        raise MileError("MISSING_ARGS", "至少需要一个视频素材")
    result = []
    for item in clips:
        if isinstance(item, (str, Path)):
            result.append({"path": str(item)})
        elif isinstance(item, dict):
            if not item.get("path"):
                raise MileError("BAD_CLIPS", "素材对象缺少 path")
            result.append(dict(item))
        else:
            raise MileError("BAD_CLIPS", f"无法识别的素材: {item!r}")
    return result


def assemble(
    project: str,
    clips,
    *,
    root: Optional[str] = None,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    captions=None,
    bgm=None,
    bgm_volume: float = 0.25,
    clip_duration=None,
    transition: Optional[str] = None,
    transition_duration=None,
    style: Optional[dict] = None,
    remember: bool = True,
    intent: Optional[str] = None,
    tags=None,
    notes: Optional[str] = None,
    allow_replace: bool = False,
    dry_run: bool = False,
) -> dict:
    """把镜头素材组装成草稿；``style`` 通常来自 ``memory.recommend_style``。"""
    project = str(project or "").strip()
    if not project:
        raise MileError("MISSING_ARGS", "缺少草稿名称")
    clips = _normalize_clips(clips)
    width, height, fps = int(width), int(height), int(fps)
    if width <= 0 or height <= 0 or fps <= 0:
        raise MileError("BAD_ARGS", "画布尺寸和帧率必须为正数")

    jy = _require_vendor()
    from pyJianYingDraft import (
        AudioMaterial,
        AudioSegment,
        ClipSettings,
        DraftFolder,
        TextBorder,
        TextSegment,
        TextStyle,
        TrackType,
        VideoMaterial,
        VideoSegment,
    )

    total_duration = 0
    planned = []
    cursor = 0
    for item in clips:
        path = Path(str(item["path"])).expanduser()
        if not path.exists():
            raise MileError("MEDIA_NOT_FOUND", f"素材不存在: {path}")
        duration = None
        if item.get("duration") is not None:
            from .timecode import parse_time

            duration = parse_time(item["duration"])
        if duration is None and clip_duration is not None:
            from .timecode import parse_time

            duration = parse_time(clip_duration)
        if duration is None:
            duration = probe_duration(path)
        if duration is None and path.suffix.lower() in IMAGE_SUFFIXES:
            duration = DEFAULT_IMAGE_DURATION
        if duration is None and not dry_run:
            raise MileError(
                "DURATION_UNKNOWN",
                f"无法读取素材时长: {path}（安装 ffmpeg/ffprobe，或用 --clip-duration 指定统一入片时长）",
            )
        start = item.get("start")
        if start is not None:
            from .timecode import parse_time

            cursor = parse_time(start)
        planned.append(
            {
                "path": str(path),
                "start": cursor,
                "duration": duration,
                "source_start": item.get("source_start", 0),
                "volume": item.get("volume", 1.0),
            }
        )
        if duration:
            cursor += duration
            total_duration = max(total_duration, cursor)
        else:
            total_duration = max(total_duration, cursor)

    captions_list = _caption_timeline(load_captions(captions), total_duration) if captions else []
    caption_style = (style or {}).get("caption") or {}
    font_size = float(caption_style.get("font_size") or 8.0)
    caption_color = caption_style.get("color") or "#ffffff"
    alignment = int(caption_style.get("alignment") if caption_style.get("alignment") is not None else 1)
    transform_y = float(caption_style.get("transform_y") if caption_style.get("transform_y") is not None else -0.8)
    bold = bool((caption_style.get("bold_ratio") or 0) >= 0.5)
    border_needed = bool((caption_style.get("border_ratio") or 0) >= 0.5)

    if dry_run:
        return {
            "dry_run": True,
            "project": project,
            "clips": planned,
            "total_duration": total_duration,
            "duration_label": format_time(total_duration, "clock"),
            "captions": captions_list,
            "caption_style": {
                "font_size": font_size,
                "color": caption_color,
                "alignment": alignment,
                "transform_y": transform_y,
                "bold": bold,
                "border": border_needed,
            },
            "bgm": str(bgm) if bgm else None,
        }

    folder_root = Path(root).expanduser() if root else drafts_root()
    folder_root.mkdir(parents=True, exist_ok=True)
    draft_folder = DraftFolder(str(folder_root))
    target_folder = folder_root / project
    if target_folder.exists() and not allow_replace:
        raise MileError("DRAFT_EXISTS", f"草稿已存在: {target_folder}（如需覆盖请传 allow_replace=true）")

    try:
        script = draft_folder.create_draft(project, width, height, fps, allow_replace=allow_replace)
    except FileExistsError as exc:
        raise MileError("DRAFT_EXISTS", f"草稿已存在: {project}") from exc

    from .timecode import parse_time

    video_track = "mile_main_video"
    script.add_track(TrackType.video, video_track)
    transition_type = resolve_transition_type(transition) if transition else None
    cursor = 0
    segments = []
    for item in planned:
        material = VideoMaterial(
            item["path"],
            duration=item["duration"],
        )
        duration = item["duration"] or material.duration
        if not duration or duration <= 0:
            raise MileError("MEDIA_INVALID", f"无法读取素材时长: {item['path']}")
        if material.material_type == "video" and duration > material.duration:
            duration = material.duration
        start = item["start"]
        seg = VideoSegment(
            material,
            jy.trange(start, duration),
            source_timerange=jy.trange(parse_time(item["source_start"]), duration),
            volume=float(item.get("volume", 1.0)),
        )
        segments.append(seg)
        cursor = max(cursor, start + duration)

    for index, seg in enumerate(segments):
        if transition_type and index < len(segments) - 1:
            limit = min(seg.target_timerange.duration, segments[index + 1].target_timerange.duration)
            seg_transition_duration = transition_duration
            if seg_transition_duration is not None:
                from .timecode import parse_time as _parse

                seg_transition_duration = min(_parse(seg_transition_duration), limit)
            seg.add_transition(transition_type, duration=seg_transition_duration)
        script.add_segment(seg, video_track)

    if captions_list:
        text_track = "mile_captions"
        script.add_track(TrackType.text, text_track)
        rgb, _ = parse_color(caption_color)
        for caption in captions_list:
            text_style = TextStyle(size=font_size, color=rgb, align=alignment, bold=bold)
            seg = TextSegment(
                caption["text"],
                jy.trange(caption["start"], caption["duration"]),
                style=text_style,
                clip_settings=ClipSettings(transform_y=transform_y),
                border=TextBorder(width=40.0) if border_needed else None,
            )
            script.add_segment(seg, text_track)

    bgm_path = None
    if isinstance(bgm, dict):
        bgm_path = bgm.get("path")
        bgm_volume = bgm.get("volume", bgm_volume)
        bgm_start = bgm.get("start", 0)
        bgm_duration = bgm.get("duration")
    else:
        bgm_path = bgm
        bgm_start = 0
        bgm_duration = None
    if bgm_path:
        bgm_file = Path(str(bgm_path)).expanduser()
        if not bgm_file.exists():
            raise MileError("MEDIA_NOT_FOUND", f"BGM 不存在: {bgm_file}")
        try:
            bgm_material = AudioMaterial(str(bgm_file))
        except Exception as exc:
            raise MileError(
                "MEDIA_UNSUPPORTED",
                f"无法读取 BGM 时长，请安装 ffmpeg/ffprobe: {bgm_file}",
                error=str(exc),
            ) from exc
        target_duration = parse_time(bgm_duration) if bgm_duration is not None else min(bgm_material.duration, cursor or bgm_material.duration)
        audio_track = "mile_bgm"
        script.add_track(TrackType.audio, audio_track)
        audio_seg = AudioSegment(
            bgm_material,
            jy.trange(parse_time(bgm_start), target_duration),
            volume=float(bgm_volume),
        )
        script.add_segment(audio_seg, audio_track)

    script.save()

    loaded = load_draft(project, root=folder_root)
    summary = {
        "project": project,
        "folder": str(loaded.folder),
        "duration": int(loaded.data.get("duration") or 0),
        "duration_label": format_time(int(loaded.data.get("duration") or 0), "clock"),
        "clip_count": len(planned),
        "caption_count": len(captions_list),
        "bgm": str(bgm_path) if bgm_path else None,
        "transition": transition,
    }
    memory_id = None
    if remember:
        try:
            record = record_session(
                project=project,
                draft_path=str(loaded.folder),
                context={
                    "intent": intent or "按素材与历史风格自动组装",
                    "tags": list(tags) if tags else ["assemble"],
                    "notes": notes,
                    "assembled_from": [Path(item["path"]).name for item in planned],
                    "caption_style": {
                        "font_size": font_size,
                        "color": caption_color,
                        "alignment": alignment,
                        "transform_y": transform_y,
                    },
                },
                outcome="assembled",
                draft=loaded,
            )
            memory_id = record.get("id")
        except Exception:
            memory_id = None
    summary["memory_id"] = memory_id
    return summary
