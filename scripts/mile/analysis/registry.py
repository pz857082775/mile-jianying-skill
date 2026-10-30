"""Optional capability registry with explicit dependency boundaries."""

from __future__ import annotations

import importlib.util

_CAPABILITIES = {
    "signal": {
        "tier": "T0/T1",
        "dependencies": ["ffmpeg", "numpy"],
        "method": "ffmpeg-numpy-rules",
        "description": "媒体时长、基础音频与画面质量信号",
    },
    "scene": {
        "tier": "T1/T2",
        "dependencies": ["ffmpeg 或 scenedetect+cv2"],
        "method": "pyscenedetect|ffmpeg-scene-signal",
        "description": "镜头边界信号；优先 PySceneDetect，缺失时降级 ffmpeg scene 分数",
    },
    "loudness": {
        "tier": "T1",
        "dependencies": ["ffmpeg"],
        "method": "ffmpeg-ebur128",
        "description": "整体综合响度 LUFS 与响度范围 LRA 实测",
    },
    "pyscenedetect": {
        "tier": "T2",
        "dependencies": ["scenedetect", "cv2"],
        "method": "pyscenedetect",
        "description": "镜头/场景边界检测",
    },
    "faster_whisper": {
        "tier": "T3",
        "dependencies": ["faster_whisper"],
        "method": "faster-whisper",
        "description": "语音转写、词级时间戳与 VAD",
    },
    "ocr": {
        "tier": "T2/T3",
        "dependencies": ["onnxruntime"],
        "method": "optional-ocr-adapter",
        "description": "烧录字幕与画面文字识别",
    },
    "sam2": {
        "tier": "T3",
        "dependencies": ["torch", "torchvision"],
        "method": "sam2",
        "description": "主体分割、跟踪与自动构图",
    },
}


def _available(name: str) -> tuple[bool, list[str]]:
    if name == "signal":
        try:
            importlib.util.find_spec("numpy")
        except (ImportError, ModuleNotFoundError, ValueError):
            return False, ["numpy"]
        return True, []
    if name == "scene":
        from ..adapters.scene_detect import scene_backend

        backend = scene_backend()
        if backend == "pyscenedetect":
            return True, []
        if backend == "ffmpeg-scene-signal":
            return True, ["scenedetect[opencv]（当前使用 ffmpeg 降级）"]
        return False, ["scenedetect[opencv] 或 ffmpeg"]
    if name == "loudness":
        try:
            from utils.media_probe import find_ffmpeg

            if find_ffmpeg():
                return True, []
        except Exception:
            pass
        return False, ["ffmpeg"]
    spec = _CAPABILITIES[name]
    missing = [dep for dep in spec["dependencies"] if importlib.util.find_spec(dep) is None]
    return not missing, missing


def capability_status() -> dict:
    result = {}
    for name, meta in _CAPABILITIES.items():
        available, missing = _available(name)
        result[name] = {
            **meta,
            "available": available,
            "missing": missing,
        }
    return result


def list_capabilities() -> list[dict]:
    return [{"name": name, **value} for name, value in capability_status().items()]
