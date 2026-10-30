"""
米乐-自动剪映视频 (mile-jianying-skill) - High Level Wrapper (Mixin Based)
旨在解决路径依赖、API 复杂度及严格校验问题。
"""

import os
import sys
import uuid
import json
from typing import Union, Optional

# 环境初始化
from utils.env_setup import setup_env
setup_env()

# 导入工具函数
from utils.constants import SYNONYMS
from utils.formatters import (
    resolve_enum_with_synonyms, format_srt_time, safe_tim,
    get_duration_ffprobe_cached, get_default_drafts_root, get_all_drafts
)

# 导入基类与 Mixins
from core.project_base import JyProjectBase
from core.media_ops import MediaOpsMixin
from core.text_ops import TextOpsMixin
from core.vfx_ops import VfxOpsMixin
from core.mocking_ops import MockingOpsMixin

# macOS 支持模块
from core.macos_ops import (
    IS_MACOS, IS_WINDOWS,
    MacOSScreenRecorder, MacOSExportGuide,
    get_jianying_drafts_root_macos, get_platform_controller
)

try:
    import pyJianYingDraft as draft
    from pyJianYingDraft import VideoSceneEffectType, TransitionType
except ImportError:
    draft = None

def _normalize_export_resolution(value) -> str:
    text = str(value or "1080").strip().upper().replace("P", "")
    aliases = {"FHD": "1080", "UHD": "4K", "QHD": "2K"}
    text = aliases.get(text, text)
    if text not in {"480", "720", "1080", "2K", "4K", "8K"}:
        raise ValueError(f"不支持的导出分辨率: {value}")
    return text


def _normalize_export_fps(value) -> str:
    text = str(value or "30").strip().lower().replace("FPS", "")
    if text.endswith(".0"):
        text = text[:-2]
    if text not in {"24", "25", "30", "50", "60"}:
        raise ValueError(f"不支持的导出帧率: {value}")
    return text


# 平台特定的草稿根目录
def _get_drafts_root_with_macos_fallback():
    """获取草稿根目录，macOS 上自动使用正确的路径"""
    if IS_MACOS:
        macos_root = get_jianying_drafts_root_macos()
        if macos_root:
            return macos_root
        # 如果没找到，返回一个合理的默认值
        return os.path.expanduser("~/Library/Application Support/JianyingPro/User Data/Projects/com.lveditor.draft")
    return get_default_drafts_root()

class JyProject(JyProjectBase, MediaOpsMixin, TextOpsMixin, VfxOpsMixin, MockingOpsMixin):
    """
    高层封装工程类。通过多重继承 Mixins 实现功能解耦。
    """
    def __init__(self, *args, **kwargs):
        # macOS 上自动使用正确的草稿目录
        if IS_MACOS and 'drafts_root' not in kwargs:
            kwargs['drafts_root'] = _get_drafts_root_with_macos_fallback()
        super().__init__(*args, **kwargs)
        self._mile_context = {}

    def _resolve_enum(self, enum_cls, name: str):
        return resolve_enum_with_synonyms(enum_cls, name, SYNONYMS)

    # ------------------------------------------------------------------
    # 记忆（memory）接口：让每次剪辑都能被记录与复用
    # ------------------------------------------------------------------
    def set_memory_context(self, *, intent: str = None, tags=None, speaker: str = None,
                           voice_speed: float = None, notes: str = None, **extra) -> dict:
        """记录本次剪辑的上下文，`save()` 时自动写入长期记忆。

        Args:
            intent: 用户的原始需求描述（越具体，后续召回越准）
            tags: 内容标签，如 ["口播", "电商", "快节奏"]
            speaker: 使用的 TTS 音色
            voice_speed: 配音倍速
        """
        ctx = self._mile_context
        if intent is not None:
            ctx["intent"] = intent
        if tags:
            merged = list(dict.fromkeys([*(ctx.get("tags") or []), *tags]))
            ctx["tags"] = merged
        if speaker is not None:
            ctx["speaker"] = speaker
        if voice_speed is not None:
            ctx["voice_speed"] = float(voice_speed)
        if notes is not None:
            ctx["notes"] = notes
        if extra:
            ctx.update(extra)
        return dict(ctx)

    def remember(self, *, intent: str = None, tags=None, outcome: str = None,
                 rating: int = None, notes: str = None, **extra) -> dict:
        """显式记录一次剪辑（覆盖同一项目 30 分钟内的自动快照）。"""
        from mile.memory import record_session

        ctx = self.set_memory_context(intent=intent, tags=tags, notes=notes, **extra)
        return record_session(
            project=self.name,
            draft_path=os.path.join(self.root, self.name),
            context=ctx,
            outcome=outcome,
            rating=rating,
            kind="session",
        )

    def recall_memory(self, query: str = None, k: int = 3) -> dict:
        """召回与当前素材/需求相似的历史剪辑风格。"""
        from mile.memory import recommend_style

        ctx = dict(self._mile_context)
        if query:
            ctx["intent"] = query
        return recommend_style(
            draft_path=os.path.join(self.root, self.name),
            context=ctx,
            k=k,
        )

    def _auto_record_memory(self):
        if os.getenv("MILE_MEMORY_AUTO", "1").strip().lower() in {"0", "false", "no", "off"}:
            return None
        try:
            from mile.memory import auto_record_from_draft

            return auto_record_from_draft(
                project=self.name,
                draft_path=os.path.join(self.root, self.name),
                context=getattr(self, "_mile_context", {}) or {},
            )
        except Exception as exc:
            print(f"[memory] 自动记录跳过: {exc}")
            return None

    def add_clip(self, media_path: str, source_start: Union[str, int], duration: Union[str, int],
                 target_start: Union[str, int] = None, track_name: str = "VideoTrack", **kwargs):
        """高层剪辑接口：从媒体指定位置裁剪指定长度，并放入轨道。"""
        if target_start is None:
            target_start = self.get_track_duration(track_name)
        return self.add_media_safe(media_path, target_start, duration, track_name, source_start=source_start, **kwargs)

    def save(self):
        """保存并执行质检报告。"""
        self.script.save()
        self._patch_styled_text_paths()
        self._patch_cloud_material_ids()
        self._force_activate_adjustments()

        draft_path = os.path.join(self.root, self.name)
        if os.path.exists(draft_path):
            os.utime(draft_path, None)

        memory_record = self._auto_record_memory()

        # macOS 提示信息
        if IS_MACOS:
            print(f"✅ Project '{self.name}' saved (macOS mode).")
            print(f"📂 Draft location: {draft_path}")
            print("💡 Please open Jianying Pro to edit and export this draft.")
        else:
            print(f"✅ Project '{self.name}' saved and patched.")

        return {
            "status": "SUCCESS",
            "draft_path": draft_path,
            "platform": "macos" if IS_MACOS else "windows",
            "memory_id": (memory_record or {}).get("id"),
        }

    def export_video(self, output_path: str, resolution: str = "1080", fps: str = "30",
                   remote_host: str = None) -> dict:
        """
        导出视频。

        Args:
            output_path: 输出 MP4 路径
            resolution: 分辨率 (480/720/1080/2K/4K/8K)
            fps: 帧率 (24/25/30/50/60)
            remote_host: 远程 Windows Export Agent 地址 (如 "http://192.168.1.100:8765")
                       - 若设置，则使用远程 Agent 导出
                       - macOS 默认使用此方式（需配置 remote_host）
                       - 不设置则使用本地导出 (Windows)

        macOS 优先使用 remote_host 指定的 Windows Agent 进行导出。
        """
        if IS_MACOS or remote_host:
            # 使用远程 Windows Agent 导出
            guide = MacOSExportGuide()

            if remote_host:
                # 使用指定的远程 Agent
                try:
                    import requests
                    import time

                    print(f"🔄 正在通过远程 Agent 导出: {remote_host}")
                    response = requests.post(
                        f"{remote_host.rstrip('/')}/export",
                        json={
                            "draft_name": self.name,
                            "output_path": output_path,
                            "resolution": _normalize_export_resolution(resolution),
                            "framerate": _normalize_export_fps(fps),
                        },
                        timeout=30,
                    )
                    response.raise_for_status()
                    result = response.json()
                    task_id = result.get("task_id")
                    print(f"📋 任务已启动: {task_id}")

                    deadline = time.time() + 1800
                    while time.time() < deadline:
                        status_data = requests.get(
                            f"{remote_host.rstrip('/')}/export/{task_id}/status",
                            timeout=15,
                        ).json()
                        status = status_data.get("status")
                        if status == "completed":
                            output = status_data.get("output_path") or output_path
                            print(f"✅ 导出完成: {output}")
                            return {"status": "success", "output": output}
                        if status == "failed":
                            return {
                                "status": "failed",
                                "error": status_data.get("error") or "远程导出失败",
                            }
                        print(f"⏳ 导出中... {status_data.get('progress', 0)}%")
                        time.sleep(5)

                    return {"status": "error", "error": "远程导出超时（30 分钟）"}
                except ImportError:
                    return {"status": "error", "error": "requests 库未安装"}
                except Exception as e:
                    return {"status": "error", "error": str(e)}

            # 没有指定远程 Agent，显示手动引导
            instructions = guide.get_export_instructions(self.name)
            print("=" * 50)
            print("⚠️  macOS 无法直接自动导出")
            print("=" * 50)
            print(f"\n📂 草稿位置: {instructions['draft_path']}")
            print("\n📝 解决方案:")
            print("   1. 在 Windows 上运行 Export Agent 服务")
            print("   2. 设置 remote_host 参数:")
            print("      project.export_video('out.mp4', remote_host='http://Windows-IP:8765')")
            print()
            guide.open_in_finder(self.name)
            return {
                "status": "remote_required",
                "draft_path": instructions["draft_path"],
                "hint": "需要配置 remote_host 指向 Windows Export Agent",
            }

        # Windows 本地导出（GUI 自动化）
        try:
            from pyJianYingDraft import (
                ExportFramerate,
                ExportResolution,
                JianyingController,
            )
        except Exception as exc:
            return {
                "status": "error",
                "error": f"自动导出不可用（需要 uiautomation 且剪映版本需兼容）: {exc}",
            }

        normalized_res = _normalize_export_resolution(resolution)
        res_suffix = "" if normalized_res in {"2K", "4K", "8K"} else "P"
        res_enum = getattr(ExportResolution, f"RES_{normalized_res}{res_suffix}", None)
        fps_enum = getattr(ExportFramerate, f"FR_{_normalize_export_fps(fps)}", None)
        if res_enum is None:
            return {"status": "error", "error": f"不支持的导出分辨率: {resolution}"}
        if fps_enum is None:
            return {"status": "error", "error": f"不支持的导出帧率: {fps}"}

        output_path = os.path.abspath(output_path)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        try:
            controller = JianyingController(keep_topmost=False)
            actual = controller.export_draft(
                self.name,
                output_path,
                resolution=res_enum,
                framerate=fps_enum,
            )
            actual = str(actual or output_path)
            try:
                from utils.export_utils import validate_exported_file

                check = validate_exported_file(actual)
            except Exception as exc:
                size = os.path.getsize(actual) if os.path.isfile(actual) else 0
                check = {"ok": size > 0, "size": size, "error": str(exc)}
            if not check.get("ok"):
                return {
                    "status": "error",
                    "error": f"导出文件校验失败: {check.get('error') or actual}",
                    "output": actual,
                }
            result = {
                "status": "success",
                "output": actual,
                "size": check.get("size"),
                "duration": check.get("duration"),
                "width": check.get("width"),
                "height": check.get("height"),
                "codec": check.get("codec"),
                "verified": check.get("method"),
            }
            return {key: value for key, value in result.items() if value is not None}
        except Exception as exc:
            return {"status": "error", "error": str(exc)}

    @staticmethod
    def get_screen_recorder(output_dir: str = None):
        """获取适合当前平台的屏幕录制器"""
        if IS_MACOS:
            return MacOSScreenRecorder(output_dir=output_dir)
        elif IS_WINDOWS:
            from tools.recording.recorder import ProGuiRecorder
            return ProGuiRecorder(output_dir=output_dir)
        else:
            raise NotImplementedError(f"不支持的平台: {sys.platform}")

    @staticmethod
    def is_macos() -> bool:
        """检查是否运行在 macOS"""
        return IS_MACOS

    @staticmethod
    def is_windows() -> bool:
        """检查是否运行在 Windows"""
        return IS_WINDOWS

# 导出工具函数以便向下兼容
__all__ = ["JyProject", "get_default_drafts_root", "get_all_drafts", "safe_tim", "format_srt_time"]

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="米乐-自动剪映视频 高层 API 命令行")
    sub = parser.add_subparsers(dest="command")

    zoom = sub.add_parser("apply-zoom", help="根据录屏事件 JSON 生成带智能变焦的草稿")
    zoom.add_argument("--name", required=True, help="草稿名称")
    zoom.add_argument("--video", required=True, help="录屏视频路径")
    zoom.add_argument("--json", required=True, help="录屏事件 JSON 路径")
    zoom.add_argument("--scale", type=float, default=150, help="缩放比例（百分比，默认 150）")
    zoom.add_argument(
        "--duration", help="视频时长兜底（如 12s）；缺省时按事件最晚时间 +1s 推断"
    )
    zoom.add_argument("--root", help="草稿根目录")

    args = parser.parse_args()
    if args.command != "apply-zoom":
        parser.print_help()
        sys.exit(2)

    duration = args.duration
    if not duration:
        duration = "10s"
        try:
            with open(args.json, "r", encoding="utf-8") as f:
                events = json.load(f)
            last_time = max((float(item.get("time") or 0) for item in events), default=0)
            duration = f"{last_time + 1:.3f}s"
        except Exception:
            pass

    kwargs = {"overwrite": True}
    if args.root:
        kwargs["drafts_root"] = args.root
    project = JyProject(args.name, **kwargs)
    segment = project.add_media_safe(args.video, "0s", duration=duration)
    if segment is None:
        print(f"❌ 无法导入视频: {args.video}")
        sys.exit(1)

    from smart_zoomer import apply_smart_zoom

    apply_smart_zoom(project, segment, args.json, zoom_scale=args.scale)
    project.save()
