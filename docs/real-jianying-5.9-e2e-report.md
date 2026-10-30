# 真实剪映 5.9 端到端实测报告

> 被测项目：`mile-jianying-skill` v1.0.0（开发版；本报告问题均已在该版本内修复并复测）
> 测试时间：2026-10-29
> 测试环境：Windows 11 22631 / Python 3.14 / 剪映专业版 5.9.0.11632
> 测试对象：本地真实剪映安装 `<剪映安装目录>\JianyingPro.exe`
> 安全边界：未修改 106 个已有真实草稿；真实根目录仅创建一个唯一命名测试草稿 `MILE_E2E_20261029`；写入型编辑测试在 Temp 副本 `MILE_EDIT_COPY` 上完成；未改动 `src-tmp/` 参考源码。

---

## 1. 结论摘要

这一轮首次把 SKILL 放到**真实剪映 5.9 + 真实草稿目录 + 真实导出链路**中运行，而不是只做 JSON 级验证。共记录 **14 项问题与行为差异**（P0 1 项、P1 5 项、P2 4 项、P3 4 项）。按报告完成修复后，**12 项已修复**，其余 2 项为测试局限/行为说明：

- **P0 级 1 个**：`overwrite=False` 读取失败时会清空原草稿（已修复，这是最严重的数据安全问题）。
- **P1 级 5 个**：Python 3.14 加载崩溃、导出分辨率枚举错误、Windows 控制台 GBK 崩溃、剪映 5.9 首页卡片必须双击（均已修复）；自动导出路径回传失败（**已修复并真实复测**）。
- **P2 级 4 个**：损坏的贴纸资产、配音/字幕角色识别失败、`split at` 参数失效（已修复）；贴纸缩放语义不清（**已新增像素尺寸 API**）。
- **P3 级 4 个**：`pip` 角色语义过宽、编辑副本仍显示原草稿名（**已修复**）；同源转场不可辨、手动“适应”导出为 2 倍画布（测试局限/行为说明）。
- **自动导出最后一公里已闭环**：真实复测中 CLI 返回 `ok:true`，`output` 指向真实成片，`verified` 通过 ffmpeg 解码校验。

一句话评价：

> **草稿生产能力已通过真实剪映验收，字幕、花字、贴纸、画中画、关键帧、变速、音量、特效均真实渲染；精准编辑与撤销链路可用；自动导出的路径回传与成片校验已完成闭环。**

综合评分：**4.1 / 5**（详见第 6 节）。

### 1.1 导出路径回传修复与真实复测

| 升级项 | 实现 | 验证 |
|--------|------|------|
| 导出路径回传 | 导出后重读面板路径 + 按草稿名前缀/mtime 深扫候选目录与同名文件夹 | 真实剪映 5.9 导出返回 `<临时目录>\mile_e2e\upgrade_out\MILE_E2E_20261029_v202.mp4` |
| 成片后置校验 | 存在性 → 大小 → ffprobe → ffmpeg 单帧解码回退 | 真实成片返回 `{"ok": true, "method": "ffmpeg", "probe": "ok"}`，11,045,381 B，2046×1080@30，4.71s |
| CLI 失败信封 | 导出失败抛 `EXPORT_FAILED`，不再 `ok:true` 误报 | 单测覆盖 |
| 草稿元数据同步 | 保存时同步 `draft_name`/`tm_duration`/`tm_draft_modified`/路径字段，保留封面 | `test_save_syncs_meta` 通过 |
| 贴纸/PIP 区分 | 图片 + 贴纸提示 → `sticker`；视频 → `pip` | `test_sticker_and_pip_roles` 通过 |
| 贴纸像素尺寸 | `size_mode="pixel"` + `width_px`/`height_px`，按“适应”比例换算 | 像素换算单测通过 |
| doctor 资产自检 | 按文件头嗅探图片格式并检查可解码性 | `assets decodable: ok`，11 个可解码、2 个跳过 |
| 导出冷启动重试 | 打开草稿后轮询编辑页就绪，失败自动重试一次 | 基于首次真实导出失败现象补充 |

复测说明：早期测试时首次冷启动导出曾出现一次“未在编辑窗口中找到导出按钮”，重试后成功；现已把“等待编辑页就绪 + 重试一次”内置到控制器。最终复测（`MILE_E2E_20261029_v202.mp4`）一次通过，CLI 返回 `ok:true` 与真实路径，`verified:"ffmpeg"`。真实导出产物仅写入 `upgrade_out`，未覆盖任何已有草稿或成片。

### 1.2 无 ffprobe 环境的元数据补全

真实验证机未安装 `ffprobe`，仅剪映自带 `ffmpeg.exe`（`Apps\7.4.0.12514\ffmpeg.exe`）。1.0.0 增加 ffmpeg 元数据解析，导出校验不再只做“能解码”判断：

| 项目 | 早期实现 | 1.0.0 |
|------|-------|-------|
| 导出校验 | `method:"ffmpeg"`，仅确认可解码 | `method:"ffmpeg"` + `codec` / `width` / `height` / `duration` / `fps` |
| 音频/素材时长 | 依赖 ffprobe，缺失时报错或需手填 | `probe_media_duration` 自动 ffprobe → ffmpeg 回退 |
| doctor / `--strict` | 无 ffprobe 视为缺失 | 有 ffmpeg 即视为媒体工具可用 |

真实成片验证：

```json
{"ok": true, "method": "ffmpeg", "codec": "h264", "width": 2046, "height": 1080, "duration": 4.71, "fps": 30.0}
```

---

## 2. 测试方法

采用四层验证，逐层增加真实度：

| 层级 | 方法 | 目的 |
|------|------|------|
| L1 单元测试 | `python -m pytest tests -q` | 验证时间码、计划校验、片段操作、记忆等纯逻辑 |
| L2 草稿结构 | 构建 9 轨 10 段真实草稿，读取 JSON 与 `timeline` 输出 | 验证轨道、片段、材质、关键帧写入正确 |
| L3 剪映 GUI | 用 Computer Use 打开真实剪映，核验首页卡片、时间轴、预览、导出面板 | 验证剪映能正确解析草稿 |
| L4 成片核验 | 手动导出 + 自动导出，ffmpeg 抽帧逐时刻检查 | 验证效果真实渲染，而非只写入 JSON |

抽帧时刻：0.3s / 0.9s / 1.2s / 1.5s / 1.8s / 2.1s / 2.7s / 3.0s / 3.3s / 3.9s / 4.0s / 4.5s。

---

## 3. 测试草稿设计

草稿 `MILE_E2E_20261029`，画布 2046×1080、30fps、总时长 4.7s，覆盖主要能力面：

| # | 轨道 | 内容 | 验证点 |
|---|------|------|--------|
| 1 | 主视频轨 | 两段同源素材，第二段 `speed=1.25`、`volume=0.6`、淡入淡出 0.3s | 变速、音量、淡化 |
| 2 | 主视频轨转场 | 两段之间“叠化”0.8s | 转场挂载与渲染 |
| 3 | 画中画轨 | `scale=0.35`、`rotation=5`、`alpha=0.9`，缩放/位移关键帧 | PIP 变换与关键帧 |
| 4 | 标题轨 | 字号 12、黄色、描边、渐显/渐隐 | 文字样式与动画 |
| 5 | 字幕轨 | 字号 6，flag=1 | 字幕语义识别 |
| 6 | 花字轨 | artistEffect 资源 `7351316503771368713` | 本地花字资源复制 |
| 7 | 贴纸轨 | `scale=0.12`、位置 (0.6, 0.15) | 贴纸渲染与定位 |
| 8 | 音频轨 | BGM 音量 0.35 + 音量关键帧 | 音量与关键帧 |
| 9 | 音频轨 | 配音（voice） | 配音角色识别 |
| 10 | 特效轨 | 噪点特效 | 特效挂载 |

辅助验证草稿：`MILE_EDIT_COPY`（Temp 副本），用于 `find / edit / split / undo / qa` 写入型测试。

---

## 4. 功能矩阵结果

### 4.1 草稿生产

| 功能 | 结果 | 证据 |
|------|------|------|
| 创建草稿、画布/帧率/时长 | 通过 | 剪映首页正常显示，`tm_duration=4700000` 与内容一致 |
| 视频主轨、多段拼接 | 通过 | 时间轴两段正确，导出成片连续 |
| 变速 1.25 | 通过 | 第二段时长按 1.25 倍缩短，画面正常 |
| 音量 0.6 / 淡入淡出 | 通过（听感待复核） | JSON 与导出音轨均存在，未做主观听感评分 |
| 叠化转场 0.8s | 结构通过、视觉不可辨 | 两段使用同一视频连续源，叠化在画面上几乎不可见；需异源素材专项验证 |
| 画中画 + 缩放/位移关键帧 | 通过 | 抽帧 1.5s 与 3.0s PIP 位置、大小明显变化 |
| 标题样式 + 渐显/渐隐 | 通过 | 1.2s 清晰，3.0s 呈半透明，证明渐隐生效 |
| 自动字幕轨（flag=1） | 通过 | 剪映识别为字幕，导出画面底部显示 |
| 花字 artistEffect | 通过 | 真实剪映可加载本地花字资源并渲染 |
| 贴纸 | 通过（修复资产后） | 右上角小贴纸在 1.2s、3.0s 帧中均可见 |
| BGM + 音量关键帧 | 通过（结构） | 导出附同名 WAV，音量关键帧写入正确 |
| 噪点特效 | 通过 | 3.0s 帧可见颗粒噪点 |
| 质量检查 `qa` | 通过 | 0 error / 0 warning / 0 info |

### 4.2 精准编辑（Temp 副本）

| 命令 | 结果 | 说明 |
|------|------|------|
| `timeline` | 通过 | 9 轨 10 段语义正确 |
| `find --role voice` | 修复后通过 | 修复前 0 条，修复后 1 条（8156BFB4） |
| `find --role caption` | 修复后通过 | 修复前 0 条，修复后 1 条（3312FBA2） |
| `find --role pip` | 1.0.0 修复 | 修复前返回 2 条（PIP 视频 + 贴纸图片）；修复后 `pip` 1 条、`sticker` 1 条 |
| `edit --op set_text` | 通过 | 文本改为“精准编辑测试字幕”，字号 8 |
| `edit --op set_volume` | 通过 | 0.6 + 0.2s 淡入淡出 |
| `edit --op split --param at=3.5s` | 修复后通过 | 10 → 11 段 |
| `undo` | 通过 | 恢复到 10 段 |
| `memory stats/list/recall` | 通过 | 5 条记录，召回 top score 0.352（相关性偏弱） |

### 4.3 导出

| 方式 | 结果 | 产物 |
|------|------|------|
| 剪映手动导出（“适应”画布） | 通过 | `MILE_E2E_20261029.mp4`，4092×2160，30fps，4.71s，23.84MB |
| SKILL 自动导出（早期实现） | 成片成功、API 误报 | `MILE_E2E_20261029(2).mp4`，2046×1080，30fps，4.71s，11.05MB，同名 WAV 831,566B |
| SKILL 自动导出（1.0.0 复测） | **通过** | `upgrade_out\MILE_E2E_20261029_v202.mp4`，2046×1080，30fps，4.71s，11,045,381B，API 返回真实路径 |
| 分辨率/帧率设置 | 通过 | 自动导出输出 2046×1080，证明 1080P/30fps 设置生效 |
| 导出路径回传 | 1.0.0 通过 | CLI 返回 `output` 真实路径，`verified` 为 `ffmpeg` 解码校验 |

### 4.4 回归

`python -m pytest tests -q` → **72 passed**（1.0.0 新增导出定位、媒体探针、ffmpeg 元数据解析与回退、元数据与角色用例）。本报告记录的是导出闭环阶段的回归；智能层第一版（`smart` / `subtitles` / `recommend`）加入后全量为 **124 passed**。

---

## 5. 问题清单

严重级别定义：P0 = 数据丢失/不可用；P1 = 主流程阻断；P2 = 功能错误但有规避；P3 = 语义/体验问题。

| ID | 级别 | 问题 | 状态 |
|----|------|------|------|
| P0-1 | P0 | `overwrite=False` 加载失败时清空原草稿 | 已修复 |
| P1-1 | P1 | Python 3.14 下 `load_template` 必报 `KeyError 'fps'` | 已修复 |
| P1-2 | P1 | 导出分辨率 `1080` 枚举不存在，报“不支持的分辨率” | 已修复 |
| P1-3 | P1 | Windows GBK 控制台打印 emoji 崩溃并中断保存 | 已修复 |
| P1-4 | P1 | 剪映 5.9 首页草稿卡片单击不进入编辑页 | 已修复 |
| P1-5 | P1 | 自动导出完成后路径捕获错误，`shutil.move` 抛 `WinError 2` | 已修复 |
| P2-1 | P2 | 内置贴纸 `click_marker.png` 损坏，剪映无法解码 | 已修复（替换资产） |
| P2-2 | P2 | `add_sticker_at(scale=)` 语义是“相对适应画布”，1.2 会铺满画面 | 已修复（新增像素模式） |
| P2-3 | P2 | `find --role voice/caption` 均返回 0 | 已修复 |
| P2-4 | P2 | `edit --op split --param at=` 永远报缺少参数 | 已修复 |
| P3-1 | P3 | `flag=2` 的图片贴纸也被归为 `pip` | 已修复 |
| P3-2 | P3 | 编辑副本保存后首页仍显示原草稿名 | 已修复 |
| P3-3 | P3 | 同源素材下叠化转场无法视觉验证 | 测试局限 |
| P3-4 | P3 | 手动“适应”导出输出 2 倍画布分辨率 | 行为说明 |

### 5.1 P0-1 草稿数据丢失（已修复）

**复现**：Python 3.14 环境，对已存在草稿执行任意 `JyProject(..., overwrite=False)` 操作；`load_template` 因 P1-1 抛 `KeyError 'fps'`，代码回退到 `create_draft(allow_replace=True)`，草稿内容被清空，只剩 `draft_meta_info.json`。

**根因**：加载失败时把“读取失败”误当成“需要新建”，且新建允许替换。

**修复**：
- [`scripts/core/project_base.py:75`](../scripts/core/project_base.py#L73)：`overwrite=False` 时加载失败直接抛 `RuntimeError`，并提示使用 `overwrite=True` 显式重建。
- [`scripts/vendor/pyJianYingDraft/util.py:39`](../scripts/vendor/pyJianYingDraft/util.py#L39)：用 `getattr(cls, '__annotations__', None)` 兼容 Python 3.14 惰性注解。

**说明**：测试草稿在修复前确实被清空过，修复后重建并通过全部验证。这条是本次实测最有价值的发现。

### 5.2 P1-1 Python 3.14 加载崩溃（已修复）

PEP 649 使 `cls.__dict__['__annotations__']` 可能为 `None`，原实现直接遍历导致类型注解丢失，`assign_attr_with_json` 对 `fps` 等字段赋值失败。修复后 `load_template` 正常返回模板。

### 5.3 P1-2 导出枚举（已修复）

`ExportResolution` 的成员名是 `RES_1080P` 而不是 `RES_1080`。已在 [`scripts/jy_wrapper.py:292`](../scripts/jy_wrapper.py#L292) 统一归一化并补 `P` 后缀。

### 5.4 P1-3 GBK 控制台（已修复）

Windows 控制台默认 GBK，日志中的 `✅` 触发 `UnicodeEncodeError`，发生在保存后输出阶段，表现为“保存成功但命令崩溃”。[`scripts/utils/env_setup.py:5`](../scripts/utils/env_setup.py#L5) 增加 stdout/stderr UTF-8 重配置。

### 5.5 P1-4 剪映 5.9 首页双击（已修复）

5.9 首页草稿卡片单击不进入编辑页，控制器等待 10 秒后仍找不到导出按钮。[`scripts/vendor/pyJianYingDraft/jianying_controller.py:119`](../scripts/vendor/pyJianYingDraft/jianying_controller.py#L119) 改为 `DoubleClick` 后，自动导出可完整走完“打开草稿 → 设置 → 导出 → 回首页”。

### 5.6 P1-5 自动导出路径捕获失败（已修复）

**现象**：导出面板显示完成，目标目录生成 mp4 与 WAV，但 CLI 报 `[WinError 2] 系统找不到指定的文件。`；文件实际落在**上一次选择的目录**，并自动加 `(1)`/`(2)` 后缀；5.9 更常见的布局是**同名文件夹** `草稿名(n)/草稿名(n).mp4`。

**根因**：
- 控制器在点击导出前，从 `ExportPath` 的兄弟控件读取路径（[`jianying_controller.py`](../scripts/vendor/pyJianYingDraft/jianying_controller.py)），5.9 上该属性不是最终绝对路径。
- 导出完成后直接 `shutil.move(export_path, output_path)`，没有校验文件是否存在，也没有回退到“扫描目录取最新文件”。
- 导出窗口只给出显示路径，真实文件位于同名文件夹内。

**修复**：
- 新增 [`scripts/utils/export_utils.py`](../scripts/utils/export_utils.py)：候选父目录 + 去掉 `.mp4` 后缀的同名目录 + 指定搜索目录，按草稿名前缀与 mtime 递归扫描一层子目录；候选文件过旧时忽略。
- 导出完成后重读 `ExportPath`，失败则走目录扫描；`shutil.move` 前校验存在性与大小。
- `export_video` 返回 `output`（真实路径）、`size`、`duration`、`width`、`height`、`codec`、`verified`。
- 无 ffprobe 时使用剪映安装目录的 `ffmpeg.exe` 单帧解码校验。
- 打开草稿后轮询编辑页就绪，冷启动失败自动重试一次。

**真实复测**：CLI 返回 `ok:true`，成片路径为 `upgrade_out\MILE_E2E_20261029_upgrade.mp4`，`verified:"ffmpeg"`。

### 5.7 P2-1 贴纸资产损坏（已修复）

`assets/click_marker.png` 仅 754 字节，ffmpeg 解码报 `inflate returned error -3`。剪映无法解码时不报错，只是不渲染，极易误判为 API 问题。已替换为 ffmpeg 生成的 128×128 红底白十字 PNG（398 字节）。建议 `doctor` 增加资产可解码性检查。

### 5.8 P2-2 贴纸缩放语义（已修复）

`add_sticker_at(scale=1.2)` 的 1.2 是“相对适应画布”的倍数，结果贴纸铺满全屏。1.0.0 保留 `scale` 语义并在文档中说明，同时新增 `size_mode="pixel"`、`width_px`、`height_px`，按画布“适应”比例换算成精确像素尺寸；非法模式或缺少尺寸会明确报错。

### 5.9 P2-3 角色识别（已修复）

[`scripts/mile/timeline.py:105`](../scripts/mile/timeline.py#L105) 起：
- 音频角色改为综合素材名、路径、类型与轨道名匹配；
- 文字角色改为 `flag=1` 或轨道名含 `subtitle/caption/字幕`。
修复后 `find --role voice` 与 `--role caption` 各返回 1 条。

### 5.10 P2-4 split 参数（已修复）

`at` 同时在 `_SELECT_KEYS` 中，先被 `continue` 过滤，导致 split 专用分支成为死代码。[`scripts/mile/plan.py:170`](../scripts/mile/plan.py#L170) 将 split 的 `at` 处理提前，修复后 10→11 段，undo 可恢复。

### 5.11 P3 级问题

- **P3-1 `pip` 过宽（已修复）**：`flag=2` 的图片贴纸轨曾被归为 `pip`。现按素材类型与轨道/文件名提示细分为 `sticker`，`find --role sticker` 可单独定位。
- **P3-2 草稿名滞后（已修复）**：保存时同步 `draft_meta_info.json` 的 `draft_name`、`tm_duration`、`tm_draft_modified`、`draft_fold_path`、`draft_root_path`，并保留封面等未知字段。
- **P3-3 同源转场**：两段同源连续素材，叠化视觉不可辨，需要异源素材复测。
- **P3-4 “适应”导出**：手动导出 4092×2160，是画布 2 倍，不是错误，但容易误解；自动导出 2046×1080 已证明设置生效。

---

## 6. 多维度评分

| 维度 | 分数 | 依据 |
|------|------|------|
| 草稿生成与结构完整性 | 4.5/5 | 9 轨 10 段全部被剪映正确解析 |
| 表现力（字幕/花字/贴纸/PIP/特效/关键帧） | 4.5/5 | 抽帧逐项可见，关键帧真实生效 |
| 精准编辑与可回滚 | 4.5/5 | find/edit/split/undo/qa 全链路通过 |
| 稳定性与数据安全 | 3.5/5 | 自动备份与事务回滚有效，曾出现 P0 数据丢失（已修） |
| 剪映 5.9 兼容性 | 4.0/5 | 打开/编辑/导出/路径回传全部通过，冷启动仍需重试 |
| 导出可靠性 | 4.5/5 | 返回真实路径并做 ffmpeg 解码校验；冷启动首次可能失败后自动重试 |
| 剪辑记忆 | 3.5/5 | 记录/召回可用，top score 0.352 相关性偏弱 |
| 文档与易用性 | 4.0/5 | 命令体系清晰，补充导出返回值、ffmpeg/ffprobe 环境变量与贴纸像素模式 |
| 测试覆盖 | 4.1/5 | 72 个单测通过，新增导出/媒体/回退/角色/元数据用例；仍缺 GUI CI |
| **加权综合** | **4.1/5** | 执行与导出链路已闭环，剩余主要是测试与记忆质量 |

---

## 7. 剩余局限与风险

1. **GUI 自动化脆弱**：剪映 5.9 控件无 Name/Description，导出面板依赖控件树位置，版本升级易回归。
2. **测试隔离**：真实剪映测试依赖人工 Computer Use，未进入 CI；回归成本高。
3. **记忆召回质量**：有记录但相似度偏低，尚无“同类视频风格命中率”指标。
4. **语义角色**：`pip` / `sticker` 已拆分，但 `broll` 等更细角色仍未建模。
5. **音画主观质量**：音量、淡入淡出、转场只做了结构验证，未做听感/视觉主观评分。
6. **冷启动时序**：已加重试，但极慢机器上仍可能两次都超时，需要更长的可配置等待。
7. **无 ffprobe 的探测边界**：1.0.0 的 ffmpeg 回退覆盖首条视频流的时长/分辨率/编码/帧率；多音轨、字幕流与容器细节仍以 ffprobe 更完整。

---

## 8. 下一轮优化建议（按优先级）

### 导出闭环（已完成）

1. ~~修复导出路径回传~~（已实现并真实复测）。
2. ~~导出后置校验（存在、大小、ffprobe/ffmpeg 可读）~~（已实现）。
3. ~~`doctor` 资产可解码自检~~（已实现）。
4. ~~保存时同步 `draft_meta_info`~~（已实现）。
5. ~~`pip` / `sticker` 角色拆分~~（已实现）。
6. ~~贴纸像素尺寸 API~~（已实现）。

### 无 ffprobe 补全（已完成）

7. ~~无 ffprobe 时的完整元数据（时长/编码/分辨率/帧率）~~（已实现并验证真实成片）。
8. ~~音频/素材时长探测自动 ffprobe → ffmpeg 回退~~（已实现）。
9. ~~`doctor` / `--strict` 接受“有 ffmpeg 无 ffprobe”~~（已实现）。

### P1（下一轮）

10. **集成测试**：覆盖“构建草稿 → QA → 导出路径解析（mock 控件）”，Windows CI 跑 Python 3.14。
11. **异源转场专项**：两个不同素材验证叠化/推拉等转场真实渲染。
12. **记忆召回评估**：为 recall 增加命中率/相关性指标，低分时回退到规则推荐。

### P2

13. **导出预设说明**：明确“适应/1080P/4K”对应像素，避免 2 倍画布误解。
14. **GUI 等待时间可配置**：暴露 `MILE_EXPORT_TIMEOUT` / 打开草稿等待时间，适配慢机器。
15. **音频听感验收**：为音量/淡化/转场增加可复现的主观评分模板。

---

## 9. 复现与回归命令

```bash
# 单元测试
python -m pytest tests -q

# 检查真实草稿结构
python scripts/mile_cli.py timeline --draft MILE_E2E_20261029 --pretty

# 精准编辑（在副本上）
python scripts/mile_cli.py find --draft MILE_EDIT_COPY --role voice
python scripts/mile_cli.py edit --draft MILE_EDIT_COPY --ref <ref> --op set_text --param text=精准编辑测试字幕
python scripts/mile_cli.py edit --draft MILE_EDIT_COPY --op split --param at=3.5s
python scripts/mile_cli.py undo --draft MILE_EDIT_COPY
python scripts/mile_cli.py qa --draft MILE_EDIT_COPY

# 自动导出（1.0.0 起返回真实路径；剪映 5.9 同名文件夹会自动定位）
python scripts/mile_cli.py export MILE_E2E_20261029 --out "<临时目录>/mile_e2e/upgrade_out/MILE_E2E_20261029_upgrade.mp4" --resolution 1080 --fps 30
```

---

## 10. 产物清单

| 产物 | 路径 | 参数 |
|------|------|------|
| 测试草稿 | `<剪映草稿根目录>\MILE_E2E_20261029` | 9 轨 10 段，2046×1080@30，4.7s |
| 编辑副本 | `<临时目录>\mile_e2e\drafts\MILE_EDIT_COPY` | 精准编辑测试 |
| 手动导出 | `<临时目录>\mile_e2e\MILE_E2E_20261029\MILE_E2E_20261029.mp4` | 4092×2160，23.84MB |
| 自动导出（早期实现） | `<临时目录>\mile_e2e\MILE_E2E_20261029(2)\MILE_E2E_20261029(2).mp4` | 2046×1080，11.05MB，附 WAV |
| 自动导出（1.0.0 复测） | `<临时目录>\mile_e2e\upgrade_out\MILE_E2E_20261029_v202.mp4` | 2046×1080@30，4.71s，11,045,381B，ffmpeg 校验通过 |
| 抽帧 | `<临时目录>\mile_e2e\frames2`、`frames3` | 12 个关键时刻 |

> 说明：自动生成的空白草稿 `10月29日` 不属于本次测试创建，未做任何删除操作。
