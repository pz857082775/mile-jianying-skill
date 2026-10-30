"""字幕流水线：纠错、标点归一化、断句、排版与 SRT。

能力边界（与 AI 字幕相关的诚实说明）：

- 纠错是**规则/词典型 CSC**，只覆盖高置信度的常见错写，不调用语言模型；
  没收录的语境错误不会修改，宁缺毋滥。
- 术语保护 + URL/邮箱/数字占位符，避免把品牌名、链接或小数改坏。
- 语音转字幕依赖可选安装的 ``faster-whisper``；未安装时抛
  ``ASR_UNAVAILABLE``，绝不伪造转写结果。
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Iterable, Optional

from .errors import MileError
from .timecode import format_srt

__all__ = [
    "TYPOS",
    "BUILTIN_GLOSSARY",
    "load_glossary",
    "polish_text",
    "correct_text",
    "split_caption",
    "layout_caption",
    "build_captions",
    "to_srt",
    "parse_srt",
    "correct_timeline",
    "asr_available",
    "transcribe",
]

# 高置信度错写对照（宁缺毋滥：含义有争议或属于异体/地区用法的词不收）。
TYPOS = (
    ("好高鹜远", "好高骛远"),
    ("饮鸠止渴", "饮鸩止渴"),
    ("名列前矛", "名列前茅"),
    ("受益非浅", "受益匪浅"),
    ("一愁莫展", "一筹莫展"),
    ("一诺千斤", "一诺千金"),
    ("一如继往", "一如既往"),
    ("义无返顾", "义无反顾"),
    ("随声附合", "随声附和"),
    ("相形见拙", "相形见绌"),
    ("图穷匕现", "图穷匕见"),
    ("完壁归赵", "完璧归赵"),
    ("万古常青", "万古长青"),
    ("卓而不群", "卓尔不群"),
    ("不径而走", "不胫而走"),
    ("谈笑风声", "谈笑风生"),
    ("星罗其布", "星罗棋布"),
    ("磬竹难书", "罄竹难书"),
    ("世外桃园", "世外桃源"),
    ("金榜提名", "金榜题名"),
    ("如法泡制", "如法炮制"),
    ("蓬筚生辉", "蓬荜生辉"),
    ("按耐不住", "按捺不住"),
    ("迫不急待", "迫不及待"),
    ("再接再励", "再接再厉"),
    ("默守成规", "墨守成规"),
    ("分辩率", "分辨率"),
    ("迫在眉捷", "迫在眉睫"),
    ("登陆账号", "登录账号"),
    ("登陆页面", "登录页面"),
    ("登陆系统", "登录系统"),
    ("做为", "作为"),
    ("按装", "安装"),
    ("报歉", "抱歉"),
    ("必竟", "毕竟"),
    ("部份", "部分"),
    ("沉缅", "沉湎"),
    ("重迭", "重叠"),
    ("担搁", "耽搁"),
    ("渡假", "度假"),
    ("防碍", "妨碍"),
    ("分岐", "分歧"),
    ("复盖", "覆盖"),
    ("告磬", "告罄"),
    ("勾通", "沟通"),
    ("关健", "关键"),
    ("恢谐", "诙谐"),
    ("既使", "即使"),
    ("坚苦", "艰苦"),
    ("娇健", "矫健"),
    ("精采", "精彩"),
    ("就序", "就绪"),
    ("峻工", "竣工"),
    ("腊烛", "蜡烛"),
    ("兰球", "篮球"),
    ("狼籍", "狼藉"),
    ("罗嗦", "啰嗦"),
    ("迷团", "谜团"),
    ("棉薄", "绵薄"),
    ("明片", "名片"),
    ("内咎", "内疚"),
    ("凭添", "平添"),
    ("起迄", "起讫"),
    ("迁徒", "迁徙"),
    ("清淅", "清晰"),
    ("溶入", "融入"),
    ("入场卷", "入场券"),
    ("散慢", "散漫"),
    ("摄相机", "摄像机"),
    ("申张", "伸张"),
    ("神密", "神秘"),
    ("师付", "师傅"),
    ("收迄", "收讫"),
    ("书藉", "书籍"),
    ("松驰", "松弛"),
    ("倘佯", "徜徉"),
    ("题纲", "提纲"),
    ("通谍", "通牒"),
    ("推祟", "推崇"),
    ("忘想", "妄想"),
    ("无耐", "无奈"),
    ("蜇伏", "蛰伏"),
    ("消毁", "销毁"),
    ("欣尝", "欣赏"),
    ("修茸", "修葺"),
    ("渲泄", "宣泄"),
    ("遗撼", "遗憾"),
    ("鱼翁", "渔翁"),
    ("予算", "预算"),
    ("原故", "缘故"),
    ("震憾", "震撼"),
    ("争辨", "争辩"),
    ("装璜", "装潢"),
    ("追朔", "追溯"),
    ("姿式", "姿势"),
    ("坐阵", "坐镇"),
    ("因该", "应该"),
    ("以经", "已经"),
    ("已后", "以后"),
    ("已前", "以前"),
    ("在见", "再见"),
    ("在次", "再次"),
    ("时侯", "时候"),
    ("竞然", "竟然"),
    ("即然", "既然"),
    ("甚致", "甚至"),
    ("布署", "部署"),
    ("精减", "精简"),
    ("折抠", "折扣"),
    ("优慧", "优惠"),
    ("视屏", "视频"),
    ("频幕", "屏幕"),
    ("屏慕", "屏幕"),
    ("剪揖", "剪辑"),
    ("帐号", "账号"),
    ("帐户", "账户"),
)

# 内置术语保护：这些词整体跳过纠错，避免品牌/技术词被误改。
BUILTIN_GLOSSARY = (
    "米乐",
    "剪映",
    "抖音",
    "快手",
    "小红书",
    "视频号",
    "B站",
    "UP主",
    "Vlog",
    "iPhone",
    "iPad",
    "Mac",
    "Windows",
    "Android",
    "iOS",
    "Wi-Fi",
    "USB",
    "CPU",
    "GPU",
    "SSD",
    "App",
    "AI",
    "GPT",
    "ASR",
    "TTS",
    "BGM",
    "HDR",
    "4K",
    "8K",
    "Python",
)

_CJK = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_CJK_RE = re.compile(f"[{_CJK}]")
_TOKEN_PREFIX = "\ue000"
_TOKEN_RE = re.compile(_TOKEN_PREFIX + "[\ue000-\uf8ff]")


def _token(index: int) -> str:
    return _TOKEN_PREFIX + chr(0xE000 + index)

_ASCII_PUNCT = {",": "，", ";": "；", ":": "：", "?": "？", "!": "！"}

_URL_RE = re.compile(r"(?:https?://|www\.)[^\s，。；！？]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_TOPIC_RE = re.compile(r"[@#][\w\u4e00-\u9fff-]+#?")
_TIME_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?")
_NUMBER_RE = re.compile(r"\d+(?:[.,:/]\d+)*(?:%|‰|万|亿|[kKwW])?")

_SENTENCE_END = "。！？!?；;…"
_CLOSERS = "”’」』）)]\"'"
_WEAK_END = "，,、：:"
_NO_BREAK_BEFORE = "的得地了着过是在和与就都也很吗呢吧把被让给向从对"
_DURATION_EPS = 1e-6


def load_glossary(path) -> dict:
    """读取术语表；返回 ``{"protect": [...], "replace": {错: 对}}``。

    支持 JSON（dict / list）与纯文本（一行一个术语，``错 -> 对`` 表示替换）。
    """
    if isinstance(path, dict):
        return _normalize_glossary(path)
    if isinstance(path, (list, tuple, set)):
        return {"protect": [str(item) for item in path if str(item).strip()], "replace": {}}
    file_path = Path(str(path)).expanduser()
    if not file_path.exists():
        raise MileError("FILE_NOT_FOUND", f"术语表不存在: {file_path}")
    text = file_path.read_text(encoding="utf-8-sig")
    if file_path.suffix.lower() == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MileError("BAD_GLOSSARY", f"术语表 JSON 解析失败: {exc}") from exc
        return _normalize_glossary(payload)

    protect = []
    replace = {}
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line or line.startswith("#"):
            continue
        match = re.match(r"^(.+?)\s*(?:->|=>|→)\s*(.+)$", line)
        if match:
            wrong, right = match.group(1).strip(), match.group(2).strip()
            if wrong and right:
                replace[wrong] = right
            continue
        protect.append(line)
    return {"protect": protect, "replace": replace}


def _normalize_glossary(payload) -> dict:
    if isinstance(payload, dict):
        protect = payload.get("protect") or payload.get("terms") or []
        replace = payload.get("replace") or payload.get("corrections") or {}
        if isinstance(protect, str):
            protect = [protect]
        if not isinstance(replace, dict):
            replace = {}
        return {
            "protect": [str(item).strip() for item in protect if str(item).strip()],
            "replace": {str(key): str(value) for key, value in replace.items() if str(key)},
        }
    if isinstance(payload, list):
        return {"protect": [str(item).strip() for item in payload if str(item).strip()], "replace": {}}
    raise MileError("BAD_GLOSSARY", "术语表必须是 JSON 对象/数组或纯文本")


def _mask(text: str, patterns: list[re.Pattern], store: dict) -> str:
    for pattern in patterns:
        def _repl(match):
            if len(store) >= 0x18FF:
                return match.group(0)
            token = _token(len(store))
            store[token] = match.group(0)
            return token

        text = pattern.sub(_repl, text)
    return text


def _restore(text: str, store: dict) -> str:
    return _TOKEN_RE.sub(lambda match: store.get(match.group(0), match.group(0)), text)


def _sub_count(text: str, pattern: re.Pattern, repl) -> tuple[str, int]:
    count = 0

    def _wrapped(match):
        nonlocal count
        count += 1
        return repl(match) if callable(repl) else repl

    return pattern.sub(_wrapped, text), count


def _resolve_glossary(glossary) -> tuple[list[str], dict]:
    if glossary is None:
        return [], {}
    payload = load_glossary(glossary) if not isinstance(glossary, dict) else _normalize_glossary(glossary)
    return payload.get("protect") or [], payload.get("replace") or {}


def polish_text(
    text: str,
    *,
    glossary=None,
    rules: Optional[Iterable[tuple[str, str]]] = None,
    punctuation: bool = True,
    spaces: bool = True,
) -> dict:
    """修正常见错写并归一化标点；返回修改明细而不是静默改写。

    ``glossary`` 可以是 :func:`load_glossary` 的结果、文件路径或术语列表。
    """
    original = str(text or "")
    protect_terms, replace_map = _resolve_glossary(glossary)
    custom_rules = [(str(a), str(b)) for a, b in (rules or ()) if a and b]
    typo_rules = sorted(
        [(a, b) for a, b in custom_rules] + [(a, b) for a, b in TYPOS if a not in replace_map],
        key=lambda item: len(item[0]),
        reverse=True,
    )
    protected = list(dict.fromkeys(BUILTIN_GLOSSARY + tuple(protect_terms)))

    store: dict[str, str] = {}
    working = _mask(original, [_URL_RE, _EMAIL_RE, _TOPIC_RE, _TIME_RE, _NUMBER_RE], store)
    changes: list[dict] = []

    for wrong, right in replace_map.items():
        pattern = re.compile(re.escape(wrong))
        working, count = _sub_count(working, pattern, right)
        if count:
            changes.append({"kind": "typo", "from": wrong, "to": right, "count": count})

    if protected:
        terms = sorted(protected, key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(term) for term in terms))
        hit_terms = sorted({match.group(0) for match in pattern.finditer(working)}, key=len, reverse=True)
        working = _mask(working, [pattern], store)
    else:
        hit_terms = []

    for wrong, right in typo_rules:
        if wrong == right:
            continue
        pattern = re.compile(re.escape(wrong))
        working, count = _sub_count(working, pattern, right)
        if count:
            changes.append({"kind": "typo", "from": wrong, "to": right, "count": count})

    if punctuation:
        ellipsis = re.compile(rf"(?<=[{_CJK}])\.{{3,}}")
        working, count = _sub_count(working, ellipsis, "……")
        if count:
            changes.append({"kind": "punctuation", "from": "...", "to": "……", "count": count})
        for ascii_ch, cn_ch in _ASCII_PUNCT.items():
            if _CJK_RE.search(working):
                pattern = re.compile(re.escape(ascii_ch))
            else:
                pattern = re.compile(rf"(?<=[{_CJK}]){re.escape(ascii_ch)}|{re.escape(ascii_ch)}(?=[{_CJK}])")
            working, count = _sub_count(working, pattern, cn_ch)
            if count:
                changes.append({"kind": "punctuation", "from": ascii_ch, "to": cn_ch, "count": count})
        period = re.compile(rf"(?<=[{_CJK}])\.(?=[{_CJK}]|\s|$)")
        working, count = _sub_count(working, period, "。")
        if count:
            changes.append({"kind": "punctuation", "from": ".", "to": "。", "count": count})
        collapse = re.compile(r"(?<=[，、；：])(?:[，、；：])+")
        working, count = _sub_count(working, collapse, "")
        if count:
            changes.append({"kind": "punctuation", "count": count, "note": "合并重复标点"})

    if spaces:
        cjk_space = re.compile(rf"(?<=[{_CJK}])[ \t]+(?=[{_CJK}])")
        working, count = _sub_count(working, cjk_space, "")
        if count:
            changes.append({"kind": "space", "count": count, "note": "去掉汉字之间的空格"})
        before_punct = re.compile(r"[ \t]+(?=[，。！？；：、])")
        working, count = _sub_count(working, before_punct, "")
        if count:
            changes.append({"kind": "space", "count": count, "note": "去掉标点前的空格"})
        after_punct = re.compile(r"(?<=[，。！？；：、])[ \t]+")
        working, count = _sub_count(working, after_punct, "")
        if count:
            changes.append({"kind": "space", "count": count, "note": "去掉标点后的空格"})

    corrections = _restore(working, store).strip()
    return {
        "original": original,
        "corrected": corrections,
        "changed": corrections != original,
        "changes": changes,
        "protected": hit_terms,
    }


def correct_text(text: str, **kwargs) -> str:
    """只返回纠错后的文本。"""
    return polish_text(text, **kwargs)["corrected"]


def _hard_split(chunk: str, max_chars: int) -> list[str]:
    chunk = chunk.strip()
    result = []
    while len(chunk) > max_chars:
        if len(chunk) <= max_chars * 2:
            cut = (len(chunk) + 1) // 2
        else:
            window = chunk[:max_chars]
            cut = window.rfind(" ")
            if cut < max_chars * 0.5:
                cut = max_chars
        while cut > max_chars * 0.6 and cut < len(chunk) and chunk[cut] in _NO_BREAK_BEFORE:
            cut -= 1
        result.append(chunk[:cut].strip())
        chunk = chunk[cut:].strip()
    if chunk:
        result.append(chunk)
    return result


def _split_long(chunk: str, max_chars: int) -> list[str]:
    pieces = [piece for piece in re.split(rf"(?<=[{_WEAK_END}])", chunk) if piece.strip()]
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) + len(piece) <= max_chars:
            merged[-1] += piece
        else:
            merged.append(piece)
    result = []
    for piece in merged:
        result.extend(_hard_split(piece, max_chars))
    return result


def _merge_small(parts: list[str], min_chars: int, max_chars: int) -> list[str]:
    merged: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if merged and (
            len(part) < min_chars
            or (len(merged[-1]) < min_chars and len(merged[-1]) + len(part) <= max_chars)
        ):
            merged[-1] += part
        else:
            merged.append(part)
    return merged


def split_caption(text: str, max_chars: int = 28, min_chars: int = 2) -> list[str]:
    """把一段字幕按标点与长度切成多条，返回不含时间信息的文本列表。"""
    max_chars = max(2, int(max_chars))
    text = re.sub(r"[ \t\r\n]+", " ", str(text or "")).strip()
    if not text:
        return []

    sentences: list[str] = []
    buffer = ""
    for char in text:
        if not buffer and char in _CLOSERS and sentences:
            sentences[-1] += char
            continue
        buffer += char
        if char in _SENTENCE_END:
            sentences.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        sentences.append(buffer.strip())

    parts: list[str] = []
    for sentence in sentences:
        if len(sentence) <= max_chars:
            parts.append(sentence)
        else:
            parts.extend(_split_long(sentence, max_chars))
    return _merge_small(parts, max(1, int(min_chars)), max_chars)


def layout_caption(text: str, line_chars: int = 14, max_lines: int = 2) -> str:
    """按视觉宽度折行；超出 ``max_lines`` 时截断并以省略号结尾。"""
    line_chars = max(2, int(line_chars))
    max_lines = max(1, int(max_lines))
    raw_lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not raw_lines:
        return ""
    lines: list[str] = []
    for raw in raw_lines:
        while len(raw) > line_chars:
            window = raw[:line_chars]
            punctuation_at = max((window.rfind(ch) for ch in _WEAK_END), default=-1)
            space_at = window.rfind(" ")
            if punctuation_at >= line_chars * 0.5:
                cut = punctuation_at + 1
            elif space_at >= line_chars * 0.5:
                cut = space_at
            else:
                cut = line_chars
            lines.append(raw[:cut].strip())
            raw = raw[cut:].strip()
        if raw:
            lines.append(raw)
    if len(lines) > max_lines:
        kept = lines[:max_lines]
        last = kept[-1]
        if len(last) >= line_chars:
            last = last[: line_chars - 1]
        kept[-1] = last + "…"
        lines = kept
    return "\n".join(lines)


def _allocate_durations(parts: list[str], duration: int) -> list[int]:
    if not parts:
        return []
    total = max(0, int(duration))
    weights = [max(1, len(part)) for part in parts]
    total_weight = sum(weights)
    durations = [int(total * weight / total_weight) for weight in weights]
    durations[-1] += total - sum(durations)
    return durations


def _fit_parts(parts: list[str], duration_s: float, min_duration: float) -> list[str]:
    parts = list(parts)
    if min_duration <= 0:
        return parts
    while len(parts) > 1 and len(parts) * min_duration > duration_s + _DURATION_EPS:
        sizes = [len(part) for part in parts]
        index = min(range(len(parts)), key=lambda i: sizes[i])
        if index == 0:
            other = 1
        elif index == len(parts) - 1:
            other = len(parts) - 2
        else:
            other = index - 1 if sizes[index - 1] <= sizes[index + 1] else index + 1
        left, right = sorted((index, other))
        parts[left : right + 1] = [parts[left] + parts[right]]
    return parts


def build_captions(
    captions,
    *,
    max_chars: int = 24,
    line_chars: Optional[int] = None,
    max_lines: int = 2,
    glossary=None,
    rules: Optional[Iterable[tuple[str, str]]] = None,
    correct: bool = True,
    min_duration: float = 0.5,
) -> list[dict]:
    """把粗字幕整理成逐条可用的字幕，时间单位为秒。

    输入项支持 ``{"text", "start", "duration"}`` 或 ``{"text", "start", "end"}``；
    缺少时间时按上一条结束时间顺延。长句会按 ``max_chars`` 拆分，时长按字数比例分配。
    """
    if isinstance(captions, str):
        captions = [{"text": captions}]
    if not isinstance(captions, (list, tuple)):
        raise MileError("BAD_CAPTIONS", "字幕必须是数组或字符串")
    result: list[dict] = []
    cursor = 0.0
    for item in captions:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            raise MileError("BAD_CAPTIONS", f"无法识别的字幕项: {item!r}")
        raw_text = str(item.get("text") or item.get("content") or "").strip()
        if not raw_text:
            continue
        try:
            start = float(item.get("start") if item.get("start") is not None else cursor)
            if item.get("duration") is not None:
                duration = float(item.get("duration"))
            elif item.get("end") is not None:
                duration = float(item.get("end")) - start
            else:
                duration = 2.0
        except (TypeError, ValueError) as exc:
            raise MileError("BAD_CAPTIONS", f"字幕时间无法解析: {item!r}") from exc
        duration = max(0.0, duration)

        polished = polish_text(raw_text, glossary=glossary, rules=rules) if correct else None
        text = polished["corrected"] if polished else raw_text
        parts = split_caption(text, max_chars=max_chars)
        parts = _fit_parts(parts, duration, max(min_duration, 0.0))
        durations = _allocate_durations(parts, int(round(duration * 1_000_000)))
        offset = 0
        for part, part_us in zip(parts, durations):
            rendered = layout_caption(part, line_chars, max_lines) if line_chars else part
            entry = {
                "text": rendered,
                "start": round(start + offset / 1_000_000, 3),
                "duration": round(part_us / 1_000_000, 3),
            }
            if polished and polished["changed"]:
                entry["original"] = raw_text
                entry["changes"] = polished["changes"]
            result.append(entry)
            offset += part_us
        cursor = start + duration
    return result


def to_srt(captions) -> str:
    """生成 SRT 文本；``captions`` 的时间单位为秒。"""
    items = []
    for item in captions or []:
        if isinstance(item, str):
            item = {"text": item}
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(item.get("start") or 0)
            duration = float(item.get("duration") if item.get("duration") is not None else 0)
            if item.get("end") is not None and item.get("duration") is None:
                duration = float(item.get("end")) - start
        except (TypeError, ValueError):
            continue
        items.append((start, max(0.0, duration), text))
    items.sort(key=lambda entry: entry[0])
    blocks = []
    for index, (start, duration, text) in enumerate(items, start=1):
        blocks.append(
            f"{index}\n{format_srt(int(round(start * 1_000_000)))} --> "
            f"{format_srt(int(round((start + duration) * 1_000_000)))}\n{text}\n"
        )
    return "\n".join(blocks)


_SRT_TIME_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
)


def _srt_seconds(value: str) -> float:
    hours, minutes, seconds = value.replace(",", ".").split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def parse_srt(text: str) -> list[dict]:
    """解析 SRT，返回 ``{"text", "start", "end", "duration"}``（秒）。"""
    blocks = re.split(r"\r?\n\s*\r?\n", str(text or "").strip().lstrip("\ufeff"))
    captions = []
    for block in blocks:
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        match = None
        content_start = 0
        for index, line in enumerate(lines[:3]):
            match = _SRT_TIME_RE.search(line)
            if match:
                content_start = index + 1
                break
        if not match:
            continue
        content = "\n".join(lines[content_start:]).strip()
        start = _srt_seconds(match.group("start"))
        end = _srt_seconds(match.group("end"))
        if content and end > start:
            captions.append(
                {"text": content, "start": round(start, 3), "end": round(end, 3), "duration": round(end - start, 3)}
            )
    return captions


def correct_timeline(
    timeline: dict,
    *,
    glossary=None,
    roles=("caption", "title"),
    max_chars: Optional[int] = None,
    rules: Optional[Iterable[tuple[str, str]]] = None,
) -> dict:
    """扫描时间轴中的文字片段，返回需要纠错/拆分的条目（只读，不改草稿）。"""
    role_set = set(roles or ("caption", "title"))
    checked = 0
    items = []
    for seg in timeline.get("segments") or []:
        if seg.get("track_type") != "text" or not (seg.get("text") or "").strip():
            continue
        if role_set and seg.get("role") not in role_set:
            continue
        checked += 1
        result = polish_text(seg["text"], glossary=glossary, rules=rules)
        parts = split_caption(result["corrected"], max_chars=max_chars) if max_chars else []
        if not result["changed"] and len(parts) <= 1:
            continue
        entry = {
            "ref": seg.get("ref"),
            "id": seg.get("id"),
            "track_index": seg.get("track_index"),
            "role": seg.get("role"),
            "start": seg.get("start"),
            "duration": seg.get("duration"),
            "start_label": seg.get("start_label"),
            "original": seg["text"],
            "corrected": result["corrected"],
            "changes": result["changes"],
            "protected": result["protected"],
        }
        if len(parts) > 1:
            entry["parts"] = parts
        items.append(entry)
    return {"checked": checked, "changed": len(items), "items": items}


def asr_available() -> bool:
    """是否安装了可用的本地语音识别后端。"""
    return importlib.util.find_spec("faster_whisper") is not None


def transcribe(
    path,
    *,
    model_size: Optional[str] = None,
    language: str = "zh",
    glossary=None,
    max_chars: Optional[int] = None,
    min_duration: float = 0.5,
    vad_filter: bool = True,
) -> dict:
    """用 faster-whisper 转写字幕；未安装时抛 ``ASR_UNAVAILABLE``。"""
    media = Path(str(path)).expanduser()
    if not media.exists():
        raise MileError("FILE_NOT_FOUND", f"音视频文件不存在: {media}")
    if not asr_available():
        raise MileError(
            "ASR_UNAVAILABLE",
            "未安装 faster-whisper，无法本地转写；请先 pip install faster-whisper，"
            "或改用外部字幕文件配合 subtitles 子命令",
        )
    try:
        from faster_whisper import WhisperModel
    except Exception as exc:  # pragma: no cover - 依赖环境
        raise MileError("ASR_UNAVAILABLE", f"faster-whisper 加载失败: {exc}") from exc

    model_name = model_size or "small"
    try:
        model = WhisperModel(model_name, device="auto", compute_type="int8")
        segments, info = model.transcribe(
            str(media),
            language=language,
            vad_filter=vad_filter,
            word_timestamps=False,
        )
        captions = []
        for segment in segments:
            text = (segment.text or "").strip()
            if not text:
                continue
            captions.append(
                {
                    "text": text,
                    "start": round(float(segment.start), 3),
                    "end": round(float(segment.end), 3),
                    "duration": round(float(segment.end) - float(segment.start), 3),
                }
            )
    except MileError:
        raise
    except Exception as exc:  # pragma: no cover - 依赖环境
        raise MileError("ASR_FAILED", f"语音转写失败: {exc}", model=model_name) from exc

    captions = build_captions(
        captions,
        max_chars=max_chars or 24,
        glossary=glossary,
        min_duration=min_duration,
    )
    return {
        "provider": "faster-whisper",
        "model": model_name,
        "language": getattr(info, "language", language),
        "duration": round(float(getattr(info, "duration", 0) or 0), 3),
        "captions": captions,
    }
