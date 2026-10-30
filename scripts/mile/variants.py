"""批量变体：一条底稿参数池生成 N 份差异草稿。

矩阵号 / 多平台 / A-B 测试的产能工具：给定素材与参数池（开头素材、转场、
BGM、画布比例），顺序轮换生成 ``{项目}_v1 .. _vN`` 多份独立草稿，每份单独
``qa`` 自检并写入独立记忆（带 ``variant_of`` / ``variant_index`` 标记）。

设计约定：
- 组合生成是**确定性顺序轮换**（不是随机），结果可复现；
- 相邻变体之间保证至少一个差异化参数不同（除非用户显式指定 ``names``）；
- 单份失败不影响其他变体（跳过并记 warning，全部失败才抛 ``VARIANTS_FAILED``）；
- 与 ``assemble`` 共用同一套素材时长探测与 ``--clip-duration`` 兜底。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from . import quality
from .assemble import assemble
from .errors import MileError

_RATIO_SIZES = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
    "3:4": (1080, 1440),
    "21:9": (2520, 1080),
}


def _ratio_size(ratio) -> tuple[int, int]:
    if isinstance(ratio, (tuple, list)) and len(ratio) == 2:
        return int(ratio[0]), int(ratio[1])
    key = str(ratio or "").strip()
    if key in _RATIO_SIZES:
        return _RATIO_SIZES[key]
    if "x" in key.lower():
        parts = [part.strip() for part in key.lower().split("x")]
        if len(parts) == 2:
            try:
                return int(parts[0]), int(parts[1])
            except ValueError:
                pass
    raise MileError("BAD_ARGS", f"未知比例: {ratio}；支持 {list(_RATIO_SIZES)} 或 宽x高（如 1080x1920）")


def _cycle(values, index: int):
    if not values:
        return None
    return values[index % len(values)]


def _combos(count: int, pools: dict) -> list[dict]:
    """顺序轮换生成 count 个参数组合。"""
    return [
        {
            "opening": _cycle(pools["openings"], i),
            "transition": _cycle(pools["transitions"], i),
            "bgm": _cycle(pools["bgms"], i),
            "ratio": _cycle(pools["ratios"], i),
        }
        for i in range(count)
    ]


def variants(
    project: str,
    clips,
    *,
    root: Optional[str] = None,
    width: int = 1920,
    height: int = 1080,
    fps: int = 30,
    count: int = 3,
    names: Optional[list[str]] = None,
    openings: Optional[list[str]] = None,
    transitions: Optional[list[str]] = None,
    bgms: Optional[list[str]] = None,
    ratios: Optional[list[str]] = None,
    captions=None,
    bgm_volume: float = 0.25,
    clip_duration=None,
    transition_duration=None,
    style: Optional[dict] = None,
    remember: bool = True,
    intent: Optional[str] = None,
    tags: Optional[list[str]] = None,
    notes: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """批量生成 N 份差异草稿。每一份都是独立草稿，命名 ``{项目}_vN``。"""
    project = str(project or "").strip()
    if not project:
        raise MileError("MISSING_ARGS", "缺少草稿名称")

    names = [str(n).strip() for n in (names or []) if str(n).strip()]
    count = max(1, int(count))
    pools = {
        "openings": [str(v) for v in (openings or []) if str(v).strip()],
        "transitions": [str(v) for v in (transitions or []) if str(v).strip()],
        "bgms": [str(v) for v in (bgms or []) if str(v).strip()],
        "ratios": [str(v) for v in (ratios or []) if str(v).strip()],
    }

    # 差异保证：没有显式 names 时，必须有至少一个非空且长度>=2 的参数池
    if not names:
        varied = [pool for pool in pools.values() if len(pool) >= 2]
        if count > 1 and not varied:
            raise MileError(
                "BAD_ARGS",
                "variants 需要至少一个差异化参数池（--openings/--transitions/--bgms/--ratios 至少一个包含 2 个以上选项），"
                "否则变体之间没有结构差异；或改用 --names 显式命名",
            )

    combos = _combos(count, pools)
    all_names = []
    used = set()
    for i in range(count):
        if names and i < len(names):
            name = names[i]
            if name in used:
                raise MileError("BAD_ARGS", f"变体命名重复: {name}")
        else:
            name, suffix = f"{project}_v{i + 1}", 1
            while name in used or not _name_available(name, root, dry_run):
                suffix += 1
                name = f"{project}_v{i + 1}_{suffix}"
        used.add(name)
        all_names.append(name)

    items = []
    failures = []
    for i, combo in enumerate(combos):
        name = all_names[i]
        try:
            if combo["ratio"]:
                width_c, height_c = _ratio_size(combo["ratio"])
            else:
                width_c, height_c = width, height
            opening = combo["opening"]
            clip_list = [opening, *clips] if opening else clips
            variant_tags = [*(tags or []), f"variant_of:{project}", f"variant_index:{i + 1}"]
            result = assemble(
                name,
                clip_list,
                root=root,
                width=width_c,
                height=height_c,
                fps=fps,
                captions=captions,
                bgm=combo["bgm"] or None,
                bgm_volume=bgm_volume,
                clip_duration=clip_duration,
                transition=combo["transition"] or None,
                transition_duration=transition_duration,
                style=style,
                remember=remember,
                intent=intent or f"{project} 变体 {i + 1}",
                tags=variant_tags,
                notes=notes,
                allow_replace=force,
                dry_run=dry_run,
            )
            qa = None
            if not dry_run and result.get("folder"):
                try:
                    from .draft import load_draft

                    draft = load_draft(Path(result["folder"]), root=root)
                    qa = quality.run_checks(draft.data, folder=str(draft.folder), max_issues=100)
                    qa = qa.get("summary") or qa
                except Exception:
                    qa = None
            items.append(
                {
                    "index": i + 1,
                    "name": name,
                    "params": {
                        "opening": opening,
                        "transition": combo["transition"],
                        "bgm": combo["bgm"],
                        "ratio": combo["ratio"],
                        "canvas": f"{width_c}x{height_c}",
                    },
                    "result": result,
                    "qa": qa,
                }
            )
        except MileError as exc:
            failures.append(
                {
                    "index": i + 1,
                    "name": name,
                    "params": combo,
                    "code": exc.code,
                    "reason": exc.reason,
                }
            )
        except Exception as exc:  # noqa: BLE001
            failures.append(
                {
                    "index": i + 1,
                    "name": name,
                    "params": combo,
                    "code": "VARIANTS_ITEM_FAILED",
                    "reason": str(exc),
                }
            )

    if not items and failures:
        raise MileError("VARIANTS_FAILED", f"批量变体全部失败（{len(failures)} 份）", failures=failures)

    return {
        "project": project,
        "requested": count,
        "created": len(items),
        "failed": len(failures),
        "items": items,
        "failures": failures,
        "dry_run": bool(dry_run),
    }


def _name_available(name: str, root: Optional[str], dry_run: bool) -> bool:
    """检查草稿名是否可用（dry-run 时不做磁盘判断）。"""
    if dry_run:
        return True
    from .draft import drafts_root

    folder_root = Path(root).expanduser() if root else drafts_root()
    return not (folder_root / name).exists()