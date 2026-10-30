"""剪映草稿的定位、加载、备份与原子保存。

之所以直接操作 ``draft_content.json``，而不是只依赖模板对象：
剪映 7/8 的草稿包含大量库未覆盖的字段，直接改 JSON 能最大程度保留原草稿。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .errors import MileError

__all__ = [
    "Draft",
    "drafts_root",
    "list_drafts",
    "resolve_draft",
    "load_draft",
    "save_draft",
    "list_backups",
    "undo_draft",
    "structure_signature",
    "content_fingerprint",
    "refresh_duration",
    "read_meta",
    "sync_meta",
]

BACKUP_DIRNAME = ".mile_backups"
_BACKUP_KEEP = 40


def drafts_root() -> Path:
    """返回剪映草稿根目录，可用 ``MILE_DRAFT_ROOT`` 覆盖。"""
    env = os.environ.get("MILE_DRAFT_ROOT")
    if env:
        return Path(env).expanduser()

    candidates = []
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.append(Path(local) / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft")
    candidates.append(Path.home() / "Movies" / "JianyingPro" / "User Data" / "Projects" / "com.lveditor.draft")

    try:  # 优先复用 skill 自身的路径解析（支持自定义安装）
        import sys

        scripts_dir = Path(__file__).resolve().parent.parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from utils.formatters import get_default_drafts_root  # type: ignore

        resolved = get_default_drafts_root()
        if resolved:
            candidates.insert(0, Path(resolved))
    except Exception:
        pass

    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def resolve_draft(target: str | os.PathLike, root: Optional[str | os.PathLike] = None) -> Path:
    """把草稿名称/路径解析为草稿文件夹。"""
    if target is None or str(target).strip() == "":
        raise MileError("DRAFT_REQUIRED", "请提供草稿名称或草稿文件夹路径")

    raw = Path(str(target)).expanduser()
    if raw.is_file() and raw.name == "draft_content.json":
        return raw.parent
    if raw.is_dir() and (raw / "draft_content.json").exists():
        return raw

    base = Path(root).expanduser() if root else drafts_root()
    candidate = base / str(target)
    if candidate.is_dir() and (candidate / "draft_content.json").exists():
        return candidate

    if raw.is_dir():
        raise MileError("DRAFT_NOT_FOUND", f"目录中没有 draft_content.json: {raw}")

    matches = []
    if base.is_dir():
        needle = str(target).strip().lower()
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            if entry.name.lower() == needle:
                matches.append(entry)
        if not matches:
            for entry in base.iterdir():
                if entry.is_dir() and needle in entry.name.lower():
                    matches.append(entry)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise MileError(
            "DRAFT_AMBIGUOUS",
            f"找到多个名称相近的草稿: {[m.name for m in matches[:8]]}",
            candidates=[str(m) for m in matches[:20]],
        )
    raise MileError("DRAFT_NOT_FOUND", f"未找到草稿: {target}", root=str(base))


def read_meta(folder: str | os.PathLike) -> dict:
    path = Path(folder) / "draft_meta_info.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def list_drafts(
    root: Optional[str | os.PathLike] = None,
    *,
    query: Optional[str] = None,
    limit: int = 100,
    detail: bool = False,
) -> list[dict]:
    """列出草稿文件夹；``detail`` 为真时附带时长/画布等摘要。"""
    base = Path(root).expanduser() if root else drafts_root()
    if not base.exists():
        return []
    items = []
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        content = folder / "draft_content.json"
        if not content.is_file():
            continue
        meta = read_meta(folder)
        name = str(meta.get("draft_name") or folder.name)
        if query and str(query).lower() not in name.lower():
            continue
        try:
            stat = content.stat()
        except OSError:
            continue
        item = {
            "name": name,
            "folder": str(folder),
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        }
        if detail:
            try:
                data = json.loads(content.read_text(encoding="utf-8"))
                canvas = data.get("canvas_config") or {}
                item.update(
                    {
                        "duration": int(data.get("duration") or 0),
                        "duration_label": _duration_label(int(data.get("duration") or 0)),
                        "canvas": f"{int(canvas.get('width') or 0)}x{int(canvas.get('height') or 0)}",
                        "fps": data.get("fps"),
                        "tracks": len(data.get("tracks") or []),
                    }
                )
            except Exception:
                item["unreadable"] = True
        items.append(item)
    items.sort(key=lambda entry: entry.get("modified") or "", reverse=True)
    return items[: int(limit)]


def _duration_label(us: int) -> str:
    seconds = max(0, us) // 1_000_000
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@dataclass
class Draft:
    """已加载的草稿。保存前所有修改都只发生在内存中。"""

    name: str
    folder: Path
    content_path: Path
    data: dict
    meta: dict = field(default_factory=dict)

    @property
    def id(self) -> str:
        return str(self.data.get("id") or "")

    @property
    def fps(self) -> float:
        try:
            return float(self.data.get("fps") or 0)
        except Exception:
            return 0.0

    @property
    def canvas(self) -> dict:
        canvas = self.data.get("canvas_config") or {}
        return {"width": int(canvas.get("width") or 0), "height": int(canvas.get("height") or 0), "ratio": canvas.get("ratio")}

    def save(self, *, backup: bool = True) -> Path:
        refresh_duration(self.data)
        return save_draft(self.data, self.content_path, backup=backup)

    def reload(self) -> "Draft":
        fresh = load_draft(self.folder)
        self.data = fresh.data
        self.meta = fresh.meta
        return self


def load_draft(target: str | os.PathLike, root: Optional[str | os.PathLike] = None) -> Draft:
    folder = resolve_draft(target, root)
    content_path = folder / "draft_content.json"
    try:
        data = json.loads(content_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MileError("DRAFT_INVALID", f"草稿 JSON 解析失败: {content_path}", error=str(exc)) from exc
    except OSError as exc:
        raise MileError("DRAFT_READ_FAILED", f"无法读取草稿: {content_path}", error=str(exc)) from exc

    meta = read_meta(folder)
    name = str(meta.get("draft_name") or folder.name)
    return Draft(name=name, folder=folder, content_path=content_path, data=data, meta=meta)


def _backup_dir(content_path: Path) -> Path:
    return content_path.parent / BACKUP_DIRNAME


def _next_backup_path(content_path: Path) -> Path:
    folder = _backup_dir(content_path)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    return folder / f"draft_content-{stamp}.json"


def _backup_file(content_path: Path) -> Path:
    backup = _next_backup_path(content_path)
    shutil.copy2(content_path, backup)
    backups = sorted(_backup_dir(content_path).glob("draft_content-*.json"))
    for old in backups[:-_BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass
    return backup


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{int(time.time() * 1000)}")
    try:
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def sync_meta(content_path: str | os.PathLike, data: dict) -> Optional[Path]:
    """同步 draft_meta_info 中与内容有关的展示字段。

    剪映首页以草稿文件夹名和草稿元数据为准。复制/重命名草稿后如果只写
    ``draft_content.json``，首页仍会显示旧名称与旧时长。
    """
    path = Path(content_path)
    if path.name != "draft_content.json":
        path = path / "draft_content.json"
    meta_path = path.parent / "draft_meta_info.json"
    if not meta_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None

    try:
        folder = path.parent
        try:
            duration = int(data.get("duration") or 0)
        except (TypeError, ValueError):
            duration = 0

        meta["draft_name"] = folder.name
        meta["tm_duration"] = duration
        meta["tm_draft_modified"] = int(time.time() * 1_000_000)
        meta["draft_fold_path"] = str(folder.resolve())
        meta["draft_root_path"] = str(folder.parent.resolve())
        _atomic_write_json(meta_path, meta)
    except Exception:
        return None
    return meta_path


def save_draft(data: dict, content_path: str | os.PathLike, *, backup: bool = True) -> Path:
    """原子保存草稿；默认先建备份，便于一键撤销。"""
    path = Path(content_path)
    if path.name != "draft_content.json":
        path = path / "draft_content.json"
    if not path.parent.exists():
        raise MileError("DRAFT_NOT_FOUND", f"草稿目录不存在: {path.parent}")

    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if backup and path.exists():
        _backup_file(path)

    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{int(time.time() * 1000)}")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
        sync_meta(path, data)
    except PermissionError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise MileError(
            "DRAFT_LOCKED",
            "剪映正在占用该草稿，请先在剪映中关闭此草稿后重试",
            path=str(path),
        ) from exc
    except OSError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise MileError("DRAFT_WRITE_FAILED", f"写入草稿失败: {path}", error=str(exc)) from exc
    return path


def list_backups(target: str | os.PathLike, root: Optional[str | os.PathLike] = None) -> list[dict]:
    folder = resolve_draft(target, root)
    result = []
    for path in sorted(_backup_dir(folder / "draft_content.json").glob("draft_content-*.json"), reverse=True):
        stat = path.stat()
        result.append(
            {
                "path": str(path),
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
        )
    return result


def undo_draft(target: str | os.PathLike, root: Optional[str | os.PathLike] = None) -> dict:
    """恢复最近一次备份；当前内容会先另存为 ``pre-undo`` 备份。"""
    folder = resolve_draft(target, root)
    content_path = folder / "draft_content.json"
    backups = sorted(_backup_dir(content_path).glob("draft_content-*.json"), reverse=True)
    if not backups:
        raise MileError("NO_BACKUP", "没有可用的备份")

    latest = backups[0]
    try:
        restored = json.loads(latest.read_text(encoding="utf-8"))
    except Exception as exc:
        raise MileError("BACKUP_INVALID", f"备份文件损坏: {latest}", error=str(exc)) from exc

    if content_path.exists():
        pre = _backup_dir(content_path) / f"pre-undo-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')[:-3]}.json"
        shutil.copy2(content_path, pre)
    save_draft(restored, content_path, backup=False)
    return {
        "restored_from": str(latest),
        "restored_at": datetime.fromtimestamp(latest.stat().st_mtime).isoformat(timespec="seconds"),
        "draft": str(content_path),
    }


def _material_map(data: dict) -> dict:
    result = {}
    materials = data.get("materials") or {}
    if not isinstance(materials, dict):
        return result
    for items in materials.values():
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("id"):
                    result[str(item["id"])] = item
    return result


def _text_of(material: dict) -> str:
    content = material.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except Exception:
            content = None
    if isinstance(content, dict):
        return str(content.get("text") or "")
    return str(material.get("base_content") or "")


def structure_signature(data: dict) -> dict:
    """提取与素材绝对路径无关的结构特征，用于记忆召回与相似度。"""
    mats = _material_map(data)
    track_counts: dict[str, int] = {}
    track_segments: dict[str, int] = {}
    texts: list[str] = []
    media_names: set[str] = set()
    transition_names: list[str] = []
    filter_names: list[str] = []
    effect_names: list[str] = []
    font_sizes: list[float] = []
    text_colors: list[str] = []
    shot_lengths: list[int] = []
    speeds: list[float] = []
    volumes: list[float] = []

    for track in data.get("tracks") or []:
        ttype = str(track.get("type") or "unknown")
        segs = track.get("segments") or []
        track_counts[ttype] = track_counts.get(ttype, 0) + 1
        track_segments[ttype] = track_segments.get(ttype, 0) + len(segs)
        for seg in segs:
            mat = mats.get(str(seg.get("material_id") or "")) or {}
            target = seg.get("target_timerange") or {}
            duration = int(target.get("duration") or 0)
            if ttype == "video" and duration > 0:
                shot_lengths.append(duration)
            if ttype == "audio":
                try:
                    volumes.append(round(float(seg.get("volume", 1.0)), 2))
                except Exception:
                    pass
            try:
                speeds.append(round(float(seg.get("speed", 1.0)), 2))
            except Exception:
                pass
            if ttype == "text":
                text = _text_of(mat).strip()
                if text:
                    texts.append(text[:160])
                try:
                    size = float(mat.get("font_size") or 0)
                    if size:
                        font_sizes.append(size)
                except Exception:
                    pass
                color = mat.get("text_color")
                if isinstance(color, str) and color.startswith("#"):
                    text_colors.append(color.lower())
            elif ttype in {"video", "audio", "sticker"}:
                name = mat.get("material_name") or mat.get("name") or ""
                if name:
                    media_names.add(str(name))
                if ttype == "video" and not mat.get("material_name"):
                    path = mat.get("path") or ""
                    if path:
                        media_names.add(Path(str(path)).name)

    for key, bucket in (("transitions", transition_names), ("filters", filter_names), ("effects", effect_names)):
        for item in (data.get("materials") or {}).get(key, []) or []:
            if isinstance(item, dict) and item.get("name"):
                bucket.append(str(item["name"]))

    canvas = data.get("canvas_config") or {}
    try:
        fps = float(data.get("fps") or 0)
    except Exception:
        fps = 0.0
    return {
        "canvas": [int(canvas.get("width") or 0), int(canvas.get("height") or 0)],
        "fps": round(fps, 2),
        "duration_ms": int(round(int(data.get("duration") or 0) / 1000)),
        "track_counts": track_counts,
        "track_segments": track_segments,
        "text_count": len(texts),
        "texts": texts[:60],
        "media_names": sorted(media_names)[:60],
        "transition_names": transition_names[:20],
        "filter_names": filter_names[:20],
        "effect_names": effect_names[:20],
        "font_sizes": font_sizes[:80],
        "text_colors": text_colors[:40],
        "shot_lengths_ms": [int(round(v / 1000)) for v in shot_lengths[:200]],
        "speeds": speeds[:80],
        "volumes": volumes[:80],
    }


def content_fingerprint(data: dict) -> str:
    """用于去重的草稿指纹（同一次保存不会重复记录）。"""
    sig = structure_signature(data)
    payload = json.dumps(sig, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def refresh_duration(data: dict) -> int:
    """按所有片段的最大结束时间刷新草稿总时长。"""
    end = 0
    for track in data.get("tracks") or []:
        for seg in track.get("segments") or []:
            target = seg.get("target_timerange") or {}
            try:
                seg_end = int(target.get("start") or 0) + int(target.get("duration") or 0)
            except Exception:
                continue
            end = max(end, seg_end)
    data["duration"] = end
    return end
