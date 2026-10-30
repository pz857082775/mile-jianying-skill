"""内置资产与图片的轻量解析、尺寸探测和可解码性校验。

不依赖 PIL：PNG/JPEG 用标准库直接解析，其他格式回退 PIL 或 ffprobe。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
from typing import Optional

__all__ = [
    "IMAGE_SUFFIXES",
    "MEDIA_SUFFIXES",
    "find_ffprobe",
    "find_ffmpeg",
    "parse_ffmpeg_metadata",
    "probe_with_ffmpeg",
    "probe_media_duration",
    "image_size",
    "validate_image_file",
    "probe_media_file",
    "check_assets",
]

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".aac", ".flac"}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def find_ffprobe() -> Optional[str]:
    """按 ``MILE_FFPROBE`` / ``JY_FFPROBE`` / PATH / ffmpeg 同目录的顺序查找。"""
    for key in ("MILE_FFPROBE", "JY_FFPROBE"):
        value = os.environ.get(key)
        if value and Path(value).expanduser().is_file():
            return str(Path(value).expanduser())
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if sibling.is_file():
            return str(sibling)
    return None


def _install_roots() -> list[Path]:
    roots: list[Path] = []
    for key in ("JIANYING_HOME", "JY_JIANYING_HOME", "MILE_JIANYING_HOME"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value).expanduser())
    local = os.environ.get("LOCALAPPDATA")
    if local:
        roots.append(Path(local) / "JianyingPro")
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        value = os.environ.get(key)
        if value:
            roots.append(Path(value) / "JianyingPro")
    return roots


def _walk_install_dir(root: Path, max_depth: int = 3):
    if not root.is_dir():
        return
    base_depth = len(root.parts)
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        dirs[:] = [d for d in dirs if d.lower() not in {"user data", "cache", "logs"}]
        if any(name.lower() == "ffmpeg.exe" for name in files):
            yield current_path / "ffmpeg.exe"
            return
        if len(current_path.parts) - base_depth >= max_depth:
            dirs[:] = []


def _running_process_path(exe_name: str) -> Optional[Path]:
    """返回正在运行进程的可执行文件路径（Windows，仅标准库）。"""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return None

    class ProcessEntry32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32FirstW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32W),
    ]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessEntry32W),
    ]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot in (None, 0, ctypes.c_void_p(-1).value):
        return None
    entry = ProcessEntry32W()
    entry.dwSize = ctypes.sizeof(ProcessEntry32W)
    try:
        if not kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return None
        while True:
            if entry.szExeFile.lower() == exe_name.lower():
                handle = kernel32.OpenProcess(0x1000, False, entry.th32ProcessID)
                if handle:
                    try:
                        size = wintypes.DWORD(32768)
                        buffer = ctypes.create_unicode_buffer(size.value)
                        if kernel32.QueryFullProcessImageNameW(
                            handle, 0, buffer, ctypes.byref(size)
                        ):
                            return Path(buffer.value)
                    finally:
                        kernel32.CloseHandle(handle)
            if not kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
        return None
    finally:
        kernel32.CloseHandle(snapshot)


def find_ffmpeg() -> Optional[str]:
    """按 ``MILE_FFMPEG`` / ``JY_FFMPEG`` / PATH / 剪映安装目录的顺序查找。"""
    for key in ("MILE_FFMPEG", "JY_FFMPEG"):
        value = os.environ.get(key)
        if value and Path(value).expanduser().is_file():
            return str(Path(value).expanduser())
    found = shutil.which("ffmpeg")
    if found:
        return found
    ffprobe = shutil.which("ffprobe")
    if ffprobe:
        sibling = Path(ffprobe).with_name("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if sibling.is_file():
            return str(sibling)
    for root in _install_roots():
        candidate = next(_walk_install_dir(root), None)
        if candidate is not None and candidate.is_file():
            return str(candidate)
    running = _running_process_path("JianyingPro.exe")
    if running is not None:
        candidate = next(_walk_install_dir(running.parent, max_depth=1), None)
        if candidate is not None and candidate.is_file():
            return str(candidate)
    return None


def _png_info(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("PNG 签名无效")
    position = 8
    width = height = 0
    idat: list[bytes] = []
    found_ihdr = False
    while position + 8 <= len(data):
        length = struct.unpack(">I", data[position : position + 4])[0]
        chunk_type = data[position + 4 : position + 8]
        start = position + 8
        end = start + length
        if end + 4 > len(data):
            raise ValueError("PNG 数据块被截断")
        chunk = data[start:end]
        if chunk_type == b"IHDR":
            if length < 8:
                raise ValueError("IHDR 长度无效")
            width, height = struct.unpack(">II", chunk[:8])
            found_ihdr = True
        elif chunk_type == b"IDAT":
            idat.append(chunk)
        elif chunk_type == b"IEND":
            break
        position = end + 4
    if not found_ihdr or width <= 0 or height <= 0:
        raise ValueError("PNG 缺少有效 IHDR")
    if not idat:
        raise ValueError("PNG 缺少 IDAT 图像数据")
    try:
        zlib.decompress(b"".join(idat))
    except zlib.error as exc:
        raise ValueError(f"PNG 图像数据损坏: {exc}") from exc
    return width, height


def _jpeg_info(path: Path) -> tuple[Optional[int], Optional[int]]:
    data = path.read_bytes()
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("JPEG 缺少 SOI 标记")
    if not data.rstrip(b"\x00").endswith(b"\xff\xd9"):
        raise ValueError("JPEG 数据不完整（缺少 EOI）")
    position = 2
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        marker = data[position + 1]
        if marker in {0xD8, 0xD9}:
            position += 2
            continue
        if marker == 0xDA:
            break
        length = struct.unpack(">H", data[position + 2 : position + 4])[0]
        if length < 2:
            raise ValueError("JPEG 段长度无效")
        if marker in _JPEG_SOF_MARKERS and position + 9 <= len(data):
            height, width = struct.unpack(">HH", data[position + 5 : position + 9])
            return width, height
        position += 2 + length
    return None, None


def _gif_info(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 10 or data[:6] not in {b"GIF87a", b"GIF89a"}:
        raise ValueError("GIF 文件头无效")
    width, height = struct.unpack("<HH", data[6:10])
    if width <= 0 or height <= 0:
        raise ValueError("GIF 尺寸无效")
    return width, height


def _bmp_info(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 26 or not data.startswith(b"BM"):
        raise ValueError("BMP 文件头无效")
    width, height = struct.unpack("<ii", data[18:26])
    if width <= 0 or height == 0:
        raise ValueError("BMP 尺寸无效")
    return width, abs(height)


def _webp_info(path: Path) -> tuple[Optional[int], Optional[int]]:
    data = path.read_bytes()
    if len(data) < 16 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ValueError("WebP 文件头无效")
    return None, None


def _pil_info(path: Path) -> tuple[int, int]:
    from PIL import Image  # type: ignore

    with Image.open(path) as image:
        image.verify()
    with Image.open(path) as image:
        return int(image.width), int(image.height)


def _ffprobe_info(path: Path) -> Optional[dict]:
    ffprobe = find_ffprobe()
    if not ffprobe:
        return None
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
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return {"ok": False, "error": (completed.stderr or "ffprobe 读取失败").strip()[:300]}
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        return None
    streams = payload.get("streams") or []
    stream = streams[0] if streams else {}
    fmt = payload.get("format") or {}
    return {
        "ok": True,
        "width": stream.get("width"),
        "height": stream.get("height"),
        "codec": stream.get("codec_name"),
        "duration": float(fmt.get("duration") or 0),
    }


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_VIDEO_STREAM_RE = re.compile(
    r"Stream #\d+:\d+.*?Video:\s*([^\s,(]+).*?(\d{2,5})x(\d{2,5})"
)
_FPS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*fps\b")


def parse_ffmpeg_metadata(text: str) -> dict:
    """从 ffmpeg 的 stderr 中提取时长、编码、分辨率和帧率。"""
    info: dict = {}
    match = _DURATION_RE.search(text or "")
    if match:
        hours, minutes, seconds = match.groups()
        info["duration"] = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    match = _VIDEO_STREAM_RE.search(text or "")
    if match:
        info["codec"] = match.group(1)
        info["width"] = int(match.group(2))
        info["height"] = int(match.group(3))
    match = _FPS_RE.search(text or "")
    if match:
        info["fps"] = float(match.group(1))
    return info


def _ffmpeg_info(path: Path, *, decode_video: bool = False) -> Optional[dict]:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    command = [ffmpeg, "-hide_banner", "-nostats", "-i", str(path)]
    if decode_video:
        command += ["-map", "0:v:0", "-frames:v", "1", "-f", "null", "-"]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=60 if decode_video else 20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "method": "ffmpeg", "error": str(exc)}
    output = f"{completed.stderr or ''}\n{completed.stdout or ''}"
    info = parse_ffmpeg_metadata(output)
    if decode_video and completed.returncode != 0:
        return {
            "ok": False,
            "method": "ffmpeg",
            "error": output.strip()[-500:] or "ffmpeg 无法解码文件",
        }
    if not info:
        if completed.returncode != 0:
            return {
                "ok": False,
                "method": "ffmpeg",
                "error": output.strip()[-500:] or "ffmpeg 无法读取文件",
            }
        return None
    return {"ok": True, "method": "ffmpeg", **info}


def probe_with_ffmpeg(path: object, *, decode_video: bool = False) -> Optional[dict]:
    """仅用 ffmpeg 探测媒体元数据；``decode_video`` 时额外解码一帧。"""
    return _ffmpeg_info(Path(path), decode_video=decode_video)


def probe_media_duration(path: object) -> Optional[float]:
    """返回媒体时长（秒），优先 ffprobe，缺失时回退 ffmpeg。"""
    target = Path(path)
    if not target.is_file():
        return None
    for info in (_ffprobe_info(target), _ffmpeg_info(target)):
        if info and info.get("ok"):
            duration = info.get("duration")
            if duration and float(duration) > 0:
                return float(duration)
    return None


def _image_format(header: bytes) -> Optional[str]:
    if header.startswith(_PNG_SIGNATURE):
        return "png"
    if header.startswith(b"\xff\xd8"):
        return "jpeg"
    if header[:6] in {b"GIF87a", b"GIF89a"}:
        return "gif"
    if header.startswith(b"BM"):
        return "bmp"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "webp"
    return None


def validate_image_file(path: object) -> dict:
    """校验图片能否被解码；按文件头判断格式，不信任扩展名。"""
    target = Path(path)
    result: dict = {"path": str(target), "ok": False}
    fmt: Optional[str] = None
    try:
        with open(target, "rb") as handle:
            header = handle.read(16)
        fmt = _image_format(header)
        if fmt == "png":
            width, height = _png_info(target)
        elif fmt == "jpeg":
            width, height = _jpeg_info(target)
        elif fmt == "gif":
            width, height = _gif_info(target)
        elif fmt == "bmp":
            width, height = _bmp_info(target)
        elif fmt == "webp":
            width, height = _webp_info(target)
        else:
            width, height = None, None
            try:
                width, height = _pil_info(target)
            except Exception:
                info = _ffprobe_info(target) or _ffmpeg_info(target)
                if info is None:
                    result["error"] = "无法识别图片格式"
                    return result
                if not info.get("ok"):
                    result["error"] = info.get("error") or "图片解码失败"
                    return result
                width, height = info.get("width"), info.get("height")
        result.update({"ok": True, "width": width, "height": height})
        return result
    except Exception as exc:
        result["error"] = str(exc)
        if fmt is not None:
            # 已知格式解析失败即为损坏；ffmpeg 能读元数据不代表图片完好
            return result
        info = _ffprobe_info(target) or _ffmpeg_info(target)
        if info and info.get("ok"):
            result.update(
                {
                    "ok": True,
                    "width": info.get("width"),
                    "height": info.get("height"),
                    "warning": f"标准库解析失败，已用 ffprobe/ffmpeg 兜底: {exc}",
                }
            )
        return result


def image_size(path: object) -> Optional[tuple[int, int]]:
    info = validate_image_file(path)
    if not info.get("ok"):
        return None
    width, height = info.get("width"), info.get("height")
    if not width or not height:
        return None
    return int(width), int(height)


def probe_media_file(path: object) -> dict:
    """探测视频/音频元数据；ffprobe 缺失时回退 ffmpeg，其报错不被掩盖。"""
    target = Path(path)
    info = _ffprobe_info(target)
    if info is None:
        info = _ffmpeg_info(target)
    if info is None:
        return {"path": str(target), "ok": None, "skipped": True}
    result = {"path": str(target), **info}
    return result


def check_assets(assets_dir: object, *, limit: int = 300) -> dict:
    """批量校验资产目录中的图片与音视频文件。"""
    root = Path(assets_dir)
    result: dict = {
        "root": str(root),
        "ok": False,
        "checked": 0,
        "bad": [],
        "skipped": [],
    }
    if not root.is_dir():
        result["error"] = "资产目录不存在"
        return result

    checked = 0
    for path in sorted(root.rglob("*")):
        if checked >= limit:
            break
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in IMAGE_SUFFIXES and suffix not in MEDIA_SUFFIXES:
            continue
        if suffix in IMAGE_SUFFIXES:
            info = validate_image_file(path)
            checked += 1
            if not info.get("ok"):
                result["bad"].append({"path": str(path), "error": info.get("error") or "图片损坏"})
        else:
            info = probe_media_file(path)
            if info.get("skipped"):
                result["skipped"].append(str(path))
                continue
            checked += 1
            if not info.get("ok"):
                result["bad"].append({"path": str(path), "error": info.get("error") or "媒体文件损坏"})

    result["checked"] = checked
    result["ok"] = not result["bad"]
    return result
