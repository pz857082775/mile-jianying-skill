# 米乐-自动剪映视频 (mile-jianying-skill) v1.1.0

**用自然语言完成剪映专业版自动剪辑，并能对任意片段下达精准优化指令。**

米乐在剪映草稿层做确定性编辑：读取真实草稿 → 理解时间线 → 生成结构化剪辑计划 → 事务化落盘（失败自动回滚）→ 记录本次剪辑记忆。下一次处理同类视频时，会自动召回历史风格与成功经验。

## 核心能力

| 能力 | 说明 | 入口 |
|------|------|------|
| 自然语言智能剪辑 | 中文指令直接解析成结构化计划（默认只预览，`--apply` 才落盘），并给出置信度、澄清项与质检结论 | `mile_cli.py smart` |
| **修改前差异预览** | 计划在内存副本执行后输出 before/after 结构对比（片段级 diff + 风险提示），不落盘 | `mile_cli.py plan --preview` / `apply --preview` |
| **系列风格档案** | 一个账号/栏目一套风格（字号/颜色/字幕位置/转场/BGM/节奏），可命名、版本化、导出/导入、`assemble --profile` 一键套用 | `mile_cli.py profile` |
| **记忆迁移与负反馈** | 记忆支持导出/导入（换机迁移）；被拒绝的修改在召回中自动降权 | `mile_cli.py memory --action export/import` |
| **批量变体** | 一条底稿按参数池（开头/转场/BGM/比例）顺序轮换生成 N 份差异草稿，每份独立质检与记忆 | `mile_cli.py variants` |
| 字幕智能整理 | 错别字纠正、术语保护、标点归一、按长度断句、时长重分配、SRT 往返 | `mile_cli.py subtitles` |
| BGM / 转场推荐 | 音频节奏与能量分析 + 云端曲库元数据排序 + 按时长/空隙的转场建议 | `mile_cli.py recommend` |
| 证据分析与剪辑决策 | 只读：媒体信号（黑帧/静帧/音频节奏/镜头边界）+ 可解释的风格/节奏/转场/响度决策 | `mile_cli.py analyze` / `decide` |
| 精准片段编辑 | 按 ref / 轨道 / 时间 / 文字 / 角色定位片段，裁剪、分割、变速、调音量、改字幕、加转场、滤镜、特效、蒙版、动画、关键帧 | `mile_cli.py edit` / `find` |
| 素材入轨 | 视频/图片素材加入草稿：追加主轨或指定时间点，支持画中画/贴纸覆盖轨 | `mile_cli.py smart` / `edit --op add_media` |
| 结构化剪辑计划 | 一次提交多步操作，全部校验通过才保存；任一失败整体回滚 | `mile_cli.py plan` / `apply` |
| 剪辑记忆 | 每次剪辑自动记录需求、操作、风格与结构摘要；同类视频自动召回并推荐风格 | `mile_cli.py memory` |
| 质量检查 | 检查空片段、越界、轨道冲突、字幕错位、音量异常等，并可安全自动修复 | `mile_cli.py qa --fix` |
| 智能组装 | 给定素材、字幕、BGM，按历史风格一键生成完整草稿 | `mile_cli.py assemble` |
| 批量/无头导出 | 调用剪映完成 MP4/SRT 导出，自动定位并校验真实成片 | `mile_cli.py export` / `srt` |
| 安全兜底 | 每次保存自动备份，支持 `undo`、`backups` | `mile_cli.py undo` / `backups` |

传统能力完整保留：素材导入、AI 配音、字幕对齐、云端曲库、特效/转场/滤镜搜索、录屏与智能变焦、Web 动效转视频、影视解说生成。

## 安装

```bash
# 1. 将本目录放入 Agent 的 skills 目录（目录名保持 mile-jianying-skill）
#    例如：<workspace>/.agent/skills/mile-jianying-skill

# 2. 安装依赖
cd mile-jianying-skill
python -m pip install -r requirements.txt

# 3. 自检
python scripts/mile_cli.py doctor
```

依赖要求：

- Python 3.9+
- ffmpeg / ffprobe（强烈建议；只有 ffmpeg 时也能读取时长/分辨率/编码/帧率；两者都缺失时需显式提供素材时长。可用 `MILE_FFMPEG` / `MILE_FFPROBE` 指定路径，或设置 `JIANYING_HOME`；也会自动探测标准安装目录与运行中的剪映进程目录）
- 剪映专业版（已在 5.9.0.11632 实测；自动导出会定位剪映 5.9 的同名导出文件夹，返回真实成片路径并做可读性校验，详见 [实测报告](docs/real-jianying-5.9-e2e-report.md)）
- 智能层依赖：意图解析与字幕整理只用 Python 标准库；`recommend --audio` 的音频分析需要 `numpy` + ffmpeg；语音转字幕需要可选的 `faster-whisper`（`pip install faster-whisper`），缺失时命令会明确返回 `ASR_UNAVAILABLE`

## 快速开始（命令行）

所有命令输出统一 JSON 信封 `{"ok", "code", "reason", "data"}`，成功退出码 0，失败退出码 2。时间参数默认按**秒**，也接受 `500ms` / `1m2s` / `00:12`。

```bash
# 查看草稿与时间线
python scripts/mile_cli.py drafts --detail
python scripts/mile_cli.py timeline "我的项目"

# 找到目标片段
python scripts/mile_cli.py find "我的项目" --text "欢迎" --role caption

# 精准编辑：把第 1 条视频轨第 0 段裁到 2s-6s，并让后续片段前移
python scripts/mile_cli.py edit "我的项目" \
  --op trim --target '{"track":"video#1","index":0}' \
  --param start=2s --param end=6s --param ripple=true

# 质量检查并自动修复
python scripts/mile_cli.py qa "我的项目" --fix

# 撤销最近一次保存
python scripts/mile_cli.py undo "我的项目"
```

> 导出会返回真实成片路径与校验结果（`output` / `size` / `verified` / 时长 / 分辨率）。剪映 5.9 会把成片放进同名文件夹并可能加 `(n)` 后缀，CLI 已自动处理，无需手动查找。
>
> 贴纸默认 `scale` 是相对“适应画布”的倍数；需要精确像素时用 `size_mode="pixel"` + `width_px` / `height_px`（Python API），CLI 可用 `find --role sticker` 定位贴纸轨。

### 结构化剪辑计划

把自然语言需求拆成可校验的操作数组，一次提交、事务化执行：

```json
{
  "intent": "收紧节奏并统一字幕",
  "tags": ["快节奏", "字幕"],
  "ops": [
    {"op": "trim", "target": {"ref": "SEGV1"}, "end": "2.5s", "ripple": true},
    {"op": "add_transition", "target": {"ref": "SEGV1"}, "type": "叠化", "duration": "0.4s"},
    {"op": "set_volume", "target": {"role": "bgm"}, "volume": "20%"},
    {"op": "set_text", "target": {"text_contains": "第二句"}, "text": "第二句改好了"},
    {"op": "set_filter", "target": {"ref": "SEGV1"}, "name": "1980", "intensity": 75},
    {"op": "set_mask", "target": {"ref": "SEGV1"}, "name": "线性", "size": 0.6}
  ]
}
```

```bash
python scripts/mile_cli.py plan  "我的项目" --plan plan.json   # 只校验
python scripts/mile_cli.py apply "我的项目" --plan plan.json   # 应用并保存
```

### 记忆

```bash
# 召回相似历史剪辑（按需求描述 / 参考草稿 / 标签）
python scripts/mile_cli.py memory --action recall --intent "科技产品 30 秒竖屏" --k 3

# 给历史记录打分，强化有效经验
python scripts/mile_cli.py memory --action feedback --id <记忆id> --rating 5

# 查看统计 / 遗忘失效记忆
python scripts/mile_cli.py memory --action stats
python scripts/mile_cli.py memory --action forget --id <记忆id>
```

`apply` / `edit` / `assemble` 默认写入记忆；加 `--no-remember` 可关闭单次记录。

### 修改前差异预览（1.1.0）

对任意计划先看后改：内存副本执行 + 片段级 diff，不落盘、不改草稿：

```bash
python scripts/mile_cli.py plan  "我的项目" --plan plan.json --preview   # 输出 before/after/diff/risks
python scripts/mile_cli.py apply "我的项目" --plan plan.json --preview   # 等价：只预览，不落盘
```

### 系列风格档案（1.1.0）

```bash
# 从参考草稿提取风格，建立命名档案
python scripts/mile_cli.py profile --action create --name 科技评论 --draft "上周那期"

# 查看 / 列表 / 更新 / 删除
python scripts/mile_cli.py profile --action list
python scripts/mile_cli.py profile --action show --name 科技评论
python scripts/mile_cli.py profile --action update --name 科技评论 --tags "科技,栏目" --notes "品牌标准"
python scripts/mile_cli.py profile --action delete --name 科技评论

# 迁移 / 共享
python scripts/mile_cli.py profile --action export --name 科技评论 --out profile.json
python scripts/mile_cli.py profile --action import --source profile.json   # 同名需 --force

# 组装草稿时一键套用档案（优先级：--style > --profile > 记忆自动召回 > 默认）
python scripts/mile_cli.py assemble 素材.mp4 --project "新一期" --captions captions.srt --bgm bgm.mp3 --style-from "上周那期"
```

> `assemble --style 档案名.json` 可直接引用导出的档案文件；`profile` 档案中的风格也可覆盖 `--style-from` 的自动召回。

### 记忆迁移与负反馈（1.1.0）

```bash
# 换机迁移 / 备份
python scripts/mile_cli.py memory --action export --out memories.json               # 全量
python scripts/mile_cli.py memory --action export --out memories.json --project 科技 # 按项目
python scripts/mile_cli.py memory --action import --source memories.json            # merge，按 id 去重
python scripts/mile_cli.py memory --action import --source memories.json --mode replace  # 清空后导入（自动备份原库）

# 负反馈：被拒绝的修改在召回中自动降权
python scripts/mile_cli.py memory --action feedback --id <记忆id> --outcome rejected
python scripts/mile_cli.py memory --action feedback --id <记忆id> --outcome accepted
```

### 批量变体（1.1.0）

```bash
# 一条底稿 + 参数池 → N 份差异草稿（_v1.._vN），每份独立 qa 与记忆
python scripts/mile_cli.py variants --project 产品介绍 素材.mp4 \
  --count 4 \
  --openings 开场A.mp4,开场B.mp4 \
  --transitions 叠化,闪白,放大 \
  --bgms BGM1.mp3,BGM2.mp3 \
  --ratios 16:9,9:16 \
  --clip-duration 3s        # ffprobe 缺失时的兜底
```

> 变体之间保证至少一个参数不同（无显式命名时要求参数池含 ≥2 个选项）；单份失败不影响其他变体。

### 智能组装

```bash
python scripts/mile_cli.py assemble a.mp4 b.mp4 c.mp4 \
  --project "产品介绍-竖屏" --width 1080 --height 1920 \
  --captions captions.srt --bgm bgm.mp3 \
  --transition 叠化 --intent "科技产品 30 秒竖屏"
```

> 1.0.0 起只有 ffmpeg 也能探测时长；两者都缺失时，用 `--clip-duration 3s` 为所有素材指定统一入片时长；图片默认 3 秒。

### 智能剪辑（自然语言 / 字幕 / 推荐）

```bash
# 一句话下达指令：默认只解析 + 预演，确认无误再加 --apply 写盘
python scripts/mile_cli.py smart "我的项目" "把BGM音量调到30%，开头删掉1秒"
python scripts/mile_cli.py smart "我的项目" "把BGM音量调到30%，开头删掉1秒" --apply

# 滤镜 / 特效 / 蒙版 / 动画（名称按剪映素材库；预览确认后用 --apply 落盘）
python scripts/mile_cli.py smart "我的项目" "给第1个视频片段加1980滤镜，强度70"
python scripts/mile_cli.py smart "我的项目" "去掉所有蒙版" --apply

# 字幕纠错 + 断句（先预览，再落盘；断句后时长按字数比例重新分配）
python scripts/mile_cli.py subtitles "我的项目" --split-max 8
python scripts/mile_cli.py subtitles "我的项目" --split-max 8 --line-chars 14 --apply

# 推荐 BGM（云端曲库元数据排序）与转场（按时长/空隙/BPM 的规则建议）
python scripts/mile_cli.py recommend "我的项目" --tags "轻快,日常"
python scripts/mile_cli.py recommend "我的项目" --transitions --audio bgm.mp3

# 证据分析与剪辑决策（只读）：媒体信号 + 镜头边界 + 可解释建议与候选计划
python scripts/mile_cli.py analyze "我的项目" --media 素材.mp4 --scenes
python scripts/mile_cli.py decide "我的项目" --intent "科技产品 30 秒竖屏" --plan-out plan.json
```

智能层的能力边界（第一版刻意不夸大）：

- 意图解析是**规则/槽位解析器**，不是大模型；目标、时间或数值缺失时返回 `clarify` 让你补话，而不是猜测后改草稿。滤镜、视频/人物特效、蒙版、入场/出场/组合动画、字幕（文字）动画、缩放关键帧、素材入轨与文字描边/阴影/背景已支持通过计划写入 5.9 草稿；缓动曲线插值、调色等未验证能力会明确写入 `warnings`。
- 字幕纠错基于错写规则 + 术语表 + 标点归一，数字、URL、邮箱、话题会被保护；不做语义改写。
- `recommend` 的 BPM/能量来自 ffmpeg + numpy 的**信号分析**，曲库排序只用标题/分类/时长元数据；返回结果带 `method` 与 `note`，不代表“听懂”了音乐或看懂了画面。
- 语音转字幕（`smart "<草稿>" "生成字幕" --audio 素材`）需要可选后端 `faster-whisper`；未安装时返回 `ASR_UNAVAILABLE`，不会伪造文案。
- `analyze` / `decide` 是只读的证据层与决策层：镜头边界优先用可选的 PySceneDetect，未安装时降级为 ffmpeg scene 分数（`method` 字段会如实标注），两者都没有时返回 `SCENE_DETECT_UNAVAILABLE`。响度（LUFS/LRA）由 ffmpeg `ebur128` 整文件实测，`decide` 偏差超过 3 LU 给出 `review-loudness` 增益参考但不自动写盘——整文件测量不定位片段，必须人工确认。决策结果是带 `confidence`/`risk` 的候选计划，不是自动执行的“AI 判断”。

## 快速开始（Python API）

```python
import os
import sys

skill_root = os.environ.get("JY_SKILL_ROOT", os.path.abspath("."))
sys.path.insert(0, os.path.join(skill_root, "scripts"))

from jy_wrapper import JyProject

project = JyProject("我的视频")
project.add_media_safe(os.path.join(skill_root, "assets", "video.mp4"), "0s")
project.add_text_simple("米乐自动剪辑", start_time="1s", duration="3s", anim_in="复古打字机")
project.save()
```

## 设计原则

- **只改目标片段**：所有操作基于草稿时间线模型定位，不整份重写、不丢失剪映 7/8 的未知字段。
- **失败不留痕**：计划先在内存中执行并校验，全部成功后才原子保存；保存前自动备份。
- **内部微秒、对外秒**：避免浮点误差导致的帧级错位。
- **稳定错误码**：`MileError(code, reason, details)` + 统一 JSON 信封，便于 Agent 自主重试与纠错。

## 目录结构

```
mile-jianying-skill/
├── SKILL.md              # Agent 使用说明（入口）
├── README.md             # 本文件
├── rules/                # 分主题的剪辑规则与最佳实践
├── docs/                 # 执行手册、最小命令 SOP、评测/痛点/规划
├── examples/             # 可运行示例
├── prompts/              # 提示词模板
├── scripts/
│   ├── mile_cli.py       # 统一命令行（推荐入口）
│   ├── mile/             # 确定性编辑核心：timecode/draft/timeline/ops/plan/quality/assemble
│   │                     # 智能层：intent（中文意图）/ subtitles（字幕整理）/ recommend（推荐）/ smart（编排）
│   │                     # v1.1：preview（差异预览）/ profile（风格档案）/ variants（批量变体）
│   ├── jy_wrapper.py     # 高层 JyProject API
│   ├── draft_inspector.py / auto_exporter.py / asset_search.py ...
│   └── vendor/           # 内置 pyJianYingDraft（MIT，见 THIRD_PARTY_NOTICES.md）
├── tools/                # 录屏等桌面工具
├── data/                 # 云端音乐/文字样式库
└── tests/                # 单元测试（python -m unittest discover -s tests 或 pytest tests）
```

## 文档

- [SKILL.md](SKILL.md) — Agent 总入口与命令路由
- [docs/agent-playbook.md](docs/agent-playbook.md) — 执行手册与验收清单
- [docs/minimal-command-sop.md](docs/minimal-command-sop.md) — 最小命令 SOP
- [rules/precise-edit.md](rules/precise-edit.md) — 精准片段编辑
- [rules/memory.md](rules/memory.md) — 剪辑记忆与风格复用
- [rules/smart-edit.md](rules/smart-edit.md) — 自然语言到剪辑计划
- [docs/evaluation-report.md](docs/evaluation-report.md) — 功能评测结论
- [docs/real-jianying-5.9-e2e-report.md](docs/real-jianying-5.9-e2e-report.md) — 真实剪映 5.9 端到端实测报告（问题清单与维度评分）
- [docs/user-pain-points.md](docs/user-pain-points.md) — AI 剪辑需求与痛点总结
- [docs/improvement-plan.md](docs/improvement-plan.md) — 功能完善规划与路线图
- [docs/dev-requirement-v1.1.md](docs/dev-requirement-v1.1.md) — v1.1 策划开发需求文档（PRD：差异预览/风格档案/记忆治理/批量变体）
- [CHANGELOG.md](CHANGELOG.md) — 版本变更记录

## 相关链接

- 上游项目：[luoluoluo22/jianying-editor-skill](https://github.com/luoluoluo22/jianying-editor-skill)
- 内置草稿库：[pyJianYingDraft](https://github.com/GuanYixuan/pyJianYingDraft)（MIT）

## 许可

MIT License，Copyright (c) 2026 米乐。第三方组件署名见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
