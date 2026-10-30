"""可插拔媒体证据分析层。"""

from ..adapters.loudness import DEFAULT_TARGET_LUFS, measure_loudness
from ..adapters.scene_detect import detect_scenes, scene_backend
from .registry import capability_status, list_capabilities
from .signal import analyze_media, analyze_timeline_signals

__all__ = [
    "analyze_media",
    "analyze_timeline_signals",
    "capability_status",
    "list_capabilities",
    "detect_scenes",
    "scene_backend",
    "measure_loudness",
    "DEFAULT_TARGET_LUFS",
]
