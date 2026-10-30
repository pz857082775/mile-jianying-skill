"""导出产物的定位与校验。

剪映 5.9 的导出面板不会稳定返回最终文件路径：实际文件可能落在上次
选择的目录，并在重名时自动追加 ``(1)``/``(2)``。本模块负责在导出完成后
从候选目录中找出真实产物，并做存在性、大小与 ffprobe/ffmpeg 可读性校验。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Iterable, Optional

from utils.media_probe import find_ffprobe, probe_with_ffmpeg

__all__ = [
    "normalize_export_path",
    "discover_exported_file",
    "validate_exported_file",
]

_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
_MAX_SCAN_ENTRIES = 2000


def normalize_export_path(value: object) -> Optional[Path]:
    """清理 UI 读取到的路径：去引号、展开环境变量与 ``~``。"""
    if value is None:
        return None
    text = str(value).strip().strip('"').strip("'")
    if not text or "\x00" in text:
        return None
    return Path(os.path.expandvars(os.path.expanduser(text)))


def _default_export_dirs() -> list[Path]:
    home = Path.home()
    return [
        home / "Videos" / "JianyingPro",
        home / "Videos",
        home / "Desktop",
        home / "Documents",
    ]


def _unique_dirs(items: Iterable[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in items:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _search_roots(
    candidate: Optional[Path], search_dirs: Optional[Iterable[object]]
) -> tuple[list[Path], list[Path]]:
    """返回 (优先深扫目录, 普通兜底目录)。

    剪映 5.9 会把成片放进与文件名同名的文件夹，因此候选路径的父目录
    与“去掉扩展名后的同名目录”都需要向下扫描一层。
    """
    explicit: list[Path] = []
    if candidate is not None:
        explicit.append(candidate if candidate.is_dir() else candidate.parent)
        if candidate.suffix.lower() in _VIDEO_SUFFIXES:
            explicit.append(candidate.with_suffix(""))
        else:
            explicit.append(candidate)
    for item in search_dirs or []:
        path = Path(item).expanduser()
        explicit.append(path.parent if path.is_file() else path)
    fallbacks = _default_export_dirs()
    return _unique_dirs(explicit), _unique_dirs(fallbacks)


def _file_is_candidate(path: Path, since: float) -> bool:
    if not path.is_file() or path.suffix.lower() not in _VIDEO_SUFFIXES:
        return False
    try:
        stat = path.stat()
    except OSError:
        return False
    return stat.st_size > 0 and stat.st_mtime >= since


def _iter_recent_videos(
    directory: Path, since: float, *, deep: bool = False, name_prefix: str = ""
) -> Iterable[Path]:
    if not directory.is_dir():
        return
    try:
        entries = list(directory.iterdir())
    except OSError:
        return
    budget = _MAX_SCAN_ENTRIES
    for entry in entries:
        if budget <= 0:
            break
        if entry.is_file():
            budget -= 1
            if _file_is_candidate(entry, since):
                yield entry
            continue
        if not deep or not entry.is_dir():
            continue
        if name_prefix and not entry.name.lower().startswith(name_prefix):
            continue
        try:
            children = list(entry.iterdir())
        except OSError:
            continue
        for child in children:
            if budget <= 0:
                break
            budget -= 1
            if _file_is_candidate(child, since):
                yield child


def discover_exported_file(
    draft_name: str,
    started_at: float,
    candidate: object = None,
    search_dirs: Optional[Iterable[object]] = None,
    *,
    age_slack: float = 120.0,
) -> Optional[Path]:
    """在候选目录中寻找本次导出的视频文件。

    优先返回 ``candidate`` 本身（存在且是本次生成），否则扫描候选目录、
    传入目录与常见导出目录，按“文件名匹配草稿名 + 修改时间最新”排序。
    """
    since = float(started_at) - max(0.0, float(age_slack))
    candidate_path = normalize_export_path(candidate)

    if candidate_path is not None and candidate_path.is_file():
        try:
            stat = candidate_path.stat()
        except OSError:
            stat = None
        if stat is not None and stat.st_size > 0 and stat.st_mtime >= since:
            return candidate_path

    needle = str(draft_name).strip().lower()
    explicit_dirs, fallback_dirs = _search_roots(candidate_path, search_dirs)
    hits: list[Path] = []
    seen: set[str] = set()
    for directory in explicit_dirs:
        for path in _iter_recent_videos(directory, since, deep=True, name_prefix=needle):
            try:
                key = str(path.resolve()).lower()
            except OSError:
                key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(path)
    for directory in fallback_dirs:
        for path in _iter_recent_videos(directory, since):
            try:
                key = str(path.resolve()).lower()
            except OSError:
                key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            hits.append(path)

    if not hits:
        return None

    def rank(path: Path) -> tuple[int, float, int]:
        stem = path.stem.lower()
        name_match = 1 if stem == needle or stem.startswith(needle) else 0
        try:
            stat = path.stat()
            return (name_match, stat.st_mtime, stat.st_size)
        except OSError:
            return (name_match, 0.0, 0)

    hits.sort(key=rank, reverse=True)
    return hits[0]


def validate_exported_file(path: object, *, probe: bool = True) -> dict:
    """校验导出文件存在、非空，并尽可能用 ffprobe 验证可读性。"""
    target = normalize_export_path(path)
    result: dict = {
        "path": str(target) if target is not None else "",
        "exists": False,
        "size": 0,
        "ok": False,
        "method": "missing",
    }
    if target is None or not target.is_file():
        return result

    try:
        size = target.stat().st_size
    except OSError as exc:
        result["error"] = str(exc)
        return result

    result.update({"exists": True, "size": size, "method": "size"})
    if size <= 0:
        result["error"] = "导出文件为空"
        return result
    result["ok"] = True

    if not probe:
        return result

    ffprobe = find_ffprobe()
    if ffprobe:
        command = [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height:format=duration",
            "-of",
            "json",
            str(target),
        ]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            result["probe"] = "error"
            result["probe_error"] = str(exc)
            return result

        if completed.returncode != 0:
            result["ok"] = False
            result["method"] = "ffprobe"
            result["probe"] = "failed"
            result["error"] = (completed.stderr or "ffprobe 无法读取导出文件").strip()[:500]
            return result

        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError as exc:
            result["probe"] = "error"
            result["probe_error"] = str(exc)
            return result

        streams = payload.get("streams") or []
        stream = streams[0] if streams else {}
        fmt = payload.get("format") or {}
        result.update(
            {
                "method": "ffprobe",
                "probe": "ok",
                "codec": stream.get("codec_name"),
                "width": stream.get("width"),
                "height": stream.get("height"),
                "duration": float(fmt.get("duration") or 0),
            }
        )
        return result

    ffmpeg_info = probe_with_ffmpeg(target, decode_video=True)
    if ffmpeg_info is None:
        result["probe"] = "skipped"
        return result
    if not ffmpeg_info.get("ok"):
        result["ok"] = False
        result["method"] = "ffmpeg"
        result["probe"] = "failed"
        result["error"] = ffmpeg_info.get("error") or "ffmpeg 无法解码导出文件"
        return result
    result.update({"method": "ffmpeg", "probe": "ok"})
    for key in ("codec", "width", "height", "duration", "fps"):
        if ffmpeg_info.get(key) is not None:
            result[key] = ffmpeg_info[key]
    return result
