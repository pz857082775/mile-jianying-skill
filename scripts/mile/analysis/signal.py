"""T0/T1 媒体信号分析器（ffmpeg + numpy，纯信号规则，不做语义理解）。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from ..errors import MileError
from ..recommend import analyze_audio
from ..timecode import format_time
from .registry import capability_status

__all__ = ["analyze_media", "analyze_timeline_signals"]


def _ffmpeg_detect(path: Path, filter_name: str) -> list[dict]:
    """调用 ffmpeg 的 blackdetect/freezedetect，返回时间区间列表。"""
    from utils.media_probe import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise MileError("FFMPEG_MISSING", "未找到 ffmpeg，无法做画面信号检测")
    command = [
        ffmpeg,
        "-v",
        "error",
        "-i",
        str(path),
        "-vf",
        filter_name,
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MileError("AUDIO_DECODE_FAILED", f"ffmpeg 检测失败: {exc}") from exc
    text = (completed.stderr or b"").decode("utf-8", "replace")
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if filter_name == "blackdetect" and "black_start" in line:
            events.append(_parse_kv_event(line, ["black_start", "black_end", "black_duration"]))
        elif filter_name == "freezedetect" and "lavfi.freeze_start" in line:
            events.append(_parse_kv_event(line, ["lavfi.freeze_start", "lavfi.freeze_end", "lavfi.freeze_duration"]))
    return events


def _parse_kv_event(line: str, keys: list[str]) -> dict:
    """解析 ffmpeg 滤镜输出，兼容 ``key=1.5`` 与 ``key:1.5`` / ``key: 1.5`` 两种写法。"""
    result: dict = {}
    for key in keys:
        index = -1
        marker_len = 0
        for sep in ("=", ":"):
            found = line.find(f"{key}{sep}")
            if found >= 0 and (index < 0 or found < index):
                index = found
                marker_len = len(key) + 1
        if index < 0:
            continue
        rest = line[index + marker_len :].lstrip()
        end = len(rest)
        for sep in (" ", "\t"):
            position = rest.find(sep)
            if position >= 0:
                end = min(end, position)
        try:
            result[key] = float(rest[:end])
        except (TypeError, ValueError):
            result[key] = None
    return result


_AUDIO_SUFFIXES = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".wma")


def analyze_media(path, *, analyze_audio_flag: bool = True, analyze_scenes: bool = False, timeout_audio: int = 300) -> dict:
    """对一个媒体文件做可用的 T0/T1 分析，缺依赖时返回 available=false + method。

    不渲染不转码，不加载模型；输出全是信号级证据。``analyze_scenes`` 额外给出
    镜头边界（优先 PySceneDetect，缺失时降级 ffmpeg scene 信号），需要完整解码一遍。
    """
    media = Path(str(path)).expanduser()
    if not media.exists():
        raise MileError("FILE_NOT_FOUND", f"媒体文件不存在: {media}")
    capabilities = capability_status()
    result: dict = {
        "source": str(media),
        "method": "signal",
        "available": True,
        "capabilities": capabilities["signal"]["available"],
        "dependencies": capabilities["signal"]["dependencies"],
        "audio": None,
        "video": None,
        "warnings": [],
    }
    audio_analysis = None
    if analyze_audio_flag:
        try:
            audio_analysis = analyze_audio(media)
        except MileError as exc:
            result["warnings"].append({"code": exc.code, "reason": exc.reason})
            audio_analysis = {"available": False, "code": exc.code, "reason": exc.reason}
        if isinstance(audio_analysis, dict):
            audio_analysis["loudness"] = _safe_loudness(media, result["warnings"])
    result["audio"] = audio_analysis

    try:
        from utils.media_probe import probe_media_duration

        duration = probe_media_duration(media)
        result["duration_s"] = round(duration, 3) if duration else None
    except Exception as exc:
        result["warnings"].append({"code": "PROBE_FAILED", "reason": str(exc)})

    if media.suffix.lower() in _AUDIO_SUFFIXES:
        return result

    video_signals: dict = {}
    video_signals["black_frames"] = _safe_ffmpeg_events(media, "blackdetect", result["warnings"])
    video_signals["freeze_frames"] = _safe_ffmpeg_events(media, "freezedetect", result["warnings"])
    if analyze_scenes:
        video_signals["scenes"] = _safe_detect_scenes(media, result["warnings"])
    if video_signals["black_frames"] or video_signals["freeze_frames"] or analyze_scenes:
        result["video"] = video_signals
    return result


def _safe_detect_scenes(media: Path, warnings: list) -> dict:
    from ..adapters.scene_detect import detect_scenes

    try:
        return detect_scenes(media)
    except MileError as exc:
        warnings.append({"code": exc.code, "reason": exc.reason})
        return {"available": False, "method": "none", "count": 0, "scenes": []}


def _safe_loudness(media: Path, warnings: list) -> dict:
    from ..adapters.loudness import measure_loudness

    try:
        return measure_loudness(media)
    except MileError as exc:
        warnings.append({"code": exc.code, "reason": exc.reason})
        return {"available": False, "method": "none"}


def _safe_ffmpeg_events(media: Path, filter_name: str, warnings: list) -> list:
    try:
        return _ffmpeg_detect(media, filter_name)
    except MileError as exc:
        warnings.append({"code": exc.code, "reason": exc.reason})
        return []


def _upper_boundary(start: int, end: int) -> Optional[int]:
    if start is None or end is None:
        return None
    return float(start), float(end)


def _segment_quality(main_segments: list[dict], fps: float) -> list[dict]:
    interpretations = []
    for seg in main_segments:
        duration_s = seg.get("duration", 0) / 1_000_000
        start = seg.get("start", 0)
        entry = {
            "ref": seg.get("ref"),
            "start_s": round(start / 1_000_000, 2),
            "end_s": round((start + seg.get("duration", 0)) / 1_000_000, 2),
            "duration_s": round(duration_s, 2),
            "issues": [],
        }
        if duration_s <= 0.4:
            entry["issues"].append("too_short")
        elif duration_s >= 12:
            entry["issues"].append("too_long")
        interpretations.append(entry)
    return interpretations


def analyze_timeline_signals(timeline: dict, *, media_probe: bool = True) -> dict:
    """基于已有的时间轴模型给出信号级观察，不加载任何模型。

    - 主轨片段时长分布（过短/过长）
    - 字幕与语音覆盖关系
    - BGM 音量与留白
    - 可选：调用 analyze_media 探测媒体文件
    """
    segments = timeline.get("segments") or []
    main_segments = [s for s in segments if s.get("role") == "main"]
    captions = [s for s in segments if s.get("role") == "caption"]
    bgm = [s for s in segments if s.get("role") == "bgm"]
    voice = [s for s in segments if s.get("role") == "voice"]
    fps = float(timeline.get("fps") or 30)

    result: dict = {
        "method": "timeline-signal-rules",
        "available": True,
        "captions": {"count": len(captions)},
        "main": _segment_quality(main_segments, fps),
    }
    if voice:
        result["voice"] = {"count": len(voice)}
    if bgm:
        volumes = [s.get("volume") for s in bgm if isinstance(s.get("volume"), (int, float))]
        result["bgm"] = {
            "count": len(bgm),
            "volume_high": bool(volumes and max(volumes) > 4),
            "min_volume": round(min(volumes), 3) if volumes else None,
            "max_volume": round(max(volumes), 3) if volumes else None,
        }
    min_duration = min((seg["duration"] for seg in main_segments), default=0)
    result["min_main_duration_s"] = round(min_duration / 1_000_000, 2)
    return result