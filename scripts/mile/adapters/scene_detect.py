"""镜头/场景边界适配器：优先复用 PySceneDetect（T2 可选），缺失时降级 ffmpeg scene 信号（T1）。

只给出时间边界这一种信号级证据，不判断画面内容、语义或好坏；两种后端都缺失时
返回稳定错误码 ``SCENE_DETECT_UNAVAILABLE``，不伪造结果。
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from typing import Optional

from ..errors import MileError

__all__ = ["detect_scenes", "scene_backend"]


def _scenedetect_available() -> bool:
    try:
        return (
            importlib.util.find_spec("scenedetect") is not None
            and importlib.util.find_spec("cv2") is not None
        )
    except (ImportError, ValueError):
        return False


def scene_backend() -> str:
    """返回当前可用的镜头边界后端：``pyscenedetect`` / ``ffmpeg-scene-signal`` / ``none``。"""
    if _scenedetect_available():
        return "pyscenedetect"
    from utils.media_probe import find_ffmpeg

    if find_ffmpeg():
        return "ffmpeg-scene-signal"
    return "none"


def _media_duration(path: Path) -> Optional[float]:
    try:
        from utils.media_probe import probe_media_duration

        return probe_media_duration(path)
    except Exception:
        return None


def _detect_with_scenedetect(path: Path, threshold: float) -> list[float]:
    try:
        from scenedetect import detect
        from scenedetect.detectors import ContentDetector
    except Exception as exc:
        raise MileError("SCENE_DETECT_UNAVAILABLE", f"PySceneDetect 不可用: {exc}") from exc
    try:
        scene_list = detect(str(path), ContentDetector(threshold=threshold))
    except Exception as exc:
        raise MileError("SCENE_DETECT_FAILED", f"PySceneDetect 检测失败: {exc}") from exc
    boundaries: list[float] = []
    for _, end in scene_list[:-1]:
        boundaries.append(float(getattr(end, "get_seconds", lambda: 0.0)()))
    return boundaries


def _detect_with_ffmpeg(path: Path, threshold: float) -> list[float]:
    from utils.media_probe import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise MileError(
            "SCENE_DETECT_UNAVAILABLE",
            "未找到 PySceneDetect 也未找到 ffmpeg，无法检测镜头边界",
            install_hint="pip install scenedetect[opencv] 或设置 MILE_FFMPEG",
        )
    command = [
        ffmpeg,
        "-i",
        str(path),
        "-vf",
        f"select='gt(scene,{threshold})',metadata=print",
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MileError("SCENE_DETECT_FAILED", f"ffmpeg scene 检测失败: {exc}") from exc
    text = (completed.stderr or b"").decode("utf-8", "replace")
    boundaries: list[float] = []
    for line in text.splitlines():
        marker = "pts_time:"
        if marker not in line:
            continue
        rest = line.split(marker, 1)[1].strip()
        head = rest.split(" ", 1)[0].split("\t", 1)[0]
        try:
            boundaries.append(float(head))
        except ValueError:
            continue
    return boundaries


def detect_scenes(path, *, threshold: float = 0.4, min_scene_len_s: float = 0.1) -> dict:
    """检测源素材的镜头边界，返回按时间排序的场景区间。

    ``threshold`` 是场景切换灵敏度（ffmpeg scene score / ContentDetector 同义），
    常见可用范围 0.3-0.6；这不是内容理解，只是画面差异信号。
    """
    media = Path(str(path)).expanduser()
    if not media.exists():
        raise MileError("FILE_NOT_FOUND", f"媒体文件不存在: {media}")
    try:
        threshold_value = float(threshold)
    except (TypeError, ValueError) as exc:
        raise MileError("BAD_RANGE", f"threshold 必须是数字: {threshold}") from exc
    if not 0 < threshold_value < 1:
        raise MileError("BAD_RANGE", "threshold 需在 0-1 之间", value=threshold_value)

    backend = scene_backend()
    if backend == "none":
        raise MileError(
            "SCENE_DETECT_UNAVAILABLE",
            "没有可用的镜头边界后端（PySceneDetect 或 ffmpeg）",
            install_hint="pip install scenedetect[opencv]，或安装/指定 ffmpeg（MILE_FFMPEG）",
        )

    warnings: list[dict] = []
    if backend == "pyscenedetect":
        try:
            boundaries = _detect_with_scenedetect(media, threshold_value)
            method = "pyscenedetect"
        except MileError as exc:
            warnings.append({"code": exc.code, "reason": exc.reason})
            boundaries = _detect_with_ffmpeg(media, threshold_value)
            method = "ffmpeg-scene-signal"
    else:
        boundaries = _detect_with_ffmpeg(media, threshold_value)
        method = "ffmpeg-scene-signal"

    duration = _media_duration(media)
    edges = sorted({round(float(item), 3) for item in boundaries if item is not None and item > 0})
    if duration:
        edges = [item for item in edges if item < duration]

    scenes: list[dict] = []
    cursor = 0.0
    for edge in edges + ([duration] if duration else []):
        length = round(edge - cursor, 3)
        if length > 0:
            scenes.append({"start_s": round(cursor, 3), "end_s": round(edge, 3), "duration_s": length})
        cursor = edge
    if not duration and scenes:
        last = scenes[-1]
        warnings.append({"code": "DURATION_UNKNOWN", "reason": f"最后一个场景结束时间未知，起于 {last['start_s']}s"})
    elif not duration and not edges:
        warnings.append({"code": "DURATION_UNKNOWN", "reason": "素材时长与场景边界均无法确定"})

    filtered = [scene for scene in scenes if scene["duration_s"] >= min_scene_len_s or scene is scenes[0]]
    if len(filtered) != len(scenes):
        warnings.append(
            {"code": "SHORT_SCENES_MERGED", "reason": f"过滤了 {len(scenes) - len(filtered)} 个短于 {min_scene_len_s}s 的场景"}
        )

    return {
        "source": str(media),
        "method": method,
        "backend": backend,
        "available": True,
        "threshold": threshold_value,
        "duration_s": round(duration, 3) if duration else None,
        "count": len(filtered),
        "boundaries_s": edges,
        "scenes": filtered,
        "note": "镜头边界是画面差异信号，不代表内容变化；应用前请预览",
        "warnings": warnings,
    }
