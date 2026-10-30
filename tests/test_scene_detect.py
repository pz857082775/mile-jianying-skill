# ruff: noqa: E402

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
SCRIPTS = CURRENT_DIR.parent / "scripts"
for entry in (str(SCRIPTS), str(CURRENT_DIR)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from mile import ops  # noqa: F401  (keeps import parity with sibling suites)
from mile.adapters import scene_detect
from mile.analysis import analyze_media, capability_status, detect_scenes, scene_backend
from mile.errors import MileError


def _ffmpeg_binary():
    from utils.media_probe import find_ffmpeg

    return find_ffmpeg()


def _hard_cut_video(tmpdir: str) -> Path:
    ffmpeg = _ffmpeg_binary()
    if not ffmpeg:
        raise unittest.SkipTest("ffmpeg 不可用，跳过镜头边界实测")
    path = Path(tmpdir) / "cut.mp4"
    command = [
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "color=c=red:s=320x240:d=2:r=15",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=320x240:d=2:r=15",
        "-filter_complex",
        "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p",
        str(path),
    ]
    subprocess.run(command, capture_output=True, timeout=300, check=True)
    return path


class DetectScenesTests(unittest.TestCase):
    def test_ffmpeg_fallback_detects_hard_cut(self):
        if scene_backend() == "none":
            self.skipTest("没有可用的镜头边界后端")
        with tempfile.TemporaryDirectory(prefix="mile_scene_") as tmp:
            video = _hard_cut_video(tmp)
            result = detect_scenes(video)
            self.assertEqual(result["count"], 2)
            self.assertEqual(result["boundaries_s"], [2.0])
            self.assertEqual([scene["duration_s"] for scene in result["scenes"]], [2.0, 2.0])
            self.assertEqual(
                result["method"],
                "pyscenedetect" if scene_backend() == "pyscenedetect" else "ffmpeg-scene-signal",
            )
            self.assertIn("画面差异信号", result["note"])

    def test_file_not_found(self):
        with self.assertRaises(MileError) as ctx:
            detect_scenes("Z:/definitely/missing.mp4")
        self.assertEqual(ctx.exception.code, "FILE_NOT_FOUND")

    def test_threshold_validation(self):
        with tempfile.TemporaryDirectory(prefix="mile_scene_") as tmp:
            video = _hard_cut_video(tmp)
            for bad in (0, 1.5, "很多"):
                with self.assertRaises(MileError) as ctx:
                    detect_scenes(video, threshold=bad)
                self.assertEqual(ctx.exception.code, "BAD_RANGE")

    def test_no_backend_returns_stable_error(self):
        original = scene_detect.scene_backend
        scene_detect.scene_backend = lambda: "none"
        try:
            with tempfile.TemporaryDirectory(prefix="mile_scene_") as tmp:
                video = _hard_cut_video(tmp)
                with self.assertRaises(MileError) as ctx:
                    detect_scenes(video)
                self.assertEqual(ctx.exception.code, "SCENE_DETECT_UNAVAILABLE")
                self.assertIn("install_hint", ctx.exception.details)
        finally:
            scene_detect.scene_backend = original

    def test_scenedetect_branch_reports_missing_dependency(self):
        if scene_detect._scenedetect_available():
            self.skipTest("PySceneDetect 已安装，跳过缺失分支")
        with self.assertRaises(MileError) as ctx:
            scene_detect._detect_with_scenedetect(Path("anything.mp4"), 0.4)
        self.assertEqual(ctx.exception.code, "SCENE_DETECT_UNAVAILABLE")

    def test_short_scenes_filtered_with_warning(self):
        if scene_backend() == "none":
            self.skipTest("没有可用的镜头边界后端")
        with tempfile.TemporaryDirectory(prefix="mile_scene_") as tmp:
            video = _hard_cut_video(tmp)
            result = detect_scenes(video, min_scene_len_s=2.5)
            self.assertEqual(result["count"], 1)
            self.assertTrue(any(item["code"] == "SHORT_SCENES_MERGED" for item in result["warnings"]))


class AnalyzeIntegrationTests(unittest.TestCase):
    def test_analyze_media_includes_scenes_only_when_requested(self):
        if scene_backend() == "none":
            self.skipTest("没有可用的镜头边界后端")
        with tempfile.TemporaryDirectory(prefix="mile_scene_") as tmp:
            video = _hard_cut_video(tmp)
            plain = analyze_media(video, analyze_audio_flag=False)
            self.assertNotIn("scenes", plain.get("video") or {})
            with_scenes = analyze_media(video, analyze_audio_flag=False, analyze_scenes=True)
            scenes = with_scenes["video"]["scenes"]
            self.assertEqual(scenes["count"], 2)

    def test_scene_capability_is_reported(self):
        status = capability_status()
        self.assertIn("scene", status)
        self.assertEqual(status["scene"]["tier"], "T1/T2")
        self.assertEqual(status["scene"]["available"], scene_backend() != "none")

    def test_cli_analyze_scenes_is_read_only(self):
        if scene_backend() == "none":
            self.skipTest("没有可用的镜头边界后端")
        with tempfile.TemporaryDirectory(prefix="mile_scene_cli_") as tmp:
            video = _hard_cut_video(tmp)
            env = {
                **os.environ,
                "MILE_DRAFT_ROOT": str(Path(tmp) / "drafts"),
                "PYTHONIOENCODING": "utf-8",
                # 子进程 numpy/OpenBLAS 单线程，避免全量跑时线程池内存分配失败（OpenBLAS error）
                "OPENBLAS_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
            }
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "mile_cli.py"), "analyze", "__none__", "--media", str(video), "--no-audio", "--scenes"],
                capture_output=True,
                env=env,
                timeout=300,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
            payload = json.loads(completed.stdout.decode("utf-8"))
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["data"]["media"]["video"]["scenes"]["boundaries_s"], [2.0])


if __name__ == "__main__":
    unittest.main()
