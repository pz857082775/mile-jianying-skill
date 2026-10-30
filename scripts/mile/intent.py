"""规则型中文剪辑意图解析。

把“把第 2 个视频片段裁到 2s-6s，再加 0.3s 叠化”这类中文指令，解析成
``mile.plan`` 可直接执行的结构化操作。

这是一个**规则 / 槽位解析器**，不是大语言模型：

- 只处理明确、可验证的剪辑指令；
- 目标、数值、时间任一缺失时返回 ``clarify``，不猜测后直接改草稿；
- 需要知道片段边界（开头/结尾、所有转场）时，必须提供 ``timeline``；
- 无法覆盖的剪映能力（调色、美颜、倒放等）会进入 ``warnings``，而不是假装支持；
  滤镜、特效、蒙版、视频动画、字幕（文字）动画、缩放关键帧、素材入轨与文字描边/阴影/背景
  已可作为 ``set_filter`` / ``add_effect`` / ``set_mask`` / ``add_animation`` /
  ``add_text_animation`` / ``add_keyframes`` / ``add_media`` / ``set_text`` /
  ``remove_material`` 计划操作输出。

返回结构::

    {
      "text": 原始指令,
      "confidence": 0.0-1.0,
      "actions": [{op, target?, ..., editable, confidence, note}],
      "plan": {"intent": ..., "ops": [...]} | None,
      "clarify": [...],
      "warnings": [...],
      "tags": [...],
      "summary": "..."
    }
"""

from __future__ import annotations

import re
from typing import Optional

from .errors import MileError
from .timecode import US_PER_SEC, parse_time
from .timeline import select_segments

__all__ = [
    "parse_intent",
    "extract_target",
    "extract_numbers",
    "INTENT_TAGS",
]

# ---------------------------------------------------------------------------
# 基础词表与工具
# ---------------------------------------------------------------------------
_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}

_CN_NUM = r"[0-9０-９]+(?:\.[0-9]+)?|[零〇一二两三四五六七八九十百千]+"
_UNIT = r"(?:小时|钟头|分钟|分|秒钟|秒|毫秒|us|ms|h|m|s)"
_TIME = rf"(?:{_CN_NUM})\s*{_UNIT}"
_TIME_ALT = rf"(?:{_TIME}|\d{{1,2}}:\d{{2}}(?::\d{{2}})?(?:\.\d+)?)"
_TIME_RE = re.compile(_TIME, re.IGNORECASE)
_RANGE_RE = re.compile(
    rf"(?P<start>{_TIME_ALT})\s*(?:到|至|~|～|—|-)\s*(?P<end>{_TIME_ALT})",
    re.IGNORECASE,
)
_QUOTE_RE = re.compile(r'"([^"\n]+)"|“([^”\n]+)”|‘([^’\n]+)’|「([^」\n]+)」|『([^』\n]+)』')
_COLOR_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}")

_KINDS = (
    r"(视频片段|视频|片段|镜头|字幕|文字|文本|标题|花字|"
    r"背景音乐|配乐|音乐|音频|配音|旁白|贴纸)"
)
_ORDINAL_RE = re.compile(
    rf"第\s*(?P<num>{_CN_NUM})\s*"
    rf"(?:(?P<cls>个|段|条|块|支|首|句)\s*(?P<kind1>{_KINDS})?|(?P<kind2>{_KINDS}))"
)
_LAST_RE = re.compile(
    rf"最后\s*(?:一)?\s*(?:(?:个|段|条|块|支|首|句)\s*(?P<kind1>{_KINDS})?|(?P<kind2>{_KINDS}))"
)
_SCOPE_RE = re.compile(
    rf"(?:所有|全部|每一个|每个|每一条|每条|每一段|每段|各个|各段|整段|整条|全片)\s*(?P<kind>{_KINDS})?"
)

_KIND_MAP = {
    "视频片段": "main",
    "视频": "main",
    "片段": "main",
    "镜头": "main",
    "字幕": "caption",
    "文字": "text_all",
    "文本": "text_all",
    "标题": "text_all",
    "花字": "text_all",
    "背景音乐": "bgm",
    "配乐": "bgm",
    "音乐": "bgm",
    "音频": "audio_all",
    "配音": "voice",
    "旁白": "voice",
    "贴纸": "sticker",
}

_COLORS = {
    "白色": "#ffffff",
    "黑色": "#000000",
    "红色": "#ff0000",
    "黄色": "#ffff00",
    "橙色": "#ffa500",
    "绿色": "#00ff00",
    "蓝色": "#0000ff",
    "紫色": "#800080",
    "粉色": "#ffc0cb",
    "青色": "#00ffff",
    "灰色": "#808080",
    "棕色": "#8b4513",
    "金色": "#ffd700",
    "银色": "#c0c0c0",
}

_TRANSITIONS = {
    "淡入淡出": "淡入淡出",
    "叠化": "叠化",
    "擦除": "擦除",
    "滑动": "滑动",
    "缩放": "缩放",
    "闪白": "闪白",
    "黑场": "黑场",
    "淡化": "淡入淡出",
}

_RATIOS = {
    (16, 9): (1920, 1080),
    (9, 16): (1080, 1920),
    (1, 1): (1080, 1080),
    (4, 3): (1440, 1080),
    (3, 4): (1080, 1440),
    (21, 9): (2560, 1080),
}

_AUDIO_EXT = r"(?:mp3|wav|m4a|aac|flac|ogg|wma|opus)"

INTENT_TAGS = {
    "教程": ("教程", "教学", "讲解", "知识", "干货"),
    "口播": ("口播", "讲解", "旁白", "配音", "说话"),
    "vlog": ("vlog", "Vlog", "日常", "记录"),
    "美食": ("美食", "探店", "做饭", "菜谱", "吃"),
    "旅行": ("旅行", "旅游", "景点", "攻略", "出行"),
    "产品": ("产品", "带货", "种草", "广告", "品牌"),
    "开箱": ("开箱", "测评", "评测"),
    "剧情": ("剧情", "短剧", "故事", "反转"),
    "健身": ("健身", "运动", "训练", "减肥"),
    "亲子": ("亲子", "宝宝", "孩子", "家庭"),
    "音乐": ("音乐", "卡点", "舞蹈", "BGM"),
}

_UNSUPPORTED_HINTS = {
    "调色": "调色",
    "画中画": "画中画",
    "抠像": "抠像",
    "跟踪": "跟踪",
    "美颜": "美颜",
    "倒放": "倒放",
    "曲线变速": "曲线变速",
    "定格": "定格",
}

_FEATURE_KINDS = {
    "滤镜": ("set_filter", "滤镜"),
    "特效": ("add_effect", "特效"),
    "蒙版": ("set_mask", "蒙版"),
    "动画": ("add_animation", "动画"),
}


def _cn_to_int(token) -> Optional[int]:
    text = str(token or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    total = 0
    current = 0
    for ch in text:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            unit = _CN_UNITS[ch]
            total += (current or 1) * unit
            current = 0
        else:
            return None
    return total + current


def _num_value(token) -> Optional[float]:
    text = str(token or "").strip().replace("．", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        value = _cn_to_int(text)
        return float(value) if value is not None else None


def _to_us(token) -> Optional[int]:
    if token is None:
        return None
    text = str(token).strip().replace("半", "0.5")
    if not text:
        return None

    def repl(match):
        value = _cn_to_int(match.group(1))
        if value is None:
            return match.group(0)
        return f"{value}{match.group(2)}"

    text = re.sub(rf"({_CN_NUM})\s*({_UNIT})", repl, text, flags=re.IGNORECASE)
    try:
        return int(parse_time(text))
    except ValueError:
        return None


def _sec(us: int) -> float:
    return round(int(us) / US_PER_SEC, 6)


def _quotes(text: str) -> list[str]:
    result = []
    for match in _QUOTE_RE.finditer(text):
        value = next((group for group in match.groups() if group is not None), "")
        if value.strip():
            result.append(value.strip())
    return result


def _mask_quotes(text: str) -> str:
    return _QUOTE_RE.sub(lambda match: " " * len(match.group(0)), text)


def _range(text: str) -> Optional[dict]:
    match = _RANGE_RE.search(_mask_quotes(text))
    if not match:
        return None
    start = _to_us(match.group("start"))
    end = _to_us(match.group("end"))
    if start is None or end is None or end <= start:
        return None
    return {"start": start, "end": end, "start_s": _sec(start), "end_s": _sec(end), "span": match.span()}


def _point(text: str, *, skip_duration: bool = False) -> Optional[int]:
    masked = _mask_quotes(text)
    if skip_duration:
        duration_pattern = re.compile(
            rf"(?:持续|显示|停留|时长|共)\s*(?:为|是|:)?\s*{_TIME_ALT}",
            re.IGNORECASE,
        )
        masked = duration_pattern.sub(lambda match: " " * len(match.group(0)), masked)
    match = _TIME_RE.search(masked)
    if not match:
        return None
    return _to_us(match.group(0))


def _kind_from_word(word) -> Optional[str]:
    if not word:
        return None
    for key, kind in _KIND_MAP.items():
        if word == key:
            return kind
    for key, kind in _KIND_MAP.items():
        if key in word:
            return kind
    return None


def _bare_kind(text: str) -> Optional[str]:
    if re.search(r"(背景音乐|配乐|bgm)", text, re.IGNORECASE):
        return "bgm"
    if re.search(r"(配音|旁白|口播)", text):
        return "voice"
    if "字幕" in text:
        return "caption"
    if any(word in text for word in ("文字", "文本", "标题", "花字")):
        return "text_all"
    if "贴纸" in text:
        return "sticker"
    if "音频" in text:
        return "audio_all"
    if any(word in text for word in ("视频", "画面", "镜头", "片段")):
        return "main"
    return None


def _selector_for(kind: Optional[str], index=None) -> dict:
    if kind == "text_all":
        selector = {"role": ["caption", "title"]}
    elif kind == "audio_all":
        selector = {"role": ["bgm", "voice", "audio"]}
    elif kind == "video_all":
        selector = {"material_type": "video"}
    else:
        selector = {"role": kind or "main"}
    if index is not None:
        selector["index"] = index
    return selector


def _ordinal(text: str):
    match = _LAST_RE.search(text)
    if match:
        word = match.group("kind1") or match.group("kind2")
        return "last", _kind_from_word(word)
    match = _ORDINAL_RE.search(text)
    if not match:
        return None
    number = _cn_to_int(match.group("num"))
    if number is None or number < 1:
        return None
    word = match.group("kind1") or match.group("kind2")
    return number - 1, _kind_from_word(word)


def _scope(text: str):
    match = _SCOPE_RE.search(text)
    if not match:
        return None
    return True, _kind_from_word(match.group("kind"))


def _preceded_by_di(text: str, index: int) -> bool:
    prefix = text[:index].rstrip()
    return prefix.endswith("第")


def _find_volume(text: str):
    masked = _mask_quotes(text)
    if re.search(r"(静音|无声|mute|关闭原声|去掉原声|去除原声)", masked, re.IGNORECASE):
        return 0.0, "静音"
    if "一半" in masked:
        return 0.5, "一半"
    match = re.search(rf"百分之\s*({_CN_NUM})", masked)
    if match:
        value = _num_value(match.group(1))
        if value is not None:
            return max(0.0, min(4.0, value / 100.0)), "百分比"
    match = re.search(rf"({_CN_NUM})\s*[%％]", masked)
    if match:
        value = _num_value(match.group(1))
        if value is not None:
            return max(0.0, min(4.0, value / 100.0)), "百分比"
    match = re.search(r"([一二两三四五六七八九])\s*成", masked)
    if match:
        value = _cn_to_int(match.group(1))
        if value is not None:
            return max(0.0, min(4.0, value / 10.0)), "成数"
    match = re.search(
        rf"(?:音量|声音|响度|volume)[^0-9０-９零〇一二两三四五六七八九十百]{{0,10}}?({_CN_NUM})",
        masked,
        re.IGNORECASE,
    )
    if match:
        value = _num_value(match.group(1))
        if value is None:
            return None, None
        if value > 4 and value <= 100:
            return round(value / 100.0, 4), "百分比"
        if value > 100:
            return None, None
        return max(0.0, value), "数值"
    return None, None


def _find_speed(text: str):
    masked = _mask_quotes(text)
    for match in re.finditer(rf"({_CN_NUM})\s*倍", masked):
        if _preceded_by_di(masked, match.start()):
            continue
        raw = match.group(1)
        value = _num_value(raw)
        if value is None:
            continue
        if str(raw).strip() in {"一", "1"} and re.search(r"(加速|加快|快)", masked):
            return 2.0, "“一倍”按加速一倍理解"
        return value, None
    if re.search(r"(慢动作|慢放|放慢|减速|降速)", masked):
        return 0.5, "未给出倍数，按 0.5 倍慢放"
    if re.search(r"(加速|加快|快放|快进)", masked):
        return 1.5, "未给出倍数，按 1.5 倍加速"
    if "快一点" in masked or "快一些" in masked:
        return 1.25, "按 1.25 倍小幅加速"
    if "慢一点" in masked or "慢一些" in masked:
        return 0.8, "按 0.8 倍小幅放慢"
    return None, None


def _find_color(text: str):
    match = _COLOR_HEX_RE.search(text)
    if match:
        return match.group(0).lower()
    for word, value in _COLORS.items():
        if word in text:
            return value
    return None


def _find_font_size(text: str) -> Optional[float]:
    match = re.search(
        r"(?:字号|字体大小|字体尺寸|文字大小)\s*(?:改成|改为|设为|设置为|调到|调为|到|为|:)?\s*(\d+(?:\.\d+)?)",
        text,
    )
    if not match:
        return None
    return float(match.group(1))


def _find_path(text: str) -> Optional[str]:
    for value in _quotes(text):
        if re.search(rf"\.{_AUDIO_EXT}$", value, re.IGNORECASE):
            return value
    masked = _mask_quotes(text)
    match = re.search(
        rf"([A-Za-z]:[\\/][^\s\"'“”，,；;]*?\.{_AUDIO_EXT})", masked, re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    match = re.search(
        rf"((?:~|\.{{1,2}})?[\\/][^\s\"'“”，,；;]*?\.{_AUDIO_EXT})", masked, re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    match = re.search(rf"([^\s\"'“”，,；;:：]*\.{_AUDIO_EXT})", masked, re.IGNORECASE)
    if match:
        candidate = match.group(1).strip()
        cleaned = re.sub(
            r"^(?:把|将|给|用|加|放|配|添加|插入|换|替换|更换|改成|改为|换成|替换成|改用|"
            r"背景音乐|配乐|音乐|BGM)+",
            "",
            candidate,
            flags=re.IGNORECASE,
        )
        if cleaned and not cleaned.startswith("."):
            return cleaned
        return candidate
    return None


def _find_media_path(text: str) -> Optional[str]:
    """识别非纯音频的素材路径（视频/图片），供 add_media 使用。"""
    media_ext = r"(?:mp4|mov|avi|mkv|webm|flv|m4v|wmv|mpg|mpeg|png|jpg|jpeg|bmp|gif|webp|heic|tif|tiff)"
    for value in _quotes(text):
        if re.search(rf"\.{media_ext}$", value, re.IGNORECASE):
            return value
    masked = _mask_quotes(text)
    match = re.search(rf"([A-Za-z]:[\\/][^\s\"'“”，,；;]*?\.{media_ext})", masked, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(
        rf"((?:[A-Za-z0-9_.\-]+[\\/])+[A-Za-z0-9_.\-]+\.{media_ext})", masked, re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    match = re.search(rf"((?:~|\.{{1,2}})?[\\/][^\s\"'“”，,；;]*?\.{media_ext})", masked, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(rf"([^\s\"'“”，,；;:：]*\.{media_ext})", masked, re.IGNORECASE)
    if match:
        candidate = match.group(1).strip()
        cleaned = re.sub(
            r"^(?:把|将|给|用|加|放|配|添加|插入|换|替换|更换|改成|改为|换成|替换成|改用|来一?个?段?)+",
            "",
            candidate,
            flags=re.IGNORECASE,
        )
        if cleaned and not cleaned.startswith("."):
            return cleaned
        return candidate
    return None


def _find_transition(text: str):
    for word in sorted(_TRANSITIONS, key=len, reverse=True):
        if word in text:
            return _TRANSITIONS[word]
    return None


def _transition_duration(text: str) -> Optional[int]:
    masked = _mask_quotes(text)
    for match in _TIME_RE.finditer(masked):
        if _preceded_by_di(masked, match.start()):
            continue
        return _to_us(match.group(0))
    return None


def _find_ratio(text: str):
    match = re.search(r"(\d{1,2})\s*[:：]\s*(\d{1,2})", _mask_quotes(text))
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.search(r"([一二三四五六七八九十]+)\s*比\s*([一二三四五六七八九十]+)", text)
    if match:
        left = _cn_to_int(match.group(1))
        right = _cn_to_int(match.group(2))
        if left and right:
            return left, right
    return None


def _canvas_size(text: str):
    match = re.search(r"(\d{2,5})\s*[xX×*]\s*(\d{2,5})", _mask_quotes(text))
    if match:
        return int(match.group(1)), int(match.group(2))
    if "竖屏" in text:
        return 1080, 1920
    if "横屏" in text:
        return 1920, 1080
    if "方形" in text or "正方形" in text:
        return 1080, 1080
    ratio = _find_ratio(text)
    if ratio:
        if ratio in _RATIOS:
            return _RATIOS[ratio]
        return "unsupported", ratio
    return None


def _is_cut_verb(text: str) -> bool:
    return bool(re.search(r"(剪|裁|切|截|缩短|去掉|去除|删掉|删去|删除|移除)", text))


def _is_trim_verb(text: str) -> bool:
    return bool(re.search(r"(裁到|剪到|截到|裁剪|裁剪为|裁剪成|修剪|保留)", text))


def _is_delete_verb(text: str) -> bool:
    return bool(re.search(r"(删除|删掉|删去|移除|去掉|去除|清除|干掉)", text))


def _clean_note(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip("，,。；; ")


# ---------------------------------------------------------------------------
# 目标定位
# ---------------------------------------------------------------------------
def extract_target(text: str) -> dict:
    """从单个分句中抽取定位信息（不依赖草稿）。

    返回 ``{"selector": dict|None, "kind": str|None, "single": bool, "quotes": [...]}``。
    """
    quotes = _quotes(text)
    ordinal = _ordinal(text)
    scope = _scope(text)
    kind_hint = _bare_kind(text)
    selector = None
    kind = None
    single = False

    if ordinal is not None:
        index, kind = ordinal
        kind = kind or kind_hint or "main"
        selector = _selector_for(kind, index)
        single = True
    elif scope is not None:
        kind = scope[1] or kind_hint
        if kind:
            selector = _selector_for(kind)
    elif quotes and re.search(r"(包含|带有|写着|文案是|内容是)", text):
        kind = kind_hint or "caption"
        selector = {**_selector_for(kind), "text_contains": quotes[0]}

    if selector is None and kind_hint:
        kind = kind_hint
        selector = _selector_for(kind)

    return {"selector": selector, "kind": kind, "single": single, "quotes": quotes}


def _match_count(selector: Optional[dict], timeline: Optional[dict]) -> Optional[int]:
    if not selector or timeline is None:
        return None
    try:
        return len(select_segments(timeline, selector))
    except MileError:
        return None


def _fallback(selector: Optional[dict], kind: Optional[str], timeline, warnings: list[str]) -> Optional[dict]:
    """字幕轨未打 caption 标记时，退回全部文本轨，避免“所有字幕”匹配为空。"""
    if not selector or timeline is None:
        return selector
    if _match_count(selector, timeline) != 0:
        return selector
    alternatives = []
    if kind == "caption":
        alternatives.append(({"role": ["caption", "title"]}, "未识别到字幕轨，已改用全部文本轨"))
        alternatives.append(({"material_type": "text"}, "未识别到字幕轨，已改用全部文本"))
    elif kind == "main":
        alternatives.append(({"material_type": "video"}, "未识别到主轨，已改用全部视频轨"))
    elif kind == "bgm":
        alternatives.append(({"role": ["bgm", "audio"]}, "未识别到 BGM 轨，已改用全部音频"))
    elif kind == "text_all":
        alternatives.append(({"material_type": "text"}, "未识别到文本轨，已改用文本素材"))
    for candidate, message in alternatives:
        if _match_count(candidate, timeline):
            warnings.append(message)
            return candidate
    return selector


# ---------------------------------------------------------------------------
# 单分句解析
# ---------------------------------------------------------------------------
def _action(op: str, *, confidence: float = 0.9, note: str = "", editable: bool = True, **kwargs) -> dict:
    payload = {"op": op, "editable": editable, "confidence": round(float(confidence), 3), "note": note}
    payload.update(kwargs)
    return payload


def _parse_polish(text: str, target_info: dict) -> dict:
    selector = target_info.get("selector")
    if not selector or target_info.get("kind") not in {"caption", "text_all", "main"}:
        selector = {"role": ["caption", "title"]}
    return _action(
        "polish_subtitles",
        confidence=0.9,
        note="规则型中文拼写纠错与标点归一化",
        editable=False,
        target=selector,
    )


def _parse_recommend(text: str) -> dict:
    kind = "bgm" if re.search(r"(音乐|配乐|bgm)", text, re.IGNORECASE) else "transition"
    return _action(
        "recommend",
        confidence=0.8,
        note="推荐结果只读，不会自动改动草稿",
        editable=False,
        kind=kind,
    )


def _parse_canvas(text: str, warnings: list[str], clarify: list[str]) -> Optional[list[dict]]:
    size = _canvas_size(text)
    if size is None:
        clarify.append("请给出画布尺寸或比例，例如 1080x1920、9:16、竖屏")
        return None
    if isinstance(size, tuple) and size and size[0] == "unsupported":
        clarify.append(f"暂不支持该画面比例: {size[1][0]}:{size[1][1]}")
        return None
    width, height = size
    return [_action("set_canvas", confidence=0.95, note=f"画布改为 {width}x{height}", width=width, height=height)]


def _text_style_args(text: str) -> dict:
    args: dict = {}
    font_size = _find_font_size(text)
    if font_size is not None:
        args["font_size"] = font_size
    if re.search(r"(颜色|字色|字体颜色|改成|改为|换成|设为|设置)", text):
        color = _find_color(text)
        if color:
            args["color"] = color
    if re.search(r"(取消加粗|不加粗|正常粗细|去掉加粗|取消粗体)", text):
        args["bold"] = False
    elif re.search(r"(加粗|变粗|粗体|加黑)", text):
        args["bold"] = True
    if re.search(r"(取消斜体|不用斜体|去掉斜体)", text):
        args["italic"] = False
    elif "斜体" in text:
        args["italic"] = True
    if re.search(r"(取消下划线|去掉下划线)", text):
        args["underline"] = False
    elif "下划线" in text:
        args["underline"] = True
    if re.search(r"(左对齐|居左)", text):
        args["align"] = 0
    elif re.search(r"(右对齐|居右)", text):
        args["align"] = 2
    elif re.search(r"(居中|中间对齐)", text):
        args["align"] = 1
    return args


def _decoration_removed(text: str, word: str) -> bool:
    filler = r"(?:\s*(?:了|掉|所有|全部|每条|每个|整个|这条|那个|字幕|文字|标题|花字|的))*"
    return bool(
        re.search(rf"(?:去掉|取消|移除|删除|删掉|去除|不要|不带)\s*{filler}\s*{word}", text)
        or re.search(rf"{word}[^，,。；;]{{0,4}}(?:去掉|取消|移除|删除|去除)", text)
    )


def _parse_decoration(text: str, target_info: dict, warnings: list[str], clarify: list[str]):
    """解析描边/阴影/背景说法，输出 ``set_text`` 的装饰参数。"""
    mapping = (("描边", "border"), ("阴影", "shadow"), ("背景", "background"))
    if not any(word in text for word, _ in mapping):
        return None, None, None
    args: dict = {}
    notes: list[str] = []
    masked = _mask_quotes(text)
    for word, key in mapping:
        if word not in masked:
            continue
        if _decoration_removed(masked, word):
            args[key] = False
            notes.append(f"去掉{word}")
            continue
        spec: dict = {}
        color = _find_color(masked)
        if color:
            spec["color"] = color
        width_match = re.search(
            rf"(?:{word}|边框)\s*(?:宽度|粗细)\s*(?:为|是|到|:|：)?\s*(?P<num>\d+(?:\.\d+)?)", masked
        )
        if width_match and key == "border":
            spec["width"] = float(width_match.group("num"))
        distance_match = re.search(r"阴影\s*(?:距离|偏移)\s*(?:为|是|到|:|：)?\s*(?P<num>\d+(?:\.\d+)?)", masked)
        if distance_match and key == "shadow":
            spec["distance"] = float(distance_match.group("num"))
        angle_match = re.search(r"(?:阴影|文字)?\s*(?:角度)\s*(?:为|是|到|:|：)?\s*(-?\d+(?:\.\d+)?)", masked)
        if angle_match and key == "shadow":
            spec["angle"] = float(angle_match.group(1))
        if "color" not in spec:
            spec["color"] = "#000000"
            warnings.append(f"未指定{word}颜色，默认黑色")
        args[key] = spec
        notes.append(f"{word}{spec.get('color')}")
    if not args:
        return None, None, None

    selector = target_info.get("selector")
    kind = target_info.get("kind")
    if selector is None:
        kind = "caption"
        selector = {"role": ["caption", "title"]}
        warnings.append("未指明目标，默认作用于全部字幕/标题")
    if kind in {"main", "video_all", "bgm", "voice", "audio_all", "sticker", "pip"}:
        clarify.append("描边、阴影和背景只能作用于文字片段")
        return None, selector, kind
    return (
        _action("set_text", confidence=0.82, note="；".join(notes), target=selector, **args),
        selector,
        kind,
    )


def _parse_style(text: str, target_info: dict, inherited, warnings: list[str], clarify: list[str]):
    quotes = target_info.get("quotes") or []
    replace_match = re.search(r"(改成|改为|换成|替换成|替换为|替换|修改为|写成|设为|设置为)", text)
    args = _text_style_args(text)
    source_text = None
    new_text = None
    if replace_match and quotes:
        if len(quotes) >= 2:
            source_text, new_text = quotes[0], quotes[-1]
        else:
            new_text = quotes[0]
        args["text"] = new_text
    elif replace_match and not quotes:
        remainder = text[replace_match.end() :].strip(" 　，,。.!！?？'\"'“”‘’")
        looks_like_style = (
            not remainder
            or bool(re.fullmatch(rf"{_TIME_ALT}|\d+(?:\.\d+)?", remainder))
            or bool(_find_color(remainder))
            or bool(re.search(r"(字号|字体|字[体号]|大小|颜色|加粗|粗体|斜体|下划线|对齐|描边|阴影)", remainder))
        )
        if remainder and not looks_like_style and len(remainder) <= 60:
            args["text"] = remainder
        elif not args:
            clarify.append("请用引号给出要写入的文字，例如：把“欢迎”改成“欢迎来到米乐”")
            return None, None, None

    if not args:
        return None, None, None

    selector = target_info.get("selector")
    kind = target_info.get("kind")
    single = target_info.get("single")

    if source_text:
        selector = _selector_for(kind or "caption")
        selector["text_contains"] = source_text
        single = False
    elif quotes and not replace_match and selector is not None:
        if not single and "text_contains" not in selector:
            selector = dict(selector)
            selector["text_contains"] = quotes[0]

    confidence = 0.9
    if selector is None:
        if inherited and inherited.get("kind") in {"caption", "text_all"}:
            selector = inherited["selector"]
            kind = inherited["kind"]
        else:
            kind = kind or "caption"
            selector = _selector_for(kind)
            warnings.append("未指明文字目标，默认作用于全部字幕")
            confidence = 0.72
    kind = kind or "text_all"
    if kind not in {"caption", "text_all"}:
        clarify.append("字号/颜色/加粗等样式只能作用于字幕或文字片段")
        return None, selector, kind

    selector = _fallback(selector, kind, None, warnings)
    note_parts = []
    for key, label in (
        ("text", "改文案"),
        ("font_size", "改字号"),
        ("color", "改颜色"),
        ("bold", "加粗"),
        ("italic", "斜体"),
        ("underline", "下划线"),
        ("align", "对齐"),
    ):
        if key in args:
            note_parts.append(label)
    action = _action(
        "set_text",
        confidence=confidence,
        note="、".join(note_parts) or "修改文字样式",
        target=selector,
        **args,
    )
    return action, selector, kind


def _parse_add_text(text: str, warnings: list[str], clarify: list[str]):
    quotes = _quotes(text)
    if not quotes:
        clarify.append("请用引号给出要添加的文字，例如：在第 3 秒加字幕“你好”")
        return None, None, None
    content = quotes[0]
    masked = _mask_quotes(text)
    duration = None
    duration_match = re.search(
        rf"(?:持续|显示|停留|时长|共)\s*(?:为|是|:)?\s*(?P<time>{_TIME_ALT})",
        masked,
        re.IGNORECASE,
    )
    if duration_match:
        duration = _to_us(duration_match.group("time"))
    if duration is None:
        duration = 3 * US_PER_SEC
        warnings.append("未给出字幕时长，按 3 秒添加")
    start = _point(text, skip_duration=True) or 0
    style = _text_style_args(text)
    style.pop("text", None)
    return (
        _action(
            "add_text",
            confidence=0.9 if duration_match else 0.75,
            note=f"在 {_sec(start):g}s 添加字幕“{content}” {_sec(duration):g}s",
            text=content,
            start=_sec(start),
            duration=_sec(duration),
            **style,
        ),
        None,
        None,
    )


def _parse_add_audio(text: str, timeline, inherited, warnings: list[str], clarify: list[str]):
    path = _find_path(text)
    if not path:
        clarify.append("请给出音乐文件路径（mp3/wav/m4a 等），或先用 recommend 选择曲库音乐")
        return None, None, None
    volume, _ = _find_volume(text)
    if volume is None:
        volume = 0.25
    replace = bool(re.search(r"(换|替换|更换|改用)", text))
    main_duration = None
    if timeline:
        main_duration = max(
            (seg["end"] for seg in timeline.get("segments") or [] if seg.get("role") == "main"),
            default=0,
        )
    args = {
        "path": path,
        "start": 0,
        "volume": round(float(volume), 3),
        "loop": bool(main_duration),
        "name": re.sub(r"\.[^.]+$", "", path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]),
    }
    if main_duration:
        args["duration"] = _sec(main_duration)
    else:
        warnings.append("未读取到主轨时长，音乐只播放一次，不自动循环")
    actions = []
    if replace:
        actions.append(_action("delete", confidence=0.85, note="移除旧 BGM", target={"role": "bgm"}, ripple=False))
    actions.append(_action("add_audio", confidence=0.9, note=f"加入背景音乐 {path}", **args))
    return actions, None, "bgm"


def _parse_speed(text: str, target_info: dict, inherited, warnings: list[str], clarify: list[str]):
    speed, hint = _find_speed(text)
    if speed is None:
        return None, None, None
    if not 0.1 <= speed <= 10:
        clarify.append(f"变速倍数应在 0.1-10 之间，当前为 {speed}")
        return None, None, None
    selector = target_info.get("selector")
    kind = target_info.get("kind")
    if selector is None and inherited and inherited.get("kind") in {"main", "video_all"}:
        selector = inherited["selector"]
        kind = inherited["kind"]
    if selector is None:
        kind = "main"
        selector = _selector_for(kind)
        warnings.append("未指明变速目标，默认作用于主视频轨")
    if kind not in {"main", "video_all"}:
        clarify.append("变速目前只支持视频片段")
        return None, selector, kind
    keep = bool(re.search(r"(保持时长|总时长不变|时长不变)", text))
    if hint:
        warnings.append(hint)
    return (
        _action(
            "set_speed",
            confidence=0.8 if hint else 0.92,
            note=f"变速 {speed:g}x" + ("，保持时长" if keep else ""),
            target=selector,
            speed=speed,
            keep_duration=keep,
        ),
        selector,
        kind,
    )


def _parse_volume(text: str, target_info: dict, inherited, warnings: list[str], clarify: list[str]):
    volume, source = _find_volume(text)
    if volume is None:
        return None, None, None
    selector = target_info.get("selector")
    kind = target_info.get("kind")
    if selector is None and inherited and inherited.get("kind") in {"bgm", "voice", "audio_all", "main"}:
        selector = inherited["selector"]
        kind = inherited["kind"]
    if selector is None:
        if re.search(r"(背景音乐|配乐|bgm)", text, re.IGNORECASE):
            kind = "bgm"
        elif re.search(r"(配音|旁白|口播)", text):
            kind = "voice"
        elif re.search(r"(原声|视频声音|画面声音)", text):
            kind = "main"
        else:
            kind = "audio_all"
            warnings.append("未指明音频目标，默认作用于全部音频轨")
        selector = _selector_for(kind)
    if kind not in {"bgm", "voice", "audio_all", "main"}:
        clarify.append("音量只能调整视频原声或音频片段")
        return None, selector, kind
    args = {"volume": round(float(volume), 3)}
    fade_in = re.search(rf"淡入\s*(?P<time>{_TIME_ALT})", text, re.IGNORECASE)
    fade_out = re.search(rf"淡出\s*(?P<time>{_TIME_ALT})", text, re.IGNORECASE)
    if fade_in:
        args["fade_in"] = _sec(_to_us(fade_in.group("time")) or 0)
    if fade_out:
        args["fade_out"] = _sec(_to_us(fade_out.group("time")) or 0)
    note = f"音量 {volume:g}" + (f"（{source}）" if source else "")
    return _action("set_volume", confidence=0.92, note=note, target=selector, **args), selector, kind


def _parse_delete(text: str, target_info: dict, inherited, timeline, warnings: list[str], clarify: list[str]):
    selector = target_info.get("selector")
    kind = target_info.get("kind")
    if selector is None and inherited:
        selector = inherited.get("selector")
        kind = inherited.get("kind")
    if selector is None:
        range_info = _range(text)
        point = _point(text)
        if range_info or point is not None:
            kind = "main"
            selector = _selector_for(kind)
            if range_info:
                selector["range"] = [range_info["start_s"], range_info["end_s"]]
            else:
                selector["at"] = _sec(point)
        else:
            clarify.append("请指明要删除的片段，例如：删除第 2 个视频片段 / 删除所有字幕")
            return None, None, None
    selector = _fallback(selector, kind, timeline, warnings)
    if selector is None:
        clarify.append("请指明要删除的片段")
        return None, None, None
    ripple = kind == "main" or (selector.get("role") == "main")
    if selector.get("index") is None and not any(key in selector for key in ("ref", "ids", "range", "at")):
        if kind == "main":
            warnings.append("将删除主轨全部匹配片段，后续片段会自动补位")
    return _action("delete", confidence=0.9, note="删除片段", target=selector, ripple=ripple), selector, kind


def _parse_transition(text: str, target_info: dict, timeline, warnings: list[str], clarify: list[str]):
    transition = _find_transition(text) or "叠化"
    duration = _transition_duration(text)
    if duration is not None and duration <= 0:
        duration = None

    pair = re.search(
        rf"第\s*({_CN_NUM})\s*(?:个)?\s*(?:片段|视频|镜头)?\s*(?:和|与|跟)\s*第\s*({_CN_NUM})\s*(?:个)?",
        text,
    )
    if pair:
        first = _cn_to_int(pair.group(1))
        second = _cn_to_int(pair.group(2))
        if not first or not second or abs(second - first) != 1:
            clarify.append("转场需要加在相邻两个片段之间，请给出相邻的片段序号")
            return None, None, None
        index = first - 1
        target_info = {"selector": _selector_for("main", index), "kind": "main", "single": True}

    selector = target_info.get("selector")
    kind = target_info.get("kind")
    scope = _scope(text)
    ordinal = _ordinal(text)
    if scope is not None or (selector is None and ordinal is None):
        if timeline is None:
            clarify.append("为所有片段添加转场需要先读取草稿时间线")
            return None, None, None
        main = sorted(
            [seg for seg in timeline.get("segments") or [] if seg.get("role") == "main"],
            key=lambda seg: (seg["track_index"], seg["start"], seg["index_in_track"]),
        )
        pairs = []
        for prev, curr in zip(main, main[1:]):
            if prev["track_index"] != curr["track_index"]:
                continue
            if abs(curr["start"] - prev["end"]) <= 50_000:
                pairs.append((prev, curr))
        if not pairs:
            clarify.append("主轨没有首尾相接的片段，无法添加转场")
            return None, None, None
        actions = []
        for prev, curr in pairs:
            args = {"type": transition}
            if duration is not None:
                args["duration"] = _sec(duration)
            actions.append(
                _action(
                    "add_transition",
                    confidence=0.82,
                    note=f"在 {prev['ref']} 与 {curr['ref']} 之间加 {transition}",
                    target={"ref": prev["ref"]},
                    **args,
                )
            )
        if len(actions) > 1:
            warnings.append(f"已为主轨 {len(actions)} 个相邻交界生成转场")
        return actions, {"selector": {"role": "main"}, "kind": "main"}, "main"

    if kind not in {"main", "video_all"}:
        clarify.append("转场只能添加在视频片段之间")
        return None, selector, kind
    selector = _fallback(selector, "main", timeline, warnings)
    args = {"type": transition}
    if duration is not None:
        args["duration"] = _sec(duration)
    return (
        _action(
            "add_transition",
            confidence=0.88,
            note=f"添加 {transition}",
            target=selector,
            first=True,
            **args,
        ),
        selector,
        "main",
    )


def _wants_remove_transition(text: str) -> bool:
    for match in re.finditer(r"(删除|删掉|删去|移除|去掉|去除|清除|取消)", text):
        tail = text[match.end() :]
        position = tail.find("转场")
        if position != -1 and not re.search(r"(加|添加|插入)", tail[:position]):
            return True
    return bool(
        re.search(r"转场[^，,；;。]{0,6}(删除|删掉|删去|移除|去掉|去除|清除|取消)", text)
    )


def _parse_remove_transition(text: str, target_info: dict, timeline, warnings: list[str], clarify: list[str]):
    if timeline is None:
        clarify.append("移除转场需要先读取草稿时间线")
        return None, None, None
    selector = target_info.get("selector")
    kind = target_info.get("kind")
    if kind and kind != "main":
        clarify.append("转场只存在于视频片段之间")
        return None, selector, kind
    if selector is None:
        selector = {"role": "main"}
    matches = select_segments(timeline, selector)
    with_transition = [seg for seg in matches if seg.get("transition")]
    if not with_transition:
        clarify.append("所选片段没有转场")
        return None, selector, "main"
    actions = [
        _action(
            "remove_transition",
            confidence=0.85,
            note=f"移除 {seg['ref']} 的 {seg.get('transition')}",
            target={"ref": seg["ref"]},
        )
        for seg in with_transition
    ]
    return actions, selector, "main"


_FEATURE_REMOVE_RE = re.compile(r"(删除|删掉|删去|移除|去掉|去除|清除|取消|撤掉|拿掉|不要)")
_FEATURE_ADD_RE = re.compile(r"(加|添加|插入|换成|换上|替换成|改成|改为|设为|设置|用|套用|应用|放)")
_ANIMATION_KINDS = {
    "入场": "in",
    "进场": "in",
    "出现": "in",
    "进入": "in",
    "出场": "out",
    "退场": "out",
    "消失": "out",
    "退出": "out",
    "组合": "group",
    "循环": "loop",
}
_FEATURE_TYPE_NAMES = {
    "滤镜": "filter",
    "特效": "effect",
    "蒙版": "mask",
    "动画": "animation",
}


def _wants_remove_feature(text: str) -> bool:
    for match in _FEATURE_REMOVE_RE.finditer(text):
        tail = text[match.end() :]
        for keyword in _FEATURE_KINDS:
            position = tail.find(keyword)
            if position != -1 and not _FEATURE_ADD_RE.search(tail[:position]):
                return True
    return False


def _feature_name(text: str, keyword: str) -> tuple[Optional[str], bool]:
    quotes = _quotes(text)
    if quotes:
        return quotes[0], True
    head = re.sub(r"\s+", " ", text).split(keyword, 1)[0]
    head = re.sub(
        r"(?:把|将|给|在|为|主轨|视频轨|视频片段|视频|片段|镜头|所有|全部|每个|整段|这段|这条|"
        r"加上|添加|加一个|加个|加|插入|换成|换上|替换成|改成|改为|设为|设置|用|套用|应用|放一个|放个|放)",
        "",
        head,
    )
    head = re.sub(
        rf"第\s*(?:{_CN_NUM})\s*(?:个|段|条)\s*(?:视频片段|视频|片段|镜头)?",
        "",
        head,
    )
    if keyword == "动画":
        head = re.sub(r"入场|进场|出现|进入|出场|退场|消失|退出|组合|循环|字幕|文字|标题|花字", "", head)
    head = re.sub(r"\s+", " ", head).strip(" 　，,。.、；;！!？?·")
    if not head or len(head) > 20:
        return None, False
    return head, False


def _animation_kind(text: str) -> str:
    for word, kind in _ANIMATION_KINDS.items():
        if word in text:
            return kind
    return "in"


def _feature_target(target_info: dict, timeline, warnings: list[str], clarify: list[str]):
    selector = target_info.get("selector")
    kind = target_info.get("kind")
    if selector is None:
        selector = _selector_for("main")
        kind = "main"
        warnings.append("未指明目标，默认作用于主轨视频片段")
    if kind not in {"main", "video_all"}:
        clarify.append("滤镜、特效、蒙版和视频动画只能作用于视频片段")
        return None, kind
    return _fallback(selector, kind, timeline, warnings), kind


def _parse_feature(text: str, target_info: dict, timeline, warnings: list[str], clarify: list[str]):
    keywords = [keyword for keyword in _FEATURE_KINDS if keyword in text]
    if not keywords:
        return None, None, None
    keyword = keywords[0]
    text_kinds = {"caption", "text_all", "title"}
    text_animation = keyword == "动画" and (
        target_info.get("kind") in text_kinds or bool(re.search(r"(?:字幕|文字|标题|花字)动画", text))
    )
    if text_animation:
        selector = target_info.get("selector")
        kind = target_info.get("kind")
        if kind in {"main", "video_all"}:
            clarify.append("字幕动画只能作用于文字片段，请指明字幕/标题或用 ref 定位文字")
            return None, None, kind
        if selector is None:
            selector = {"role": ["caption", "title"]}
            kind = "caption"
            warnings.append("未指明目标，默认作用于全部字幕/标题片段")
        else:
            selector = _fallback(selector, kind, timeline, warnings)
    else:
        selector, kind = _feature_target(target_info, timeline, warnings, clarify)
        if selector is None:
            return None, None, kind

    if _wants_remove_feature(text):
        material_type = _FEATURE_TYPE_NAMES[keyword]
        return (
            _action(
                "remove_material",
                confidence=0.9,
                note=f"移除{keyword}",
                target=selector,
                type=material_type,
            ),
            selector,
            kind,
        )

    if not _FEATURE_ADD_RE.search(text):
        return None, None, None
    name, quoted = _feature_name(text, keyword)
    if not name:
        clarify.append(f"请给出{keyword}名称，例如：加“1980”滤镜或换成“线性”蒙版")
        return None, selector, kind

    args = {"name": name}
    if keyword == "滤镜":
        match = re.search(r"(?:强度|浓度)\s*(?:为|是|到|:|：)?\s*(\d+(?:\.\d+)?)\s*%?", text)
        if match:
            args["intensity"] = float(match.group(1))
    elif keyword == "特效":
        args["keep_old"] = not bool(re.search(r"(换成|替换成|改成|改为)", text))
    elif keyword == "动画":
        args["kind"] = _animation_kind(text)
        if text_animation and args["kind"] == "group":
            args["kind"] = "in"
            warnings.append("文字动画不支持组合，已按入场处理")
        duration = _transition_duration(text)
        if duration is not None:
            args["duration"] = _sec(duration)

    op_name = "add_text_animation" if text_animation else _FEATURE_KINDS[keyword][0]
    confidence = 0.88 if quoted else 0.76
    label = "文字动画" if text_animation else keyword
    return (
        _action(op_name, confidence=confidence, note=f"添加{label}{name}", target=selector, **args),
        selector,
        kind,
    )


def _parse_keyframe(text: str, target_info: dict, timeline, warnings: list[str], clarify: list[str]):
    """解析缩放关键帧说法：如“从第2秒到第5秒把画面放大到1.5倍”。

    只支持等比缩放（uniform scale）的两点关键帧；起始值按原始比例 1.0 假设。
    """
    if not re.search(r"(放大|变大|缩小|变小|关键帧)", text):
        return None, None, None
    masked = _mask_quotes(text)
    match = re.search(
        r"(?:放大|变大|缩小|变小)\s*(?:到|至|为|=|:|：)?\s*(?P<value>\d+(?:\.\d+)?)\s*倍",
        masked,
    )
    if not match:
        if "关键帧" in text or re.search(r"(放大|变小|变大|缩小)", text):
            clarify.append("请给出缩放目标值，例如：从第2秒到第5秒把画面放大到1.5倍")
        return None, None, None
    value = float(match.group("value"))
    if not 0.1 <= value <= 10:
        clarify.append(f"缩放倍数建议在 0.1-10 之间，当前为 {value:g}")
        return None, None, None

    # “第2秒到第5秒”这类区间里连接词后带“第”，_RANGE_RE 不接受，需要先剥离
    range_text = re.sub(r"第\s*(?=[零〇一二两三四五六七八九十百千0-9０-９])", "", masked)
    range_info = _range(range_text)
    point = None if range_info else _point(masked)
    if range_info:
        frames = [
            {"at": range_info["start_s"], "value": 1.0},
            {"at": range_info["end_s"], "value": value},
        ]
        note = f"{range_info['start_s']:g}s-{range_info['end_s']:g}s 缩放到 {value:g}x"
    elif point is not None:
        frames = [
            {"at": 0.0, "value": 1.0},
            {"at": _sec(point), "value": value},
        ]
        note = f"0-{_sec(point):g}s 缩放到 {value:g}x"
    else:
        clarify.append("请给出关键帧时间点，例如：从第2秒到第5秒放大到1.5倍")
        return None, None, None

    selector = target_info.get("selector")
    kind = target_info.get("kind")
    if selector is None:
        kind = "main"
        selector = _selector_for("main")
        warnings.append("未指明目标，默认作用于主轨视频片段")
    if kind not in {"main", "video_all"}:
        clarify.append("缩放关键帧目前只支持视频片段")
        return None, selector, kind
    selector = _fallback(selector, kind, timeline, warnings)
    warnings.append("起始缩放按 1.0 假设；片段已有缩放时请用 plan/edit 显式给两个关键帧点")
    return (
        _action(
            "add_keyframes",
            confidence=0.7,
            note=note,
            target=selector,
            property="scale",
            frames=frames,
        ),
        selector,
        kind,
    )


def _parse_split(text: str, target_info: dict, timeline, warnings: list[str], clarify: list[str]):
    point = _point(text)
    if point is None:
        clarify.append("请在指令中给出分割时间点，例如：在第 4 秒分割视频")
        return None, None, None
    selector = target_info.get("selector")
    kind = target_info.get("kind")
    if selector is None:
        kind = "main"
        selector = {"role": "main", "at": _sec(point)}
    selector = _fallback(selector, kind, timeline, warnings)
    return (
        _action(
            "split",
            confidence=0.88,
            note=f"在 {_sec(point):g}s 分割",
            target=selector,
            at=_sec(point),
        ),
        selector,
        kind,
    )


def _segments_on_track(timeline: dict, seg: dict) -> list[dict]:
    mates = [item for item in timeline.get("segments") or [] if item["track_index"] == seg["track_index"]]
    mates.sort(key=lambda item: (item["start"], item["index_in_track"]))
    return mates


def _resolve_single(selector, timeline, clarify: list[str], what: str):
    if timeline is None:
        clarify.append(f"{what}需要先读取草稿时间线")
        return None
    matches = select_segments(timeline, selector)
    if not matches:
        clarify.append(f"没有找到{what}对应的片段")
        return None
    if len(matches) > 1:
        clarify.append(f"{what}匹配到 {len(matches)} 个片段，请缩小范围或指定第几个")
        return None
    return matches[0]


def _parse_head_tail(text: str, target_info: dict, timeline, inherited, warnings: list[str], clarify: list[str]):
    verb = r"(?:剪掉|剪去|删掉|删去|删除|移除|去掉|去除|裁剪掉|裁剪|修剪|缩短|切掉|切去|截掉|裁掉)?"
    head = re.search(rf"(?:开头|片头|最前|前)\s*(?:的)?\s*{verb}\s*(?P<time>{_TIME_ALT})", text, re.IGNORECASE)
    tail = re.search(rf"(?:结尾|片尾|最后|末尾|后面|后)\s*(?:的)?\s*{verb}\s*(?P<time>{_TIME_ALT})", text, re.IGNORECASE)
    match = head or tail
    if not match:
        return None, None, None
    amount = _to_us(match.group("time"))
    if not amount or amount <= 0:
        clarify.append("请给出要剪掉的时长，例如：剪掉开头 2 秒")
        return None, None, None

    selector = target_info.get("selector")
    kind = target_info.get("kind")
    if selector is None and inherited:
        selector = inherited.get("selector")
        kind = inherited.get("kind")
    if selector is None:
        kind = "main"
        selector = _selector_for(kind)

    is_head = bool(head)
    seg = None
    if timeline is not None:
        matches = select_segments(timeline, selector)
        if matches:
            ordered = sorted(matches, key=lambda item: (item["start"], item["index_in_track"]))
            seg = ordered[0] if is_head else ordered[-1]
    if seg is None:
        _resolve_single(selector, timeline, clarify, "裁剪")
        return None, selector, kind
    mates = _segments_on_track(timeline, seg)
    track_type = seg.get("track_type")
    if is_head:
        if mates and mates[0]["id"] != seg["id"]:
            clarify.append("目前只支持从轨道第一个片段剪掉开头；请改用范围裁剪")
            return None, selector, kind
        if amount >= seg["duration"]:
            clarify.append(f"要剪掉的 {amount / US_PER_SEC:g}s 不短于该片段（{seg['duration'] / US_PER_SEC:g}s），请先分割或指定范围")
            return None, selector, kind
        new_start = seg["start"] + amount
        ops = [
            _action(
                "trim",
                confidence=0.85,
                note=f"{seg['ref']} 剪掉开头 {amount / US_PER_SEC:g}s",
                target={"ref": seg["ref"]},
                start=_sec(new_start),
                ripple=False,
            )
        ]
        group = [seg["ref"]] + [item["ref"] for item in mates if item["start"] >= seg["end"]]
        ops.append(
            _action(
                "move",
                confidence=0.85,
                note="剪掉开头后整体前移补位",
                target=group,
                delta=-_sec(amount),
            )
        )
    else:
        if mates and mates[-1]["id"] != seg["id"]:
            clarify.append("目前只支持从轨道最后一个片段剪掉结尾；请改用范围裁剪")
            return None, selector, kind
        if amount >= seg["duration"]:
            clarify.append(f"要剪掉的 {amount / US_PER_SEC:g}s 不短于该片段（{seg['duration'] / US_PER_SEC:g}s），请先分割或指定范围")
            return None, selector, kind
        new_end = seg["end"] - amount
        ops = [
            _action(
                "trim",
                confidence=0.85,
                note=f"{seg['ref']} 剪掉结尾 {amount / US_PER_SEC:g}s",
                target={"ref": seg["ref"]},
                end=_sec(new_end),
                ripple=False,
            )
        ]
        if track_type == "video" and kind == "main":
            warnings.append("只调整了主视频轨，字幕/音频时间未同步；可再用 shift 对齐其它轨道")
    return ops, selector, kind


def _parse_keep(text: str, target_info: dict, timeline, inherited, warnings: list[str], clarify: list[str]):
    match = re.search(
        rf"保留\s*(?P<direction>前|后|开头|结尾|最后)?\s*(?P<time>{_TIME_ALT})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None, None, None
    amount = _to_us(match.group("time"))
    if not amount or amount <= 0:
        clarify.append("请给出保留的时长，例如：第 2 个片段保留前 3 秒")
        return None, None, None
    selector = target_info.get("selector")
    kind = target_info.get("kind")
    if selector is None and inherited:
        selector = inherited.get("selector")
        kind = inherited.get("kind")
    if selector is None:
        clarify.append("请指明要保留哪一截片段，例如：第 2 个视频片段保留前 3 秒")
        return None, None, None
    seg = _resolve_single(selector, timeline, clarify, "保留")
    if seg is None:
        return None, selector, kind
    direction = match.group("direction") or "前"
    if amount >= seg["duration"]:
        warnings.append("目标片段本身已不超过指定时长，无需裁剪")
        return [], selector, kind
    if direction in {"前", "开头"}:
        args = {"end": _sec(seg["start"] + amount)}
        note = f"保留 {seg['ref']} 前 {amount / US_PER_SEC:g}s"
    else:
        args = {"start": _sec(seg["end"] - amount)}
        note = f"保留 {seg['ref']} 后 {amount / US_PER_SEC:g}s"
    return (
        [_action("trim", confidence=0.9, note=note, target={"ref": seg["ref"]}, **args)],
        selector,
        kind,
    )


def _parse_trim(text: str, target_info: dict, timeline, inherited, warnings: list[str], clarify: list[str]):
    keep_ops, selector, kind = _parse_keep(text, target_info, timeline, inherited, warnings, clarify)
    if keep_ops is not None:
        return keep_ops, selector, kind

    if _is_cut_verb(text) and ("开头" in text or "片头" in text or "结尾" in text or "片尾" in text or re.search(r"(前|后)\s*\d", text)):
        return _parse_head_tail(text, target_info, timeline, inherited, warnings, clarify)

    range_info = _range(text)
    if range_info and _is_trim_verb(text):
        selector = target_info.get("selector")
        kind = target_info.get("kind")
        if selector is None and inherited:
            selector = inherited.get("selector")
            kind = inherited.get("kind")
        if selector is None:
            clarify.append("请指明要裁剪的片段，例如：把第 1 个视频片段裁到 2s-6s")
            return None, None, None
        seg = _resolve_single(selector, timeline, clarify, "裁剪")
        if seg is None:
            return None, selector, kind
        return (
            [
                _action(
                    "trim",
                    confidence=0.92,
                    note=f"{seg['ref']} 裁到 {range_info['start_s']:g}s-{range_info['end_s']:g}s",
                    target={"ref": seg["ref"]},
                    start=range_info["start_s"],
                    end=range_info["end_s"],
                )
            ],
            selector,
            kind,
        )
    return None, None, None


def _parse_add_media(text: str, warnings: list[str], clarify: list[str]) -> Optional[dict]:
    """解析素材入轨说法：如“把 D:/a.mp4 加到第3秒”“加个贴纸 click.png”。"""
    path = _find_media_path(text)
    if not path:
        return None
    track = None
    if "贴纸" in text:
        track = "sticker"
    elif re.search(r"(画中画|pip|overlay)", text, re.IGNORECASE):
        track = "pip"
    masked = _mask_quotes(text)
    args: dict = {"path": path}
    if track:
        args["track"] = track
    range_info = _range(masked)
    duration_match = re.search(
        rf"(?:持续|显示|停留|时长|共)\s*(?:为|是|:|：)?\s*(?P<time>{_TIME_ALT})",
        masked,
        re.IGNORECASE,
    )
    if range_info:
        args["start"] = range_info["start_s"]
        args["duration"] = round(range_info["end_s"] - range_info["start_s"], 6)
    elif duration_match:
        duration_us = _to_us(duration_match.group("time"))
        if duration_us:
            args["duration"] = _sec(duration_us)
        point = _point(masked, skip_duration=True)
        if point is not None:
            args["start"] = _sec(point)
    else:
        point = _point(masked)
        if point is not None:
            args["start"] = _sec(point)
        else:
            warnings.append("未给出时间点，素材将追加到目标轨道末尾")
    if re.search(r"\.(?:png|jpg|jpeg|bmp|webp|gif|heic|tif|tiff)$", path, re.IGNORECASE) and "duration" not in args:
        warnings.append("图片未给定时长按 3 秒入片")
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return _action(
        "add_media",
        confidence=0.85,
        note=f"加入素材 {name}" + (f" 到 {track}" if track else ""),
        **args,
    )


def _clause_actions(
    clause: str,
    *,
    timeline,
    inherited,
    warnings: list[str],
    clarify: list[str],
):
    actions, selector, kind = _clause_actions_raw(
        clause,
        timeline=timeline,
        inherited=inherited,
        warnings=warnings,
        clarify=clarify,
    )
    if isinstance(actions, dict):
        actions = [actions]
    return actions or [], selector, kind


def _clause_actions_raw(
    clause: str,
    *,
    timeline,
    inherited,
    warnings: list[str],
    clarify: list[str],
):
    text = clause.strip()
    if not text:
        return [], None, None
    target_info = extract_target(text)
    target_info["selector"] = _fallback(target_info.get("selector"), target_info.get("kind"), timeline, warnings)

    # 非编辑动作：字幕纠错 / 推荐 / 语音转字幕
    if re.search(r"(错别字|错字|纠错|纠正错|校正错|改写错|润色字幕|字幕校对)", text):
        return [_parse_polish(text, target_info)], None, None
    if re.search(r"(推荐|建议|挑一?个|选一?个|选个|找一?个)", text) and re.search(
        r"(音乐|配乐|bgm|转场)", text, re.IGNORECASE
    ):
        return [_parse_recommend(text)], None, None
    if (
        not _find_path(text)
        and re.search(r"(背景音乐|配乐|bgm|音乐)", text, re.IGNORECASE)
        and re.search(r"(配|换|替换|更换|加|添加|放|来一?[首个段])", text, re.IGNORECASE)
    ):
        return [_parse_recommend(text)], None, None
    if re.search(r"(生成|识别|转写|提取|听写|语音转).{0,4}(字幕|文案|台词)", text) or "ASR" in text:
        return [
            _action(
                "transcribe",
                confidence=0.6,
                note="需要可选安装 faster-whisper",
                editable=False,
            )
        ], None, None

    if re.search(r"(画布|分辨率|竖屏|横屏|方形|正方形|比例|\d+\s*[:：]\s*\d+)", text):
        actions = _parse_canvas(text, warnings, clarify)
        if actions:
            return actions, None, None

    if "字幕动画" not in text and re.search(r"(加|添加|插入|放).{0,4}(字幕|文字|标题|花字)", text):
        actions, selector, kind = _parse_add_text(text, warnings, clarify)
        if actions:
            return [actions], selector, kind

    if re.search(r"(背景音乐|配乐|bgm|音乐)", text, re.IGNORECASE) and re.search(
        r"(换|替换|更换|改用|加|添加|放|配)", text
    ) and _find_path(text):
        actions, selector, kind = _parse_add_audio(text, timeline, inherited, warnings, clarify)
        if actions:
            return actions, selector, kind

    if re.search(r"(滤镜|特效|蒙版|动画)", text) and re.search(
        r"(加|添加|插入|换|替换|改成|改为|设为|设置|用|放|移除|删除|去掉|取消|清除|撤掉|不要)",
        text,
    ):
        actions, selector, kind = _parse_feature(text, target_info, timeline, warnings, clarify)
        if actions:
            return [actions] if isinstance(actions, dict) else actions, selector, kind

    if _find_media_path(text) and re.search(r"(添加|插入|加入|放一个|放个|放(?!大|小|映)|来一?个?段?|加(?!剧|倍))", text):
        action = _parse_add_media(text, warnings, clarify)
        if action:
            return [action], None, None

    decoration_action, selector, kind = _parse_decoration(text, target_info, warnings, clarify)
    if decoration_action:
        return [decoration_action], selector, kind

    style_action, selector, kind = _parse_style(text, target_info, inherited, warnings, clarify)
    if style_action:
        return [style_action], selector, kind

    if re.search(r"(分割|拆分|切开|切成两段|切断)", text):
        actions, selector, kind = _parse_split(text, target_info, timeline, warnings, clarify)
        if actions:
            return actions, selector, kind

    speed, hint = _find_speed(text)
    if speed is not None and re.search(r"(加速|加快|减速|放慢|慢放|慢动作|快放|快进|倍速|速度)", text):
        actions, selector, kind = _parse_speed(text, target_info, inherited, warnings, clarify)
        if actions:
            return actions, selector, kind

    if _wants_remove_transition(text):
        actions, selector, kind = _parse_remove_transition(
            text, target_info, timeline, warnings, clarify
        )
        if actions:
            return actions, selector, kind

    if "转场" in text or (
        _find_transition(text) and re.search(r"(加|添加|插入|放|用|做|换|改成)", text)
    ):
        actions, selector, kind = _parse_transition(text, target_info, timeline, warnings, clarify)
        if actions:
            return actions, selector, kind

    keyframe_action, selector, kind = _parse_keyframe(text, target_info, timeline, warnings, clarify)
    if keyframe_action:
        return [keyframe_action], selector, kind

    volume, _source = _find_volume(text)
    if volume is not None and re.search(r"(音量|声音|响度|静音|原声|mute)", text, re.IGNORECASE):
        actions, selector, kind = _parse_volume(text, target_info, inherited, warnings, clarify)
        if actions:
            return actions, selector, kind

    keep_ops, keep_selector, keep_kind = _parse_keep(text, target_info, timeline, inherited, warnings, clarify)
    if keep_ops is not None:
        return keep_ops, keep_selector, keep_kind

    if _is_cut_verb(text) and re.search(r"(开头|片头|结尾|片尾|前|后)", text) and _range(text) is None:
        actions, selector, kind = _parse_head_tail(text, target_info, timeline, inherited, warnings, clarify)
        if actions:
            return actions, selector, kind

    if _range(text) and _is_trim_verb(text) and not _is_delete_verb(text):
        actions, selector, kind = _parse_trim(text, target_info, timeline, inherited, warnings, clarify)
        if actions:
            return actions, selector, kind

    if _is_delete_verb(text):
        actions, selector, kind = _parse_delete(text, target_info, inherited, timeline, warnings, clarify)
        if actions:
            return actions, selector, kind

    return [], None, None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
_ACTION_START = r"(?:把|将|给|在|加|改|删|裁|剪|调|设|换|放|配|分割|静音|添|去|移|再)"
_CLAUSE_RE = re.compile(r"[，,；;。!?！？\n]+|(?:然后|接着|随后|并且|同时|并|再)(?=" + _ACTION_START + r")")
_BARE_DURATION_RE = re.compile(
    rf"^(?:持续|显示|停留|时长|保持|共)?\s*(?P<time>{_TIME_ALT})\s*(?:左右)?$",
    re.IGNORECASE,
)


def _split_clauses(text: str) -> list[str]:
    return [part.strip(" 　") for part in _CLAUSE_RE.split(text) if part and part.strip(" 　")]


def _bare_duration(clause: str) -> Optional[int]:
    match = _BARE_DURATION_RE.match(clause.strip())
    if not match:
        return None
    return _to_us(match.group("time"))


_BARE_DECORATION_RE = re.compile(
    r"^(?:描边)?\s*(?P<field>宽度|粗细|距离|偏移|角度|羽化|模糊)\s*(?:为|是|到|:|：)?\s*(?P<value>-?\d+(?:\.\d+)?)\s*$"
)
_BARE_DECORATION_FIELDS = {
    "宽度": ("border", "width"),
    "粗细": ("border", "width"),
    "距离": ("shadow", "distance"),
    "偏移": ("shadow", "distance"),
    "角度": ("shadow", "angle"),
    "羽化": ("shadow", "feather"),
    "模糊": ("shadow", "feather"),
}


def _bare_decoration_param(clause: str):
    match = _BARE_DECORATION_RE.match(clause.strip())
    if not match:
        return None
    field = _BARE_DECORATION_FIELDS.get(match.group("field"))
    if field is None:
        return None
    return field[0], field[1], float(match.group("value"))


_BARE_INTENSITY_RE = re.compile(
    r"^(?:强度|浓度)\s*(?:为|是|到|:)?\s*(?P<value>\d+(?:\.\d+)?)\s*%?$",
    re.IGNORECASE,
)


def _bare_intensity(clause: str) -> Optional[float]:
    match = _BARE_INTENSITY_RE.match(clause.strip())
    if not match:
        return None
    value = float(match.group("value"))
    if not 0 <= value <= 100:
        return None
    return value


def _tags_for(text: str) -> list[str]:
    result = []
    lower = text.lower()
    for tag, words in INTENT_TAGS.items():
        if any(word.lower() in lower for word in words):
            result.append(tag)
    return result


def _unsupported(text: str) -> list[str]:
    return [label for key, label in _UNSUPPORTED_HINTS.items() if key in text]


def parse_intent(text: str, *, timeline: Optional[dict] = None) -> dict:
    """把中文剪辑指令解析为结构化计划。

    Args:
        text: 用户指令，例如“把第 2 个视频片段裁到 2s-6s，再加 0.3s 叠化”。
        timeline: ``mile.timeline.build_timeline`` 的结果；涉及开头/结尾、
            全部转场、保留前/后 N 秒等依赖片段边界的指令时必须提供。
    """
    raw = str(text or "").strip()
    if not raw:
        raise MileError("MISSING_ARGS", "请提供剪辑指令")

    actions: list[dict] = []
    clarify: list[str] = []
    warnings: list[str] = []
    inherited = None

    for clause in _split_clauses(raw):
        duration = _bare_duration(clause)
        if duration is not None:
            if actions and actions[-1].get("op") == "add_text":
                actions[-1]["duration"] = _sec(duration)
                actions[-1]["note"] = f"字幕持续 {duration / US_PER_SEC:g}s"
            else:
                warnings.append(f"忽略了无法归属的时长描述：{clause}")
            continue

        intensity = _bare_intensity(clause)
        if intensity is not None:
            if actions and actions[-1].get("op") == "set_filter":
                actions[-1]["intensity"] = intensity
                actions[-1]["note"] = f"{actions[-1]['note']}，强度{intensity:g}"
            else:
                warnings.append(f"忽略了无法归属的强度描述：{clause}")
            continue

        decoration_param = _bare_decoration_param(clause)
        if decoration_param is not None:
            action_key, field, value = decoration_param
            attached = None
            for item in reversed(actions):
                if item.get("op") == "set_text" and isinstance(item.get(action_key), dict):
                    attached = item
                    break
            if attached is not None:
                attached[action_key][field] = value
                attached["note"] = f"{attached.get('note', '')} {field}{value:g}"
            else:
                warnings.append(f"忽略了无法归属的装饰参数描述：{clause}")
            continue

        before = len(actions)
        clarify_before = len(clarify)
        clause_actions, selector, kind = _clause_actions(
            clause,
            timeline=timeline,
            inherited=inherited,
            warnings=warnings,
            clarify=clarify,
        )
        actions.extend(clause_actions)
        if selector is not None:
            inherited = {"selector": selector, "kind": kind}
        if len(actions) == before and len(clarify) == clarify_before:
            warnings.append(f"未识别该分句：{clause}")

    unsupported = _unsupported(raw)
    for label in unsupported:
        warnings.append(f"当前意图解析暂不覆盖「{label}」，请用 edit/plan 精确指定")

    if not actions:
        clarify.append("没有识别到可执行的剪辑指令；请说明要改哪个片段、怎么改，例如：删除第 2 个视频片段")

    editable = [item for item in actions if item.get("editable")]
    confidences = [float(item.get("confidence") or 0.5) for item in (editable or actions)] or [0.15]
    confidence = min(confidences)
    if clarify:
        confidence = min(confidence, 0.35)

    plan_ops = []
    for item in editable:
        dropped = {"editable", "confidence"}
        if item.get("op") not in {"add_animation", "add_text_animation"}:
            dropped.add("kind")
        op = {key: value for key, value in item.items() if key not in dropped}
        plan_ops.append(op)
    plan = {"intent": raw, "tags": _tags_for(raw), "ops": plan_ops} if plan_ops and not clarify else None

    notes = [item.get("note") for item in actions if item.get("note")]
    summary = "；".join(dict.fromkeys(notes)) if notes else "未生成操作"
    return {
        "text": raw,
        "confidence": round(confidence, 3),
        "actions": actions,
        "plan": plan,
        "clarify": clarify,
        "warnings": list(dict.fromkeys(warnings)),
        "unsupported": unsupported,
        "tags": _tags_for(raw),
        "summary": summary,
    }


def extract_numbers(text: str) -> dict:
    """调试辅助：返回指令中识别到的时间、倍数、音量与颜色。"""
    volume, volume_source = _find_volume(text)
    speed, speed_hint = _find_speed(text)
    range_info = _range(text)
    return {
        "range": range_info and {"start": range_info["start_s"], "end": range_info["end_s"]},
        "point": _point(text),
        "volume": volume,
        "volume_source": volume_source,
        "speed": speed,
        "speed_hint": speed_hint,
        "color": _find_color(text),
        "font_size": _find_font_size(text),
        "transition": _find_transition(text),
        "canvas": _canvas_size(text),
    }
