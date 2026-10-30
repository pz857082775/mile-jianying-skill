"""时间码解析与格式化。

约定：

- 本包对外接口中的数字时间**默认按秒**理解，字符串支持
  ``12`` / ``12.5s`` / ``500ms`` / ``1m2s`` / ``1h2m3s`` /
  ``00:01:02.500`` / ``01:02``（分:秒）以及中文单位 ``分/秒/毫秒``。
- 内部与草稿 JSON 一律使用**微秒（int）**。
"""

from __future__ import annotations

import re

__all__ = [
    "US_PER_SEC",
    "parse_time",
    "parse_range",
    "format_time",
    "format_srt",
    "round_to_frame",
]

US_PER_SEC = 1_000_000

_CLOCK_RE = re.compile(r"^(?P<neg>-)?(?:(?P<h>\d+):)?(?P<m>\d{1,2}):(?P<s>\d{1,2}(?:\.\d+)?)$")
_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(ms|us|h|m|s)", re.IGNORECASE)
_UNIT_US = {"h": 3600 * US_PER_SEC, "m": 60 * US_PER_SEC, "s": US_PER_SEC, "ms": 1000, "us": 1}
_RANGE_SEPS = ("~", "～", "至", "到", "-", "—")

_ZH_UNITS = (
    ("小时", "h"),
    ("分钟", "m"),
    ("秒钟", "s"),
    ("毫秒", "ms"),
    ("微秒", "us"),
    ("时", "h"),
    ("分", "m"),
    ("秒", "s"),
)


def parse_time(value, *, numeric: str = "s") -> int:
    """把时间值解析为微秒。

    Args:
        value: 数字、字符串或已有的微秒整数。
        numeric: 数字输入的单位，``"s"``（默认，秒）或 ``"us"``（微秒）。
    """
    if value is None:
        raise ValueError("时间值不能为空")
    if isinstance(value, bool):
        raise ValueError("时间值不能是布尔值")
    if isinstance(value, (int, float)):
        factor = US_PER_SEC if numeric == "s" else 1
        return int(round(float(value) * factor))
    if not isinstance(value, str):
        raise ValueError(f"无法解析时间值: {value!r}")

    text = value.strip().lower().replace("µs", "us").replace("μs", "us")
    if not text:
        raise ValueError("时间值不能为空")

    neg = False
    if text[0] in "+-":
        neg = text[0] == "-"
        text = text[1:].strip()
        if not text:
            raise ValueError(f"无法解析时间值: {value!r}")

    clock = _CLOCK_RE.match(text)
    if clock:
        hours = int(clock.group("h") or 0)
        minutes = int(clock.group("m") or 0)
        sec_value = float(clock.group("s") or 0)
        total = (hours * 3600 + minutes * 60 + sec_value) * US_PER_SEC
        return -int(round(total)) if neg else int(round(total))

    for zh, en in _ZH_UNITS:
        text = text.replace(zh, en)

    total = 0.0
    pos = 0
    matched = False
    for token in _TOKEN_RE.finditer(text):
        gap = text[pos:token.start()].strip(" ,，+")
        if gap:
            raise ValueError(f"无法解析时间值: {value!r}（多余内容 {gap!r}）")
        total += float(token.group(1)) * _UNIT_US[token.group(2).lower()]
        pos = token.end()
        matched = True

    if matched:
        rest = text[pos:].strip(" ,，+")
        if rest:
            raise ValueError(f"无法解析时间值: {value!r}（多余内容 {rest!r}）")
        return -int(round(total)) if neg else int(round(total))

    try:
        return int(round(float(text) * US_PER_SEC * (-1 if neg else 1)))
    except ValueError as exc:
        raise ValueError(f"无法解析时间值: {value!r}") from exc


def parse_range(spec) -> tuple[int, int | None]:
    """解析时间范围，返回 ``(start_us, duration_us)``；单点选择时 duration 为 None。

    支持 ``"12s-18s"``、``"12s~18s"``、``"12s+6s"``、``"00:00:12-00:00:18"``、
    ``{"start": "12s", "end": "18s"}``、``[12, 18]``（秒）。
    """
    if isinstance(spec, dict):
        start = parse_time(spec.get("start", 0))
        if spec.get("end") is not None:
            return start, parse_time(spec["end"]) - start
        if spec.get("duration") is not None:
            return start, parse_time(spec["duration"])
        return start, None
    if isinstance(spec, (list, tuple)):
        if len(spec) == 1:
            return parse_time(spec[0]), None
        if len(spec) == 2:
            start = parse_time(spec[0])
            return start, parse_time(spec[1]) - start
        raise ValueError("时间范围应为 [start, end] 或 [start]")
    if isinstance(spec, (int, float)):
        return parse_time(spec), None
    if not isinstance(spec, str):
        raise ValueError(f"无法解析时间范围: {spec!r}")

    text = spec.strip()
    for sep in _RANGE_SEPS:
        if sep in text[1:]:
            left, right = text.split(sep, 1)
            start = parse_time(left)
            end = parse_time(right)
            return start, end - start
    if "+" in text:
        left, right = text.split("+", 1)
        return parse_time(left), parse_time(right)
    return parse_time(text), None


def format_time(us: int, style: str = "clock") -> str:
    """格式化微秒时间。

    style: ``"clock"``（00:00:12.500）、``"short"``（00:00:12）、
    ``"s"``（12.5s）、``"hms"``（1h2m3s）。
    """
    us = int(us)
    sign = "-" if us < 0 else ""
    us = abs(us)

    if style == "s":
        seconds = us / US_PER_SEC
        text = f"{seconds:.3f}".rstrip("0").rstrip(".")
        return f"{sign}{text}s"

    if style == "hms":
        total_s, rem_us = divmod(us, US_PER_SEC)
        h, rem = divmod(total_s, 3600)
        m, s = divmod(rem, 60)
        parts = []
        if h:
            parts.append(f"{h}h")
        if m or h:
            parts.append(f"{m}m")
        if rem_us:
            parts.append(f"{s + rem_us / US_PER_SEC:.3f}".rstrip("0").rstrip(".") + "s")
        else:
            parts.append(f"{s}s")
        return sign + "".join(parts)

    total_ms = int(round(us / 1000.0))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    if style == "short":
        return f"{sign}{h:02d}:{m:02d}:{s:02d}"
    return f"{sign}{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def format_srt(us: int) -> str:
    """SRT 时间戳格式：``HH:MM:SS,mmm``。"""
    us = max(0, int(us))
    total_ms = int(round(us / 1000.0))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def round_to_frame(us: int, fps: float, mode: str = "nearest") -> int:
    """把微秒时间对齐到帧边界。"""
    if not fps or fps <= 0:
        return int(us)
    frame_us = US_PER_SEC / float(fps)
    frames = us / frame_us
    if mode == "floor":
        frames = int(frames)
    elif mode == "ceil":
        frames = int(-(-frames // 1))
    else:
        frames = int(round(frames))
    return int(round(frames * frame_us))
