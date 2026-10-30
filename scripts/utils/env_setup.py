import os
import sys
from utils.skill_path import resolve_skill_root

def _ensure_utf8_stdio():
    """Windows 控制台默认 GBK，emoji/中文日志会抛 UnicodeEncodeError 并中断流程。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        encoding = (getattr(stream, "encoding", "") or "").lower()
        if encoding in {"utf-8", "utf8"}:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def setup_env():
    """
    统一初始化 米乐-自动剪映视频 (mile-jianying-skill) 运行环境。
    将 scripts、vendor 及跨 Skill 的依赖路径注入到 sys.path 中。
    """
    _ensure_utf8_stdio()
    try:
        current_frame = sys._getframe(1)
        caller_file = current_frame.f_globals.get('__file__')
        if caller_file:
            start_dir = os.path.dirname(os.path.abspath(caller_file))
        else:
            start_dir = os.getcwd()
    except Exception:
        start_dir = os.getcwd()

    skill_root, _ = resolve_skill_root(start_dir)
            
    if skill_root:
        scripts_dir = os.path.join(skill_root, "scripts")
        vendor_dir = os.path.join(scripts_dir, "vendor")
        
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
            
        if vendor_dir not in sys.path:
            sys.path.insert(0, vendor_dir)
            
        possible_api_roots = [
            os.path.join(skill_root, "..", "antigravity-api-skill", "libs"),
            os.path.abspath(os.path.join(skill_root, "../../antigravity-api-skill/libs"))
        ]
        for api_path in possible_api_roots:
            if os.path.exists(api_path) and api_path not in sys.path:
                sys.path.append(api_path)
                break
