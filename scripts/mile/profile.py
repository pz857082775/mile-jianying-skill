"""系列风格档案（style profile）。

把一个账号 / 栏目 / 系列的风格固定成可命名的档案：字幕字号、颜色、位置、
转场、BGM 音量、镜头节奏、画布比例等。档案存于 ``MILE_MEMORY_DIR/profiles/``
（默认 ``~/.mile-jianying/memory/profiles/``），按名字增删查改，可导出 / 导入
用于团队共享与换机迁移，也可被 ``assemble --profile`` 直接套用。

- 创建：``create_profile(name, draft_path=..., style=..., tags=..., notes=...)``
- 套用：``resolve_style(name)`` 返回档案的 style 字典（供 assemble 使用）
- 覆盖：同名创建需 ``force=True``，版本号自增，旧版留在 ``versions/`` 备份
- 迁移：``export_profile`` / ``import_profile`` 与记忆包走同一本地明文 JSON
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .draft import load_draft
from .errors import MileError
from .memory import extract_style

__all__ = [
    "profile_dir",
    "create_profile",
    "list_profiles",
    "get_profile",
    "update_profile",
    "delete_profile",
    "export_profile",
    "import_profile",
    "resolve_style",
    "_slug",
]

_PROFILE_VERSION = 1
_MAX_IMPORT_BYTES = 20 * 1024 * 1024


def profile_dir() -> Path:
    env = os.environ.get("MILE_PROFILE_DIR")
    if env:
        path = Path(env).expanduser()
    else:
        from .memory import memory_dir

        path = memory_dir() / "profiles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _slug(name: str) -> str:
    """档案文件名安全化：保留中文、字母、数字、-_，其余替换为 _。"""
    text = str(name or "").strip()
    if not text:
        raise MileError("BAD_ARGS", "档案名不能为空")
    cleaned = re.sub(r'[^\w\u4e00-\u9fff\-.]', "_", text, flags=re.UNICODE)
    cleaned = cleaned.strip("._")
    if not cleaned:
        raise MileError("BAD_ARGS", "档案名只能包含中文、字母、数字与 -_")
    return cleaned


def _profile_path(name: str) -> Path:
    return profile_dir() / f"{_slug(name)}.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _load_profile(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MileError("PROFILE_CORRUPT", f"档案文件损坏: {path.name}: {exc}") from exc
    if not isinstance(payload, dict) or not payload.get("name"):
        raise MileError("PROFILE_CORRUPT", f"档案文件缺少 name 字段: {path.name}")
    return payload


def _write_profile(payload: dict, path: Path) -> None:
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def create_profile(
    name: str,
    *,
    draft_path: Optional[str] = None,
    style: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    notes: Optional[str] = None,
    force: bool = False,
) -> dict:
    """从参考草稿（或显式 style）创建一个系列风格档案。"""
    path = _profile_path(name)
    if path.exists() and not force:
        raise MileError(
            "PROFILE_EXISTS",
            f"档案「{name}」已存在；如需覆盖请加 force=True（版本号将自增，旧版保留在 versions/）",
        )

    style_data = dict(style or {})
    source_draft = None
    if draft_path:
        source_draft = str(draft_path)
        try:
            draft = load_draft(draft_path)
            style_data = extract_style(draft.data)
        except MileError:
            if not style:
                raise MileError("DRAFT_NOT_FOUND", f"无法读取参考草稿: {draft_path}")
    if not style_data:
        raise MileError("BAD_ARGS", "创建档案需要 style 或可读取 draft_path 之一")

    version = 1
    if path.exists():
        old = _load_profile(path)
        version = int(old.get("version") or 1) + 1
        _backup_version(path, old)
    now = _now()
    payload = {
        "kind": "mile-profile",
        "name": str(name).strip(),
        "version": version,
        "created_at": now,
        "updated_at": now,
        "source_draft": source_draft,
        "tags": [str(t) for t in (tags or [])],
        "notes": notes,
        "style": style_data,
    }
    _write_profile(payload, path)
    return {"name": payload["name"], "path": str(path), "version": version, "style": style_data}


def _backup_version(path: Path, payload: dict) -> None:
    versions_dir = path.parent / "versions"
    versions_dir.mkdir(parents=True, exist_ok=True)
    backup = versions_dir / f"{path.stem}.v{payload.get('version', 0)}.json"
    backup.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def list_profiles() -> list[dict]:
    items = []
    for path in sorted(profile_dir().glob("*.json")):
        try:
            payload = _load_profile(path)
            style = payload.get("style") or {}
            caption = style.get("caption") or {}
            items.append(
                {
                    "name": payload.get("name"),
                    "version": payload.get("version"),
                    "updated_at": payload.get("updated_at"),
                    "created_at": payload.get("created_at"),
                    "source_draft": payload.get("source_draft"),
                    "tags": payload.get("tags") or [],
                    "summary": {
                        "font_size": caption.get("font_size"),
                        "color": caption.get("color"),
                        "transform_y": caption.get("transform_y"),
                        "transitions": (style.get("transitions") or [])[:3],
                        "bgm": (style.get("bgm") or [])[:3],
                        "canvas": style.get("canvas"),
                        "avg_shot_ms": style.get("avg_shot_ms"),
                    },
                }
            )
        except MileError:
            continue
    return items


def get_profile(name: str) -> dict:
    path = _profile_path(name)
    if not path.exists():
        names = ", ".join(item["name"] for item in list_profiles()[:10]) or "（无档案）"
        raise MileError("PROFILE_NOT_FOUND", f"档案「{name}」不存在；可用的档案: {names}")
    return _load_profile(path)


def resolve_style(name: str) -> dict:
    """返回档案的 style 字典；不存在时抛 PROFILE_NOT_FOUND。"""
    return get_profile(name).get("style") or {}


def _deep_merge(base: dict, extra: dict) -> dict:
    """递归合并两层 dict；子 dict 按键合并，其余直接被 extra 覆盖。"""
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def update_profile(
    name: str,
    *,
    style: Optional[dict] = None,
    tags: Optional[list[str]] = None,
    notes: Optional[str] = None,
    draft_path: Optional[str] = None,
) -> dict:
    """更新档案（tags / notes / style / draft_path）；版本号自增并备份旧版。"""
    path = _profile_path(name)
    if not path.exists():
        raise MileError("PROFILE_NOT_FOUND", f"档案「{name}」不存在")
    payload = _load_profile(path)
    _backup_version(path, payload)
    payload["version"] = int(payload.get("version") or 1) + 1
    payload["updated_at"] = _now()
    if draft_path:
        try:
            payload["style"] = extract_style(load_draft(draft_path).data)
            payload["source_draft"] = str(draft_path)
        except MileError as exc:
            raise MileError("DRAFT_NOT_FOUND", f"无法读取参考草稿: {exc.reason}") from exc
    if style:
        payload["style"] = _deep_merge(payload.get("style") or {}, style)
    if tags is not None:
        payload["tags"] = [str(t) for t in tags]
    if notes is not None:
        payload["notes"] = notes
    _write_profile(payload, path)
    return {"name": name, "version": payload["version"], "updated_at": payload["updated_at"]}


def delete_profile(name: str) -> dict:
    path = _profile_path(name)
    if not path.exists():
        raise MileError("PROFILE_NOT_FOUND", f"档案「{name}」不存在")
    os.remove(path)
    return {"deleted": name, "remaining": len(list_profiles())}


def export_profile(name: str, out: str | os.PathLike) -> dict:
    """把单个档案导出为 JSON 文件（含 kind 标记，供 import_profile 回收）。"""
    payload = get_profile(name)
    payload = dict(payload)
    payload["kind"] = "mile-profile"
    path = Path(str(out)).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return {"name": name, "version": payload.get("version"), "path": str(path)}


def import_profile(source: str | os.PathLike, *, force: bool = False) -> dict:
    """从导出的档案 JSON 导入；默认同名已存在时拒绝（force=True 覆盖）。"""
    path = Path(str(source)).expanduser()
    if not path.exists():
        raise MileError("IMPORT_FAILED", f"导入文件不存在: {path}")
    if path.stat().st_size > _MAX_IMPORT_BYTES:
        raise MileError("IMPORT_FAILED", "导入文件超过大小上限")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MileError("IMPORT_BAD_FORMAT", f"导入文件不是合法 JSON: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("kind") != "mile-profile" or not payload.get("name"):
        raise MileError("IMPORT_BAD_FORMAT", "导入文件缺少 mile-profile 标记或 name 字段")
    name = payload["name"]
    target = _profile_path(name)
    if target.exists() and not force:
        raise MileError("PROFILE_EXISTS", f"档案「{name}」已存在；如需覆盖请加 force=True")
    payload.setdefault("version", 1)
    payload["updated_at"] = _now()
    _write_profile(payload, target)
    return {"name": name, "version": payload["version"], "path": str(target)}