"""米乐-自动剪映视频（mile-jianying-skill）核心包。

面向剪映专业版草稿的确定性编辑层：

- ``timecode``: 时间码解析与格式化（内部统一使用微秒）
- ``draft``: 草稿定位、加载、备份与原子保存
- ``timeline``: 草稿时间轴建模与语义定位
- ``ops``: 片段级精准操作（不改动其它轨道）
- ``plan``: 将自然语言需求落成结构化剪辑计划并事务化应用
- ``memory``: 剪辑记忆与风格推荐
- ``quality``: 成片质量检查与安全修复
- ``assemble``: 按记忆风格智能组装新草稿
"""

import sys
from pathlib import Path

__version__ = "1.0.0"
__author__ = "米乐"
__all__ = ["ensure_vendor_path", "__version__", "__author__"]


def ensure_vendor_path() -> str:
    """把内置的 ``vendor`` 目录加入 ``sys.path``，供 pyJianYingDraft 使用。"""
    vendor = Path(__file__).resolve().parent.parent / "vendor"
    if vendor.is_dir() and str(vendor) not in sys.path:
        sys.path.append(str(vendor))
    return str(vendor)
