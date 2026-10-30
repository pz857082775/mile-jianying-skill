"""基于媒体证据的可解释剪辑决策，不直接修改草稿。"""

from __future__ import annotations

from typing import Optional

from .recommend import recommend_transitions

__all__ = ["decide_timeline"]

_STYLE_RULES = {
    "讲解": {
        "label": "讲解/知识",
        "target_shot_s": (2.5, 8.0),
        "transition": "hard-cut-or-j-cut",
        "effect_intensity": "low",
    },
    "vlog": {
        "label": "Vlog/生活",
        "target_shot_s": (1.2, 5.0),
        "transition": "hard-cut-or-light-dissolve",
        "effect_intensity": "medium",
    },
    "科技": {
        "label": "产品/科技",
        "target_shot_s": (0.6, 3.5),
        "transition": "beat-aligned-hard-cut",
        "effect_intensity": "medium",
    },
    "情绪": {
        "label": "情绪/故事",
        "target_shot_s": (2.0, 7.0),
        "transition": "emotion-led-dissolve",
        "effect_intensity": "low",
    },
}


def _style_for(intent: str, tags: Optional[list[str]]) -> tuple[str, dict]:
    text = f"{intent or ''} {' '.join(tags or [])}".lower()
    for key, rule in _STYLE_RULES.items():
        if key.lower() in text or key in text:
            return key, rule
    if any(word in text for word in ("快节奏", "卡点", "燃", "动感")):
        return "科技", _STYLE_RULES["科技"]
    if any(word in text for word in ("故事", "情绪", "电影", "氛围")):
        return "情绪", _STYLE_RULES["情绪"]
    if any(word in text for word in ("vlog", "日常", "旅行", "生活")):
        return "vlog", _STYLE_RULES["vlog"]
    return "讲解", _STYLE_RULES["讲解"]


def _evidence_confidence(timeline: dict, timeline_signals: Optional[dict], audio: Optional[dict]) -> float:
    value = 0.45
    if timeline.get("segments"):
        value += 0.2
    if timeline_signals and timeline_signals.get("available"):
        value += 0.15
    if audio and audio.get("bpm") is not None:
        value += min(0.15, float(audio.get("bpm_confidence") or 0) * 0.15)
    return round(min(value, 0.95), 3)


def decide_timeline(
    timeline: dict,
    *,
    intent: str = "",
    tags: Optional[list[str]] = None,
    audio: Optional[dict] = None,
    timeline_signals: Optional[dict] = None,
) -> dict:
    """输出剪辑判断、理由、风险和候选计划；不写草稿。"""
    style_key, style = _style_for(intent, tags)
    segments = timeline.get("segments") or []
    main = [seg for seg in segments if seg.get("role") == "main"]
    captions = [seg for seg in segments if seg.get("role") == "caption"]
    voice = [seg for seg in segments if seg.get("role") == "voice"]
    bgm = [seg for seg in segments if seg.get("role") == "bgm"]
    low, high = style["target_shot_s"]
    decisions = []
    plan_ops = []
    manual_review = []

    for seg in main:
        duration_s = float(seg.get("duration") or 0) / 1_000_000
        if duration_s > high:
            decisions.append({
                "decision": "review-long-shot",
                "target": {"ref": seg.get("ref")},
                "why": f"{style['label']}建议镜头约 {low:g}-{high:g}s，当前 {duration_s:.2f}s，建议按语义/停顿拆分",
                "confidence": 0.63,
                "risk": "不能仅凭时长自动删除内容",
                "plan_ops": [],
                "manual_review": True,
            })
            manual_review.append(seg.get("ref"))
        elif duration_s < low:
            decisions.append({
                "decision": "protect-short-shot",
                "target": {"ref": seg.get("ref")},
                "why": f"当前镜头 {duration_s:.2f}s 已短于 {style['label']}建议下限，避免再叠加转场造成闪烁",
                "confidence": 0.78,
                "risk": "短镜头可能是有意的节奏强调",
                "plan_ops": [],
                "manual_review": False,
            })

    bpm = (audio or {}).get("bpm")
    transition_result = recommend_transitions(timeline, bpm=bpm)
    transition_items = transition_result.get("items") or []
    for item in transition_items:
        previous = item.get("after_ref")
        rule_suggests = item.get("transition") and item.get("transition") != "无"
        if not previous or not rule_suggests:
            continue
        use_transition = style_key in {"vlog", "情绪"}
        decision = {
            "decision": "suggest-transition" if use_transition else "prefer-hard-cut",
            "target": {"ref": previous},
            "why": (
                f"{style['label']}按规则可轻量转场（{item.get('reason')}）"
                if use_transition
                else f"{style['label']}以节奏优先，规则虽允许叠化（{item.get('reason')}）但建议保持硬切"
            ),
            "confidence": 0.61 if use_transition else 0.74,
            "risk": "转场只根据时间线与 BPM，不理解画面语义",
            "plan_ops": [],
            "manual_review": True,
        }
        if use_transition:
            decision["plan_ops"] = [{
                "op": "add_transition",
                "target": {"ref": previous},
                "type": item.get("transition") or "叠化",
                "duration": min(float(item.get("duration") or 0.25), 0.4),
            }]
        decisions.append(decision)
    if main and len(main) > 1 and not any(d["decision"] in {"suggest-transition", "prefer-hard-cut"} for d in decisions):
        decisions.append({
            "decision": "prefer-hard-cut",
            "why": "当前镜头之间没有满足规则的转场条件，保持硬切更安全",
            "confidence": 0.72,
            "risk": "没有画面内容证据",
            "plan_ops": [],
            "manual_review": False,
        })

    if voice and bgm:
        decisions.append({
            "decision": "protect-voice-over-bgm",
            "why": "存在旁白与 BGM，先保护语音清晰度；需要 ducking 时应先分析音频再应用",
            "confidence": 0.84,
            "risk": "没有旁白响度测量时不自动改音量",
            "plan_ops": [],
            "manual_review": True,
        })
    loudness = (audio or {}).get("loudness") or {}
    if loudness.get("available") and loudness.get("integrated_lufs") is None and loudness.get("silent"):
        decisions.append({
            "decision": "review-loudness",
            "why": f"素材 {loudness.get('source', '')} 接近数字静音，需确认是否漏录/损坏",
            "confidence": 0.6,
            "risk": "ebur128 对静音输出 -inf，无法给出增益建议",
            "plan_ops": [],
            "manual_review": True,
        })
    elif loudness.get("integrated_lufs") is not None and abs(float(loudness.get("deviation_lu") or 0)) > 3:
        deviation = float(loudness["deviation_lu"])
        measured = float(loudness["integrated_lufs"])
        target = float(loudness.get("target_lufs") or -16)
        suggested = loudness.get("suggested_volume")
        decisions.append({
            "decision": "review-loudness",
            "why": (
                f"实测综合响度 {measured:g} LUFS，比目标 {target:g} LUFS "
                f"偏{'低' if deviation < 0 else '高'} {abs(deviation):g} LU；"
                f"若该素材按原音量入片，约需 volume {suggested}（{float(loudness.get('suggested_gain_db') or 0):+g} dB）"
            ),
            "confidence": 0.6,
            "risk": "LUFS 是整文件测量，不定位到片段；素材与片段的音量映射、混音占比需人工确认",
            "plan_ops": [],
            "manual_review": True,
        })
    if captions:
        decisions.append({
            "decision": "preserve-caption-timing",
            "why": f"检测到 {len(captions)} 条字幕，节奏调整不能覆盖字幕/语音区间",
            "confidence": 0.9,
            "risk": "字幕可能与素材共享引用",
            "plan_ops": [],
            "manual_review": False,
        })

    confidence = _evidence_confidence(timeline, timeline_signals, audio)
    return {
        "method": "explainable-style-rules",
        "available": True,
        "style": {"key": style_key, **style},
        "evidence": {
            "main_count": len(main),
            "caption_count": len(captions),
            "voice_count": len(voice),
            "bgm_count": len(bgm),
            "audio": audio,
            "timeline_signals": timeline_signals,
            "transition_analysis": transition_result,
        },
        "decisions": decisions,
        "plan_ops": plan_ops,
        "confidence": confidence,
        "risk": "第一阶段只使用时间线与信号规则，不包含画面语义理解",
        "manual_review": manual_review,
    }
