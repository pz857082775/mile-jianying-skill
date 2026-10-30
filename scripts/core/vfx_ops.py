import os
import time
from typing import Union, Optional
import pyJianYingDraft as draft
from pyJianYingDraft.exceptions import SegmentOverlap
from utils.formatters import safe_tim, tim
from utils.media_probe import image_size
from utils.sizing import scale_for_pixel_size

class VfxOpsMixin:
    """
    JyProject 的特效与转场 Mixin。
    """
    def add_effect_simple(self, effect_name: str, start_time: Union[str, int] = None, duration: Union[str, int] = "3s", track_name: str = "EffectTrack"):
        if start_time is None:
            start_time = self.get_track_duration(track_name)
        self._ensure_track(draft.TrackType.effect, track_name)

        eff_type = self._resolve_enum(draft.VideoSceneEffectType, effect_name)
        if not eff_type:
            return None

        t_range = draft.Timerange(safe_tim(start_time), safe_tim(duration))
        self.script.add_effect(eff_type, t_range, track_name)
        return self.script.tracks[track_name].segments[-1]

    def add_transition_simple(
        self,
        transition_name: str,
        video_segment: Optional[draft.VideoSegment] = None,
        duration: Union[str, int] = "1s",
        track_name: Optional[str] = None,
    ):
        # 兼容调用：如果未直接给 segment，则尝试从指定轨道获取最后一个视频片段
        if video_segment is None and track_name:
            track = self.script.tracks.get(track_name)
            if not track or not getattr(track, "segments", None):
                return None
            video_segment = track.segments[-1]

        if video_segment is None:
            return None

        trans_type = self._resolve_enum(draft.TransitionType, transition_name)
        if not trans_type:
            return None

        video_segment.add_transition(trans_type, duration=safe_tim(duration))
        transition = video_segment.transition
        if transition is not None and transition not in self.script.materials.transitions:
            # 片段先入轨、后加转场时，add_segment 已经执行过，需补登记素材
            self.script.materials.transitions.append(transition)
        return transition

    def add_sticker_at(
        self,
        image_path: str,
        start_time: Union[str, int],
        duration: Union[str, int] = 500000,
        track_name: str = "StickerTrack",
        scale: float = 0.08,
        transform_x: Optional[float] = None,
        transform_y: Optional[float] = None,
        *,
        size_mode: str = "scale",
        width_px: Optional[float] = None,
        height_px: Optional[float] = None,
    ):
        """把本地图片（如点击标记）作为小图层放到指定时间点。

        ``size_mode="scale"`` 时 ``scale`` 是相对“适应画布”的倍数；
        ``size_mode="pixel"`` 时通过 ``width_px`` 或 ``height_px`` 指定
        画面上的目标像素尺寸，内部按画布与图片比例换算成 scale。
        """
        if not os.path.exists(image_path):
            print(f"❌ Sticker missing: {image_path}")
            return None

        start_us = safe_tim(start_time)
        duration_us = safe_tim(duration)
        if duration_us <= 0:
            raise ValueError("贴纸时长必须大于 0")
        if size_mode not in {"scale", "pixel"}:
            raise ValueError("size_mode 仅支持 'scale' 或 'pixel'")
        if size_mode == "pixel" and (width_px is None) == (height_px is None):
            raise ValueError("pixel 模式需且仅需提供 width_px 或 height_px 之一")

        mat = draft.VideoMaterial(image_path, duration=duration_us)
        if os.path.splitext(image_path)[1].lower() in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
            mat.material_type = "photo"

        scale_value = float(scale)
        if size_mode == "pixel":
            dimensions = image_size(image_path)
            if not dimensions:
                raise ValueError("无法获取贴纸图片尺寸，请改用 size_mode='scale'")
            canvas_w = int(getattr(self.script, "width", 0) or 1920)
            canvas_h = int(getattr(self.script, "height", 0) or 1080)
            scale_value = scale_for_pixel_size(
                canvas_w,
                canvas_h,
                dimensions[0],
                dimensions[1],
                width_px=width_px,
                height_px=height_px,
            )

        seg = draft.VideoSegment(
            mat,
            draft.Timerange(start_us, duration_us),
            source_timerange=draft.Timerange(0, duration_us),
            clip_settings=draft.ClipSettings(
                scale_x=scale_value,
                scale_y=scale_value,
                transform_x=float(transform_x or 0.0),
                transform_y=float(transform_y or 0.0),
            ),
        )

        for attempt in range(5):
            name = track_name if attempt == 0 else f"{track_name}_{attempt + 1}"
            self._ensure_track(draft.TrackType.video, name)
            try:
                self.script.add_segment(seg, name)
                return seg
            except SegmentOverlap:
                continue
        print(f"⚠️ 贴纸轨道冲突，已跳过: {image_path}")
        return None

    def add_web_asset_safe(self, html_path: str, start_time: Union[str, int] = None, duration: Union[str, int] = "5s", 
                           track_name: str = "WebVfxTrack", output_dir: Optional[str] = None):
        from web_recorder import record_web_animation
        
        if start_time is None:
            start_time = self.get_track_duration(track_name)
        if output_dir is None:
            output_dir = os.path.join(self.root, self.name, "temp_assets")
        os.makedirs(output_dir, exist_ok=True)
        
        video_output = os.path.join(output_dir, f"web_vfx_{int(time.time())}.webm")
        if record_web_animation(html_path, video_output, max_duration=safe_tim(duration)/1e6 + 5):
            return self.add_media_safe(video_output, start_time, duration, track_name=track_name)
        return None
