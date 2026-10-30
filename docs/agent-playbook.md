# Agent Playbook

任务路由与验收清单。所有命令以 `<SKILL_ROOT>` 指代本 Skill 根目录。

## 0) 路由：先判断是"改"还是"建"

| 场景 | 路径 |
|------|------|
| 修改已有草稿、精修某个片段、换字幕/调音量/加转场 | **改**：`mile_cli.py` 的 `timeline/find/edit/plan/apply/qa` |
| 用户直接甩一句中文需求（"把BGM音量调到30%""删掉所有转场"） | **智能改**：`mile_cli.py smart`（先看预览，再 `--apply`） |
| 字幕有错别字/太长/要重新断句 | **字幕整理**：`mile_cli.py subtitles`（预览 → `--apply`） |
| 问"配什么音乐""要不要加转场" | **推荐**：`mile_cli.py recommend`（只读，不改草稿） |
| 用素材从零生成一个新视频 | **建**：`assemble` 或 `JyProject` 脚本 |
| 两者都有 | 先 `assemble` 出底稿，再用 `edit`/计划精修 |

### 改（默认路径）

1. `mile_cli.py timeline "<草稿>" --pretty` 看清轨道与片段 ref。
2. `mile_cli.py find "<草稿>" ...` 锁定目标。
3. 单点修改 `mile_cli.py edit ...`；多步修改写计划 → `plan` → `apply`。
4. `mile_cli.py qa "<草稿>" --fix` 体检。
5. 用户满意后 `memory --action feedback --id <id> --rating 5`。

### 建（Quick Build Template）

1. 确认草稿根目录与素材存在（不要递归扫全盘）。
2. 二选一：
   - **推荐**：`mile_cli.py assemble clips... --project "..." --captions ... --bgm ... --intent "..."`，自动套用历史风格并写入记忆。
   - 需要云端素材/TTS/特效时：生成一个 `JyProject` 脚本，放用户项目根目录，运行一次。
3. 运行后必须执行验收清单。

## 1) 云端视频 + BGM 草稿

- 阅读：`rules/setup.md`、`rules/media.md`、`rules/audio-voice.md`
- 示例：`examples/cloud_video_music_tts_demo.py`
- 验收：BGM 在 audio 轨；`project.save()` 成功。

## 2) 旁白 + 字幕对齐

- 阅读：`rules/text.md`、`rules/audio-voice.md`
- API：`add_tts_intelligent`、`add_narrated_subtitles`
- 验收：字幕时间与旁白对齐；字幕轨存在。

## 3) 精准片段编辑

- 阅读：`rules/precise-edit.md`
- 工具：`mile_cli.py edit / find / timeline`
- 验收：
  - 命中片段与预期一致（核对 `ref`）
  - 非目标片段未被修改
  - `qa` 无新增 error
  - 转场目标为交界处前一个片段

## 4) 自然语言多步需求

- 阅读：`rules/smart-edit.md`
- 工具：优先 `mile_cli.py smart`（见第 10 节，先预览后 `--apply`）；指令复杂或有歧义时回到 `plan` → `apply`
- 验收：
  - `plan` 命中列表与需求一致
  - `apply` 返回的 `changes` 覆盖全部需求
  - 失败时草稿未变（事务回滚）
  - 记忆已记录本次 `intent` 与操作序列

## 5) 记忆与风格复用

- 阅读：`rules/memory.md`
- 工具：`mile_cli.py memory --action recall/feedback/stats`
- 验收：
  - 召回结果与当前题材相关
  - 采用的历史风格有明确来源（`profiles[].id`）
  - 用户反馈已写入

## 6) 录屏 + 智能变焦

- 阅读：`rules/recording.md`
- 执行：`tools/recording/recorder.py`
- 验收：录制文件存在；生成的草稿可打开。

## 7) 特效 / 转场

- 阅读：`rules/effects.md`、`rules/keyframes.md`
- 工具：`scripts/asset_search.py`
- 验收：素材 ID 可解析；无 None 返回值。

## 8) 影视解说

- 阅读：`rules/generative.md`
- 提示词：`prompts/movie_commentary.md`
- 执行：`scripts/movie_commentary_builder.py`
- 验收：输入素材已优化（≤30 分钟、360p 优先）；分镜 JSON 成功应用。

## 9) 导出与 CI 验收

- 阅读：`rules/core.md`、`rules/cli.md`
- 执行：
  - `scripts/auto_exporter.py`
  - `scripts/api_validator.py --json`
- 验收：输出文件存在；返回码与 JSON 契约有效。

## 10) 一句话智能剪辑（smart）

- 阅读：`rules/smart-edit.md`（第 7 节）、`docs/improvement-plan.md` P0.4
- 工具：`mile_cli.py smart "<草稿>" "<中文指令>"`
- 返回结构：`data.intent`（`text` / `confidence` / `summary` / `tags` / `warnings` / `unsupported` / `clarify`）、`data.plan`（最终计划 `ops`）、`data.plan_result`（`operations[]` / `before` / `after` / `saved`）、`data.subtitles.preview` 与 `data.subtitles.applied`、`data.recommend`、`data.qa`、`data.memory_id`。
- 执行顺序（**必须先预览再落盘**）：
  1. `smart "<草稿>" "把BGM音量调到30%，第二条字幕改成今天上新" --pretty`（默认 dry-run，`plan_result.dry_run=true`，不写盘）
  2. 核对 `data.plan.ops` 与 `data.intent.summary` / `confidence`，确认命中片段与改法符合预期
  3. `data.intent.clarify` 非空 → 不执行，把追问原样转达用户，改用 `edit`/`plan` 精确指定
  4. `data.intent.warnings` / `unsupported` 非空 → 明确告知哪些说法未生效（蒙版、关键帧、调色、滤镜、特效、抠像等）
  5. 用户确认后 `smart ... --apply`，读取 `data.plan_result.saved`（落盘路径）、`data.qa.ok`
- 可选：`--polish --split-max 14` 同时整理字幕；`--recommend all` 附加推荐；`--audio x.mp3` 用于 BPM 分析或语音转字幕；`--no-backup` 关闭备份；`--no-remember` 不写记忆。
- 验收：
  - dry-run 时草稿文件 mtime 未变化
  - `--apply` 只产生一个备份、一次保存（字幕改写与计划应用共用同一次事务）
  - `data.qa.ok` 为 true；`data.plan_result.after` 的时长/片段数符合预期
  - 非目标片段（音量/文字/时长）未被连带修改

## 11) 字幕整理（subtitles）

- 阅读：`rules/smart-edit.md`（第 8 节）、`rules/text.md`
- 工具：`mile_cli.py subtitles "<草稿>" [--split-max N] [--glossary 文件] --apply`
- 返回结构：预览为 `checked` / `changed`（条目数）/ `items[]`（`original` → `corrected` + `changes` + `protected`，需要断句时带 `parts`）；`--apply` 后追加 `applied`（`changed[]` / `split[]` / `skipped[]`）与 `qa`。
- 验收：
  - 先看 `items[]`：改写是否只是错写纠正，数字/URL/邮箱是否出现在 `protected` 里
  - 断句产生的新片段数量看 `applied.split`；`applied.skipped` 表示共享字幕素材被跳过，需向用户解释
  - 术语表命中的专有名词不被改写（`protect` 优先于 `replace`）
  - `--apply` 后 `mile_cli.py srt "<草稿>" --out check.srt` 可正常导出

## 12) BGM 与转场推荐（recommend）

- 阅读：`rules/smart-edit.md`（第 9 节）、`rules/audio-voice.md`
- 工具：`mile_cli.py recommend "<草稿>" --bgm --audio music.mp3` / `--transitions`
- 诚实表述要求（**不得对用户夸大**）：
  - BGM 排序基于曲库元数据（标题/分类/时长）+ 音频信号（BPM/能量），返回 `method` 与 `reason`；不是「听懂了视频内容」。
  - 无 numpy/ffmpeg 时 `analyze_audio` 返回 `available:false` 与原因，只走元数据排序；此时不要声称做了节奏分析。
  - 转场建议是「镜头时长 + 空隙 + BPM」规则，`count: 0` 表示当前拼接不需要转场，不是失败。
  - 未安装 faster-whisper 时语音转字幕返回 `ASR_UNAVAILABLE`，直接说明需要先安装依赖或改用外部 SRT，禁止编造字幕。
- 验收：用户选择后，用 `edit --op add_audio` / `add_transition` 真正落地，再跑 `qa`。

## 通用验收清单（Mandatory）

- 草稿目录存在于剪映草稿根目录
- 保存成功（`ok=true` 或 `project.save()` 无异常）
- 至少一个 video 片段
- BGM 在 audio 轨（若使用）
- 有旁白时有对齐的字幕片段
- `mile_cli.py qa` 无 `error` 级问题
- 本次操作已写入记忆（或显式 `--no-remember`）

## 错误处理

- `MileError`：按 `code` 处理，见 `rules/precise-edit.md` 错误码表。
- `SegmentOverlap`：移动片段到轨道末尾或换轨。
- `MISSING_ARGS`：`smart` 缺少指令文本，补一句中文需求再执行。
- `ASR_UNAVAILABLE`：未安装 faster-whisper，不能本地转写；改用外部 SRT 或 `pip install faster-whisper`。
- `ASR_FAILED`：模型或音频解码失败；如实转达，不要伪造字幕。
- 音频分析失败：`NUMPY_MISSING` / `FFMPEG_MISSING` / `AUDIO_DECODE_FAILED` / `AUDIO_EMPTY` / `AUDIO_UNSUPPORTED` / `FILE_NOT_FOUND`，`recommend` 会把它们放进 `audio.available=false`；此时只有曲库元数据排序可用。
- 曲库缺失：`LIBRARY_NOT_FOUND`，检查 `data/cloud_music_library.csv` 或 `--library` 路径。
- 素材缺失：切换到已知本地素材或有效云端 ID。
- 路径失败：设置 `JY_SKILL_ROOT` 后重跑 bootstrap。
- 缺 ffprobe/ffmpeg：安装，或显式传入 `duration`。
- `intent.clarify` 非空：不是错误，代表需求有歧义；追问用户而不是猜着执行。

## 反模式（避免）

- 对全工作区反复递归扫描
- 未经 `timeline/find` 直接按序号改片段
- `smart` 不看 `clarify` / `warnings` 直接 `--apply`
- 把 `recommend` 的规则/信号结果说成「AI 看懂了视频内容」
- 多步修改不写计划、失败后留下半套编辑
- 修改用户真实草稿做测试（应使用临时草稿副本）
- 首次生成前反复搜索样式/特效
