# 变更记录

## 1.0.0 (2026-10-29) — 米乐

首个版本，处于开发阶段。基于上游基线完成二次开发，并经真实剪映 5.9.0.11632 端到端验证。

### 确定性编辑层与 CLI

- **统一 CLI** `scripts/mile_cli.py`：`version/doctor/drafts/timeline/find/plan/apply/edit/qa/undo/backups/srt/smart/subtitles/recommend/export/assemble/memory`，统一 JSON 信封与退出码。
- **确定性编辑核心** `scripts/mile/`：
  - `ops` / `plan`：新增剪映 5.9 schema 适配的 `set_filter`、`add_effect`、`set_mask`、`add_animation`、`add_text_animation`、`add_keyframes`、`remove_keyframes`、`remove_material`、`add_media`，支持多目标、替换/保留语义、失败回滚与未引用素材回收；文字动画条目字段（`material_type:"sticker"`、`panel:""`）与关键帧字段（`KFTypeScaleX` + `uniform_scale.on`）经真实 5.9 草稿比对确认；`add_media` 素材入轨沿用 vendored `VideoMaterial` 模板（图片素材 3h 占位时长与真实草稿一致），`track=pip/贴纸` 自动建用 `flag=2` 覆盖轨。同时修复 `_resolve_track` 在空 spec 下重复建轨的问题（追加不再叠加新轨道）。
  - `timecode`：秒/毫秒/时钟/中文单位解析，内部统一微秒。
  - `draft`：草稿定位、原子保存、自动备份、撤销、列表。
  - `timeline`：轨道与片段语义建模（ref、role、字幕/主轨/BGM 识别）。
  - `ops`：25 种片段级操作，只改目标片段。
  - `plan`：结构化计划校验与事务化应用，失败回滚。
  - `quality`：17 类质量检查与安全自动修复。
  - `assemble`：按素材、字幕、BGM 与历史风格组装草稿。
- **剪辑记忆** `scripts/mile/memory.py`：自动记录需求/操作/结构/风格，指纹去重、相似度召回、Top-K 风格合并、反馈与遗忘。
- **文档**：`rules/precise-edit.md`、`rules/memory.md`、`rules/smart-edit.md`；重写 README、SKILL、Agent Playbook 与最小命令 SOP；新增 `docs/evaluation-report.md`、`docs/user-pain-points.md`、`docs/improvement-plan.md`。

### 智能层：自然语言、字幕整理与推荐（第一版）

- **中文意图解析** `scripts/mile/intent.py`：规则/槽位解析器，把“把BGM音量调到30%，开头删掉1秒”“给第1个视频片段加0.5秒叠化”“删除所有转场”等说法直接编译成 `mile.plan` 可执行的操作；输出 `confidence`、`clarify`、`warnings`、`unsupported`、`tags`。目标/时间/数值缺失时要求补话而不是猜测。滤镜、特效、蒙版、入场/出场/组合动画、字幕（文字）动画、缩放关键帧、素材入轨与“去掉滤镜/蒙版”等说法解析为 `set_filter` / `add_effect` / `set_mask` / `add_animation` / `add_text_animation` / `add_keyframes` / `add_media` / `remove_material`；未加引号的名称按原文提取并降低置信度，素材库不匹配时返回稳定错误码与 `suggestions`。调色、美颜、倒放等未覆盖能力显式提示，不伪造支持。
- **字幕整理** `scripts/mile/subtitles.py`：错写规则表 + 术语表（`protect` / `错 -> 对`）+ 全角标点归一；数字、URL、邮箱、`#话题`、`@用户`、时间码先掩码保护再改写。`split_caption` / `layout_caption` / `build_captions` 负责断句、折行和按时长比例重新分配，`to_srt` / `parse_srt` 往返，`correct_timeline` 产出只读修改报告。语音转字幕走可选后端 `faster-whisper`，缺失时返回 `ASR_UNAVAILABLE`。
- **BGM 与转场推荐** `scripts/mile/recommend.py`：`analyze_audio` 用 ffmpeg/WAV 解码 + numpy 能量自相关估计 BPM、能量、动态范围、静音比与频谱亮度；`rank_bgm` 在 616 条云端曲库元数据（标题/分类/时长）上排序并给出理由；`recommend_transitions` 按镜头时长、空隙与 BPM 给规则建议。所有结果都带 `method` 与诚实的 `note`。
- **智能编排** `scripts/mile/smart.py`：`run_smart` 一次完成“解析 → 预演 → 字幕改写 + 计划应用（单次事务保存）→ 质检 → 记忆”；`run_subtitles` 独立处理字幕；断句通过 `clone_text_segment` 生成继承样式的独立片段，共享同一文字素材的字幕自动跳过并写入 `skipped`；`clarify` 存在时在任何写盘动作之前返回。
- **时间线可观测性**：`timeline` 片段新增 `transition` 字段，`find`/`smart` 据此定位并删除已挂载的转场。
- **测试**：新增 `tests/test_intent.py`、`tests/test_subtitles.py`、`tests/test_recommend.py`、`tests/test_smart.py` 与 `tests/test_features.py`（含 CLI 冒烟、5.9 schema 写入、dry-run、备份和回滚），意图层追加滤镜/特效/蒙版/动画/字幕动画/缩放关键帧/素材入轨/文字装饰/移除素材自然语言与边界用例，另有 `tests/test_text_decoration.py` 覆盖描边/阴影/背景写入与移除，全量 257 项通过。
- **文档**：README 能力表与“智能剪辑”章节、SKILL 命令路由与智能层小节、`rules/smart-edit.md` 新增第 7-11 节、`rules/cli.md` 子命令表、`docs/api.md` 智能层与证据分析 API 索引；`requirements.txt` / `pyproject.toml` 标注可选 `numpy`（音频分析）与 `faster-whisper`（ASR）依赖。

### 智能剪辑升级：证据分析、决策引擎与镜头边界（策划 → 实现）

- **策划文档** `docs/smart-editing-plan.md`：功能范围、剪辑决策逻辑、开源复用清单（含来源 URL 与许可证）、剪映 5.9 覆盖策略与 Phase 1-4 路线、验收标准。
- **证据分析层** `scripts/mile/analysis/`：`registry.py` 能力矩阵（signal T0/T1、scene T1/T2、pyscenedetect T2、faster_whisper T3、ocr、sam2 T3，含缺失依赖如实报告）；`signal.py` 提供 `analyze_media`（黑帧/静帧/音频节奏/时长）与 `analyze_timeline_signals`（主轨时长分布、字幕覆盖、BGM 音量、语音存在），全部只读并带 `method`/`note`。
- **剪辑决策引擎** `scripts/mile/decide.py`：`decide_timeline` 把证据映射为可解释决策——风格节奏区间（讲解/vlog/科技/情绪）、长镜头复核、短镜头保护、转场建议（空隙/时长/BPM 规则）、语音优先于 BGM、字幕时间保护，输出 `plan_ops` 候选计划与 `confidence`/`risk`/`manual_review`；低置信度不自动执行。
- **镜头边界适配器** `scripts/mile/adapters/scene_detect.py`：`detect_scenes` 优先复用开源 [PySceneDetect](https://github.com/Breakthrough/PySceneDetect)（BSD-3-Clause），未安装时降级 ffmpeg `select=gt(scene,t)` 信号，两者皆无返回 `SCENE_DETECT_UNAVAILABLE` + `install_hint`；输出 `boundaries_s`/`scenes` 区间与“差异信号≠内容理解”的诚实标注。`analyze_media(..., analyze_scenes=True)` 与 CLI `analyze --scenes` 接入。
- **响度证据适配器** `scripts/mile/adapters/loudness.py`：`measure_loudness` 用 ffmpeg `ebur128` T1 实测整文件综合响度 I（LUFS）与 LRA，默认目标 -16 LUFS，输出偏差、建议增益与 clamp 后的 `suggested_volume`；-70 LUFS 门限按 `silent` 标注不给建议，稳定错误码 `FILE_NOT_FOUND`/`BAD_RANGE`/`FFMPEG_MISSING`/`LOUDNESS_FAILED`。接入 `analyze_media` 的 `audio.loudness` 与 CLI `decide --audio`；`decide` 新增 `review-loudness` 决策（|偏差|>3 LU 触发，`manual_review`，因整文件测量无法定位片段而不生成 plan_ops）。registry 增 `loudness` T1 能力（可用性走 ffmpeg 探测）。
- **CLI 只读命令**：`mile_cli.py analyze`（能力矩阵 + 媒体/时间线证据）与 `mile_cli.py decide`（意图→建议→`--plan-out` 候选计划），均不写草稿。
- **文字描边/阴影/背景（Phase 4）** `ops._apply_text_decoration`（经 `set_text` 暴露 `border`/`shadow`/`background`）：键名按真实 5.9 草稿实测——描边写 `content.styles[i].strokes`（`width` 用剪映显示值 0-100，按 `width/100*0.2` 存 raw，实测 30→0.06 对齐）；阴影写 `shadows` 且真实草稿使用 **`feather`** 键（vendored 库写的是 `diffuse`，已按实测键名落地），素材层镜像 `has_shadow`/`shadow_alpha`/`shadow_color`(hex)/`shadow_distance`/`shadow_angle`；背景写素材层 `background_style/color/alpha/round_radius/height/width/horizontal_offset/vertical_offset`（偏移按用户 0-1 映射 `-1~1`，默认 0.5→0.0）。传 `False` 即移除（清 strokes/shadows 并复位镜像）。范围校验 `BAD_RANGE`、样式校验 `BAD_ARGS`、颜色 `BAD_COLOR`（仅十六进制/RGB）。`intent` 新增 `_parse_decoration` 槽位（`给第1个字幕加白色描边，宽度30`、`去掉所有阴影`、`字幕背景去掉`），并支持把裸「宽度/距离/角度/羽化」分句挂到上一条装饰参数；「描边」「阴影」从 `_UNSUPPORTED_HINTS` 移除。
- **字幕动画写入（Phase 4）** `ops.add_text_animation`：文字片段的入场/出场/循环字幕动画，复用 vendored `Text_animation` 导出；schema 经三重验证（枚举 `title` 匹配、holder 结构、真实 5.9 草稿字段分布：动画条目 `material_type="sticker"` 278 次 / `panel:""` 278 次），`loop` 按真实草稿覆盖全片段时长，同类替换、`NOT_TEXT`/`TEXT_ANIMATION_NOT_FOUND`/`BAD_ARGS`/`BAD_RANGE` 稳定错误码；`plan` 白名单+别名（`字幕动画`/`文字动画`），`intent` 把「给第1个字幕加卡拉OK入场动画」解析为可执行操作并从 `_UNSUPPORTED_HINTS` 中移除；`remove_material type=animation` 同时覆盖文字动画 holder 摘除。
- **关键帧写入（Phase 4）** `ops.add_keyframes` / `ops.remove_keyframes`：复用 vendored `KeyframeList`/`Keyframe` 导出，属性支持 `scale/x/y/rotation/alpha/volume/scale_x/scale_y`；schema 经真实 5.9 草稿比对确认（`KFTypeScaleX` 55 次配 `uniform_scale.on:true` 3126 段、无 `KFTypeScaleY` 列表——等比缩放语义一致；单帧 `curveType:"Line"` + `time_offset` + `values:[v]` 418 处）。等比 `scale` 写 `KFTypeScaleX` 并置 `uniform_scale.on=true`，分轴 `scale_x/scale_y` 置 `false`；同属性关键帧合并且按 `time_offset` 排序；超出片段时长的时间点收敛到结尾并回传 `warning`；`BAD_TARGET`（如 alpha 作用于音频）/`BAD_ARGS`/`BAD_RANGE` 稳定错误码。`plan` 白名单+别名（`关键帧`/`移除关键帧`/`keyframes`）；`intent` 把「从第2秒到第5秒把画面放大到1.5倍」解析为两点 `add_keyframes`（起始值按 1.0 假设并 `warning` 提示，时间点/目标值缺失走 `clarify`），「关键帧」从 `_UNSUPPORTED_HINTS` 移除；修复 `_RANGE_RE` 不认连接词后「第」前缀导致 `第2秒到第5秒` 退化为单点的问题。
- **素材入轨（Phase 4）** `ops.add_media`：视频/图片直接写入已有草稿（与 `add_text`/`add_audio` 同层，不再只能靠 `assemble` 新建）。素材 dict 采用 vendored `VideoMaterial` 模板并经真实草稿核对（图片 `type:"photo"`、`duration=10800000000` 占位，实际时长由片段 `source_timerange` 决定）；`track=pip/画中画/sticker/贴纸` 复用或创建 `flag=2` 覆盖轨（真实草稿 PIP/StickerTrack 命名一致），缺省追加主视频轨末尾或指定 `start`；宽高走 `probe_media_file`/`image_size`，探测失败按画布兜底；图片默认 3 秒、`DURATION_UNKNOWN`/`MEDIA_NOT_FOUND`/`SOURCE_OUT_OF_RANGE`/`BAD_RANGE` 稳定错误码；源区间越界收敛并回传 `warning`。顺带修复既有缺陷：`_resolve_track` 在空 spec 时总是新建轨道，导致重复 `add_audio`/`add_text` 叠加多余轨道——现在先复用同类轨道，测试「两次 pip 入轨共用同一覆盖轨、追加落主轨末尾」。`plan` 白名单+别名（`加素材`/`入轨`/`加贴纸`/`media`），`intent` 识别 `把 b.mp4 加到第3秒`、`加个贴纸 x.png`、`画中画插入 从2秒到6秒`（路径支持相对/盘符/带中文与空格引号形式，非音频后缀判定，避免劫持 `add_audio`/缩放关键帧分支）。
- **测试**：新增 `tests/test_analysis.py`、`tests/test_decide.py`、`tests/test_scene_detect.py`、`tests/test_loudness.py`（硬切实测、阈值校验、后端降级、缺失后端稳定错误码、静音门限、CLI 只读冒烟）。

### 导出闭环与媒体探测

- **导出产物定位** `scripts/utils/export_utils.py`：支持剪映 5.9 的“同名文件夹”导出布局，按草稿名前缀 + 修改时间扫描候选目录与一级子目录，返回真实成片路径；候选路径过期时拒绝误判。
- **导出后置校验**：`validate_exported_file` 依次检查存在性、大小、ffprobe 可读性；无 ffprobe 时用 ffmpeg 解码一帧并解析元数据回退。`export` 返回 `size` / `verified` / `duration` / `width` / `height` / `codec`。
- **媒体探针** `scripts/utils/media_probe.py`：按文件头嗅探 PNG/JPEG/GIF/BMP/WebP，自动探测 `MILE_FFMPEG` / `MILE_FFPROBE` / PATH / `JIANYING_HOME` / 剪映标准安装目录 / 正在运行的剪映进程目录。
- **无 ffprobe 补全**：`parse_ffmpeg_metadata` 从 ffmpeg 输出提取时长、编码、分辨率、帧率；`probe_with_ffmpeg` 与 `probe_media_duration` 在 ffprobe 缺失时自动回退 ffmpeg；`formatters`、`ops.add_audio`、`assemble` 的时长探测统一走该入口。
- **doctor 资产自检**：`doctor` 新增 `assets decodable` 检查，损坏图片/音频会明确报错；ffprobe 缺失但 ffmpeg 可用时视为媒体工具可用，`--strict` 不再因此失败。
- **导出冷启动重试**：打开草稿后轮询编辑页就绪，首次未进入时自动重试一次。

### 真实剪映 5.9 兼容与数据安全

- **P0 数据安全**：`overwrite=False` 加载失败时不再调用 `create_draft(allow_replace=True)`，改为报错中止，避免清空用户草稿。
- **Python 3.14 兼容**：`pyJianYingDraft` 读取类型注解改用 `getattr`，修复惰性注解导致的 `load_template` `KeyError 'fps'`。
- **导出分辨率**：`ExportResolution` 枚举补全 `P` 后缀，修复 `1080/720` 报“不支持的分辨率”。
- **Windows 控制台**：stdout/stderr 自动切换 UTF-8，修复 emoji 日志触发 GBK `UnicodeEncodeError` 中断流程。
- **剪映 5.9 首页**：草稿卡片改为双击进入编辑页，修复自动导出找不到导出按钮。
- **贴纸资产**：替换损坏的 `assets/click_marker.png`（原文件无法解码导致贴纸不渲染）。
- **语义定位**：`find --role voice/caption` 修复配音与字幕识别，现各返回 1 条；`flag=2` 的图片轨按素材类型与轨道/文件名提示细分为 `sticker`，视频轨仍为 `pip`。
- **贴纸像素尺寸**：`add_sticker_at` 新增 `size_mode="pixel"`、`width_px`、`height_px`，按画布“适应”比例换算，避免 `scale` 语义混淆。
- **草稿元数据同步**：保存时同步 `draft_meta_info.json` 的 `draft_name` / `tm_duration` / `tm_draft_modified` / `draft_fold_path` / `draft_root_path`，并保留封面等未知字段。
- **图片校验语义**：按文件头判断格式、不信任扩展名；已知 PNG/JPEG/GIF/BMP/WebP 解析失败时不再被 ffmpeg 元数据兜底为可解码，损坏资产仍会报错；仅未知格式才回退。`使用思路.jpg` 等 PNG 伪装扩展名的文件不再被误判为损坏。
- **媒体探针失败不被掩盖**：`probe_media_file` 仅在 ffprobe 缺失时回退 ffmpeg；ffprobe 明确报错时保留失败结论。

### 其他修复

- 转场语义改为挂在前一个片段上（与剪映/pyJianYingDraft 一致），并校验后一片段同轨相邻。
- `plan` 参数校验：`set_canvas`/`add_text` 缺任一必填参数即报错；`add_text`/`add_audio` 的轨道参数不再丢失。
- `add_audio` 在无 ffprobe 时可用显式 `duration` 工作。
- `assemble` 修正轨道创建与命名、`path@3s` 素材时长写法、stdin 字幕读取、中文编码问题；不再把 `duration=None` 交给底层导致 `ValueError`（图片默认 3 秒，新增 `--clip-duration` 统一入片时长，无法探测时返回 `DURATION_UNKNOWN`）。
- `pyJianYingDraft` 的 `pymediainfo` 改为可选导入，缺失时回退 ffprobe，避免整包不可用。
- `list_drafts` 补齐声明但缺失的实现。
- `add_media_safe` 的 `transform_x/transform_y/scale/rotation/alpha/flip_*` 现在真正写入 `ClipSettings`；修正无探测工具时兜底时长被放大 10 倍的问题。
- `timeline` 的 `index_in_track` 统一为 0 基，与 CLI 帮助和文档一致。
- `apply-zoom` 新增 `--duration`；无 ffprobe 时按事件最晚时间 +1s 推断时长，避免录屏无法导入。
- `export` 在导出失败时抛 `EXPORT_FAILED`，不再出现 `status=error` 但外层 `ok=true`；指定 `--out` 时移动到目标并返回实际路径。

### 变更

- 品牌统一为「米乐-自动剪映视频」/ `mile-jianying-skill`；版本号统一为 1.0.0。
- 新增 `pyproject.toml` 项目元数据与 `THIRD_PARTY_NOTICES.md`。
- `requirements.txt` 标注可选依赖并为 Windows 专属依赖添加环境标记。

### 测试

- 阶段性回归：`python -m pytest tests -q` → **72 passed**（导出闭环与媒体探测完成后），覆盖导出目录发现、文件校验、图片嗅探、像素换算、ffmpeg 元数据解析、ffprobe 缺失回退、导出 ffmpeg 校验成败、元数据同步与角色细分。智能层、5.9 核心操作（含字幕动画、关键帧、素材入轨与文字装饰）、证据分析/决策层、镜头边界与响度适配器加入后全量为 **257 passed**（见上文「测试」条目）。

### 真实验证

- 环境：Windows 11 / Python 3.14 / 剪映 5.9.0.11632；`ffprobe` 不存在，使用剪映目录 `Apps\7.4.0.12514\ffmpeg.exe`。
- 自动导出一次通过，CLI 返回真实路径；`validate_exported_file("...\MILE_E2E_20261029_v202.mp4")` → `{"ok": true, "method": "ffmpeg", "codec": "h264", "width": 2046, "height": 1080, "duration": 4.71, "fps": 30.0}`。
- 字幕、花字、贴纸、画中画、关键帧、变速、音量、特效均真实渲染，见 [docs/real-jianying-5.9-e2e-report.md](docs/real-jianying-5.9-e2e-report.md)。

## 上游基线

来自 [luoluoluo22/jianying-editor-skill](https://github.com/luoluoluo22/jianying-editor-skill) 的最后一个版本，作为本项目二开的起点（上游自身版本号为 1.5.0，未在本项目内沿用）。
