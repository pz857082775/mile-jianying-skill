---
name: mile-jianying-skill
description: 米乐-自动剪映视频。面向剪映专业版的自然语言自动剪辑 Skill：草稿时间线理解、片段级精准编辑、结构化剪辑计划、剪辑记忆与风格复用、质量检查、素材/TTS/字幕/配乐/特效/录屏与导出。
version: 1.0.0
metadata:
  openclaw:
    requires:
      env:
        - JY_SKILL_ROOT
      bins:
        - python3
        - ffmpeg
    primaryEnv: JY_SKILL_ROOT
    emoji: "🎬"
    author: 米乐
---

# 米乐-自动剪映视频 (mile-jianying-skill)

当用户想要自动剪辑视频、修改剪映草稿、或对某个片段下达精准优化指令时使用本 Skill。

**默认入口是统一命令行** `scripts/mile_cli.py`：输出统一 JSON 信封、稳定错误码、可重复执行。需要创造全新内容或调用云端能力时，再使用 `scripts/jy_wrapper.py` 的 `JyProject` 高层 API。

## 🚨 重要开发原则 (CRITICAL RULES)

1. **脚本位置**：**禁止在 Skill 内部目录创建业务剪辑脚本**。用户的剪辑逻辑代码（`.py`）必须存放在用户当前项目的根目录（或 `scripts/` 子目录），保持 Skill 库纯净、可升级。
2. **不碰真实草稿做实验**：端到端测试必须使用临时目录中的草稿副本（`MILE_DRAFT_ROOT`），禁止直接修改用户真实草稿。
3. **先看再改**：任何修改前先 `timeline` / `find` 确认目标片段；优先用 `ref` 定位，避免用易漂移的序号。
4. **优先计划化**：多步修改用 `plan` + `apply`，一次事务化提交，失败自动回滚，避免半套编辑。
5. **改完必检**：保存后执行 `qa`，确认无空片段、越界、轨道冲突与字幕错位。
6. **配乐选择**：简单演示用默认音乐；实际项目优先检索 `data/cloud_music_library.csv`，按主题关键词推荐并询问用户。

## 🎯 Agent 命令路由

| 用户意图 | 命令 |
|----------|------|
| 用一句话描述剪辑需求 | `mile_cli.py smart "<草稿>" "中文指令"`（默认预览，确认后加 `--apply`） |
| 字幕有错别字 / 字幕太长要断句 | `mile_cli.py subtitles "<草稿>" --split-max 8` → `--apply` |
| 该配什么音乐 / 要不要加转场 | `mile_cli.py recommend "<草稿>" [--bgm|--transitions|--audio 素材]` |
| 先看证据再给建议（只读） | `mile_cli.py analyze "<草稿>" [--media 素材 --scenes]` → `mile_cli.py decide "<草稿>" --intent "..."`（可 `--plan-out` 导出候选计划） |
| 看看有哪些草稿 / 草稿里有什么 | `mile_cli.py drafts --detail` / `timeline "<草稿>"` |
| 找到某个片段（按文字/轨道/时间/角色） | `mile_cli.py find "<草稿>" --text "..."` |
| 对特定片段做精准修改 | `mile_cli.py edit "<草稿>" --op ... --target ...` |
| 把一段视频/图片加进草稿（含贴纸/画中画） | `mile_cli.py smart "<草稿>" "把 b.mp4 加到第3秒"` 或 `edit --op add_media --param path=b.mp4` |
| 多步剪辑需求 | `mile_cli.py plan` 校验 → `mile_cli.py apply` 应用 |
| 成片体检 / 自动修复 | `mile_cli.py qa "<草稿>" --fix` |
| 撤销 / 查看备份 | `mile_cli.py undo` / `backups` |
| 导出字幕 / 视频 | `mile_cli.py srt` / `export` |
| 从素材一键组装新片 | `mile_cli.py assemble ...` |
| 查看 / 召回 / 评价剪辑记忆 | `mile_cli.py memory --action ...` |
| 检查环境 | `mile_cli.py doctor` |

统一信封：

```json
{"ok": true, "code": "OK", "reason": "成功", "data": {}}
```

失败时 `ok=false`、`code` 为稳定错误码、退出码 2。时间默认单位为**秒**，支持 `500ms` / `1m2s` / `00:12`。

## 🤖 智能层（smart / subtitles / recommend / analyze / decide）

```bash
# 中文指令 → 计划 → 预演；确认后再加 --apply 落盘
python <SKILL_ROOT>/scripts/mile_cli.py smart "项目" "把BGM音量调到30%，开头删掉1秒"
python <SKILL_ROOT>/scripts/mile_cli.py smart "项目" "把BGM音量调到30%，开头删掉1秒" --apply

# 滤镜 / 特效 / 蒙版 / 动画：smart 直接说，或 edit/plan 精确指定
python <SKILL_ROOT>/scripts/mile_cli.py smart "项目" "给第1个视频片段加1980滤镜，强度70"
python <SKILL_ROOT>/scripts/mile_cli.py smart "项目" "去掉所有蒙版" --apply

# 字幕纠错 / 术语保护 / 断句 / 时长重分配
python <SKILL_ROOT>/scripts/mile_cli.py subtitles "项目" --split-max 8 --line-chars 14
python <SKILL_ROOT>/scripts/mile_cli.py subtitles "项目" --split-max 8 --apply --glossary terms.txt

# 推荐：BGM 走曲库元数据排序，转场走镜头时长/空隙/BPM 规则
python <SKILL_ROOT>/scripts/mile_cli.py recommend "项目" --tags "轻快,日常"
python <SKILL_ROOT>/scripts/mile_cli.py recommend "项目" --transitions --audio bgm.mp3

# 证据分析与剪辑决策（只读，不写草稿）
python <SKILL_ROOT>/scripts/mile_cli.py analyze "项目" --media 素材.mp4 --scenes
python <SKILL_ROOT>/scripts/mile_cli.py decide "项目" --intent "科技产品 30 秒竖屏" --plan-out plan.json
```

使用约定：

- `smart` 返回 `intent.confidence`、`clarify`、`warnings`、`plan`、`plan_result`（命中片段）、`qa`。出现 `clarify` 时**先向用户追问**，不要自行补全目标后直接 `--apply`。
- 字幕断句会产生新的文字片段（克隆原样式），共享同一文字素材的字幕会被跳过并写进 `skipped`，需要人工确认。
- 智能层是**规则/信号方法**，不是大模型：意图解析靠词表与槽位，BGM 排序靠标题/分类/时长元数据，节奏分析靠 ffmpeg+numpy 的能量自相关；返回中的 `method`/`note` 字段说明了这一点。
- 语音转字幕需要可选后端 `faster-whisper`；缺失时返回 `ASR_UNAVAILABLE`，此时应改用外部字幕文件（`assemble --captions` 或 `add_text`）。
- `analyze` / `decide` 是**只读**命令，不写草稿；`decide` 的 `plan_ops` 只是候选计划，须经用户确认再走 `plan`/`apply`。低置信度（<0.6）的建议必须转述 `confidence`/`risk`/`manual_review`，不得自动落盘。
- 镜头边界（`analyze --media --scenes`）优先使用可选的 PySceneDetect，未安装时诚实降级为 ffmpeg scene 分数信号；两者都没有返回 `SCENE_DETECT_UNAVAILABLE` 并附安装提示，不伪造“看懂了画面”。
- 响度（LUFS/LRA）由 ffmpeg `ebur128` 整文件实测（T1，无需额外依赖），`analyze --media` 的 `audio.loudness` 与 `decide --audio` 均附带；偏差超过 3 LU 生成 `review-loudness` 决策并给出 `suggested_volume` 参考，但不生成 plan_ops——整文件测量不定位片段，须人工确认后再 `set_volume`。静音（≤ -70 LUFS）如实标注 `silent`，不给增益建议。
- 滤镜、视频/人物特效、蒙版、入场/出场/组合动画、字幕动画、缩放关键帧、素材入轨与文字描边/阴影/背景已通过 vendored `pyJianYingDraft` 的 5.9 schema 适配，可用 `smart` 自然语言（`"给第1个视频片段加1980滤镜，强度70"`、`"从第2秒到第5秒把画面放大到1.5倍"`、`"给第1个字幕加白色描边，宽度30"`）或 `edit` / `plan` 精确写入；未加引号的名称按原文提取、置信度较低，预览命中 `FILTER_NOT_FOUND` / `EFFECT_NOT_FOUND` / `MASK_NOT_FOUND` / `ANIMATION_NOT_FOUND` / `TEXT_ANIMATION_NOT_FOUND` / `BAD_COLOR` 时按 `suggestions` 修正名称。缓动曲线插值、调色、音频增强、VIP 专属素材等未验证能力当前不会被伪造，`smart` 会写进 `warnings`。

## 🧩 精准编辑（edit）

支持的 `--op`：`delete` `trim` `move` `shift` `split` `set_text` `set_volume` `set_speed` `set_transform` `set_visible` `set_canvas` `add_transition` `remove_transition` `set_filter` `add_effect` `set_mask` `add_animation` `add_text_animation` `add_keyframes` `remove_keyframes` `remove_material` `clone_text` `add_text` `add_audio` `add_media`。其中滤镜/特效/蒙版/视频动画只作用于视频片段，`add_text_animation`（字幕动画）只作用于文字片段；`add_media` 无需 target，视频/图片素材入轨（`track=pip/贴纸` 放覆盖轨，缺省追加主轨末尾）；`add_keyframes` 的 `property` 支持 `scale/x/y/rotation/alpha/volume/scale_x/scale_y`（`frames` 为 `[{"at": 秒, "value": 数值}]`），`remove_material` 支持 `filter` / `effect` / `mask` / `animation` / `transition`。

`--target` 可以是片段 `ref`，也可以是定位条件 JSON：

```json
{"track": "video#1", "index": 0}
{"text_contains": "欢迎", "role": "caption"}
{"range": "12s-18s", "track": "video"}
```

示例：

```bash
# 裁掉开场前 2 秒，后续片段自动前移
python <SKILL_ROOT>/scripts/mile_cli.py edit "项目" \
  --op trim --target '{"track":"video#1","index":0}' \
  --param start=2s --param ripple=true

# 把第 3 条字幕改成新文案
python <SKILL_ROOT>/scripts/mile_cli.py edit "项目" \
  --op set_text --target '{"text_contains":"旧文案"}' --param text="新文案"

# 给片段与其后一片段之间加 0.4s 叠化
python <SKILL_ROOT>/scripts/mile_cli.py edit "项目" \
  --op add_transition --target '{"ref":"SEGV1"}' \
  --param type=叠化 --param duration=0.4s
```

注意：转场挂在前一个片段上；`target` 必须指向交界处的**前一个**片段，且后一个片段同轨相邻。

## 🧠 剪辑记忆（memory）

- `apply` / `edit` / `assemble` 默认自动记录：需求描述（intent）、标签、操作序列、草稿指纹、结构签名、风格摘要、结果与自评分。
- 相同草稿的重复保存会在 30 分钟窗口内合并，避免刷屏。
- 处理同类视频前先召回：

```bash
python <SKILL_ROOT>/scripts/mile_cli.py memory --action recall \
  --intent "科技产品 30 秒竖屏" --k 3
```

- 用户认可结果后，写入正反馈以强化风格权重：

```bash
python <SKILL_ROOT>/scripts/mile_cli.py memory --action feedback --id <id> --rating 5
```

- 记忆目录默认在用户配置目录，可用 `MILE_MEMORY_DIR` 覆盖；`--no-remember` 可关闭单次记录。
- `assemble` 默认读取历史风格；可用 `--style-from` / `--style` / `--no-memory-style` 控制。

## 📋 结构化计划（plan / apply）

```json
{
  "intent": "收紧节奏并统一字幕",
  "tags": ["快节奏", "字幕"],
  "ops": [
    {"op": "trim", "target": {"ref": "SEGV1"}, "end": "2.5s", "ripple": true},
    {"op": "add_transition", "target": {"ref": "SEGV1"}, "type": "叠化", "duration": "0.4s"},
    {"op": "set_volume", "target": {"role": "bgm"}, "volume": "20%"},
    {"op": "set_text", "target": {"text_contains": "第二句"}, "text": "第二句改好了"}
  ]
}
```

```bash
python <SKILL_ROOT>/scripts/mile_cli.py plan  "<草稿>" --plan plan.json   # 只校验，不改动
python <SKILL_ROOT>/scripts/mile_cli.py apply "<草稿>" --plan plan.json   # 事务化应用
```

`plan`/`apply` 支持 `--plan -` 从 stdin 读取，便于 Agent 直接管道传入。

## ✅ 质量检查（qa）

```bash
python <SKILL_ROOT>/scripts/mile_cli.py qa "<草稿>"
python <SKILL_ROOT>/scripts/mile_cli.py qa "<草稿>" --fix --no-backup
```

检查项包括：空片段、时间区间越界/重叠、主轨断裂、字幕与语音错位、音量异常、转场挂在错误端等。`--fix` 只修复可确定的问题，并把修复动作写入返回结果。

## 📖 规则指南 (Rules)

- [rules/setup.md](rules/setup.md) — **必读**：环境初始化与路径探测
- [rules/core.md](rules/core.md) — 保存、导出、草稿管理
- [rules/cli.md](rules/cli.md) — CLI 契约与机器可读输出
- [rules/precise-edit.md](rules/precise-edit.md) — 片段级精准编辑与定位
- [rules/memory.md](rules/memory.md) — 剪辑记忆、召回与风格复用
- [rules/smart-edit.md](rules/smart-edit.md) — 自然语言需求 → 剪辑计划
- [rules/media.md](rules/media.md) — 素材导入与 AI 视频分析优化（30 分钟/360p）
- [rules/text.md](rules/text.md) — 字幕、文本与标题
- [rules/keyframes.md](rules/keyframes.md) — 关键帧动画
- [rules/effects.md](rules/effects.md) — 滤镜、特效、转场搜索与应用
- [rules/recording.md](rules/recording.md) — 录屏与智能变焦
- [rules/web-vfx.md](rules/web-vfx.md) — Web 动效转视频
- [rules/generative.md](rules/generative.md) — 生成式剪辑思维链
- [rules/audio-voice.md](rules/audio-voice.md) — TTS 配音与 BGM 选源

## 📚 执行手册

- [docs/agent-playbook.md](docs/agent-playbook.md) — 任务路由、运行时模板、验收清单
- [docs/minimal-command-sop.md](docs/minimal-command-sop.md) — 最小命令 SOP
- [docs/evaluation-report.md](docs/evaluation-report.md) — 功能评测结论
- [docs/real-jianying-5.9-e2e-report.md](docs/real-jianying-5.9-e2e-report.md) — 真实剪映 5.9 端到端实测报告
- [docs/user-pain-points.md](docs/user-pain-points.md) — 需求与痛点总结
- [docs/improvement-plan.md](docs/improvement-plan.md) — 完善规划与路线图

## ⚠️ 剪映版本兼容性

- **已验证**：剪映专业版 5.9.0.11632（Windows）可完成草稿创建、打开、编辑、自动导出与成片校验。
- **GUI 自动导出**：剪映 5.9 已适配首页双击进入、冷启动重试与同名文件夹导出；`export` 返回真实成片路径（`output`）并携带 `size` / `verified` / 时长 / 分辨率。若返回 `EXPORT_FAILED`，按 `details.export` 中的候选路径排查，不要盲目重试。
- **ffmpeg**：无 `ffprobe` 时会自动使用 `MILE_FFMPEG` / `JIANYING_HOME` / PATH / 剪映标准安装目录 / 正在运行的剪映进程目录中的 `ffmpeg.exe`，解码一帧并解析时长/分辨率/编码/帧率；也可用 `MILE_FFPROBE` 指定探针。
- **贴纸缩放**：`add_sticker_at(scale=...)` 的 scale 是相对“适应画布”的倍数，不是像素；小标记建议 0.08–0.15。需要精确像素时使用 `size_mode="pixel"` + `width_px` / `height_px`。
- **角色识别**：`find --role` 支持 `caption/title/voice/bgm/pip/sticker`；图片贴纸与视频画中画会分别返回 `sticker` 与 `pip`。

## 🧰 常用工具

```bash
# 草稿检查
python <SKILL_ROOT>/scripts/draft_inspector.py list --limit 20
python <SKILL_ROOT>/scripts/draft_inspector.py summary --name "DraftName"

# 素材/特效/转场搜索
python <SKILL_ROOT>/scripts/asset_search.py "复古" -c filters
python <SKILL_ROOT>/scripts/asset_search.py "雾化" -c transitions

# 影视解说生成
python <SKILL_ROOT>/scripts/movie_commentary_builder.py --video "video.mp4" --json "storyboard.json"

# 录屏 + 智能变焦
python <SKILL_ROOT>/tools/recording/recorder.py

# 无头导出
python <SKILL_ROOT>/scripts/auto_exporter.py "DraftName" "output.mp4" --res 1080 --fps 60

# 环境自检
python <SKILL_ROOT>/scripts/api_validator.py --json
```

## 🚀 Python API 快速开始

```python
import os
import sys

current_dir = os.path.dirname(os.path.abspath(__file__))
env_root = os.getenv("JY_SKILL_ROOT", "").strip()
skill_root = next((p for p in [
    env_root,
    os.path.join(current_dir, ".agent", "skills", "mile-jianying-skill"),
    os.path.join(current_dir, ".trae", "skills", "mile-jianying-skill"),
    os.path.join(current_dir, ".claude", "skills", "mile-jianying-skill"),
    os.path.join(current_dir, "skills", "mile-jianying-skill"),
    os.path.abspath(".agent/skills/mile-jianying-skill"),
    os.path.abspath(".claude/skills/mile-jianying-skill"),
    os.path.dirname(current_dir),
] if p and os.path.exists(os.path.join(p, "scripts", "jy_wrapper.py"))), None)

if not skill_root:
    raise ImportError("Could not find mile-jianying-skill root. Set JY_SKILL_ROOT.")
sys.path.insert(0, os.path.join(skill_root, "scripts"))
from jy_wrapper import JyProject

if __name__ == "__main__":
    project = JyProject("我的视频")
    assets_dir = os.path.join(skill_root, "assets")

    project.add_media_safe(os.path.join(assets_dir, "video.mp4"), "0s")
    project.add_media_safe(os.path.join(assets_dir, "audio.mp3"), "0s", track_name="Audio")
    project.add_text_simple("米乐自动剪辑", start_time="1s", duration="3s", anim_in="复古打字机")

    project.save()
```

## 🛠️ 项目规范

- 初始化 `JyProject` 时按主视频比例设置分辨率，默认横屏 1920x1080。
- 业务脚本放在用户项目根目录；Skill 目录只放工具集源码。
- 内部时间统一微秒，对外接口默认秒；不要手写微秒字面量。
- 草稿保存为原子写入并自动备份；不要绕过 `Draft.save()` 直接写文件。
