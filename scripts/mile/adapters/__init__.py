"""可选后端适配器：成熟开源能力优先，缺失时诚实降级或返回稳定错误码。"""

from .loudness import DEFAULT_TARGET_LUFS, measure_loudness
from .scene_detect import detect_scenes, scene_backend

__all__ = ["detect_scenes", "scene_backend", "measure_loudness", "DEFAULT_TARGET_LUFS"]
