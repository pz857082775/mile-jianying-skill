import argparse
import os
import sys

from utils.cli_protocol import emit_result, make_result
from utils.errors import InfraError
from utils.logging_utils import setup_logger

logger = setup_logger("api_validator")


def _bootstrap_import():
    current_dir = os.path.dirname(os.path.abspath(__file__))
    if current_dir not in sys.path:
        sys.path.insert(0, current_dir)
    try:
        from jy_wrapper import JyProject

        return JyProject
    except ImportError as e:
        raise InfraError(f"Failed to load jy_wrapper: {e}") from e


def check_media_tools() -> tuple[bool, bool]:
    from utils.media_probe import find_ffmpeg, find_ffprobe

    ffprobe = find_ffprobe()
    ffmpeg = find_ffmpeg()
    if ffprobe:
        logger.info("FFprobe available: %s", ffprobe)
    if ffmpeg:
        logger.info("FFmpeg available: %s", ffmpeg)
    if not ffprobe and ffmpeg:
        logger.info("FFprobe missing; using ffmpeg metadata fallback.")
    if not ffprobe and not ffmpeg:
        logger.warning("Neither ffprobe nor ffmpeg found. Duration fallback may fail.")
    return bool(ffprobe), bool(ffmpeg)


def check_ffprobe() -> bool:
    ffprobe_ok, ffmpeg_ok = check_media_tools()
    return ffprobe_ok or ffmpeg_ok


def run_diagnostic(project_name: str, video_path: str, strict: bool = False) -> tuple[int, dict]:
    try:
        JyProject = _bootstrap_import()
    except InfraError as e:
        return 1, make_result(
            False, "import_failed", str(e), {"project": project_name, "video": video_path}
        )

    logger.info("Starting JianYing skill diagnostic...")
    ffprobe_ok, ffmpeg_ok = check_media_tools()
    if strict and not (ffprobe_ok or ffmpeg_ok):
        return 2, make_result(
            False,
            "media_tools_missing",
            "ffprobe/ffmpeg not found",
            {"project": project_name, "video": video_path},
        )

    video_exists = os.path.exists(video_path)
    if not video_exists:
        logger.warning("Test asset missing at: %s", video_path)
        if strict:
            return 2, make_result(
                False,
                "video_missing",
                "test video does not exist",
                {"project": project_name, "video": video_path},
            )

    try:
        project = JyProject(project_name, overwrite=True)
        seg1 = project.add_media_safe(video_path)
        project.add_text_simple("诊断测试: 检查溢出警告")

        if (
            seg1
            and hasattr(seg1, "material_instance")
            and hasattr(seg1.material_instance, "duration")
        ):
            dur_us = seg1.material_instance.duration
            logger.info("Base video duration: %.2fs", dur_us / 1_000_000)
            project.add_media_safe(
                video_path, source_start=0, duration=f"{(dur_us / 1_000_000) + 10}s"
            )

        logger.info("Testing Timeline Audit warning path...")
        for _ in range(6):
            project.add_media_safe(video_path, source_start="0s", duration="1s")

        save_result = project.save()
        logger.info("Diagnostic completed. Check above logs for warnings.")
        return 0, make_result(
            True,
            "ok",
            "",
            {
                "project": project_name,
                "video": video_path,
                "ffprobe_ok": ffprobe_ok,
                "ffmpeg_ok": ffmpeg_ok,
                "video_exists": video_exists,
                "save_result": save_result,
            },
        )
    except Exception as e:
        logger.exception("Diagnostic failed: %s", e)
        return 1, make_result(
            False,
            "exception",
            str(e),
            {
                "project": project_name,
                "video": video_path,
                "ffprobe_ok": ffprobe_ok,
                "ffmpeg_ok": ffmpeg_ok,
                "video_exists": video_exists,
            },
        )


def main() -> int:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    skill_root = os.path.dirname(current_dir)
    default_video = os.path.join(skill_root, "assets", "video.mp4")

    parser = argparse.ArgumentParser(description="JianYing Skill API diagnostic")
    parser.add_argument("--project", default="Diagnostic_Test", help="Diagnostic draft name")
    parser.add_argument("--video", default=default_video, help="Path to test video")
    parser.add_argument(
        "--strict", action="store_true", help="Fail if ffprobe/ffmpeg or test asset is missing"
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON summary")
    args = parser.parse_args()
    code, summary = run_diagnostic(args.project, args.video, strict=args.strict)
    emit_result(summary, args.json)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
