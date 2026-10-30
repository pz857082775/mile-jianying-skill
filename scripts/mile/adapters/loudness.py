"""响度证据适配器：用 ffmpeg ebur128 实测整体综合响度（LUFS）与响度范围（LRA）。

T1 能力，只需要 ffmpeg；输出是 K 加权整体测量，不是逐片段响度，也不承诺平台交付标准。
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Optional

from ..errors import MileError

__all__ = ["measure_loudness", "DEFAULT_TARGET_LUFS"]

DEFAULT_TARGET_LUFS = -16.0
"""单声道语音/口播的常用近似目标；不等同于各平台的交付标准。"""

SILENCE_FLOOR_LUFS = -70.0
"""EBU R128 绝对门限：ffmpeg ebur128 对数字静音输出该值（部分版本为 -inf）。"""

_LUFS_RE = re.compile(r"I:\s*(\S+)\s*LUFS")
_LRA_RE = re.compile(r"LRA:\s*(-?\d+(?:\.\d+)?)\s*LU")


def _last_float(tokens: list[str]) -> tuple[Optional[float], bool]:
    """返回 (值, 是否静音/未知)。``-inf`` 或达到 -70 LUFS 门限都视为静音。"""
    if not tokens:
        return None, False
    token = tokens[-1]
    try:
        value = float(token)
    except ValueError:
        return None, True
    if value <= SILENCE_FLOOR_LUFS:
        return None, True
    return value, False


def measure_loudness(path, *, target_lufs: float = DEFAULT_TARGET_LUFS) -> dict:
    """实测一个音频/视频文件的综合响度，并给出到目标的增益建议。"""
    media = Path(str(path)).expanduser()
    if not media.exists():
        raise MileError("FILE_NOT_FOUND", f"音频/视频文件不存在: {media}")
    try:
        target = float(target_lufs)
    except (TypeError, ValueError) as exc:
        raise MileError("BAD_RANGE", f"target_lufs 必须是数字: {target_lufs}") from exc

    from utils.media_probe import find_ffmpeg

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise MileError(
            "FFMPEG_MISSING",
            "未找到 ffmpeg，无法实测响度",
            install_hint="设置 MILE_FFMPEG，或安装 ffmpeg 后重试",
        )

    command = [ffmpeg, "-v", "info", "-i", str(media), "-af", "ebur128", "-f", "null", "-"]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MileError("LOUDNESS_FAILED", f"ebur128 运行失败: {exc}") from exc
    text = (completed.stderr or b"").decode("utf-8", "replace")

    integrated, silent = _last_float(_LUFS_RE.findall(text))
    if integrated is None and not silent:
        raise MileError("LOUDNESS_FAILED", "无法解析 ebur128 响度输出", tail=text[-200:])
    lra, _ = _last_float(_LRA_RE.findall(text))

    result: dict = {
        "source": str(media),
        "method": "ffmpeg-ebur128",
        "available": True,
        "integrated_lufs": None if integrated is None else round(integrated, 1),
        "loudness_range_lu": lra,
        "target_lufs": target,
        "silent": bool(silent),
        "note": "整体综合响度（K 加权），不定位到具体片段；目标值只是常见近似，以剪映预览与交付标准为准",
    }
    if integrated is not None:
        deviation = round(integrated - target, 1)
        gain_db = round(target - integrated, 1)
        suggested = 10.0 ** (gain_db / 20.0)
        result["deviation_lu"] = deviation
        result["suggested_gain_db"] = gain_db
        result["suggested_volume"] = round(min(4.0, max(0.0, suggested)), 3)
    return result
