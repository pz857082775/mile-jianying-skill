
"""
智能网页录屏工具 (Smart Web Recorder)

依赖:
    pip install playwright
    playwright install chromium

功能:
    1. 启动无头浏览器并开启视频录制。
    2. 加载目标 URL/HTML。
    3. 智能等待 JS 信号 (window.animationFinished) 或固定时长。
    4. 导出为高清 MP4 素材文件。
"""
import os
import shutil
import subprocess
import time

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

def record_web_animation(url_or_path: str, output_path: str, max_duration=30):
    """
    录制网页动画直至完成信号触发。

    Returns:
        成功时返回实际输出文件路径（请求 MP4 但缺少 ffmpeg 时可能是同名 .webm），
        失败返回 ``False``。
    """
    if not sync_playwright:
        print("⚠️ 未检测到 Playwright，跳过网页录制。")
        print("请安装: pip install playwright && playwright install chromium")
        return False

    # 确保路径是绝对路径
    if not url_or_path.startswith('http'):
        url_or_path = 'file://' + os.path.abspath(url_or_path)
    
    output_dir = os.path.dirname(os.path.abspath(output_path))
    temp_video_dir = os.path.join(output_dir, "temp_rec")
    
    if os.path.exists(temp_video_dir):
        shutil.rmtree(temp_video_dir)

    print(f"🎥 Starting Recorder for: {url_or_path}")
    
    with sync_playwright() as p:
        # Launch browser with recording enabled
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            record_video_dir=temp_video_dir,
            record_video_size={"width": 1920, "height": 1080},
            viewport={"width": 1920, "height": 1080}
        )
        
        page = context.new_page()

        try:
            page.goto(url_or_path)
            
            # 智能等待 JavaScript 信号
            # 这里的 predicate 会在浏览器上下文中反复执行，直到返回 true
            print("⏳ Waiting for 'window.animationFinished' signal...", end="", flush=True)
            page.wait_for_function("() => window.animationFinished === true", timeout=max_duration * 1000)
            print(" ✅ Signal Received!")
            
            # 稍微多录一点点 (0.5s) 作为缓冲，防止截断
            time.sleep(0.5)

        except Exception as e:
            print(f"\n❌ Timeout or Error waiting for animation: {e}")
            # 即使超时，也保存已录制的内容
        
        # Close context to save video
        context.close()
        browser.close()
        
        # Playwright 保存的文件名是随机的，我们需要找到它并重命名
        video_files = [f for f in os.listdir(temp_video_dir) if f.endswith('.webm')] if os.path.isdir(temp_video_dir) else []
        if not video_files:
            print("❌ No video file generated.")
            return False

        src_video = os.path.join(temp_video_dir, video_files[0])
        requested_ext = (os.path.splitext(output_path)[1] or ".webm").lower()
        os.makedirs(output_dir, exist_ok=True)

        final_path = output_path
        if requested_ext == ".webm":
            if not os.path.splitext(output_path)[1]:
                final_path = output_path + ".webm"
            shutil.move(src_video, final_path)
        else:
            ffmpeg = shutil.which("ffmpeg")
            converted = False
            if ffmpeg:
                try:
                    subprocess.run(
                        [ffmpeg, "-y", "-i", src_video, "-c:v", "libx264",
                         "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an", output_path],
                        check=True,
                        capture_output=True,
                    )
                    converted = True
                except Exception as exc:
                    print(f"⚠️ ffmpeg 转码失败，保留 WebM: {exc}")
            if converted:
                final_path = output_path
            else:
                final_path = os.path.splitext(output_path)[0] + ".webm"
                shutil.move(src_video, final_path)
                print(f"⚠️ 未安装或无法调用 ffmpeg，已保存为 WebM: {final_path}")

        print(f"💾 Video saved to: {final_path}")
        shutil.rmtree(temp_video_dir, ignore_errors=True)
        return final_path

if __name__ == "__main__":
    # 测试代码
    skill_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html_path = os.path.join(skill_root, "assets", "web_vfx_demo.html")
    output_video = os.path.join(skill_root, "assets", "generated_vfx.webm")
    
    if os.path.exists(html_path):
        record_web_animation(html_path, output_video)
    else:
        print("HTML file not found.")
