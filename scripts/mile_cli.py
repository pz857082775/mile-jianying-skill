#!/usr/bin/env python3
"""米乐-自动剪映视频 统一命令行。

所有子命令输出统一 JSON 信封::

    {"ok": true, "code": "OK", "reason": "成功", "data": {...}}

失败时 ``ok`` 为 false、``code`` 为稳定错误码、进程退出码为 2。
时间参数默认单位为**秒**，也接受 ``12s`` / ``500ms`` / ``1m2s`` / ``00:12``。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import traceback
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mile import __version__  # noqa: E402
from mile import memory as memory_mod  # noqa: E402
from mile import profile as profile_mod  # noqa: E402
from mile import quality as quality_mod  # noqa: E402
from mile.assemble import assemble  # noqa: E402
from mile.draft import (  # noqa: E402
    Draft,
    drafts_root,
    list_backups,
    list_drafts,
    load_draft,
    undo_draft,
)
from mile.errors import MileError  # noqa: E402
from mile.plan import apply_plan, compile_plan  # noqa: E402
from mile.preview import preview_plan  # noqa: E402
from mile.recommend import analyze_audio, rank_bgm, recommend_transitions  # noqa: E402
from mile.smart import run_smart, run_subtitles  # noqa: E402
from mile.subtitles import asr_available  # noqa: E402
from mile.timecode import format_srt, parse_time  # noqa: E402
from mile.timeline import build_timeline, segment_public, select_segments  # noqa: E402

NAME = "米乐-自动剪映视频"


# ---------------------------------------------------------------------------
# 输出与错误
# ---------------------------------------------------------------------------
def _configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def _emit(payload: dict, pretty: bool = False) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2 if pretty else None)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(text)


def _ok(data, pretty: bool = False) -> int:
    _emit({"ok": True, "code": "OK", "reason": "成功", "data": data}, pretty)
    return 0


def _fail(exc: BaseException, pretty: bool = False, debug: bool = False) -> int:
    if isinstance(exc, MileError):
        payload = {"ok": False, "code": exc.code, "reason": exc.reason}
        if exc.details:
            payload["details"] = exc.details
    else:
        payload = {
            "ok": False,
            "code": "INTERNAL_ERROR",
            "reason": f"{type(exc).__name__}: {exc}",
        }
        if debug:
            payload["details"] = {"traceback": traceback.format_exc()}
    _emit(payload, pretty)
    return 2


def _coerce(raw: str):
    text = str(raw).strip()
    if text == "":
        return ""
    try:
        return json.loads(text)
    except Exception:
        return raw


def _load_json_arg(value: str, what: str):
    """读取 JSON 文件 / 内联 JSON / stdin(``-``)。"""
    if value == "-":
        text = sys.stdin.read()
    elif str(value).lstrip().startswith(("{", "[")):
        text = str(value)
    else:
        path = Path(value).expanduser()
        if not path.exists():
            raise MileError("FILE_NOT_FOUND", f"{what}文件不存在: {path}")
        text = path.read_text(encoding="utf-8-sig")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise MileError("BAD_JSON", f"{what} JSON 解析失败: {exc}") from exc


def _tags(value) -> Optional[list[str]]:
    if not value:
        return None
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).replace("，", ",").split(",") if item.strip()]


def _split_ids(value) -> Optional[list[str]]:
    if value is None:
        return None
    return [item.strip() for item in str(value).replace("，", ",").split(",") if item.strip()]


# ---------------------------------------------------------------------------
# 子命令实现
# ---------------------------------------------------------------------------
def cmd_version(args) -> dict:
    from mile import ensure_vendor_path

    ensure_vendor_path()
    vendor_ok = importlib.util.find_spec("pyJianYingDraft") is not None
    return {
        "name": NAME,
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "vendor_importable": vendor_ok,
        "scripts": str(SCRIPT_DIR),
    }


def cmd_doctor(args) -> dict:
    checks = []

    def check(name, ok, detail="", level="warn"):
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "level": "ok" if ok else level})

    check("python>=3.9", sys.version_info >= (3, 9), sys.version.split()[0], "fail")

    vendor_error = ""
    vendor_ok = False
    try:
        from mile import ensure_vendor_path

        ensure_vendor_path()
        import pyJianYingDraft  # noqa: F401

        vendor_ok = True
    except Exception as exc:  # pragma: no cover - 环境相关
        vendor_error = str(exc)
    check("vendor pyJianYingDraft", vendor_ok, vendor_error or "已就绪", "fail")

    from utils.media_probe import find_ffmpeg, find_ffprobe

    ffprobe = find_ffprobe()
    ffmpeg = find_ffmpeg()
    if ffprobe:
        check("ffprobe", True, ffprobe)
        check("ffmpeg", bool(ffmpeg), ffmpeg or "未找到（导出校验可用 ffprobe）", "info")
    elif ffmpeg:
        check("ffprobe", True, f"未找到，使用 ffmpeg 回退: {ffmpeg}", "info")
        check("ffmpeg", True, ffmpeg)
    else:
        check("ffprobe", False, "未找到（可设置 MILE_FFPROBE）")
        check("ffmpeg", False, "未找到（可设置 MILE_FFMPEG）")

    for module, level, hint in (
        ("pymediainfo", "warn", "素材时长解析更准确"),
        ("uiautomation", "warn", "Windows 自动导出需要"),
        ("requests", "warn", "远程导出 Agent 需要"),
        ("edge_tts", "info", "免费 TTS 需要"),
        ("cv2", "info", "画面分析需要"),
    ):
        found = importlib.util.find_spec(module) is not None
        check(module, found, hint if not found else "已安装", level)

    root = Path(args.root).expanduser() if args.root else drafts_root()
    check("drafts root", root.exists(), str(root), "warn")
    if root.exists():
        check("drafts root writable", os.access(root, os.W_OK), str(root), "warn")

    mem_dir = memory_mod.memory_dir()
    check("memory dir", mem_dir.exists() or os.access(mem_dir.parent, os.W_OK), str(mem_dir), "warn")

    assets_dir = SCRIPT_DIR.parent / "assets"
    try:
        from utils.media_probe import check_assets

        asset_result = check_assets(assets_dir)
        if asset_result.get("ok"):
            detail = f"{asset_result.get('checked', 0)} 个文件可解码"
            skipped = asset_result.get("skipped") or []
            if skipped:
                detail += f"，跳过 {len(skipped)} 个"
        else:
            bad = asset_result.get("bad") or []
            detail = asset_result.get("error") or "；".join(
                f"{Path(item['path']).name}: {item['error']}" for item in bad[:3]
            )
        check("assets decodable", asset_result.get("ok"), detail, "warn")
    except Exception as exc:  # pragma: no cover - 环境相关
        check("assets decodable", False, f"检查失败: {exc}", "info")

    hard = [item for item in checks if not item["ok"] and item["level"] == "fail"]
    return {
        "status": "fail" if hard else ("warn" if any(not c["ok"] for c in checks) else "ok"),
        "checks": checks,
        "drafts_root": str(root),
        "memory_dir": str(mem_dir),
    }


def cmd_drafts(args) -> dict:
    items = list_drafts(args.root, query=args.query, limit=args.limit, detail=args.detail)
    return {"root": str(Path(args.root).expanduser() if args.root else drafts_root()), "count": len(items), "drafts": items}


def _load(args) -> Draft:
    return load_draft(args.draft, root=args.root)


def cmd_timeline(args) -> dict:
    draft = _load(args)
    timeline = build_timeline(draft.data)
    segments = timeline.get("segments") or []
    if not args.full:
        segments = [segment_public(seg) for seg in segments[: args.limit]]
    return {
        "draft": draft.name,
        "folder": str(draft.folder),
        "summary": timeline.get("summary"),
        "canvas": timeline.get("canvas"),
        "fps": timeline.get("fps"),
        "duration": timeline.get("duration"),
        "tracks": timeline.get("tracks"),
        "segments": segments,
    }


def cmd_find(args) -> dict:
    draft = _load(args)
    timeline = build_timeline(draft.data)
    spec: dict = {}
    if args.ref:
        spec["ref"] = args.ref
    if args.ids:
        spec["ids"] = _split_ids(args.ids)
    if args.track is not None:
        spec["track"] = args.track
    if args.index is not None:
        spec["index"] = args.index
    if args.range is not None:
        spec["range"] = args.range
    if args.at is not None:
        spec["at"] = args.at
    if args.text:
        spec["text_contains"] = args.text
    if args.name:
        spec["name_contains"] = args.name
    if args.role:
        spec["role"] = _tags(args.role)
    if args.material_type:
        spec["material_type"] = args.material_type
    if args.min_duration is not None:
        spec["min_duration"] = args.min_duration
    if args.max_duration is not None:
        spec["max_duration"] = args.max_duration
    if args.limit:
        spec["limit"] = args.limit
    if not spec:
        raise MileError("MISSING_ARGS", "至少需要一个定位条件（--ref/--track/--range/--text 等）")
    matches = select_segments(timeline, spec)
    return {
        "draft": draft.name,
        "selector": spec,
        "count": len(matches),
        "segments": [segment_public(seg) for seg in matches],
    }


def _record_memory(args, draft: Draft, outcome: str, *, default_intent: str) -> Optional[dict]:
    if getattr(args, "no_remember", False):
        return None
    try:
        return memory_mod.record_session(
            project=draft.name,
            draft_path=str(draft.folder),
            context={
                "intent": getattr(args, "intent", None) or default_intent,
                "tags": _tags(getattr(args, "tags", None)) or [],
                "notes": getattr(args, "notes", None),
            },
            outcome=outcome,
            rating=getattr(args, "rating", None),
            draft=draft,
        )
    except Exception:
        return None


def cmd_plan(args) -> dict:
    draft = _load(args)
    payload = _load_json_arg(args.plan, "计划")
    if getattr(args, "preview", False):
        result = preview_plan(draft.data, payload)
        result["draft"] = draft.name
        result["folder"] = str(draft.folder)
        return result
    result = apply_plan(draft, payload, dry_run=True)
    result["draft"] = draft.name
    result["folder"] = str(draft.folder)
    return result


def cmd_apply(args) -> dict:
    draft = _load(args)
    payload = _load_json_arg(args.plan, "计划")
    if getattr(args, "preview", False):
        result = preview_plan(draft.data, payload)
        result["draft"] = draft.name
        result["folder"] = str(draft.folder)
        return result
    result = apply_plan(draft, payload, dry_run=args.dry_run, backup=not args.no_backup)
    if not args.dry_run:
        record = _record_memory(args, draft, "applied", default_intent="按计划自动编辑草稿")
        result["memory_id"] = (record or {}).get("id")
    return result


def cmd_edit(args) -> dict:
    draft = _load(args)
    target = args.target
    if target and not str(target).lstrip().startswith("{"):
        target = {"ref": str(target)}
    elif isinstance(target, str):
        target = json.loads(target)
    op: dict = {"op": args.op}
    if target:
        op["target"] = target
    if args.first:
        op["first"] = True
    for item in args.param or []:
        key, _, raw = str(item).partition("=")
        key = key.strip()
        if not key:
            raise MileError("BAD_ARGS", f"无效参数: {item}")
        op[key] = _coerce(raw)
    result = apply_plan(draft, {"intent": args.intent or f"手动 {args.op}", "ops": [op]}, dry_run=args.dry_run)
    if not args.dry_run:
        record = _record_memory(args, draft, "applied", default_intent=f"手动 {args.op}")
        result["memory_id"] = (record or {}).get("id")
    return result


def cmd_qa(args) -> dict:
    draft = _load(args)
    checks = quality_mod.run_checks(draft.data, folder=draft.folder, max_issues=args.max_issues)
    response = {
        "draft": draft.name,
        "folder": str(draft.folder),
        "summary": checks["summary"],
        "issues": checks["issues"],
        "ok": checks["ok"],
        "declared_duration": checks["declared_duration"],
        "actual_duration": checks["actual_duration"],
    }
    if args.fix and checks["issues"]:
        fixed = quality_mod.autofix(draft.data, checks["issues"])
        if fixed["fixed"]:
            draft.save(backup=not args.no_backup)
        after = quality_mod.run_checks(draft.data, folder=draft.folder, max_issues=args.max_issues)
        response["fix"] = fixed
        response["after"] = {"summary": after["summary"], "ok": after["ok"], "issues": after["issues"]}
    return response


def cmd_undo(args) -> dict:
    return undo_draft(args.draft, root=args.root)


def cmd_backups(args) -> dict:
    return {"draft": args.draft, "backups": list_backups(args.draft, root=args.root)}


def cmd_memory(args) -> dict:
    action = args.action
    if action == "list":
        records = memory_mod.list_memory(
            query=args.query,
            tags=_tags(args.tag),
            project=args.project,
            limit=args.limit,
        )
        return {"count": len(records), "records": records}
    if action == "recall":
        context = {"intent": args.intent, "tags": _tags(args.tags)} if (args.intent or args.tags) else {}
        result = memory_mod.recommend_style(
            draft_path=args.draft,
            context=context or None,
            k=args.k,
            tags=_tags(args.tags),
        )
        return result
    if action == "show":
        return memory_mod.get_memory(args.id)
    if action == "forget":
        return memory_mod.forget(args.id)
    if action == "feedback":
        return memory_mod.update_feedback(
            args.id,
            rating=args.rating,
            outcome=args.outcome,
            notes=args.notes,
        )
    if action == "stats":
        return memory_mod.stats()
    if action == "export":
        return memory_mod.export_memory(
            args.out,
            project=args.project,
            since=args.since,
        )
    if action == "import":
        return memory_mod.import_memory(
            args.source,
            mode=args.mode,
            overwrite=args.overwrite,
        )
    raise MileError("BAD_ARGS", f"未知的 memory 动作: {action}")


def cmd_variants(args) -> dict:
    from mile.variants import variants as variants_run

    style = _load_json_arg(args.style, "样式") if args.style else None
    return variants_run(
        args.project,
        args.clips,
        root=args.root,
        width=args.width,
        height=args.height,
        fps=args.fps,
        count=args.count,
        names=_tags(args.projects),
        openings=_tags(args.openings),
        transitions=_tags(args.transitions),
        bgms=_tags(args.bgms),
        ratios=_tags(args.ratios),
        captions=args.captions,
        bgm_volume=args.bgm_volume,
        clip_duration=args.clip_duration,
        transition_duration=args.transition_duration,
        style=style,
        remember=not args.no_remember,
        intent=args.intent,
        tags=_tags(args.tags),
        notes=args.notes,
        force=args.allow_replace,
        dry_run=args.dry_run,
    )


def cmd_profile(args) -> dict:
    action = args.action
    if action == "create":
        return profile_mod.create_profile(
            args.name,
            draft_path=args.draft,
            style=json.loads(args.style) if args.style else None,
            tags=_tags(args.tags),
            notes=args.notes,
            force=args.force,
        )
    if action == "list":
        return {"count": len(profile_mod.list_profiles()), "profiles": profile_mod.list_profiles()}
    if action == "show":
        return profile_mod.get_profile(args.name)
    if action == "update":
        return profile_mod.update_profile(
            args.name,
            style=json.loads(args.style) if args.style else None,
            tags=_tags(args.tags),
            notes=args.notes,
            draft_path=args.draft,
        )
    if action == "delete":
        return profile_mod.delete_profile(args.name)
    if action == "export":
        return profile_mod.export_profile(args.name, args.out)
    if action == "import":
        return profile_mod.import_profile(args.source, force=args.force)
    raise MileError("BAD_ARGS", f"未知的 profile 动作: {action}")


def cmd_srt(args) -> dict:
    draft = _load(args)
    timeline = build_timeline(draft.data)
    segments = [seg for seg in timeline.get("segments") or [] if seg.get("track_type") == "text" and (seg.get("text") or "").strip()]
    if args.role:
        roles = set(_tags(args.role) or [])
        segments = [seg for seg in segments if seg.get("role") in roles]
    segments.sort(key=lambda seg: (seg["start"], seg["track_index"]))
    lines = []
    for index, seg in enumerate(segments, start=1):
        start = format_srt(seg["start"])
        end = format_srt(seg["end"])
        lines.append(f"{index}\n{start} --> {end}\n{seg['text'].strip()}\n")
    content = "\n".join(lines)
    out_path = Path(args.out).expanduser() if args.out else Path.cwd() / f"{args.draft}.srt"
    if args.out == "-":
        return {"count": len(segments), "srt": content}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    return {"count": len(segments), "path": str(out_path), "bytes": len(content.encode("utf-8"))}


def cmd_export(args) -> dict:
    import jy_wrapper  # noqa: F401  (确保环境初始化)

    draft = _load(args)
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        project = jy_wrapper.JyProject(
            draft.folder.name,
            drafts_root=str(Path(args.root).expanduser()) if args.root else str(draft.folder.parent),
            overwrite=False,
        )
        result = project.export_video(
            args.out,
            resolution=args.resolution,
            fps=args.fps,
            remote_host=args.remote_host,
        )
    result["draft"] = draft.name
    result["log"] = buffer.getvalue().strip().splitlines()[-5:]
    if result.get("status") != "success":
        raise MileError(
            "EXPORT_FAILED",
            result.get("error") or "导出失败",
            export=result,
        )
    return result


def _clip_specs(clips: list[str]) -> list[dict]:
    """把 ``path@3s`` 解析为带时长的素材对象；解析失败按普通路径处理。"""
    items = []
    for raw in clips:
        text = str(raw)
        path, sep, duration = text.rpartition("@")
        if sep and path and duration:
            try:
                parse_time(duration)
                items.append({"path": path, "duration": duration})
                continue
            except MileError:
                pass
        items.append(text)
    return items


def cmd_assemble(args) -> dict:
    style = None
    recall = None
    if not args.no_memory_style:
        recall = memory_mod.recommend_style(
            draft_path=args.style_from,
            context={"intent": args.intent, "tags": _tags(args.tags)} if (args.intent or args.tags) else None,
            k=3,
            tags=_tags(args.tags),
        )
        style = recall.get("style") or None
    if args.style:
        override = _load_json_arg(args.style, "样式")
        style = {**(style or {}), **override}
    result = assemble(
        args.project,
        _clip_specs(args.clips),
        root=args.root,
        width=args.width,
        height=args.height,
        fps=args.fps,
        captions=args.captions,
        bgm=args.bgm,
        bgm_volume=args.bgm_volume,
        clip_duration=args.clip_duration,
        transition=args.transition,
        transition_duration=args.transition_duration,
        style=style,
        remember=not args.no_remember,
        intent=args.intent,
        tags=_tags(args.tags),
        notes=args.notes,
        allow_replace=args.allow_replace,
        dry_run=args.dry_run,
    )
    if recall:
        result["memory"] = {
            "match_count": recall.get("match_count"),
            "profiles": recall.get("profiles"),
            "advice": recall.get("advice"),
        }
    return result


def cmd_smart(args) -> dict:
    draft = _load(args)
    return run_smart(
        draft,
        args.instruction,
        apply=args.apply,
        polish=args.polish,
        split_max=args.split_max,
        line_chars=args.line_chars,
        max_lines=args.max_lines,
        recommend=args.recommend,
        audio=args.audio,
        library=args.library,
        limit=args.limit,
        tags=_tags(args.tags),
        glossary=args.glossary,
        roles=_tags(args.roles) or ("caption", "title"),
        backup=not args.no_backup,
        remember=not args.no_remember,
        rating=args.rating,
        notes=args.notes,
    )


def cmd_subtitles(args) -> dict:
    draft = _load(args)
    return run_subtitles(
        draft,
        apply=args.apply,
        split_max=args.split_max,
        line_chars=args.line_chars,
        max_lines=args.max_lines,
        glossary=args.glossary,
        roles=_tags(args.roles) or ("caption", "title"),
        backup=not args.no_backup,
    )


def cmd_recommend(args) -> dict:
    draft = _load(args)
    timeline = build_timeline(draft.data)
    want_bgm = bool(args.bgm) or not args.transitions
    want_transitions = bool(args.transitions) or not args.bgm
    result: dict = {
        "draft": draft.name,
        "folder": str(draft.folder),
        "duration": timeline.get("duration"),
        "asr_available": asr_available(),
    }
    analysis = None
    if args.audio:
        try:
            analysis = analyze_audio(args.audio)
            result["audio"] = analysis
        except MileError as exc:
            result["audio"] = {"available": False, "code": exc.code, "reason": exc.reason}
    if want_bgm:
        target = timeline.get("summary", {}).get("main_duration") or timeline.get("duration") or 0
        try:
            result["bgm"] = rank_bgm(
                args.library,
                duration_s=(target / 1_000_000) if target else None,
                mood=analysis,
                tags=_tags(args.tags),
                limit=args.limit,
            )
        except MileError as exc:
            result["bgm"] = {"available": False, "code": exc.code, "reason": exc.reason}
    if want_transitions:
        result["transitions"] = recommend_transitions(
            timeline,
            bpm=(analysis or {}).get("bpm"),
        )
    return result


def cmd_analyze(args) -> dict:
    """只读媒体/时间线证据分析，不改草稿。"""
    from mile.analysis import analyze_media, analyze_timeline_signals, capability_status

    result: dict = {"capabilities": capability_status()}
    if args.media:
        result["media"] = analyze_media(
            args.media,
            analyze_audio_flag=not args.no_audio,
            analyze_scenes=bool(getattr(args, "scenes", False)),
        )
        return result
    draft = _load(args)
    timeline = build_timeline(draft.data)
    result.update(
        {
            "draft": draft.name,
            "folder": str(draft.folder),
            "timeline": analyze_timeline_signals(timeline),
        }
    )
    return result


def cmd_decide(args) -> dict:
    """只读剪辑决策：证据 → 可解释建议 → 候选计划，不改草稿。"""
    from mile.analysis import analyze_media, analyze_timeline_signals
    from mile.decide import decide_timeline

    draft = _load(args)
    timeline = build_timeline(draft.data)
    signals = analyze_timeline_signals(timeline)
    audio = None
    if args.audio:
        try:
            audio = analyze_audio(args.audio)
            from mile.analysis import measure_loudness

            try:
                audio["loudness"] = measure_loudness(args.audio)
            except MileError as exc:
                audio["loudness"] = {"available": False, "code": exc.code, "reason": exc.reason}
        except MileError as exc:
            audio = {"available": False, "code": exc.code, "reason": exc.reason}
    result = decide_timeline(
        timeline,
        intent=args.intent or "",
        tags=_tags(args.tags),
        audio=audio,
        timeline_signals=signals,
    )
    result.update({"draft": draft.name, "folder": str(draft.folder), "duration": timeline.get("duration")})
    if args.media:
        result["media"] = analyze_media(args.media, analyze_audio_flag=not args.no_audio)
    if args.plan_out:
        Path(args.plan_out).expanduser().write_text(
            json.dumps({"intent": args.intent or "", "ops": result.get("plan_ops") or []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        result["plan_file"] = str(Path(args.plan_out).expanduser())
    return result


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", help="草稿根目录（默认自动探测）")
    parser.add_argument("--pretty", action="store_true", help="美化 JSON 输出")
    parser.add_argument("--debug", action="store_true", help="出错时输出 traceback")


def _draft_parser(subparsers, name: str, help_text: str, func) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, help=help_text)
    parser.add_argument("draft", help="草稿名称或草稿文件夹路径")
    _common(parser)
    parser.set_defaults(func=func)
    return parser


def _selector_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ref", help="片段 ref / id 前缀")
    parser.add_argument("--ids", help="多个片段 ref，逗号分隔")
    parser.add_argument("--track", help="轨道：序号 / 名称 / video#2 / 视频1")
    parser.add_argument("--index", help="轨道内序号（从 0 开始，或 last）")
    parser.add_argument("--range", help="时间范围，如 12s-18s")
    parser.add_argument("--at", help="时间点，如 12.5s")
    parser.add_argument("--text", help="文本包含")
    parser.add_argument("--name", help="素材名/路径包含")
    parser.add_argument("--role", help="角色：caption/title/bgm/voice/pip/sticker 等")
    parser.add_argument("--material-type", help="素材类型：video/photo/text/audio/sticker")
    parser.add_argument("--min-duration", help="最短时长")
    parser.add_argument("--max-duration", help="最长时长")


def _memory_write_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--intent", help="本次剪辑需求描述（用于记忆召回）")
    parser.add_argument("--tags", help="标签，逗号分隔")
    parser.add_argument("--rating", type=float, help="自评分 1-5")
    parser.add_argument("--notes", help="备注")
    parser.add_argument("--no-remember", action="store_true", help="本次不写入记忆")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mile", description=NAME)
    subparsers = parser.add_subparsers(dest="command", required=True)

    version = subparsers.add_parser("version", help="显示版本与环境")
    _common(version)
    version.set_defaults(func=lambda args: cmd_version(args))

    doctor = subparsers.add_parser("doctor", help="检查运行环境")
    _common(doctor)
    doctor.set_defaults(func=cmd_doctor)

    drafts = subparsers.add_parser("drafts", help="列出草稿")
    drafts.add_argument("--query", help="按名称过滤")
    drafts.add_argument("--limit", type=int, default=50)
    drafts.add_argument("--detail", action="store_true", help="附带时长/画布摘要（较慢）")
    _common(drafts)
    drafts.set_defaults(func=cmd_drafts)

    timeline = _draft_parser(subparsers, "timeline", "查看草稿时间线", cmd_timeline)
    timeline.add_argument("--limit", type=int, default=200, help="返回的片段数量上限")
    timeline.add_argument("--full", action="store_true", help="返回全部片段字段")

    find = _draft_parser(subparsers, "find", "按条件查找片段", cmd_find)
    _selector_args(find)
    find.add_argument("--limit", type=int, default=50)

    plan = _draft_parser(subparsers, "plan", "校验剪辑计划（不保存）", cmd_plan)
    plan.add_argument("--plan", required=True, help="计划 JSON 文件路径，或 '-' 从 stdin 读取")
    plan.add_argument("--preview", action="store_true", help="输出内存执行后的差异预览（不落盘）")

    apply = _draft_parser(subparsers, "apply", "应用剪辑计划", cmd_apply)
    apply.add_argument("--plan", required=True, help="计划 JSON 文件路径，或 '-' 从 stdin 读取")
    apply.add_argument("--dry-run", action="store_true")
    apply.add_argument("--preview", action="store_true", help="只输出差异预览，不落盘")
    apply.add_argument("--no-backup", action="store_true")
    _memory_write_args(apply)

    edit = _draft_parser(subparsers, "edit", "对单个操作做精准编辑", cmd_edit)
    edit.add_argument("--op", required=True, help="delete/trim/move/shift/split/set_text/set_volume/set_speed/set_transform/set_visible/set_canvas/add_transition/remove_transition/clone_text/add_text/add_audio")
    edit.add_argument("--target", help="片段 ref，或定位条件 JSON")
    edit.add_argument("--param", action="append", help="操作参数 key=value，可重复")
    edit.add_argument("--first", action="store_true", help="多目标时只取第一个")
    edit.add_argument("--dry-run", action="store_true")
    _memory_write_args(edit)

    qa = _draft_parser(subparsers, "qa", "质量检查与安全修复", cmd_qa)
    qa.add_argument("--fix", action="store_true", help="自动修复可确定的问题")
    qa.add_argument("--no-backup", action="store_true")
    qa.add_argument("--max-issues", type=int, default=300)

    undo = _draft_parser(subparsers, "undo", "撤销最近一次保存", cmd_undo)
    backups = _draft_parser(subparsers, "backups", "列出草稿备份", cmd_backups)

    srt = _draft_parser(subparsers, "srt", "导出字幕 SRT", cmd_srt)
    srt.add_argument("--out", help="输出文件路径；'-' 直接返回文本")
    srt.add_argument("--role", help="只导出指定角色字幕")

    smart = _draft_parser(subparsers, "smart", "自然语言智能剪辑（默认只预览）", cmd_smart)
    smart.add_argument("instruction", nargs="?", help="中文剪辑指令，如：把BGM音量调到30%%")
    smart.add_argument("--apply", action="store_true", help="真正写入草稿（默认 dry-run）")
    smart.add_argument("--polish", action="store_true", help="对字幕做纠错/断句")
    smart.add_argument("--split-max", type=int, help="字幕每条最大字数（开启断句）")
    smart.add_argument("--line-chars", type=int, help="字幕单行字数上限")
    smart.add_argument("--max-lines", type=int, default=2, help="字幕最多行数")
    smart.add_argument("--recommend", choices=["bgm", "transition", "all"], help="附加推荐")
    smart.add_argument("--audio", help="用于 BPM 分析/语音转字幕的音视频文件")
    smart.add_argument("--library", help="BGM 曲库 CSV 路径")
    smart.add_argument("--limit", type=int, default=5, help="推荐数量上限")
    smart.add_argument("--glossary", help="术语表文件（字幕纠错）")
    smart.add_argument("--roles", default="caption,title", help="参与纠错的角色，逗号分隔")
    smart.add_argument("--no-backup", action="store_true")
    _memory_write_args(smart)

    subs = _draft_parser(subparsers, "subtitles", "字幕纠错与断句（默认只预览）", cmd_subtitles)
    subs.add_argument("--apply", action="store_true", help="真正写入草稿（默认 dry-run）")
    subs.add_argument("--split-max", type=int, help="每条字幕最大字数（开启断句）")
    subs.add_argument("--line-chars", type=int, help="单行字数上限")
    subs.add_argument("--max-lines", type=int, default=2, help="最多行数")
    subs.add_argument("--glossary", help="术语表文件")
    subs.add_argument("--roles", default="caption,title", help="参与纠错的角色，逗号分隔")
    subs.add_argument("--no-backup", action="store_true")

    rec = _draft_parser(subparsers, "recommend", "推荐 BGM 与转场（只读）", cmd_recommend)
    rec.add_argument("--bgm", action="store_true", help="只推荐 BGM")
    rec.add_argument("--transitions", action="store_true", help="只推荐转场")
    rec.add_argument("--audio", help="配乐/人声音频文件（BPM 分析）")
    rec.add_argument("--library", help="BGM 曲库 CSV 路径")
    rec.add_argument("--limit", type=int, default=5)
    rec.add_argument("--tags", help="偏好标签，逗号分隔")

    analyze = _draft_parser(subparsers, "analyze", "分析媒体与时间线证据（只读）", cmd_analyze)
    analyze.add_argument("--media", help="分析指定音视频文件；省略则分析草稿时间线")
    analyze.add_argument("--no-audio", action="store_true", help="跳过音频 BPM/能量分析")
    analyze.add_argument("--scenes", action="store_true", help="附带镜头边界检测（需完整解码一遍）")

    decide = _draft_parser(subparsers, "decide", "给出可解释剪辑决策（只读）", cmd_decide)
    decide.add_argument("--intent", help="剪辑意图，如：科技产品 30 秒竖屏")
    decide.add_argument("--tags", help="偏好标签，逗号分隔")
    decide.add_argument("--audio", help="配乐/人声音频文件（BPM 分析）")
    decide.add_argument("--media", help="额外分析指定音视频文件")
    decide.add_argument("--no-audio", action="store_true", help="跳过音频 BPM/能量分析")
    decide.add_argument("--plan-out", help="把候选操作计划写入 JSON 文件（供 plan/apply 使用）")

    export = _draft_parser(subparsers, "export", "调用剪映导出视频（GUI 自动化）", cmd_export)
    export.add_argument("--out", required=True, help="输出 MP4 路径")
    export.add_argument("--resolution", default="1080", help="480/720/1080/2K/4K/8K")
    export.add_argument("--fps", default="30", help="24/25/30/50/60")
    export.add_argument("--remote-host", help="远程 Windows Export Agent 地址")

    assemble_parser = subparsers.add_parser("assemble", help="按素材与历史风格组装草稿")
    assemble_parser.add_argument("--project", required=True, help="新草稿名称")
    assemble_parser.add_argument("clips", nargs="+", help="视频/图片素材路径")
    assemble_parser.add_argument("--width", type=int, default=1920)
    assemble_parser.add_argument("--height", type=int, default=1080)
    assemble_parser.add_argument("--fps", type=int, default=30)
    assemble_parser.add_argument("--captions", help="SRT/JSON/TXT 字幕文件")
    assemble_parser.add_argument("--bgm", help="背景音乐路径")
    assemble_parser.add_argument("--bgm-volume", type=float, default=0.25)
    assemble_parser.add_argument(
        "--clip-duration", help="所有素材统一入片时长（如 3s；ffprobe 缺失时的兜底）"
    )
    assemble_parser.add_argument("--transition", help="转场名称，如 叠化")
    assemble_parser.add_argument("--transition-duration", help="转场时长，如 0.5s")
    assemble_parser.add_argument("--style-from", help="参考草稿；用于召回相似历史风格")
    assemble_parser.add_argument("--style", help="样式 JSON 文件或内联 JSON，覆盖记忆风格")
    assemble_parser.add_argument("--no-memory-style", action="store_true", help="不使用历史风格")
    assemble_parser.add_argument("--allow-replace", action="store_true")
    assemble_parser.add_argument("--dry-run", action="store_true")
    _memory_write_args(assemble_parser)
    _common(assemble_parser)
    assemble_parser.set_defaults(func=cmd_assemble)

    variants_parser = subparsers.add_parser("variants", help="批量生成差异草稿（矩阵号/多平台/A-B 测试）")
    variants_parser.add_argument("--project", required=True, help="底稿名称；变体命名为 {project}_v1..vN")
    variants_parser.add_argument("clips", nargs="+", help="视频/图片素材路径")
    variants_parser.add_argument("--count", type=int, default=3, help="变体数量")
    variants_parser.add_argument("--projects", help="显式变体命名，逗号分隔（数量需与变体数匹配）")
    variants_parser.add_argument("--openings", help="开头素材池，逗号分隔")
    variants_parser.add_argument("--transitions", help="转场池，逗号分隔")
    variants_parser.add_argument("--bgms", help="BGM 池，逗号分隔")
    variants_parser.add_argument("--ratios", help="画布比例池，逗号分隔（16:9/9:16/1:1/4:5/宽x高）")
    variants_parser.add_argument("--width", type=int, default=1920)
    variants_parser.add_argument("--height", type=int, default=1080)
    variants_parser.add_argument("--fps", type=int, default=30)
    variants_parser.add_argument("--captions", help="SRT/JSON/TXT 字幕文件")
    variants_parser.add_argument("--bgm-volume", type=float, default=0.25)
    variants_parser.add_argument("--clip-duration", help="所有素材统一入片时长（ffprobe 缺失时兜底）")
    variants_parser.add_argument("--transition-duration", help="转场时长，如 0.5s")
    variants_parser.add_argument("--style", help="样式 JSON 文件或内联 JSON")
    variants_parser.add_argument("--allow-replace", action="store_true")
    variants_parser.add_argument("--dry-run", action="store_true")
    _memory_write_args(variants_parser)
    _common(variants_parser)
    variants_parser.set_defaults(func=cmd_variants)

    memory = subparsers.add_parser("memory", help="查看/召回/反馈/迁移剪辑记忆")
    memory.add_argument("--action", required=True, choices=["list", "recall", "show", "forget", "feedback", "stats", "export", "import"])
    memory.add_argument("--id", help="记忆 id")
    memory.add_argument("--query", help="文本检索")
    memory.add_argument("--tag", action="append", help="标签过滤，可重复")
    memory.add_argument("--project", help="项目过滤（export 时按项目导出）")
    memory.add_argument("--draft", help="recall 时参考的草稿")
    memory.add_argument("--intent", help="recall 时的需求描述")
    memory.add_argument("--tags", help="recall 时的标签，逗号分隔")
    memory.add_argument("--k", type=int, default=3, help="召回数量")
    memory.add_argument("--limit", type=int, default=20)
    memory.add_argument("--rating", type=float)
    memory.add_argument("--outcome")
    memory.add_argument("--notes")
    memory.add_argument("--out", help="export 的输出文件路径")
    memory.add_argument("--source", help="import 的源文件路径")
    memory.add_argument("--mode", default="merge", choices=["merge", "replace"], help="import 模式")
    memory.add_argument("--since", help="export 起始时间，如 2026-01-01")
    memory.add_argument("--overwrite", action="store_true", help="import 时覆盖已存在的同 id 记忆")
    _common(memory)
    memory.set_defaults(func=cmd_memory)

    profile = subparsers.add_parser("profile", help="系列风格档案管理")
    profile.add_argument("--action", required=True, choices=["create", "list", "show", "update", "delete", "export", "import"])
    profile.add_argument("--name", help="档案名")
    profile.add_argument("--draft", help="参考草稿（create/update 时提取风格）")
    profile.add_argument("--style", help="显式风格 JSON（覆盖草稿提取结果）")
    profile.add_argument("--tags", help="标签，逗号分隔")
    profile.add_argument("--notes", help="备注")
    profile.add_argument("--force", action="store_true", help="允许覆盖同名档案")
    profile.add_argument("--out", help="export 的输出文件路径")
    profile.add_argument("--source", help="import 的源文件路径")
    _common(profile)
    profile.set_defaults(func=cmd_profile)

    return parser


def main(argv=None) -> int:
    _configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        data = args.func(args)
        return _ok(data, getattr(args, "pretty", False))
    except MileError as exc:
        return _fail(exc, getattr(args, "pretty", False), getattr(args, "debug", False))
    except KeyboardInterrupt:
        return _fail(MileError("INTERRUPTED", "用户中断"), False)
    except Exception as exc:  # noqa: BLE001
        return _fail(exc, getattr(args, "pretty", False), getattr(args, "debug", False))


if __name__ == "__main__":
    raise SystemExit(main())
