# 米乐智能化视频剪辑开发策划

> 版本：1.0.0（开发阶段）
> 日期：2026-10-29
> 目标：在不破坏剪映草稿、不伪造模型能力的前提下，把“媒体证据 → 剪辑决策 → 可校验计划 → 剪映 5.9 草稿”打通。

## 1. 目标与边界

### 1.1 本轮目标

1. 建立媒体分析层：镜头边界、音频节拍/能量/静音、字幕与语音时间线、画面质量信号。
2. 建立可解释决策层：根据镜头长度、节奏、语音活动、空隙、素材质量与用户意图，选择剪切、速度、转场、字幕布局、BGM 与特效强度。
3. 建立能力注册表：每个分析器声明依赖、许可证、模型、输出证据、失败降级策略。
4. 扩展剪映 5.9 核心写入能力：优先覆盖当前 vendored `pyJianYingDraft` 已经能写入的滤镜、特效、遮罩、文字动画、花字、音频效果与关键帧。
5. 保持原有确定性编辑层：所有写入仍通过 `plan/apply` 事务、备份、QA；智能层只产生证据与计划，不直接绕过写入层。

### 1.2 不能在第一阶段宣称的能力

- 不把规则、BPM、曲库元数据称为“理解了视频内容”。
- 不把 CLIP、VLM、SAM2、ASR 未安装时的空结果当成模型判断。
- 不承诺覆盖剪映 VIP 云端服务、在线素材版权、商业模型额度或剪映 GUI 私有算法。
- 剪映 5.9 的“全部核心功能”按可编程草稿能力拆分；无法从当前草稿 API 安全写入的功能列为“检测/建议/人工落地”，不伪造已支持。

## 2. 功能范围

| 层级 | 第一阶段 | 后续可选扩展 |
|---|---|---|
| 媒体探测 | ffmpeg 解码、时长、分辨率、帧率、音轨存在性 | PyAV/ffprobe 优化 |
| 镜头理解 | 黑帧、静帧、模糊、音频静音、基础镜头边界 | PySceneDetect、TransNetV2 |
| 语音理解 | 外部 SRT、已有字幕、faster-whisper 可选接入 | WhisperX 词级强制对齐、说话人分离 |
| 音乐理解 | BPM、onset、能量、动态范围、静音比、LUFS 可选 | librosa/madmom、pyloudnorm |
| 视觉理解 | Pillow/ffmpeg 信号指标；质量评分 | ONNX CLIP/人脸/审美模型，SAM2 主体分割 |
| 剪辑决策 | 节奏档位、镜头时长建议、卡点容差、转场风险、语音保护、字幕布局 | 内容相关性、情绪曲线、hook/highlight、自动重构 |
| 剪映写入 | 25 个操作 + 计划事务；滤镜/特效/遮罩/视频动画/字幕动画/关键帧/素材入轨/文字描边·阴影·背景已落地（5.9 schema 实测） | 花字预设、缓动曲线插值、音频效果等仍未接入 |
| 交付 | dry-run → apply → QA → 记忆 | 多版本、人工审阅、OTIO 交换 |

## 3. 智能剪辑决策逻辑

### 3.1 证据对象

分析器输出统一结构：

```json
{
  "source": "path-or-draft-ref",
  "method": "signal-rules|pyscenedetect|faster-whisper|model",
  "available": true,
  "confidence": 0.0,
  "items": [],
  "warnings": [],
  "dependencies": []
}
```

每个片段证据包含 `start/end/duration`，并可包含 `speech_ratio`、`bpm_distance`、`blur_score`、`black_ratio`、`silence_ratio`、`text_density`、`content_score`。没有证据就不能触发对应自动操作。

### 3.2 评分与决策

1. **内容优先**：语音区间、字幕区间、用户指定片段不可被节奏剪辑覆盖。
2. **质量过滤**：黑帧、近静帧、严重模糊、无音频且无视觉信息的片段降权；只在置信度达到阈值时建议删除。
3. **节奏档位**：
   - 讲解/知识：镜头较长，优先硬切与 J/L-cut，字幕完整优先。
   - Vlog/生活：中等镜头长度，轻叠化仅用于时间/空间变化。
   - 产品/科技：短镜头、BPM 对齐、少量强调特效，避免连续转场。
   - 情绪/故事：保留呼吸段，转场跟随情绪而非机械卡点。
4. **卡点**：候选切点距 beat 的偏差不超过 `min(0.08s, beat_interval*0.12)`；语音区间内不强行切点。
5. **转场**：同素材连续镜头默认硬切；空隙或镜头不足 1.2 秒不自动加转场；空间/时间变化且相邻镜头足够长时才建议叠化；高频变化不使用重型特效。
6. **字幕**：语音/字幕优先于美观；单行长度、最大行数、屏幕安全区决定排版；术语、数字、URL、邮箱受保护。
7. **特效与贴纸**：先确定用途（强调、遮挡、品牌、情绪），再从剪映资产表按标签/时长/画面区域选择；同一镜头最多一个主强调特效，避免堆叠。
8. **记忆反馈**：用户接受、拒绝或修改决策后写入记忆；后续只调整权重，不直接覆盖确定性安全规则。

决策输出必须包含：`decision`、`evidence`、`why`、`confidence`、`risk`、`plan_ops`、`manual_review`。

## 4. 第三方开源能力复用清单

| 能力 | 来源与许可证 | 调用方式 | 集成边界 |
|---|---|---|---|
| 剪映草稿写入 | [pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)，MIT；已 vendored | 复用 `scripts/vendor/pyJianYingDraft` API；复杂写入由适配器封装 | 只写草稿 JSON；不等于拥有剪映 VIP 服务 |
| 场景检测 | [PySceneDetect](https://github.com/Breakthrough/PySceneDetect)，BSD-3-Clause | 可选 `pip install scenedetect[opencv]`；`detect(path, ContentDetector())` | 需要 OpenCV；只给镜头边界，不理解语义 |
| 语音转写 | [faster-whisper](https://github.com/SYSTRAN/faster-whisper)，仓库 LICENSE 需随安装包核对；可选 | `WhisperModel(...).transcribe(path, word_timestamps=True, vad_filter=True)` | 模型下载、CPU/GPU 和语言准确率由环境决定；缺失返回 `ASR_UNAVAILABLE` |
| 音乐分析 | [librosa](https://github.com/librosa/librosa)，ISC | `librosa.beat.beat_track`、onset strength；可选安装 | 当前 Python 3.14 环境未装；不能替代内容理解 |
| OCR | [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)，Apache-2.0；或 [RapidOCR](https://github.com/RapidAI/RapidOCR)，以仓库 LICENSE 为准 | 独立 OCR 服务/ONNX Runtime 适配器；输入帧，输出文本框与置信度 | 模型与运行时较重；字幕 OCR 只作证据，不直接改原字幕 |
| 主体分割 | [SAM 2](https://github.com/facebookresearch/sam2)，代码/模型 Apache-2.0，数据集另有 CC BY 4.0 | Torch 模型 predictor，点/框提示后传播 mask | 需要 PyTorch 2.5.1+，Windows 推荐 WSL；列为 T3 可选能力 |
| 时间线交换 | [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO)，Apache-2.0 | `pip install opentimelineio`；用 `otio.schema` 建立编辑中间表示 | 不是媒体容器；不直接替代剪映 draft JSON，作为跨软件中间层 |
| 音频响度 | [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm)，需以仓库 LICENSE 核对 | `Meter.integrated_loudness` / `normalize.loudness` | 可选 T2；写入剪映前必须转成安全音量参数 |
| 质量指标 | ffmpeg `blackdetect` / `freezedetect`，FFmpeg 许可证体系 | 子进程读取检测结果 | 只做信号级拒绝；VMAF/SSIM 不自动等价于“更好看” |

### 复用原则

- 优先调用已安装的本地能力；不在运行时静默下载模型。
- 新增依赖必须是 optional extra，并在 `doctor` 报告是否可用。
- 外部项目地址、版本、许可证、模型来源写入 `THIRD_PARTY_NOTICES.md`；第三方代码不复制进 `src-tmp/`。
- 每个适配器提供 `available()`、`analyze()`、稳定错误码和 `method`，失败只降级，不编造结果。

## 5. 剪映 5.9 核心功能覆盖策略

### 已可写入

视频/音频/文字/贴纸片段、裁剪/删除/移动/拆分、速度、音量、变换、可见性、画布、转场、字幕新增与改写、BGM 添加、关键帧、滤镜、特效、遮罩、花字/气泡、文字入出场与循环动画、音频淡入淡出与部分音频效果。

### 需要新增确定性适配器

- `set_filter`、`add_effect`、`set_mask`
- `set_text_animation`、`set_text_bubble`
- `set_keyframe`、`set_audio_fade`、`set_audio_effect`
- `set_chroma`、`set_blend`（必须先验证 5.9 draft schema）
- 特效/滤镜/转场/花字资产的标签检索与 VIP 标识读取

### 明确列为不可直接承诺

色度抠图、混合模式、降噪、响度字段、声画分离、剪映云端 AI、VIP 在线资源、GUI 私有算法。先做 schema 样本验证，验证通过后再进入写入白名单。

## 6. 集成架构

```text
CLI smart/analyze/decide
        ↓
Capability Registry
        ↓
Evidence Analyzers (signal / optional OSS / model)
        ↓
Decision Engine (rules + style memory + confidence)
        ↓
Explainable Plan JSON
        ↓
plan.compile_plan → apply_plan(transaction + backup)
        ↓
qa → memory feedback
```

新增模块建议：

- `scripts/mile/analysis/registry.py`：能力注册、依赖探测、统一错误。
- `scripts/mile/analysis/signal.py`：ffmpeg/numpy 级分析。
- `scripts/mile/decide.py`：证据到决策与计划操作。
- `scripts/mile/adapters/`：PySceneDetect、faster-whisper、PaddleOCR、SAM2、OTIO 适配器。
- `scripts/mile/asset_policy.py`：资产标签、VIP 标志、许可证与风险。

## 7. 分阶段开发

### Phase 1：可运行的证据与决策（本轮）

- 复用现有 `analyze_audio`，补充镜头/黑帧/静音/冻结帧信号。
- 新增 `decide` 输出解释性剪辑建议与可执行计划。
- 新增 `analyze`/`decide` CLI 只读命令，不改变真实草稿。
- 新增测试与能力矩阵。

### Phase 2：成熟可选后端

- PySceneDetect：镜头边界。✅ 已落地 `mile.adapters.detect_scenes`：装了 `scenedetect[opencv]` 走 `pyscenedetect`，否则降级 ffmpeg `select=gt(scene,t)` 信号；两者皆无返回 `SCENE_DETECT_UNAVAILABLE` + `install_hint`，不伪造结果。CLI 入口 `analyze --media --scenes`。
- faster-whisper：词级字幕与语音活动。⚠️ 已有 `subtitles.transcribe` 可选后端与 `ASR_UNAVAILABLE` 降级；词级时间戳/VAD 增强待安装环境后接入。
- librosa/pyloudnorm：节拍与响度。✅ 响度已落地：`mile.adapters.measure_loudness` 用 ffmpeg `ebur128` T1 实测 I(LUFS)/LRA（含 -70 门限静音处理与稳定错误码），接入 `analyze_media` 与 `decide`（`review-loudness` 决策，manual_review、不自动写盘）；librosa 节拍增强仍为可选 T2，registry 如实报告缺失。
- RapidOCR/PaddleOCR：烧录字幕与封面文字。⏳ 仅能力注册（`ocr`），适配器未接入前不返回任何“识别结果”。

### Phase 3：模型级视觉理解

- ONNX CLIP/人脸/审美模型：相关性、主体、画面质量。
- SAM2：主体分割与自动构图。
- 仅在模型、权重、许可证、CPU/GPU 环境明确后启用。

### Phase 4：剪映核心功能扩展

- 每增加一个写入操作，先用临时草稿副本验证 5.9 schema，再加入 plan whitelist、QA、回滚与 E2E 测试。
- 不以“枚举存在”作为“写入成功”的依据。

## 8. 验收标准

- 只读 `analyze`/`decide` 不修改草稿 mtime。
- 每个自动决策都有证据、方法、置信度、风险与人工复核标志。
- 无可选依赖时仍能运行 T0/T1；缺失能力返回稳定错误码。
- `apply` 只通过事务计划层，失败自动回滚并留下备份。
- 真实剪映测试只使用临时草稿副本；版本号保持 1.0.0。
- 全量测试通过，新增决策用例覆盖“语音保护、短镜头不加转场、BPM 容差、低置信度不自动执行”。
