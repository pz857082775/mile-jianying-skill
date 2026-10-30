"""剪映自动化控制，主要与自动导出有关"""

import os
import time
import shutil
from pathlib import Path

import uiautomation as uia

from enum import Enum
from typing import Optional, Literal, Callable

from . import exceptions
from .exceptions import AutomationError


class ExportResolution(Enum):
    """导出分辨率"""

    RES_8K = "8K"
    RES_4K = "4K"
    RES_2K = "2K"
    RES_1080P = "1080P"
    RES_720P = "720P"
    RES_480P = "480P"


class ExportFramerate(Enum):
    """导出帧率"""

    FR_24 = "24fps"
    FR_25 = "25fps"
    FR_30 = "30fps"
    FR_50 = "50fps"
    FR_60 = "60fps"


class ControlFinder:
    """控件查找器，封装部分与控件查找相关的逻辑"""

    @staticmethod
    def desc_matcher(
        target_desc: str, depth: int = 2, exact: bool = False
    ) -> Callable[[uia.Control, int], bool]:
        """根据full_description查找控件的匹配器"""
        target_desc = target_desc.lower()

        def matcher(control: uia.Control, _depth: int) -> bool:
            if _depth != depth:
                return False
            full_desc: str = control.GetPropertyValue(30159).lower()
            return (target_desc == full_desc) if exact else (target_desc in full_desc)

        return matcher

    @staticmethod
    def class_name_matcher(
        class_name: str, depth: int = 1, exact: bool = False
    ) -> Callable[[uia.Control, int], bool]:
        """根据ClassName查找控件的匹配器"""
        class_name = class_name.lower()

        def matcher(control: uia.Control, _depth: int) -> bool:
            if _depth != depth:
                return False
            curr_class_name: str = control.ClassName.lower()
            return (class_name == curr_class_name) if exact else (class_name in curr_class_name)

        return matcher


class JianyingController:
    """剪映控制器"""

    app: uia.WindowControl
    """剪映窗口"""
    app_status: Literal["home", "edit", "pre_export"]

    def __init__(self, keep_topmost: bool = True):
        """初始化剪映控制器, 此时剪映应该处于目录页"""
        self.keep_topmost = keep_topmost
        self.get_window(topmost=keep_topmost)

    def export_draft(
        self,
        draft_name: str,
        output_path: Optional[str] = None,
        *,
        resolution: Optional[ExportResolution] = None,
        framerate: Optional[ExportFramerate] = None,
        timeout: float = 1200,
    ) -> str:
        """导出指定的剪映草稿, **目前仅支持剪映6及以下版本**

        **注意: 需要确认有导出草稿的权限(不使用VIP功能或已开通VIP), 否则可能陷入死循环**

        Args:
            draft_name (`str`): 要导出的剪映草稿名称
            output_path (`str`, optional): 导出路径, 支持指向文件夹或直接指向文件, 不指定则使用剪映默认路径.
            resolution (`Export_resolution`, optional): 导出分辨率, 默认不改变剪映导出窗口中的设置.
            framerate (`Export_framerate`, optional): 导出帧率, 默认不改变剪映导出窗口中的设置.
            timeout (`float`, optional): 导出超时时间(秒), 默认为20分钟.

        Returns:
            `str`: 导出文件的实际路径（已移动到 ``output_path``，若指定）。

        Raises:
            `DraftNotFound`: 未找到指定名称的剪映草稿
            `AutomationError`: 剪映操作失败，或导出完成后无法定位文件
        """
        print(f"开始导出 {draft_name} 至 {output_path}")
        self.get_window()
        self.switch_to_home()

        # 打开草稿并等待编辑页就绪，冷启动时自动重试一次
        self._open_draft_editor(draft_name)

        # 点击导出按钮
        export_btn = self.app.TextControl(
            searchDepth=2, Compare=ControlFinder.desc_matcher("MainWindowTitleBarExportBtn")
        )
        if not export_btn.Exists(0):
            raise AutomationError("未在编辑窗口中找到导出按钮")
        export_btn.Click(simulateMove=False)
        time.sleep(10)
        self.get_window()

        # 获取原始导出路径（带后缀名）
        export_path = self._read_export_path()
        if export_path is None:
            print("[!] 未能读取导出路径，将在导出完成后扫描目录定位文件")

        # 设置分辨率
        if resolution is not None:
            setting_group = self.app.GroupControl(
                searchDepth=1,
                Compare=ControlFinder.class_name_matcher("PanelSettingsGroup_QMLTYPE"),
            )
            if not setting_group.Exists(0):
                raise AutomationError("未找到导出设置组")
            resolution_btn = setting_group.TextControl(
                searchDepth=2, Compare=ControlFinder.desc_matcher("ExportSharpnessInput")
            )
            if not resolution_btn.Exists(0.5):
                raise AutomationError("未找到导出分辨率下拉框")
            resolution_btn.Click(simulateMove=False)
            time.sleep(0.5)
            resolution_item = self.app.TextControl(
                searchDepth=2, Compare=ControlFinder.desc_matcher(resolution.value)
            )
            if not resolution_item.Exists(0.5):
                raise AutomationError(f"未找到{resolution.value}分辨率选项")
            resolution_item.Click(simulateMove=False)
            time.sleep(0.5)

        # 设置帧率
        if framerate is not None:
            setting_group = self.app.GroupControl(
                searchDepth=1,
                Compare=ControlFinder.class_name_matcher("PanelSettingsGroup_QMLTYPE"),
            )
            if not setting_group.Exists(0):
                raise AutomationError("未找到导出设置组")
            framerate_btn = setting_group.TextControl(
                searchDepth=2, Compare=ControlFinder.desc_matcher("FrameRateInput")
            )
            if not framerate_btn.Exists(0.5):
                raise AutomationError("未找到导出帧率下拉框")
            framerate_btn.Click(simulateMove=False)
            time.sleep(0.5)
            framerate_item = self.app.TextControl(
                searchDepth=2, Compare=ControlFinder.desc_matcher(framerate.value)
            )
            if not framerate_item.Exists(0.5):
                raise AutomationError(f"未找到{framerate.value}帧率选项")
            framerate_item.Click(simulateMove=False)
            time.sleep(0.5)

        # 点击导出
        export_btn = self.app.TextControl(
            searchDepth=2, Compare=ControlFinder.desc_matcher("ExportOkBtn", exact=True)
        )
        if not export_btn.Exists(0):
            raise AutomationError("未在导出窗口中找到导出按钮")
        started_at = time.time()
        export_btn.Click(simulateMove=False)
        time.sleep(5)

        # 等待导出完成
        st = time.time()
        while True:
            self.get_window()
            if self.app_status != "pre_export":
                continue

            succeed_close_btn = self.app.TextControl(
                searchDepth=2, Compare=ControlFinder.desc_matcher("ExportSucceedCloseBtn")
            )
            if succeed_close_btn.Exists(0):
                # 导出完成后路径框才会刷新为最终文件名，先重读一次
                latest_path = self._read_export_path()
                if latest_path:
                    export_path = latest_path
                succeed_close_btn.Click(simulateMove=False)
                break

            if time.time() - st > timeout:
                raise AutomationError("导出超时, 时限为%d秒" % timeout)

            time.sleep(1)
        time.sleep(2)

        # 回到目录页
        self.get_window()
        self.switch_to_home()
        time.sleep(2)

        # 5.9 上路径框可能只返回文件名，导出后统一做一次目录扫描
        actual_path: Optional[Path] = None
        try:
            from utils.export_utils import discover_exported_file
        except Exception:
            discover_exported_file = None
        if discover_exported_file is not None:
            actual_path = discover_exported_file(
                draft_name, started_at, candidate=export_path
            )
        if actual_path is None and export_path:
            fallback = Path(str(export_path).strip().strip('"'))
            if fallback.is_file() and fallback.stat().st_size > 0:
                actual_path = fallback
        if actual_path is None:
            raise AutomationError(
                "导出已完成，但无法定位导出文件。"
                f"候选路径: {export_path or '未知'}；"
                "请检查剪映上次使用的导出目录，或手动设置导出目录后重试。"
            )

        # 复制导出的文件到指定目录
        if output_path is not None:
            target = Path(output_path).expanduser()
            if target.is_dir():
                target = target / actual_path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if actual_path.resolve() != target.resolve():
                try:
                    os.replace(actual_path, target)
                except OSError:
                    if target.exists():
                        target.unlink()
                    shutil.move(str(actual_path), str(target))
            actual_path = target

        print(f"导出 {draft_name} 至 {actual_path} 完成")
        return str(actual_path)

    def _open_draft_editor(self, draft_name: str, timeout: float = 60) -> None:
        """从首页打开草稿并等待编辑页就绪，冷启动或加载慢时重试一次。"""
        for attempt in range(2):
            if self.app_status != "home":
                try:
                    self.switch_to_home()
                except AutomationError:
                    pass
            draft_name_text = self.app.TextControl(
                searchDepth=2,
                Compare=ControlFinder.desc_matcher(
                    f"HomePageDraftTitle:{draft_name}", exact=True
                ),
            )
            if not draft_name_text.Exists(0):
                raise exceptions.DraftNotFound(f"未找到名为{draft_name}的剪映草稿")
            draft_btn = draft_name_text.GetParentControl()
            assert draft_btn is not None
            # 剪映 5.9 起首页草稿卡片需要双击才能进入编辑页
            draft_btn.DoubleClick(simulateMove=False)
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(2)
                try:
                    self.get_window()
                except AutomationError:
                    continue
                if self.app_status != "edit":
                    continue
                export_btn = self.app.TextControl(
                    searchDepth=2,
                    Compare=ControlFinder.desc_matcher("MainWindowTitleBarExportBtn"),
                )
                if export_btn.Exists(0):
                    return
            print(f"[!] 第{attempt + 1}次打开草稿后编辑页未就绪，重试")
        raise AutomationError("未在编辑窗口中找到导出按钮")

    def _read_export_path(self) -> Optional[str]:
        """读取导出面板中的路径文本；5.9 上可能只有文件名或为空。"""
        try:
            control = self.app.TextControl(
                searchDepth=2, Compare=ControlFinder.desc_matcher("ExportPath")
            )
            if not control.Exists(0):
                return None
            sibling = control.GetSiblingControl(lambda ctrl: True)
            if sibling is None:
                return None
            value = sibling.GetPropertyValue(30159)
            text = str(value).strip() if value else ""
            return text or None
        except Exception:
            return None

    def switch_to_home(self) -> None:
        """切换到剪映主页"""
        if self.app_status == "home":
            return
        if self.app_status != "edit":
            raise AutomationError("仅支持从编辑模式切换到主页")
        close_btn = self.app.GroupControl(searchDepth=1, ClassName="TitleBarButton", foundIndex=3)
        close_btn.Click(simulateMove=False)
        time.sleep(2)
        self.get_window(topmost=self.keep_topmost)

    def get_window(self, *, topmost: Optional[bool] = None) -> None:
        """寻找剪映窗口，并按需置顶"""
        if topmost is None:
            topmost = self.keep_topmost
        if hasattr(self, "app") and self.app.Exists(0):
            self.app.SetTopmost(False)

        self.app = uia.WindowControl(searchDepth=1, Compare=self.__jianying_window_cmp)
        if not self.app.Exists(0):
            raise AutomationError("剪映窗口未找到")

        # 寻找可能存在的导出窗口
        export_window = self.app.WindowControl(searchDepth=1, Name="导出")
        if export_window.Exists(0):
            self.app = export_window
            self.app_status = "pre_export"

        self.app.SetActive()
        self.app.SetTopmost(bool(topmost))

    def release_topmost(self) -> None:
        """取消窗口置顶，避免自动化结束后持续抢占前台。"""
        if hasattr(self, "app") and self.app.Exists(0):
            self.app.SetTopmost(False)

    def __jianying_window_cmp(self, control: uia.WindowControl, depth: int) -> bool:
        if control.Name != "剪映专业版":
            return False
        if "HomePage".lower() in control.ClassName.lower():
            self.app_status = "home"
            return True
        if "MainWindow".lower() in control.ClassName.lower():
            self.app_status = "edit"
            return True
        return False
