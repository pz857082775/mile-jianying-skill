"""智能编排：把自然语言、字幕整理与推荐组合成一次可预览/可应用的流程。

设计原则：
- 默认只预览（``apply=False``），任何写盘动作都必须显式开启；
- 复用 ``plan.apply_plan`` 的事务校验，字幕改写失败时回滚内存；
- 结果中明确区分“已执行”“仅建议”“能力不可用”，不把规则建议包装成 AI 结论。
"""

from __future__ import annotations

import copy
from typing import Optional

from . import memory as memory_mod
from . import ops
from .draft import Draft
from .errors import MileError
from .intent import parse_intent
from .plan import apply_plan
from .quality import run_checks
from .recommend import analyze_audio, rank_bgm, recommend_transitions
from .subtitles import asr_available, correct_timeline, layout_caption, transcribe
from .timeline import build_timeline

__all__ = ["run_smart", "run_subtitles", "apply_subtitle_fixes"]


def _material_use_counts(data: dict) -> dict:
    counts: dict[str, int] = {}
    for track in data.get("tracks") or []:
        if str(track.get("type") or "") != "text":
            continue
        for seg in track.get("segments") or []:
            material_id = str(seg.get("material_id") or "")
            if material_id:
                counts[material_id] = counts.get(material_id, 0) + 1
    return counts


def apply_subtitle_fixes(
    data: dict,
    items: list[dict],
    *,
    split_max: Optional[int] = None,
    line_chars: Optional[int] = None,
    max_lines: int = 2,
) -> dict:
    """把 :func:`correct_timeline` 的结果写入草稿数据（不保存）。"""
    snapshot = copy.deepcopy(data)
    uses = _material_use_counts(data)
    changed = []
    split = []
    skipped = []
    try:
        for item in items:
            seg_id = str(item.get("id") or "")
            try:
                track, seg, _ = ops._find_segment(data, seg_id)
            except MileError:
                skipped.append({"ref": item.get("ref"), "reason": "片段已不存在"})
                continue
            if str(track.get("type") or "") != "text":
                skipped.append({"ref": item.get("ref"), "reason": "目标不是文字片段"})
                continue
            material_id = str(seg.get("material_id") or "")
            if uses.get(material_id, 0) > 1:
                skipped.append(
                    {
                        "ref": item.get("ref"),
                        "reason": "文字素材被多个片段共享，自动改写会同时影响其它字幕",
                    }
                )
                continue

            parts = item.get("parts") or []
            corrected = item.get("corrected") or item.get("original") or ""
            if split_max and len(parts) > 1:
                target = seg.get("target_timerange") or {}
                start_us = int(target.get("start") or 0)
                duration_us = int(target.get("duration") or 0)
                weights = [max(1, len(part)) for part in parts]
                total_weight = sum(weights)
                durations = [int(duration_us * weight / total_weight) for weight in weights]
                durations[-1] += duration_us - sum(durations)
                fps = float(data.get("fps") or 30) or 30.0
                minimum = max(1, int(round(1_000_000 / fps)))
                if duration_us <= 0 or min(durations) < minimum:
                    raise MileError("SUBTITLE_TOO_SHORT", "字幕时长为 0 或拆分后不足一帧，无法拆分")
                rendered = [
                    layout_caption(part, line_chars, max_lines) if line_chars else part for part in parts
                ]
                track_index = (data.get("tracks") or []).index(track)
                clones = []
                offset = 0
                for index, (text, part_us) in enumerate(zip(rendered, durations)):
                    if index:
                        clone = ops.clone_text_segment(
                            data,
                            seg_id,
                            text=text,
                            start=(start_us + offset) / 1_000_000,
                            duration=part_us / 1_000_000,
                            track=track_index,
                        )
                        clones.append({"ref": clone["ref"], "text": text})
                    offset += part_us
                ops.set_text(data, [seg_id], text=rendered[0])
                ops.trim_segment(data, seg_id, end=(start_us + durations[0]) / 1_000_000, ripple=False)
                split.append(
                    {
                        "ref": item.get("ref"),
                        "original": item.get("original"),
                        "parts": rendered,
                        "clones": clones,
                    }
                )
            else:
                if not corrected:
                    continue
                ops.set_text(data, [seg_id], text=corrected)
                changed.append(
                    {
                        "ref": item.get("ref"),
                        "original": item.get("original"),
                        "corrected": corrected,
                    }
                )
    except Exception as exc:
        data.clear()
        data.update(snapshot)
        if isinstance(exc, MileError):
            raise
        raise MileError("SUBTITLE_APPLY_FAILED", f"字幕写入失败: {exc}") from exc
    return {"changed": changed, "split": split, "skipped": skipped}


def _qa_payload(draft: Draft, *, stage: str, max_issues: int = 100) -> dict:
    checks = run_checks(draft.data, folder=draft.folder, max_issues=max_issues)
    return {
        "stage": stage,
        "ok": checks["ok"],
        "summary": checks["summary"],
        "declared_duration": checks["declared_duration"],
        "actual_duration": checks["actual_duration"],
        "issues": checks["issues"][:max_issues],
    }


def _intent_payload(parsed: Optional[dict]) -> Optional[dict]:
    if not parsed:
        return None
    return {
        "text": parsed.get("text"),
        "confidence": parsed.get("confidence"),
        "tags": parsed.get("tags"),
        "summary": parsed.get("summary"),
        "warnings": parsed.get("warnings"),
        "unsupported": parsed.get("unsupported"),
        "clarify": parsed.get("clarify"),
    }


def run_subtitles(
    draft: Draft,
    *,
    apply: bool = False,
    split_max: Optional[int] = None,
    line_chars: Optional[int] = None,
    max_lines: int = 2,
    glossary=None,
    roles=("caption", "title"),
    backup: bool = True,
) -> dict:
    """字幕纠错/断句流水线；默认只返回预览。"""
    timeline = build_timeline(draft.data)
    report = correct_timeline(
        timeline,
        glossary=glossary,
        roles=roles,
        max_chars=split_max,
    )
    response = {
        "draft": draft.name,
        "folder": str(draft.folder),
        "apply": bool(apply),
        "checked": report["checked"],
        "changed": report["changed"],
        "items": report["items"],
    }
    if not apply:
        return response
    if not report["items"]:
        response["qa"] = _qa_payload(draft, stage="current")
        return response

    applied = apply_subtitle_fixes(
        draft.data,
        report["items"],
        split_max=split_max,
        line_chars=line_chars,
        max_lines=max_lines,
    )
    draft.save(backup=backup)
    response["applied"] = applied
    response["qa"] = _qa_payload(draft, stage="after")
    return response


def _collect_recommendations(
    timeline: dict,
    kind: Optional[str],
    *,
    audio=None,
    library=None,
    tags=None,
    limit: int = 5,
) -> dict:
    result: dict = {}
    analysis = None
    if kind in {"bgm", "transition", "all"} and audio:
        try:
            analysis = analyze_audio(audio)
            result["audio"] = analysis
        except MileError as exc:
            result["audio"] = {"available": False, "code": exc.code, "reason": exc.reason}
    if kind in {"bgm", "all"}:
        duration_s = timeline.get("summary", {}).get("main_duration") or timeline.get("duration") or 0
        try:
            result["bgm"] = rank_bgm(
                library,
                duration_s=(duration_s / 1_000_000) if duration_s else None,
                mood=analysis,
                tags=tags,
                limit=limit,
            )
        except MileError as exc:
            result["bgm"] = {"available": False, "code": exc.code, "reason": exc.reason}
    if kind in {"transition", "all"}:
        result["transitions"] = recommend_transitions(
            timeline,
            bpm=(analysis or {}).get("bpm"),
        )
    return result


def run_smart(
    draft: Draft,
    instruction: Optional[str] = None,
    *,
    apply: bool = False,
    polish: bool = False,
    split_max: Optional[int] = None,
    line_chars: Optional[int] = None,
    max_lines: int = 2,
    recommend: Optional[str] = None,
    audio=None,
    library=None,
    limit: int = 5,
    tags=None,
    glossary=None,
    roles=("caption", "title"),
    backup: bool = True,
    remember: bool = True,
    rating: Optional[float] = None,
    notes: Optional[str] = None,
) -> dict:
    """自然语言 + 智能流水线；``apply=False`` 时只返回计划与预览。"""
    if not instruction and not polish and not recommend and not audio:
        raise MileError("MISSING_ARGS", "请提供剪辑指令，或使用 --polish / --recommend / --audio")

    timeline = build_timeline(draft.data)
    parsed = parse_intent(instruction, timeline=timeline) if instruction else None
    warnings: list[str] = list(parsed.get("warnings") or []) if parsed else []
    clarify: list[str] = list(parsed.get("clarify") or []) if parsed else []
    if parsed and parsed.get("clarify"):
        return {
            "draft": draft.name,
            "folder": str(draft.folder),
            "instruction": instruction,
            "apply": bool(apply),
            "intent": _intent_payload(parsed),
            "plan": None,
            "clarify": parsed["clarify"],
            "warnings": warnings,
            "apply_hint": "先补充指令，再执行 smart",
        }

    plan_ops = list(((parsed or {}).get("plan") or {}).get("ops") or [])
    do_polish = bool(polish)
    recommend_kind = recommend
    do_transcribe = False
    for action in (parsed or {}).get("actions") or []:
        if action.get("editable"):
            continue
        if action.get("op") == "polish_subtitles":
            do_polish = True
        elif action.get("op") == "recommend":
            recommend_kind = action.get("kind") or recommend_kind or "bgm"
        elif action.get("op") == "transcribe":
            do_transcribe = True

    asr_result = None
    caption_ops: list[dict] = []
    if do_transcribe:
        if not audio:
            warnings.append("语音转字幕需要 --audio 指定音视频文件")
        elif not asr_available():
            asr_result = {
                "available": False,
                "code": "ASR_UNAVAILABLE",
                "reason": "未安装 faster-whisper；可改用外部字幕文件或先安装本地 ASR 后端",
            }
            warnings.append("ASR 后端不可用，未生成字幕")
        else:
            try:
                asr_result = transcribe(audio, glossary=glossary, max_chars=split_max or 24)
                caption_ops = [
                    {
                        "op": "add_text",
                        "text": caption["text"],
                        "start": caption["start"],
                        "duration": caption["duration"],
                    }
                    for caption in asr_result.get("captions") or []
                ]
            except MileError as exc:
                asr_result = {"available": True, "ok": False, "code": exc.code, "reason": exc.reason}
                warnings.append(f"语音转写失败：{exc.reason}")

    subtitle_report = None
    if do_polish:
        subtitle_report = correct_timeline(
            timeline,
            glossary=glossary,
            roles=roles,
            max_chars=split_max,
        )

    final_ops = plan_ops + caption_ops
    final_plan = (
        {
            "intent": (parsed or {}).get("text") or instruction or "智能处理",
            "tags": (parsed or {}).get("tags") or list(tags or []),
            "ops": final_ops,
        }
        if final_ops
        else None
    )

    plan_result = None
    subtitle_applied = None
    if apply:
        if subtitle_report and subtitle_report["items"]:
            subtitle_applied = apply_subtitle_fixes(
                draft.data,
                subtitle_report["items"],
                split_max=split_max,
                line_chars=line_chars,
                max_lines=max_lines,
            )
        elif subtitle_report is not None:
            subtitle_applied = {"changed": [], "split": [], "skipped": []}

        if final_plan:
            plan_result = apply_plan(draft, final_plan, dry_run=False, backup=backup)
        elif subtitle_applied and (subtitle_applied["changed"] or subtitle_applied["split"]):
            draft.save(backup=backup)
        if plan_result:
            warnings.extend(plan_result.get("warnings") or [])
            warnings = list(dict.fromkeys(warnings))
    elif final_plan:
        plan_result = apply_plan(draft, final_plan, dry_run=True)
        warnings.extend(plan_result.get("warnings") or [])
        warnings = list(dict.fromkeys(warnings))

    recommendations = _collect_recommendations(
        timeline if not apply else build_timeline(draft.data),
        recommend_kind,
        audio=audio,
        library=library,
        tags=tags,
        limit=limit,
    )

    memory_id = None
    if apply and remember:
        try:
            record = memory_mod.record_session(
                project=draft.name,
                draft_path=str(draft.folder),
                context={
                    "intent": instruction or "智能字幕/推荐处理",
                    "tags": list(tags or []) or (parsed or {}).get("tags") or ["smart"],
                    "notes": notes,
                },
                outcome="smart-applied",
                rating=rating,
                draft=draft,
            )
            memory_id = record.get("id")
        except Exception:
            memory_id = None

    qa = _qa_payload(draft, stage="after" if apply else "current")
    return {
        "draft": draft.name,
        "folder": str(draft.folder),
        "instruction": instruction,
        "apply": bool(apply),
        "intent": _intent_payload(parsed),
        "plan": final_plan,
        "plan_result": plan_result,
        "subtitles": {
            "preview": subtitle_report,
            "applied": subtitle_applied,
        }
        if subtitle_report is not None
        else None,
        "asr": asr_result,
        "recommend": recommendations or None,
        "warnings": warnings,
        "qa": qa,
        "memory_id": memory_id,
    }
