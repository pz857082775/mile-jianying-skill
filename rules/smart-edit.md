---
name: smart-edit
description: 把自然语言剪辑需求翻译成结构化剪辑计划，校验后事务化执行，并写入记忆。
metadata:
  tags: plan, natural-language, workflow, transactional
---

# 自然语言 → 剪辑计划

面向"帮我把这条视频剪得更紧凑/换个字幕/配个音乐/把开头改一下"这类需求。默认流程：

```
理解需求 → 查看时间线 → 生成计划 → plan 校验 → apply 执行 → qa 体检 → 记忆反馈
```

## 1. 需求拆解

先把口语需求拆成可定位、可量化的操作：

| 用户说法 | 计划表达 |
|----------|----------|
| "开头太长了" | `trim` 主轨第一段，`end=3s`，`ripple=true` |
| "这段字幕错了" | `set_text`，`target={"text_contains":"旧文案"}` |
| "把这段删掉，后面接上" | `delete`，`ripple=true` |
| "中间加个转场" | `add_transition`，`target` 指向前一段，`type=叠化` |
| "BGM 太吵" | `set_volume`，`target={"role":"bgm"}`，`volume=0.2` |
| "整体加快一点" | 逐段 `set_speed`，或在组装时用快节奏风格 |
| "竖屏 1080x1920" | `set_canvas`，`width=1080`,`height=1920` |
| "字幕换成大一点白色" | `set_text`，`font_size=...`，`color=#ffffff` |
| "给主轨加个复古滤镜" | `set_filter`，`target={"role":"main"}`，`name=1980`（用 `asset_search.py -c filters` 查准确名称） |
| "给字幕加卡拉OK入场动画" | `add_text_animation`，`target={"role":"caption"}`，`name=卡拉OK`，`kind=in` |
| "从第2秒到第5秒把画面放大到1.5倍" | `add_keyframes`，`property=scale`，`frames=[{"at":2,"value":1.0},{"at":5,"value":1.5}]`（起始值按 1.0 假设，预览确认） |
| "把 b.mp4 加到第3秒" / "加个贴纸 x.png" | `add_media`，`path=b.mp4`，`start=3s`；贴纸/画中画用 `track="sticker"` / `"pip"`，不给时间则追加轨道末尾 |
| "给字幕加白色描边" / "去掉阴影" | `set_text`，`border={"color":"#ffffff","width":30}` / `shadow=False`；背景用 `background={"color":...,"style":1\|2}` |
| "加个线性蒙版" | `set_mask`，`name=线性`；蒙版名可用 `linear/circular/rectangle/heart/star` 或中文 |
| "给这段加故障特效" | `add_effect`，`name=故障`（`asset_search.py -c video_scene_effects` 查名） |
| "第1段加入场动画" | `add_animation`，`kind=in`，`name` 用剪映动画名（`asset_search.py -c animations` 或枚举名） |
| "去掉滤镜/蒙版/特效" | `remove_material`，`type=filter/mask/effect/animation` |

拆不动、目标不唯一或会破坏内容时，**先问用户**，不要猜。

## 2. 计划格式

```json
{
  "intent": "收紧开场节奏并把字幕改短",
  "tags": ["快节奏", "字幕"],
  "ops": [
    {"op": "trim", "target": {"role": "main", "index": 0}, "end": "3s", "ripple": true},
    {"op": "set_text", "target": {"text_contains": "欢迎来到我们的频道"}, "text": "欢迎"},
    {"op": "set_volume", "target": {"role": "bgm"}, "volume": "20%"},
    {"op": "add_transition", "target": {"ref": "SEGV1"}, "type": "叠化", "duration": "0.4s"}
  ]
}
```

- 顶层 `ops` 也接受 `operations`；单个操作可直接传一个操作对象。
- 操作别名：`删除/裁剪/分割/改文字/音量/变速/画面/隐藏/转场/滤镜/特效/蒙版/动画/字幕动画/关键帧/移除关键帧/移除素材/加素材/入轨/加贴纸/克隆字幕/加字幕/加音乐/画布`。
- 控制字段：`first: true`（多目标只取第一个）、`note`/`description`（备注，不执行）。
- 目标字段可直接写在操作上（如 `"text_contains": "欢迎"`），也可放进 `target` 对象。
- `trim` / `split` / `add_transition` / `remove_transition` / `clone_text` 一次只能作用于一个片段；多目标必须缩小条件或加 `first: true`。
- `add_text` / `add_audio` / `set_canvas` 不需要 target；`add_text` 必须提供 `text` 与 `duration`，`add_audio` 必须提供 `path`。

## 3. 校验与执行

```bash
# 只校验：返回解析后的操作、命中片段、预计变更，不写草稿
python <SKILL_ROOT>/scripts/mile_cli.py plan "<草稿>" --plan plan.json --pretty

# 应用：全部成功才保存；任一失败回滚内存修改，草稿保持原样
python <SKILL_ROOT>/scripts/mile_cli.py apply "<草稿>" --plan plan.json \
  --intent "收紧开场节奏并把字幕改短" --tags "快节奏,字幕"

# 预演但不保存
python <SKILL_ROOT>/scripts/mile_cli.py apply "<草稿>" --plan plan.json --dry-run

# 从 stdin 传入计划
cat plan.json | python <SKILL_ROOT>/scripts/mile_cli.py apply "<草稿>" --plan -
```

执行顺序即数组顺序；后面的操作可以引用前面操作产生的片段（例如先 `add_text`，再按 `text_contains` 改它）。

## 4. 何时用 `edit`，何时用计划

- **单点修改**（改一句字幕、裁一个片段、调一次音量）：直接 `edit`，少一层 JSON。
- **多步修改 / 有依赖 / 需要一次性成功**：用计划，保证事务性，并让记忆记录完整操作序列。
- **从零组装**：用 `assemble`，不要手写几十条 `add_*` 操作。

## 5. 执行后的验收

```bash
python <SKILL_ROOT>/scripts/mile_cli.py qa "<草稿>"
```

验收要点：

- 目标片段确实变化（对比 `apply` 返回的 `changes`）。
- 非目标片段未被误改（计划返回命中列表，可核对）。
- 主轨无断裂、字幕无重叠、音量与转场在安全范围。
- 用户确认后写反馈：

```bash
python <SKILL_ROOT>/scripts/mile_cli.py memory --action feedback --id <id> --rating 5
```

## 6. 失败处理

| 情况 | 做法 |
|------|------|
| `NO_TARGET` | 重新 `find`，放宽或修正定位条件 |
| `TARGET_AMBIGUOUS` | 缩小条件，或明确 `"first": true` |
| `MISSING_ARGS` | 补齐该 op 必需参数 |
| `SEGMENT_TOO_SHORT` | 调整 `end`/`duration`，保留至少一帧 |
| `TRACK_OVERLAP` | 调整目标时间，或用 `move` 的 `overlap: "allow"` |
| `NOT_ADJACENT` | 转场目标改为交界处前一个片段 |

`apply` 失败时草稿不变；修正计划后可直接重试，无需手动恢复。

## 7. 直接说中文：`smart`

不想手写 JSON 时，用 `smart` 让规则解析器把中文指令翻译成计划：

```bash
# 默认 dry-run：返回 intent / plan / 命中片段 / qa，不写盘
python <SKILL_ROOT>/scripts/mile_cli.py smart "<草稿>" "把BGM音量调到30%，开头删掉1秒"

# 用户确认后再落盘
python <SKILL_ROOT>/scripts/mile_cli.py smart "<草稿>" "把BGM音量调到30%，开头删掉1秒" --apply

# 顺带整理字幕，并附带推荐
python <SKILL_ROOT>/scripts/mile_cli.py smart "<草稿>" "字幕纠错，推荐一个配乐" \
  --polish --split-max 8 --recommend bgm
```

阅读返回结果的顺序：

1. `intent.clarify` 非空 → 目标/时间/数值有歧义，**先追问**，不要猜。
2. `intent.confidence` → 规则命中的最低置信度；低于 0.6 时把 `plan` 展示给用户确认。
3. `plan_result.operations[*].target_detail` → 核对是否只命中了预期片段。
4. `intent.warnings` → 未覆盖能力（调色、美颜、倒放、抠像跟踪等）会在这里说明，不是失败。滤镜、蒙版、特效、视频/字幕动画、**缩放关键帧**、素材入轨与文字**描边/阴影/背景**已可通过 `smart`/`plan` 写入 5.9 草稿；其余属性的关键帧请用 `edit --op add_keyframes` 显式给 `frames`。
5. `qa` → 应用后的体检结论。

可识别的说法示例：`把BGM音量调到30%`、`开头删掉1秒`、`结尾裁剪2秒`、`把第2个字幕改成你好`、`给第1个视频片段加0.5秒叠化`、`删除所有转场`、`在第2秒分割视频`、`把视频速度调到1.5倍`、`画布改成9:16`、`给第1个视频片段加1980滤镜，强度70`、`去掉所有蒙版`、`加个故障特效`、`加Kira游动入场动画`、`给第1个字幕加卡拉OK入场动画`、`从第2秒到第5秒把画面放大到1.5倍`、`把 b.mp4 加到第3秒`、`加个贴纸 x.png`、`给第1个字幕加白色描边，宽度30`、`去掉所有阴影`、`字幕纠错`、`推荐一个配乐`、`生成字幕`。分句可用 `，` `。` `然后` `再` `并` 连接。

> 滤镜/特效/蒙版/动画名称按分句原文提取，不保证命中剪映素材库；预览若返回 `FILTER_NOT_FOUND` / `EFFECT_NOT_FOUND` / `MASK_NOT_FOUND` / `ANIMATION_NOT_FOUND`，按 `suggestions` 修正名称，或改用 `edit --op set_filter --param name=<准确名>`。未加引号时置信度较低（<0.8），低于 0.6 请先展示计划让用户确认。

## 8. 字幕整理：`subtitles`

```bash
python <SKILL_ROOT>/scripts/mile_cli.py subtitles "<草稿>" --split-max 8 --line-chars 14   # 预览
python <SKILL_ROOT>/scripts/mile_cli.py subtitles "<草稿>" --split-max 8 --apply           # 写入
```

- 纠错内容：错写规则表 + 术语表（`--glossary`，支持 `术语列表.txt` 或 `错 -> 对` 写法）+ 全角标点归一；数字、URL、邮箱、`#话题`、`@用户`、时间码都会被保护，不参与改写。
- 断句结果：一条字幕超长时会拆成多条，时长按字数比例重新分配，克隆片段继承原样式。
- `applied.skipped` 里列出**共享同一文字素材**的片段——改写会连带影响其它字幕，因此默认跳过，需要人工确认后再单独 `edit --op set_text`。
- 预览（不加 `--apply`）不会写盘、不会产生备份，可以先用 `items[*].parts` 给用户看效果。

## 9. 推荐：`recommend`

```bash
python <SKILL_ROOT>/scripts/mile_cli.py recommend "<草稿>" --tags "轻快,日常"      # BGM 候选
python <SKILL_ROOT>/scripts/mile_cli.py recommend "<草稿>" --transitions           # 转场建议
python <SKILL_ROOT>/scripts/mile_cli.py recommend "<草稿>" --audio bgm.mp3         # 先分析节奏
```

诚实标注能力来源，不要把结果说成“AI 听懂了音乐/看懂了画面”：

| 输出字段 | 方法 | 局限 |
|----------|------|------|
| `bgm.method = "metadata-rules"` | 云端曲库的标题/分类/时长做关键词与时长匹配 | 没有音频指纹、没有跨模态理解；分数只用于缩小候选 |
| `audio.bpm / energy_db / silence_ratio` | ffmpeg 解码 PCM + numpy 能量自相关（`bpm_method`） | 单节拍估计，复合节奏/人声轨不稳；置信度是自相关强度，不是模型概率 |
| `transitions.method = "duration-rules"` | 镜头时长、片段空隙、可选 BPM | 不看画面内容，必须配合预览与 `qa` |
| `scenes.method = "pyscenedetect" / "ffmpeg-scene-signal"` | 画面差异分数的镜头边界；优先开源 PySceneDetect，未安装时降级 ffmpeg `select=gt(scene,t)` | 只是差异信号，不代表内容变化；阈值 0-1 需按素材调；两者都缺失返回 `SCENE_DETECT_UNAVAILABLE` |
| `audio.loudness.method = "ffmpeg-ebur128"` | 整文件综合响度 I（LUFS）与 LRA 实测，默认目标 -16 LUFS（口播近似） | 不定位到具体片段；达到 -70 LUFS 门限按静音标注 `silent`，不给增益建议；素材与片段的音量映射需人工确认 |

应用建议的方式：把 `transitions.items[*]` 转成 `add_transition` 计划，或直接把 `bgm.items[*].url` / `music_id` 交给 `edit --op add_audio` / 云端音乐接口，用户确认后再 `--apply`。

## 10. 语音转字幕的边界

`smart "<草稿>" "生成字幕" --audio 素材.mp4` 依赖可选后端 `faster-whisper`：

- 未安装 → 返回 `asr.available=false`、错误码 `ASR_UNAVAILABLE`，并给出提示，不会编造文案。
- 已安装 → 转写结果经同一套纠错/断句流水线整理后生成 `add_text` 操作，默认仍只预览。
- 没有本地 ASR 时，优先让用户提供字幕文件（`assemble --captions` 或 SRT → `add_text`）。

## 11. 证据分析与剪辑决策：`analyze` / `decide`（只读）

两个命令都不改草稿（不写盘、不改 mtime），用于“先看证据、再给建议、最后才执行”：

```bash
# 能力矩阵 + 时间线观察（主轨时长分布、字幕覆盖、BGM 音量）
python <SKILL_ROOT>/scripts/mile_cli.py analyze "<草稿>"

# 媒体信号：黑帧/静帧/音频节奏；--scenes 附带镜头边界
python <SKILL_ROOT>/scripts/mile_cli.py analyze "<草稿>" --media 素材.mp4 --scenes

# 决策：意图 → 风格节奏区间 → 可解释建议 + 候选 plan_ops（低置信度只进 manual_review）
python <SKILL_ROOT>/scripts/mile_cli.py decide "<草稿>" --intent "科技产品 30 秒竖屏" --plan-out plan.json
```

使用边界：

- `analyze` 返回 `capabilities`（signal/scene/pyscenedetect/faster_whisper/ocr/sam2 的可用性与缺失依赖）；能力缺失不报错，写进 `missing` 或 `warnings`。
- `--scenes` 的后端选择是诚实的：装了 `scenedetect[opencv]` 走 `pyscenedetect`，否则降级 `ffmpeg-scene-signal`，都没有返回 `SCENE_DETECT_UNAVAILABLE`。边界只是画面差异信号，用于校准“这里可能是一个镜头切换”，不等于看懂内容。
- `analyze --media` 的音频证据附带 `loudness`（ffmpeg ebur128 实测 LUFS/LRA）；`decide --audio` 同样接入，偏差超过 3 LU 会生成 `review-loudness` 决策——它给出参考增益（`suggested_volume`）但**不生成 plan_ops**，因为整文件响度无法定位到片段，必须人工确认后手动 `set_volume`。
- `decide` 的 `plan_ops` 是候选计划，确认后交给 `plan`/`apply` 事务化执行；`confidence`、`risk`、`manual_review` 必须如实转述给用户，低置信度不自动落盘。

