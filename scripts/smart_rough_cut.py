"""智能粗剪：从长视频中按场景切换挑选高光片段并生成草稿。

原实现依赖不存在的 ``api_client``，无法运行。现改为使用本地
``video_analyzer``（ffmpeg 场景检测）挑选镜头，再通过 ``JyProject`` 粗剪。
"""

import argparse
import json
import os
import sys

from utils.env_setup import setup_env

setup_env()

try:
    from jy_wrapper import JyProject
except ImportError:
    print("❌ 无法加载 jy_wrapper，请检查 Skill 安装或设置 JY_SKILL_ROOT")
    sys.exit(1)

try:
    from video_analyzer import analyze_video
except ImportError:
    analyze_video = None


def analyze_video_content(video_path, min_duration=2.0, max_duration=5.0, max_clips=12):
    """用场景检测挑选 2-5 秒的高光镜头，返回 [{start, duration, description}]。"""
    if analyze_video is None:
        print("❌ video_analyzer 不可用（需要 ffmpeg）")
        return []

    print(f"[*] 场景分析中: {video_path}")
    try:
        result = analyze_video(video_path)
    except Exception as exc:
        print(f"[-] 分析失败: {exc}")
        return []

    scenes = result.get("scenes") or []
    if not scenes:
        print("[-] 未检测到场景变化")
        return []

    clips = []
    for index, scene in enumerate(scenes):
        duration = min(max(float(scene.duration), min_duration), max_duration)
        start = max(0.0, float(scene.start_time))
        clips.append(
            {
                "start": start,
                "duration": round(duration, 2),
                "description": f"场景 {index + 1}",
            }
        )
        if len(clips) >= max_clips:
            break
    return clips


def create_rough_cut_project(video_path, clips, project_name=None):
    if not clips:
        print("[-] 没有可用的高光片段")
        return None

    stem = os.path.splitext(os.path.basename(video_path))[0]
    project_name = project_name or f"RoughCut_{stem}_{len(clips)}clips"
    print(f"[*] 创建草稿: {project_name}")

    jy = JyProject(project_name, overwrite=True)

    for index, clip in enumerate(clips):
        start_sec = float(clip.get("start", 0))
        duration_sec = float(clip.get("duration", 3))
        description = clip.get("description") or f"Clip {index + 1}"

        print(f"  > 片段 {index + 1}: {description} ({start_sec:.2f}s, {duration_sec:.2f}s)")
        try:
            jy.add_clip(
                media_path=video_path,
                source_start=f"{start_sec:.3f}s",
                duration=f"{duration_sec:.3f}s",
                track_name="MainVideo",
            )
            clip_start_us = jy.get_track_duration("MainVideo") - int(duration_sec * 1_000_000)
            jy.add_text_simple(
                text=description,
                start_time=max(0, clip_start_us),
                duration=f"{duration_sec:.3f}s",
                track_name="Subtitles",
                font_size=6,
                color_rgb=(1.0, 1.0, 1.0),
                transform_y=-0.85,
            )
        except Exception as exc:
            print(f"  [!] 片段添加失败: {exc}")

    jy.save()
    print(f"[+] 草稿已保存: {project_name}")
    return project_name


def main():
    parser = argparse.ArgumentParser(description="按场景切换生成粗剪草稿")
    parser.add_argument("video", help="视频路径")
    parser.add_argument("--name", help="草稿名称")
    parser.add_argument("--max-clips", type=int, default=12, help="最多保留片段数")
    parser.add_argument("--min-duration", type=float, default=2.0, help="片段最短时长（秒）")
    parser.add_argument("--max-duration", type=float, default=5.0, help="片段最长时长（秒）")
    parser.add_argument("--json-out", help="将选中的片段写入 JSON 文件")
    args = parser.parse_args()

    video_file = args.video.strip('"')
    if not os.path.exists(video_file):
        print(f"文件不存在: {video_file}")
        sys.exit(1)

    clips = analyze_video_content(
        video_file,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        max_clips=args.max_clips,
    )
    print(f"[*] 选中 {len(clips)} 个片段")
    print(json.dumps(clips, indent=2, ensure_ascii=False))

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(clips, f, ensure_ascii=False, indent=2)

    create_rough_cut_project(video_file, clips, project_name=args.name)


if __name__ == "__main__":
    main()
