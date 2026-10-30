"""画布尺寸与片段缩放之间的换算。"""

from __future__ import annotations

from typing import Optional

__all__ = ["scale_for_pixel_size"]


def scale_for_pixel_size(
    canvas_width: int,
    canvas_height: int,
    image_width: int,
    image_height: int,
    *,
    width_px: Optional[float] = None,
    height_px: Optional[float] = None,
) -> float:
    """把“希望显示为多少像素”换算成剪映的 ``scale``。

    剪映的 ``scale=1.0`` 表示素材按画布“适应”（contain）。换算时先求
    适应后的实际显示尺寸，再用目标像素除以该尺寸。
    """
    try:
        canvas_w = int(canvas_width)
        canvas_h = int(canvas_height)
        image_w = int(image_width)
        image_h = int(image_height)
    except (TypeError, ValueError) as exc:
        raise ValueError("画布与图片尺寸必须是正整数") from exc
    if min(canvas_w, canvas_h, image_w, image_h) <= 0:
        raise ValueError("画布与图片尺寸必须是正整数")

    if (width_px is None) == (height_px is None):
        raise ValueError("width_px 与 height_px 必须且只能提供一个")
    target = width_px if width_px is not None else height_px
    try:
        target_value = float(target)
    except (TypeError, ValueError) as exc:
        raise ValueError("目标尺寸必须是正数") from exc
    if target_value <= 0:
        raise ValueError("目标尺寸必须大于 0")

    fit = min(canvas_w / image_w, canvas_h / image_h)
    if width_px is not None:
        displayed = image_w * fit
    else:
        displayed = image_h * fit
    if displayed <= 0:
        raise ValueError("无法计算适应画布后的显示尺寸")
    return target_value / displayed
