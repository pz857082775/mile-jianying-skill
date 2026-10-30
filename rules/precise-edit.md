---
name: precise-edit
description: 对剪映草稿做片段级精准编辑：定位、裁剪、分割、移动、变速、音量、字幕、转场与画面变换。
metadata:
  tags: edit, segment, timeline, precise
---

# 精准片段编辑

所有编辑都通过 `scripts/mile_cli.py` 完成。核心原则：**先定位、再修改、后体检**。

## 1. 先看时间线

```bash
python <SKILL_ROOT>/scripts/mile_cli.py timeline "<草稿>" --pretty
python <SKILL_ROOT>/scripts/mile_cli.py timeline "<草稿>" --limit 50 --pretty
```

每个片段返回 `ref`（稳定定位符）、`track_type`、`track_label`、`index_in_track`、`start`/`duration`/`end`（秒）、`role`、`text`、`name` 等字段。

- `ref` 是首选定位方式；序号会随增删而漂移。
- `role`：`main` 主轨 / `broll` 画中画轨 / `caption` 自动字幕 / `title` 普通文本 / `bgm` / `voice`。

## 2. 查找目标片段

```bash
# 按字幕内容
python <SKILL_ROOT>/scripts/mile_cli.py find "<草稿>" --text "欢迎" --role caption

# 按轨道 + 序号
python <SKILL_ROOT>/scripts/mile_cli.py find "<草稿>" --track "video#1" --index 0

# 按时间范围
python <SKILL_ROOT>/scripts/mile_cli.py find "<草稿>" --range 12s-18s

# 按素材文件名
python <SKILL_ROOT>/scripts/mile_cli.py find "<草稿>" --name "片头.mp4"
```

定位选择器可用于 `find --target` 与 `find` 的各个参数，也可写入计划里的 `target`：

| 键 | 含义 |
|----|------|
| `ref` / `ids` | 片段 ref 或 id 前缀（可多个） |
| `track` | 轨道序号 / 名称 / `video#2` / `视频1` |
| `index` | 轨道内序号（0 开始，或 `last`） |
| `range` | 时间范围 `12s-18s` |
| `at` | 时间点 `12.5s` |
| `text_contains` | 文字包含 |
| `name_contains` | 素材名/路径包含 |
| `role` | `caption`/`title`/`bgm`/`voice`/`pip`/`main`/`broll` |
| `material_type` | `video`/`photo`/`text`/`audio`/`sticker` |
| `min_duration` / `max_duration` | 时长过滤 |

多目标时默认全部处理；`--first` 只处理第一个。

## 3. 执行编辑

```bash
python <SKILL_ROOT>/scripts/mile_cli.py edit "<草稿>" \
  --op <操作> --target '<ref 或 JSON>' --param key=value [--param ...] \
  [--first] [--dry-run]
```

| op | 参数 | 说明 |
|----|------|------|
| `trim` | `start` / `end` / `duration` / `ripple` | 调整入出点；`ripple=true` 时后续片段整体前移/后移 |
| `split` | `at` | 在时间点一分为二，自动处理源区间与变速素材 |
| `delete` | `ripple` | 删除片段；`ripple=true` 时后移补位 |
| `move` | `start` / `delta` / `track` / `overlap` | 移动片段，可跨同类型轨道 |
| `shift` | `delta` | 相对平移 |
| `set_text` | `text` / `font_size` / `color` / `bold` / `italic` / `underline` / `align` / `alpha` / `letter_spacing` / `line_spacing` / `border` / `shadow` / `background` | 修改字幕/文本内容与样式；`border={"color","width"(0-100),"alpha"}` 描边、`shadow={"color","alpha","distance"(0-100),"angle"(-180~180),"feather"(0-1)}` 阴影、`background={"color","style"(1|2),"alpha","round_radius","height","width","horizontal_offset","vertical_offset"}` 背景，传 `False` 移除；颜色支持十六进制或 RGB 数组 |
| `set_volume` | `volume`（0-4）/ `fade_in` / `fade_out` | 音量与淡入淡出 |
| `set_speed` | `speed`（0.1-10）/ `keep_duration` | 固定变速 |
| `set_transform` | `x` / `y` / `scale` / `scale_x` / `scale_y` / `rotation` / `alpha` / `flip_horizontal` / `flip_vertical` | 画面变换 |
| `set_visible` | `visible` | 显示/隐藏 |
| `set_canvas` | `width` / `height` | 修改画布分辨率 |
| `add_transition` | `type` / `duration` | 加转场（作用于片段与其后一片段之间） |
| `remove_transition` | — | 移除该交界处转场 |
| `clone_text` | `text` / `start` / `duration` | 复制文本片段并可改文案/时间 |
| `add_text` | `text` / `start` / `duration` / `track` / 样式参数 | 新建文本 |
| `add_audio` | `path` / `start` / `duration` / `volume` / `track` / `loop` | 新建音频 |
| `add_media` | `path` / `start` / `duration` / `source_start` / `track` / `volume` / `scale` / `x` / `y` / `rotation` / `alpha` / `name` | 视频/图片素材入轨：缺省追加主视频轨末尾；`track` 取 `pip`/`画中画`/`sticker`/`贴纸` 时放入 `flag=2` 覆盖轨（自动创建并复用）；图片默认 3 秒，视频时长优先探测，无法探测返回 `DURATION_UNKNOWN`；音频文件请用 `add_audio` |
| `set_filter` | `name` / `intensity`（0-100）/ `keep_old` | 设置视频片段滤镜，默认替换旧滤镜 |
| `add_effect` | `name` / `params`（0-100 数组）/ `keep_old` | 添加视频或人物特效 |
| `set_mask` | `name` / `center_x` / `center_y` / `size` / `rotation` / `feather` / `invert` / `rect_width` / `round_corner` | 设置视频片段蒙版，默认替换旧蒙版 |
| `add_animation` | `name` / `kind`（`in`/`out`/`group`）/ `duration` | 添加入场、出场或组合动画，同类默认替换 |
| `add_text_animation` | `name` / `kind`（`in`/`out`/`loop`）/ `duration` | 给文字片段加字幕入场/出场/循环动画，同类默认替换；只作用于文字片段 |
| `add_keyframes` | `property` / `frames` | 为属性添加关键帧：`property` 取 `scale`/`x`/`y`/`rotation`/`alpha`/`volume`/`scale_x`/`scale_y`；`frames` 为 `[{"at": 秒, "value": 数值}]`，同属性合并并按时间排序。`scale` 走等比缩放（`uniform_scale.on=true` + `KFTypeScaleX`，与真实 5.9 草稿一致） |
| `remove_keyframes` | `property`（可省略） | 移除指定属性的关键帧列表；省略时清空全部 |
| `remove_material` | `type`（`filter`/`effect`/`mask`/`animation`/`transition`） | 摘除指定挂载素材并回收未引用素材 |

参数值经 JSON 优先解析：`ripple=true` 会变成布尔值，`volume=0.2` 变成数字，`text=你好` 保持字符串。

### 转场语义（容易错）

剪映的转场挂在**前一个**片段上，描述它与后一片段的交界。因此：

```bash
# SEGV1 与它后面的 SEGV2 之间加叠化
python <SKILL_ROOT>/scripts/mile_cli.py edit "<草稿>" \
  --op add_transition --target '{"ref":"SEGV1"}' \
  --param type=叠化 --param duration=0.4s
```

- `target` 必须指向交界处的前一个片段，且后一个片段同轨相邻。
- 转场时长不能超过任一侧片段时长，超出会被收敛并返回 `warning`。
- 对最后一个片段加转场会得到 `NOT_ADJACENT`。

## 4. 体检与回滚

```bash
python <SKILL_ROOT>/scripts/mile_cli.py qa "<草稿>" --fix
python <SKILL_ROOT>/scripts/mile_cli.py backups "<草稿>"
python <SKILL_ROOT>/scripts/mile_cli.py undo "<草稿>"
```

- 每次保存前自动备份；`undo` 回滚最近一次保存。
- `qa --fix` 只做确定安全的修复（负开始时间归零、音量收敛到 4、时长字段同步、画布奇数修正），结构性问题保留在报告中。
- 修改前可加 `--dry-run` 预演，只返回将要发生的变更。

## 5. 常见错误码

| code | 含义 | 处理 |
|------|------|------|
| `SEGMENT_NOT_FOUND` | ref/条件未命中 | 重新 `find`，确认 ref |
| `TARGET_REQUIRED` | 多目标操作未指定目标 | 补 `--target` 或 `--first` |
| `SEGMENT_TOO_SHORT` | 结果不足一帧 | 放宽入出点 |
| `BAD_RANGE` | 时间/音量/速度越界 | 按提示范围修正 |
| `TRACK_NOT_FOUND` | 轨道不存在 | 用 `timeline` 查实际轨道 |
| `TRACK_OVERLAP` | 移动后与同轨片段重叠 | 用 `overlap=allow` 或换轨 |
| `NOT_ADJACENT` | 转场目标后无相邻片段 | 指向前一个片段 |
| `MATERIAL_MISSING` | 素材引用失效 | 用 `qa` 定位并修复 |
| `MISSING_ARGS` | `smart`/`plan` 缺少指令或参数 | 补一句中文需求，或补齐 `--param` |
| `ASR_UNAVAILABLE` | 未安装 faster-whisper，无法本地转写 | 装依赖，或改用外部 SRT 走 `subtitles` |
| `ASR_FAILED` | 转写模型/解码失败 | 换模型或素材，不要伪造字幕 |
| `NUMPY_MISSING` | 音频 BPM 分析缺 numpy | `pip install numpy`，或只用曲库元数据推荐 |
| `FFMPEG_MISSING` / `AUDIO_DECODE_FAILED` / `AUDIO_EMPTY` / `AUDIO_UNSUPPORTED` | 音频无法解码分析 | 检查 `--audio` 路径与编码，或跳过信号分析 |
| `LIBRARY_NOT_FOUND` | 曲库 CSV 不存在 | 检查 `data/cloud_music_library.csv` 或 `--library` |

## 6. 推荐节奏

1. `timeline` / `find` 建立目标清单（记录 ref）。
2. 单步修改用 `edit`；多步需求写计划走 `plan` → `apply`。
3. `qa` 体检；有问题按 `code` 定向处理。
4. 用户确认后 `memory feedback --rating` 强化经验。
