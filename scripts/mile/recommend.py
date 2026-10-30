"""BGM 与转场推荐。

- ``analyze_audio``：用 ffmpeg 解码 PCM、numpy 计算 BPM/能量/动态范围/静音比/亮度。
  这是**信号分析**，不是音乐语义理解；BPM 置信度为自相关强度，不是模型概率。
- ``rank_bgm``：在云端曲库的**元数据**上做标签与时长匹配，曲库没有音频指纹时
  不声称“听懂”了音乐。
- ``recommend_transitions``：基于镜头时长、节奏与空隙的规则建议，配合预览使用。
"""

from __future__ import annotations

import csv
import math
import subprocess
import wave
from pathlib import Path
from typing import Optional

from .errors import MileError

__all__ = [
    "music_library_path",
    "load_music_library",
    "analyze_audio",
    "rank_bgm",
    "recommend_transitions",
]

_MOOD_KEYWORDS = {
    "燃": ["动感", "运动", "燃", "激情", "摇滚", "电子"],
    "轻快": ["轻快", "快乐", "活力", "综艺", "可爱"],
    "活力": ["活力", "动感", "快乐", "轻快"],
    "叙事": ["叙事", "企业", "VLOG", "纪录片"],
    "舒缓": ["舒缓", "治愈", "放松", "安静", "钢琴"],
    "温柔": ["温柔", "温暖", "治愈", "钢琴"],
    "安静": ["安静", "舒缓", "钢琴", "治愈"],
    "氛围": ["氛围", "电影", "史诗", "空灵"],
    "有力": ["动感", "企业", "运动"],
    "通用": ["VLOG"],
}


def music_library_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "cloud_music_library.csv"


def load_music_library(path=None) -> list[dict]:
    """读取云端曲库 CSV（``music_id,title,duration_s,categories,url``）。"""
    file_path = Path(str(path)).expanduser() if path else music_library_path()
    if not file_path.exists():
        raise MileError("LIBRARY_NOT_FOUND", f"曲库文件不存在: {file_path}")
    lines = [
        line
        for line in file_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    reader = csv.DictReader(lines)
    items = []
    for row in reader:
        music_id = (row.get("music_id") or row.get("identifier") or "").strip()
        title = (row.get("title") or "").strip()
        if not music_id or not title:
            continue
        try:
            duration = float(row.get("duration_s") or 0)
        except (TypeError, ValueError):
            duration = 0.0
        categories = [part.strip() for part in (row.get("categories") or "").replace(",", "|").split("|") if part.strip()]
        items.append(
            {
                "music_id": music_id,
                "title": title,
                "duration_s": round(duration, 2),
                "categories": categories,
                "url": (row.get("url") or "").strip(),
            }
        )
    return items


def _decode_wav(path: Path):
    import numpy as np

    with wave.open(str(path), "rb") as handle:
        channels = handle.getnchannels()
        width = handle.getsampwidth()
        rate = handle.getframerate()
        frames = handle.readframes(handle.getnframes())
    if width != 2:
        raise MileError("AUDIO_UNSUPPORTED", "WAV 位深不是 16bit，无法用内置解码器读取")
    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def _decode_pcm(path: Path, sample_rate: int = 22050):
    import numpy as np

    from utils.media_probe import find_ffmpeg

    if path.suffix.lower() == ".wav":
        try:
            return _decode_wav(path)
        except MileError:
            raise
        except Exception:
            pass
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise MileError("FFMPEG_MISSING", "未找到 ffmpeg，无法解码音频；可设置 MILE_FFMPEG")
    command = [
        ffmpeg,
        "-v",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "s16le",
        "-",
    ]
    try:
        completed = subprocess.run(command, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise MileError("AUDIO_DECODE_FAILED", f"ffmpeg 解码失败: {exc}") from exc
    if completed.returncode != 0:
        message = (completed.stderr or b"").decode("utf-8", "replace").strip()[:300]
        raise MileError("AUDIO_DECODE_FAILED", f"ffmpeg 解码失败: {message or '未知错误'}")
    samples = np.frombuffer(completed.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size == 0:
        raise MileError("AUDIO_EMPTY", f"音频为空或无法播放: {path}")
    return samples, sample_rate


def _frame_energy(samples, frame: int, hop: int):
    import numpy as np

    count = 1 + max(0, (len(samples) - frame) // hop)
    if count <= 0:
        return np.zeros(0, dtype=np.float32)
    energy = np.empty(count, dtype=np.float32)
    for index in range(count):
        window = samples[index * hop : index * hop + frame]
        energy[index] = float(np.dot(window, window))
    return energy


def _tempo(samples, sample_rate: int, hop: int = 512, frame: int = 1024):
    import numpy as np

    energy = _frame_energy(samples, frame, hop)
    if energy.size < 16:
        return None, 0.0
    envelope = np.log1p(energy * 1000.0)
    flux = np.maximum(0.0, np.diff(envelope))
    if flux.size < 8:
        return None, 0.0
    window = np.hanning(5)
    window = window / window.sum()
    flux = np.convolve(flux, window, mode="same")
    centered = flux - flux.mean()
    total = float(np.dot(centered, centered))
    if total <= 1e-12:
        return None, 0.0

    min_lag = max(2, int(round(60 * sample_rate / (hop * 180))))
    max_lag = min(centered.size - 2, int(round(60 * sample_rate / (hop * 60))))
    scores = []
    for lag in range(min_lag, max_lag + 1):
        left = centered[:-lag]
        right = centered[lag:]
        denominator = math.sqrt(float(np.dot(left, left)) * float(np.dot(right, right))) + 1e-12
        correlation = float(np.dot(left, right)) / denominator
        bpm = 60 * sample_rate / (hop * lag)
        prior = math.exp(-0.5 * (math.log2(bpm / 120.0) / 0.75) ** 2)
        scores.append((lag, correlation, correlation * prior))
    if not scores:
        return None, 0.0
    best_index = max(range(len(scores)), key=lambda i: scores[i][2])
    lag, correlation, _ = scores[best_index]
    refined = float(lag)
    if 0 < best_index < len(scores) - 1:
        prev_r = scores[best_index - 1][1]
        next_r = scores[best_index + 1][1]
        denominator = prev_r - 2 * correlation + next_r
        if abs(denominator) > 1e-9:
            offset = 0.5 * (prev_r - next_r) / denominator
            if abs(offset) <= 0.5:
                refined += offset
    bpm = 60 * sample_rate / (hop * refined)
    return round(bpm, 1), round(max(0.0, correlation), 3)


def _brightness(samples, sample_rate: int) -> Optional[float]:
    import numpy as np

    frame = 2048
    if len(samples) < frame:
        return None
    positions = np.linspace(0, len(samples) - frame, num=min(64, len(samples) - frame + 1)).astype(int)
    windows = np.stack([samples[pos : pos + frame] * np.hanning(frame) for pos in positions])
    spectrum = np.abs(np.fft.rfft(windows, axis=1))
    freqs = np.fft.rfftfreq(frame, 1.0 / sample_rate)
    weights = spectrum.sum(axis=1)
    centroids = (spectrum * freqs).sum(axis=1) / np.maximum(weights, 1e-9)
    return round(float(np.mean(centroids)), 1)


def _describe_mood(bpm, energy_db, brightness, silence_ratio) -> dict:
    if bpm is None:
        tempo = "未知"
    elif bpm >= 125:
        tempo = "快"
    elif bpm >= 95:
        tempo = "中"
    else:
        tempo = "慢"
    if energy_db >= -14:
        energy = "高"
    elif energy_db >= -26:
        energy = "中"
    else:
        energy = "低"
    if brightness is None:
        bright = "未知"
    elif brightness >= 2500:
        bright = "明亮"
    elif brightness < 1200:
        bright = "温暖"
    else:
        bright = "均衡"
    label = {
        ("快", "高"): "燃",
        ("快", "中"): "轻快",
        ("快", "低"): "轻快",
        ("中", "高"): "活力",
        ("中", "中"): "叙事",
        ("中", "低"): "舒缓",
        ("慢", "高"): "氛围",
        ("慢", "中"): "温柔",
        ("慢", "低"): "安静",
        ("未知", "高"): "有力",
        ("未知", "中"): "通用",
        ("未知", "低"): "安静",
    }.get((tempo, energy), "通用")
    tags = [label]
    if tempo != "未知":
        tags.append(f"{tempo}节奏")
    tags.append(f"{energy}能量")
    if silence_ratio > 0.4:
        tags.append("留白多")
    return {"label": label, "tempo": tempo, "energy": energy, "brightness": bright, "tags": tags}


def analyze_audio(path, *, sample_rate: int = 22050) -> dict:
    """分析音频节奏与能量；返回可解释的特征，而不是“情绪结论”。"""
    media = Path(str(path)).expanduser()
    if not media.exists():
        raise MileError("FILE_NOT_FOUND", f"音频文件不存在: {media}")
    try:
        import numpy  # noqa: F401
    except Exception as exc:
        raise MileError("NUMPY_MISSING", "音频分析需要 numpy；请先 pip install numpy") from exc

    samples, rate = _decode_pcm(media, sample_rate)
    duration = len(samples) / float(rate)

    frame = max(1, int(rate * 0.05))
    hop = frame
    energy = _frame_energy(samples, frame, hop)
    rms = math.sqrt(float(energy.mean()) / frame) if energy.size else 0.0
    rms = max(rms, 1e-9)
    energy_db = round(20 * math.log10(rms), 2)

    frame_rms = [math.sqrt(max(float(value), 0.0) / frame) for value in energy]
    threshold = max(rms * 0.2, 1e-3)
    silence_ratio = round(sum(1 for value in frame_rms if value < threshold) / max(1, len(frame_rms)), 3)
    db_values = sorted(20 * math.log10(max(value, 1e-9)) for value in frame_rms)
    if db_values:
        low = db_values[int(len(db_values) * 0.1)]
        high = db_values[min(len(db_values) - 1, int(len(db_values) * 0.95))]
        dynamic_range = round(high - low, 2)
    else:
        dynamic_range = 0.0

    bpm, bpm_confidence = _tempo(samples, rate)
    brightness = _brightness(samples, rate)
    mood = _describe_mood(bpm, energy_db, brightness, silence_ratio)
    return {
        "path": str(media),
        "duration_s": round(duration, 2),
        "sample_rate": rate,
        "bpm": bpm,
        "bpm_confidence": bpm_confidence,
        "bpm_method": "onset-autocorrelation",
        "energy_db": energy_db,
        "dynamic_range_db": dynamic_range,
        "silence_ratio": silence_ratio,
        "brightness_hz": brightness,
        "mood": mood,
    }


def rank_bgm(
    library=None,
    *,
    duration_s: Optional[float] = None,
    mood=None,
    tags=None,
    limit: int = 5,
) -> dict:
    """按曲库元数据排序 BGM；``mood`` 可以是 :func:`analyze_audio` 的结果或标签字符串。"""
    items = library if isinstance(library, list) else load_music_library(library)
    mood_payload = mood if isinstance(mood, dict) else {"label": str(mood or "")}
    label = (mood_payload.get("mood") or {}).get("label") if isinstance(mood_payload.get("mood"), dict) else mood_payload.get("label")
    desired = []
    if label:
        desired.extend(_MOOD_KEYWORDS.get(label, [label]))
    if isinstance(mood_payload.get("mood"), dict):
        desired.extend(mood_payload["mood"].get("tags") or [])
    for tag in tags or []:
        text = str(tag).strip()
        if text:
            desired.append(text)
    desired = list(dict.fromkeys(item for item in desired if item))
    if not desired:
        desired = ["VLOG"]

    ranked = []
    for item in items:
        haystack = f"{item['title']} {' '.join(item['categories'])}".casefold()
        score = 0.0
        reasons = []
        hits = [word for word in desired if word.casefold() in haystack]
        if hits:
            score += min(4.0, 1.4 * len(hits))
            reasons.append(f"标签命中：{'、'.join(hits[:4])}")
        if duration_s and item["duration_s"]:
            target = float(duration_s)
            if item["duration_s"] >= target:
                slack = (item["duration_s"] - target) / max(target, 1e-6)
                score += max(0.5, 2.5 - min(2.0, slack))
                reasons.append("时长足够，无需循环")
            else:
                shortage = (target - item["duration_s"]) / max(target, 1e-6)
                score -= min(3.0, shortage * 4)
                reasons.append(f"时长不足约 {target - item['duration_s']:.0f}s，需要循环或裁剪")
        ranked.append(
            {
                "music_id": item["music_id"],
                "title": item["title"],
                "duration_s": item["duration_s"],
                "categories": item["categories"],
                "url": item["url"],
                "score": round(score, 1),
                "reasons": reasons or ["仅按曲库默认顺序"],
            }
        )
    ranked.sort(key=lambda row: (-row["score"], row["duration_s"] or 0))
    return {
        "method": "metadata-rules",
        "note": "曲库只有标题/分类/时长，未做音频指纹或语义理解；分数用于缩小候选范围",
        "desired": desired,
        "count": len(ranked[: max(1, int(limit))]),
        "items": ranked[: max(1, int(limit))],
    }


def recommend_transitions(timeline: dict, *, bpm: Optional[float] = None, max_items: int = 30) -> dict:
    """按镜头时长与节奏给出转场建议；只是规则参考，不理解画面内容。"""
    main_segments = [
        seg
        for seg in timeline.get("segments") or []
        if seg.get("role") == "main" and seg.get("track_type") == "video"
    ]
    track_size = len({seg.get("track_index") for seg in main_segments})
    if main_segments and track_size > 1:
        main_track = max(
            {seg.get("track_index") for seg in main_segments},
            key=lambda index: sum(1 for seg in main_segments if seg.get("track_index") == index),
        )
        main_segments = [seg for seg in main_segments if seg.get("track_index") == main_track]
    main_segments.sort(key=lambda seg: (seg["start"], seg["index_in_track"]))

    beat = 60.0 / bpm if bpm and bpm > 0 else None
    items = []
    for prev, curr in zip(main_segments, main_segments[1:]):
        gap = curr["start"] - prev["end"]
        duration_s = prev["duration"] / 1_000_000
        entry = {
            "after_ref": prev["ref"],
            "before_ref": curr["ref"],
            "boundary_s": round(prev["end"] / 1_000_000, 2),
        }
        if gap > 100_000:
            entry.update(
                {
                    "transition": "无",
                    "duration": 0.0,
                    "confidence": 0.8,
                    "reason": f"两段之间有 {gap / 1_000_000:.2f}s 空隙，无法直接加转场",
                }
            )
        elif duration_s < 1.2:
            entry.update(
                {
                    "transition": "无",
                    "duration": 0.0,
                    "confidence": 0.6,
                    "reason": "镜头不足 1.2s，硬切更利落，转场会吃掉有效画面",
                }
            )
        elif prev.get("material_id") and prev.get("material_id") == curr.get("material_id"):
            entry.update(
                {
                    "transition": "叠化",
                    "duration": 0.25,
                    "confidence": 0.6,
                    "reason": "相邻片段来自同一素材，叠化可以弱化跳切",
                }
            )
        else:
            if beat:
                duration = round(min(0.6, max(0.2, beat / 2)), 2)
                reason = f"按 {bpm:g} BPM 取半拍叠化"
            else:
                duration = 0.3 if duration_s < 4 else 0.4
                reason = "常规叙事节奏使用短叠化"
            entry.update(
                {
                    "transition": "叠化",
                    "duration": duration,
                    "confidence": 0.6,
                    "reason": reason,
                }
            )
        items.append(entry)

    return {
        "method": "duration-rules",
        "note": "依据镜头时长、空隙与 BPM 的规则建议，不看画面内容；应用前请预览",
        "bpm": bpm,
        "count": len(items[: max(0, int(max_items))]),
        "items": items[: max(0, int(max_items))],
    }
