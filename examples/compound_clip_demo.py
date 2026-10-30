"""模块化叠加示例（原 Compound Clip 示例的诚实版本）。

pyJianYingDraft 未暴露"复合片段（Compound Clip）"素材接口，因此本示例演示
可稳定工作的替代方案：把子模块素材作为画中画叠加到主草稿，并保留子工程
草稿用于单独编辑/预览。
"""

import os

import pyJianYingDraft as draft

from _bootstrap import ensure_skill_scripts_on_path

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT, _ = ensure_skill_scripts_on_path(CURRENT_DIR)

from jy_wrapper import JyProject


def create_overlay_demo() -> None:
    assets_dir = os.path.join(SKILL_ROOT, "assets")
    video_path = os.path.join(assets_dir, "video.mp4")
    if not os.path.exists(video_path):
        print(f"Video not found: {video_path}")
        return

    print("Creating sub project for standalone editing...")
    sub_project = JyProject("Sub_Project_Nested")
    sub_project.add_media_safe(video_path, "0s", duration="3s", track_name="SubVideo")
    sub_project.add_text_simple("Nested content", start_time="0.5s", duration="2s", font_size=10)
    sub_project.save()

    print("Creating main project with overlay module...")
    main_project = JyProject("Main_Project_With_Overlay")
    main_project.add_media_safe(video_path, "0s", duration="8s", track_name="Background")

    main_project.add_media_safe(
        video_path,
        "2s",
        duration="3s",
        track_name="Overlay",
        transform_y=0.2,
        scale=0.4,
    )
    main_project.add_text_simple(
        "Overlay module (2s-5s)",
        start_time="2s",
        duration="3s",
        track_name="OverlayLabel",
        transform_y=-0.6,
        font_size=8,
    )

    main_project.add_text_simple(
        "Main project with overlay module",
        start_time="0s",
        duration="8s",
        clip_settings=draft.ClipSettings(transform_y=0.8),
        track_name="MainTitle",
    )
    main_project.save()
    print("Done. Open JianYing and find draft: Main_Project_With_Overlay")


if __name__ == "__main__":
    create_overlay_demo()
