# 米乐-自动剪映视频 Skill v1.1 策划开发需求文档（PRD）

> 文档性质：功能完善策划与开发需求说明
> 对应产品：mile-jianying-skill（米乐-自动剪映视频）
> 基线版本：v1.0.0（2026-10-29，257 项测试）
> 目标版本：v1.1.0
> 编写：米乐 AI 工作搭档（基于深度审阅 + 实测基线）
> 日期：2026-10-30
> 阅读对象：米乐本人、参与本 Skill 迭代的开发者、使用本 Skill 的 Agent

---

## 1. 文档信息

| 项 | 内容 |
|---|---|
| 文档标题 | mile-jianying-skill v1.1 功能完善策划开发需求文档 |
| 版本记录 | v1.1 PRD 初稿（2026-10-30）；后续按修订记录表补充 |
| 作者 | 米乐 |
| 阅读对象 | Skill 作者、维护者、上层 Agent |

### 版本记录

| 版本 | 日期 | 修订人 | 说明 |
|---|---|---|---|
| 0.1 | 2026-10-30 | 米乐 | PRD 初稿：深度分析基线 + 完善项清单 + 本轮开发范围 |

---

## 2. 产品概述

### 2.1 项目背景

`mile-jianying-skill` v1.0.0 已经实现了「确定性编辑层 + 智能层第一版 + 记忆骨架」：

- 一段自然语言可以变成可校验的剪辑计划（`smart`，规则/槽位解析，默认只预览）；
- 任意片段可以用 ref/轨道/时间/文字/角色精确定位并修改（`edit` / `plan`）；
- 每次剪辑留下结构化记忆，同类风格可召回复用（`memory`）；
- 质量检查、事务回滚、自动备份、真实剪映 5.9 端到端验证均已闭环（实测评分 4.1/5，257 项测试通过）。

v1.0.0 的主干已经稳了。**本轮 v1.1 不是重新搭楼，而是把用户感知最强、实现成本可控的几块拼图补齐**，让「先看后改」「越剪越懂你」「一条素材出 N 条变体」这三件事真正可用。

### 2.2 产品定位

一句话：**自然语言驱动的剪映自动剪辑工具，主打「可控的自动化」——把重复劳动交给代码，把判断权留给人。**

v1.1 的定位是 v1.0 的「体验补全版」：

- 从「能改」到「敢改」：所有修改先给差异预览，确认后才落盘；
- 从「有记忆」到「会用记忆」：风格档案成为一等公民，可命名、可继承、可迁移；
- 从「一条一条做」到「一次做一批」：批量变体覆盖矩阵号/多平台场景。

### 2.3 产品目标

| 目标 | 说明 | 可度量指标 |
|---|---|---|
| G1 先看后改 | 任何计划执行前可输出「修改前后」的结构化差异 | `apply --preview` 覆盖 plan/edit/smart 三入口；差异项与实际执行结果 100% 一致 |
| G2 风格档案化 | 记忆升级为可命名、可继承、可迁移的系列风格档案 | 支持 profile 的增删查改/导出导入；`assemble --profile` 一键套用 |
| G3 记忆可回灌可治理 | 记忆支持导出/导入、负反馈降权、按项目清理 | export/import 往返无损；rejected 记忆在召回中降权 |
| G4 批量变体 | 一条底稿参数池生成 N 份差异草稿 | `variants` 一次产出 ≥3 份且命名规范、结构可差分化 |
| G5 零门槛继续成立 | 以上功能保持零第三方必装依赖 | 新增模块仅用 Python 标准库，测试可在无 numpy/requests/ffmpeg 环境全绿 |

### 2.4 名词解释

| 术语 | 说明 |
|---|---|
| 草稿（draft） | 剪映工程文件（draft_content.json + draft_meta_info.json） |
| 时间线模型（timeline） | 对草稿的结构化只读投影：轨道、片段、ref、角色 |
| ref | 片段稳定引用（id 前缀），编辑不依赖易变下标 |
| 计划（plan） | 可校验的操作数组，`{intent, tags, ops[]}`，事务化执行 |
| 记忆（memory） | 剪辑会话的结构化记录：需求、操作、风格、结果、反馈 |
| 风格档案（profile） | 命名的、可复用的风格集合（字幕样式/转场/BGM/节奏），可多版本 |
| 变体（variant） | 同一底稿按参数池产出的一份差异化草稿 |
| 差异预览（preview） | 计划执行前后时间线的结构化对比，不落盘 |

---

## 3. 用户与场景

### 3.1 目标用户画像

| 画像 | 特征 | 一句话诉求 |
|---|---|---|
| 口播自媒体 | 周更 3-5 条，嫌粗剪烦 | "我改一句字幕，别把整条视频搞乱" |
| 短视频小白 | 不懂时间轴 | "我说人话它就能剪，改之前让我看一眼" |
| 本地生活/矩阵号 | 一条素材发 N 个账号 | "一个底稿给我出三五个不一样的开头" |
| 企业内容团队 | 品牌风格统一 | "我们栏目的字幕样式和转场是固定的，别再每次手动调" |
| Skill 维护者（米乐） | 迭代作者 | "加功能不破坏旧的，测试全绿" |

### 3.2 角色划分

| 角色 | 说明 | 需要的功能 |
|---|---|---|
| 终端用户 | 用自然语言下达剪辑指令 | smart / preview / variants |
| 上层 Agent | 调用 CLI 自动执行剪辑流水线 | plan --preview / variants / profile（机器可读输出） |
| 维护开发者 | 改代码、升级兼容 | 单测、稳定错误码、文档 |

### 3.3 核心使用场景

**场景 A：改之前先看到会变成什么样（G1）**

用户：`mile smart "我的视频" "把开头3秒删掉，BGM音量降到20%" --preview`
期望：输出「将删除片段 SEGV1（0-3s，ripple 后总时长 42s→39s）；BGM 音量 35%→20%」，**不落盘**；确认后再 `--apply`。

**场景 B：建立一个「科技评论」风格档案（G2）**

用户：`mile memory profile create 科技评论 --from "上周那期"` 或 `mile profile save 科技评论 --draft "科技评测EP3"`
期望：从参考草稿提取字幕字号/颜色/位置、转场、BGM 音量、镜头节奏，存入命名档案；以后 `assemble ... --profile 科技评论` 一键套用。

**场景 C：把记忆搬到另一台电脑（G3）**

用户：`mile memory export --out memories.json` → 换机 → `mile memory import --in memories.json`
期望：记忆完整迁移，样式/反馈不丢失。

**场景 D：一条底稿出 5 条变体（G4）**

用户：`mile variants "产品介绍" --count 5 --openings opening1.mp4 opening2.mp4 --transitions 叠化,闪白 --projects 变体A,变体B...`
期望：生成 5 份独立草稿（_v1~_v5），各自记录记忆，结构差异可审计。

---

## 4. 功能需求

> 功能编号规则：V1.1-F1 ~ F5。优先级 P0（本轮必做）/ P1（本轮建议做）/ P2（远期）。

### F1 计划差异预览（preview）

- **功能编号**：F1
- **名称**：修改前差异预览 `mile plan --preview` / `mile apply --preview`
- **优先级**：P0

**功能描述**：对任意 `plan`（JSON 或来自 `smart`），先在实际草稿的**内存副本**上完整执行一遍，产出「修改前 vs 修改后」的结构化对比，全程不写磁盘、不动原草稿。这是「先看后改」原则的具体落地。

**用户故事**：
- 作为口播自媒体，我想在删掉一段素材前看到「会连带挪动哪些片段、总时长变多少」，避免误删后才发现。
- 作为上层 Agent，我想在流水线里先取 preview 给用户确认，确认后才 apply。

**前置条件**：
- 草稿存在且可解析（同 `plan` 的既有约束）。
- `--preview` 与 `--apply` 互斥；同时给出时报错。

**主流程**：
1. 读取草稿，构建时间线 before；
2. 深拷贝草稿数据到内存副本；
3. 在副本上执行计划（复用 plan 的校验与逐 op 执行逻辑），任何一步失败则回滚副本并返回错误（与 apply 相同语义）；
4. 构建时间线 after；
5. 对比输出：
   - 元差异：总时长、片段数、轨道数、画布；
   - 片段差异：每个受影响片段（ref、轨道、角色、时间、文字）的 before/after；
   - 操作明细：每步 op、目标、参数、命中片段；
   - 风险提示：如「裁剪后该片段仅剩 0.2s」「删除后主轨出现空隙」（复用 qa 的规则做轻量提示）。
6. 返回 JSON 信封，退出码 0。**不调用 draft.save()**。

**分支与异常流程**：
- 计划校验失败：返回编译错误（与 `plan` 一致），不执行任何一步。
- 执行中途失败：副本回滚，输出失败步骤与原因（与 `apply` 一致），**磁盘无任何痕迹**。
- 草稿不存在/不可读：`DRAFT_NOT_FOUND` / 解析错误，稳定错误码。

**界面要求/交互说明**：
- CLI：`mile plan "<草稿>" --plan plan.json --preview` 与 `mile apply "<草稿>" --plan plan.json --preview` 行为一致（apply 带 --preview 时只预览，不落盘）；
- `smart` 增加 `--preview` 别名开关（dry-run 已有，preview 输出更详细的时间线差异）；
- 输出 JSON 结构：`{"before": {...}, "after": {...}, "diff": {meta, segments, ops}, "risks": [...]}`。

**业务规则**：
- preview 输出中的 diff 必须与真实 apply 后的结果**字段级一致**（同一套执行路径，只是不保存）。
- preview 不写入记忆（没有产生实际修改）。

**权限要求**：无新增（本地只读 + 内存副本）。

**字段与数据说明**：
- 时间全部为秒（符合既有「对外秒」约定）。
- segment diff 用 ref 对齐，不依赖下标。

**验收标准**：
1. `apply --preview` 后草稿的 mtime/指纹不变；
2. preview 的 after 与真实 apply 后 build_timeline 输出完全一致（单测断言）；
3. 预览输出包含 meta/segments/ops/risks 四块内容；
4. 与 `apply --dry-run` 并存且语义不冲突。

---

### F2 系列风格档案（profile）

- **功能编号**：F2
- **名称**：命名风格档案 `memory --action profile`（子命令 `profile`）
- **优先级**：P0

**功能描述**：把「记忆里的风格」升级为可命名的档案。一个账号/栏目一套风格档案（字幕字号/颜色/位置、转场类型与时长、BGM 音量、镜头节奏、画布比例），支持**创建、列表、查看、更新、删除、导出、导入、继承**。`assemble` 支持 `--profile <name>` 直接套用，替代或覆盖记忆自动召回。

**用户故事**：
- 作为企业内容团队，我们栏目的字幕样式和转场固定，建一个 `品牌标准` 档案，所有新视频直接套用；
- 作为矩阵号运营，`探店A` 和 `探店B` 是两套风格，分别建档案，互不污染；
- 我想把 `科技评论` 档案分享给同事，导出成文件发过去。

**前置条件**：
- 参考草稿可解析（创建时）；
- 档案目录可写（默认记忆目录下 `profiles/`，可用环境变量覆盖）。

**主流程（创建）**：
1. 输入档案名 + 参考草稿（或显式 JSON 风格）；
2. 调用既有 `extract_style` + `structure_signature` 提取风格；
3. 写 `profiles/<name>.json`，含 `name/version/styles/structure/tags/notes/created_at/updated_at/source_draft`；
4. 返回档案摘要。

**主流程（套用）**：
1. `assemble ... --profile 科技评论`；
2. 读取档案 styles，作为 style 参数传入 assemble（优先级：显式 `--style` > `--profile` > 记忆自动召回 > 默认）；
3. 若档案不存在：`PROFILE_NOT_FOUND` + 现有档案名提示。

**分支与异常流程**：
- 同名档案已存在：默认拒绝覆盖，`--force` 更新，并 bump `version`；保留历史版本（可选 `versions/` 目录）；
- 参考草稿缺失：允许手动传 styles JSON；
- 档案目录不可写：`PROFILE_DIR_NOT_WRITABLE`。

**业务规则**：
- 档案是可解释的：`profile show` 输出每个风格的「来源依据」（来自哪些记忆/草稿）；
- 强制覆盖时版本号自增，覆盖前导出一个备份副本；
- 与既有 `memory` 召回不冲突：`--profile` 显式指定时跳过自动召回（除非 `--merge-memory-style`）。

**验收标准**：
1. 创建 → 列表 → 查看 → 套用（assemble dry-run）全链路可用；
2. 同名创建不加 `--force` 时报 `PROFILE_EXISTS`；
3. 导出文件 → 改名目录 → 导入，风格字段无损；
4. PowerShell/GBK 控制台下中文档案名/路径不崩（沿用 UTF-8 重配置）。

---

### F3 记忆治理：导出 / 导入 / 负反馈 / 定时清理

- **功能编号**：F3
- **名称**：记忆导出导入与负反馈学习 `memory --action export|import`，召回降权
- **优先级**：P0（export/import 与负反馈为本轮必做；自动清理策略为 P1）

**功能描述**：记忆目前是「本地 JSONL 明文」，缺少三个能力：
1. **可迁移**：`export` 把全部/部分记忆导出为 JSON 文件，`import` 合并回收，支持换机与备份；
2. **负反馈学习**：用户拒绝了某次修改（outcome=rejected 或低评分）后，召回时降低同类风格权重；
3. **治理**：按项目/时间范围统计与清理（可选，P1）。

**用户故事**：
- 换新电脑，把旧电脑的记忆导出带过来，风格不丢；
- 上次「蓝色大标题」用户不喜欢并给了 1 星，下次召回时排序自动下沉；
- 半年后的记忆统计里发现 3 个废弃项目，清理掉。

**主流程（export）**：
1. `memory export --out memories.json [--project X] [--since 2026-01-01]`；
2. 导出 `{version, exported_at, count, records[]}`。
3. 文件写原子（先写临时文件再重命名）。

**主流程（import）**：
1. `memory import --in memories.json [--mode merge|replace]`；
2. merge：按 id 去重，已有的跳过（或 `--overwrite` 更新），新记录追加；
3. replace：清空后导入；
4. 汇报「新增 N、跳过 M、覆盖 K」。

**业务规则（负反馈）**：
- `update_feedback` 记录 `outcome in {accepted, rejected, modified}`；
- `recommend_style` 召回打分时：rejected 记录 × 0.5 降权；accept 记录 × 1.1 加权；低评分（<3）按既有规则微降；
- 被 `forget` 的记忆不再参与召回。

**异常**：
- 导入文件格式非法/版本不兼容：`IMPORT_BAD_FORMAT`，保证原子性（先校验完再写）；
- 导出路径不可写：`EXPORT_FAILED`。

**验收标准**：
1. export → import（merge）→ stats 计数正确，往返无损耗；
2. rejected 记录在 recall 排序中名次明显下沉（单测断言相对顺序）；
3. 损坏 JSON 导入被拒且原记忆不受影响。

---

### F4 批量变体（variants）

- **功能编号**：F4
- **名称**：一条底稿批量生成差异草稿 `variants`
- **优先级**：P0（MVP：基于 assemble 循环 + 参数池）

**功能描述**：给定一组素材（底稿参数）+ 参数池（开头素材、BGM、转场、字幕位置、画布比例、标题文案前缀），批量生成 N 份独立草稿，命名 `{项目}_v1 ... {项目}_vN`。每份草稿写入独立记忆，并共享/引用同一系列档案。这是矩阵号、多平台、A/B 测试的产能工具。

**用户故事**：
- 本地生活服务商把一条门店素材出 10 个不同开头和文案的版本分发给矩阵账号；
- 一条产品视频分别出 16:9（B站）和 9:16（抖音）两个版本。

**前置条件**：
- 素材文件存在可探测（无 ffprobe 时用 `--clip-duration` 兜底，与 assemble 一致）；
- 参数池中每一项合法（转场名在素材库、BGM 文件存在等）。

**主流程**：
1. 解析变体参数池（见接口）；
2. 计算组合方案（默认：开头素材轮换 × 转场轮换 × 比例轮换，总数为 count 上限）——默认策略为「顺序轮换」，不做随机；
3. 循环调用 assemble 生成 `{项目}_v{i}`；
4. 每份草稿 `qa` 自检，失败变体单独报错并继续（不整体失败）；
5. 每份写入记忆（context 带 `variant_of=<项目>`、`variant_index=i`、参数快照）；
6. 输出变体清单（名称、参数、qa 结果、草稿路径）。

**分支与异常**：
- 素材缺一个：跳过该组合并记 warning，其他继续；
- 全部失败：`VARIANTS_FAILED` 带每份原因；
- 命名冲突（项目_v1 已存在）：`--force` 覆盖，否则 `_v{i}` 自动 +1 找空位。

**业务规则**：
- 变体之间**结构必须有差异**（至少一个参数不同），输出带差异摘要；
- 变体名不允许与既有草稿撞名（除非 --force）；
- 每个变体独立 `qa`，结果计入输出。

**验收标准**：
1. `--count 3` 产出 3 份草稿，命名 `_v1.._v3`，qa 全过；
2. 变体之间的转场/开头/比例至少一项不同（单测断言差异）；
3. 每份变体记忆含 `variant_of` 标记，可按项目筛选；
4. 失败变体不影响成功变体。

---

### F5 零依赖与既有能力守护

- **功能编号**：F5
- **名称**：零第三方依赖回归与错误码守护（横切）
- **优先级**：P0

**功能描述**：本轮所有新增模块只用 Python 标准库；现有 257 项测试不回归；CLI 信封与错误码风格统一。新增稳定错误码：

| 错误码 | 场景 |
|---|---|
| `PROFILE_NOT_FOUND` / `PROFILE_EXISTS` / `PROFILE_DIR_NOT_WRITABLE` | 档案相关 |
| `IMPORT_BAD_FORMAT` / `IMPORT_FAILED` / `EXPORT_FAILED` | 记忆迁移 |
| `VARIANTS_FAILED` | 变体全部失败 |
| `PREVIEW_CONFLICT` | --preview 与 --apply 同时指定 |

**验收标准**：`python -m unittest discover -s tests` 在无 numpy/requests 环境：既有 232 项通过（4 项因缺第三方依赖跳过/报错属基线已知），新增测试全绿。

---

## 5. 非功能需求

| 类别 | 要求 |
|---|---|
| 性能 | 预览/档案/导入导出均为秒级；500 片段草稿 preview < 2s（与 apply 同量级，因复用同一执行路径）；变体 10 份 < 5 分钟（受素材探测与磁盘写影响） |
| 安全与合规 | 预览/导出/导入不触碰用户草稿目录之外；导入先校验再写，避免损坏记忆库；记忆文件保持本地明文（沿用现状），文档明示导出内容含原文 |
| 兼容性 | Python 3.9-3.14；Windows GBK 控制台继续 UTF-8 重配置；剪映 5.9 草稿 schema 不动；上游 pyJianYingDraft 不改 vendor 行为 |
| 可用性与可维护性 | 新模块放在 `scripts/mile/`（preview.py / variants.py / profile 进 memory.py）；统一 json 信封；错误码文档化；每功能一条 rules 或 docs 条目 + 示例 |
| 可回滚 | 记忆导入支持 replace 前自动备份原记忆文件；档案覆盖前备份 |

---

## 6. 界面与交互

本项目为 CLI 工具，无 GUI。交互设计遵循既有约定：

| 命令 | 用法 | 交互要点 |
|---|---|---|
| `mile plan --preview` / `apply --preview` | 预览差异 | 输出 before/after/diff/risks，用户确认后再不带 preview 执行 |
| `mile profile create|list|show|update|delete|export|import` | 档案管理 | 每个子命令独立 JSON 输出 |
| `mile memory export` / `import` | 记忆迁移 | --out/--in 必填；import 默认 merge |
| `mile variants` | 批量变体 | --count、参数池、--projects 显式命名 |

预览输出示例（措辞说明，非最终契约）：

```json
{
  "ok": true,
  "data": {
    "draft": "我的视频",
    "before": {"duration": 42.0, "segments": 18, "tracks": 4},
    "after":  {"duration": 39.0, "segments": 18, "tracks": 4},
    "diff": {
      "meta": {"duration_delta": -3.0, "segments_delta": 0},
      "segments": [
        {"ref": "SEGV1", "status": "trimmed", "before": {"start": 0, "end": 3}, "after": {"start": 2, "end": 3}, "ripple": true}
      ]
    },
    "ops": [
      {"op": "trim", "targets": ["SEGV1"], "params": {"start": "2s"}}
    ],
    "risks": ["主轨开头将出现 0.5s 空隙"]
  }
}
```

---

## 7. 业务流程

### 7.1 预览-确认-执行流

```text
用户/Agent
   │  自然语言或计划 JSON
   ▼
smart (dry-run) ──► plan --preview / apply --preview
   │                     │  内存副本执行 + diff + risks
   │                     ▼
   │           用户确认? ──否──► 结束（草稿零改动）
   │                     │是
   ▼                     ▼
apply（事务化落盘） ──► qa ──► memory
```

### 7.2 档案生命周期

```text
创建: 参考草稿/风格JSON ──► extract_style ──► profiles/<name>.json (v1)
套用: assemble --profile <name> ──► 风格注入（优先级高于自动召回）
更新: profile update --force ──► version+1，旧版备份
迁移: export ──► 文件 ──► import（merge/replace）
```

### 7.3 变体流水线

```text
底稿参数 + 参数池(开头/BGM/转场/比例)
   │ 组合计算（顺序轮换，去重）
   ▼
assemble × N（每份独立草稿 _v1.._vN）
   │ 每份 qa 自检（失败不影响其他）
   ▼
每份写记忆（variant_of 标记）→ 输出变体清单
```

---

## 8. 异常与边界

| 异常 | 处理 |
|---|---|
| preview 遇到失败计划 | 与 apply 相同报错语义，副本回滚，磁盘无痕 |
| 同名档案/草稿冲突 | 报 `PROFILE_EXISTS`/命名冲突，需 `--force` |
| 导入损坏 JSON | `IMPORT_BAD_FORMAT`，原记忆不动 |
| 变体某个失败 | 单独记错继续，最后汇总 |
| 无 ffprobe 时长未知 | 沿用 assemble 的 `--clip-duration` 兜底，不伪造时长 |
| 中文路径/控制台 GBK | 沿用 UTF-8 重配置，新增用例覆盖 |
| 超大记忆文件导入 | 设置文件大小上限（默认 100MB）并报错 |

---

## 9. 数据与埋点（数据项层面）

| 数据项 | 说明 | 归属 |
|---|---|---|
| 档案记录 | name/version/styles/structure/tags/notes/时间戳/来源草稿 | F2 |
| 记忆记录扩展 | 增加 `variant_of` / `variant_index`（F4）、`rejected` 反馈（F3） | F3/F4 |
| 导出的记忆包 | `{version, exported_at, count, records[]}` | F3 |
| 变体清单 | 名称/参数快照/qa 结果/路径 | F4 |

统计口径：
- `memory stats` 扩展 `profiles` 数量、`by_outcome` 分布（rejected/accepted）；
- `profile list` 显示每个档案的版本号与更新时间。

（不涉及数据库表结构。）

---

## 10. 运营与后台

- CLI 本身即「后台」：档案管理、记忆治理均由 CLI 子命令提供；
- 配置项：`MILE_MEMORY_DIR`（含 profiles 目录）、`MILE_PROFILE_DIR`（可选单独指定）；
- 风险控制：import `--mode replace` 前必须自动备份；profile --force 更新前自动备份；
- 审核：档案与记忆均为本地文件，无云端同步（远期可做 profiles 分享市场，P3）。

---

## 11. 版本规划与里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1（本轮 v1.1.0） | F1 preview + F2 profile + F3 export/import/负反馈 + F4 variants MVP + F5 回归 | 新增测试全绿；257 基线不回退 |
| M2（v1.2，远期） | 意图语料基准 50 条、LLM 后端可选适配、歧义候选追问 | 一次成功率 ≥80% |
| M3（v1.3，远期） | 记忆语义召回（vector，可选依赖降级）、媒体级质检、MCP 包装 | 召回 Top-3 有用率 ≥70% |
| M4（v2.0，远期） | ASR 词级对齐、高光/长转短、比例重构、卡点/ducking | 长转短命中率 ≥70% |

---

## 12. 风险与依赖、附录

| 风险 | 影响 | 应对 |
|---|---|---|
| 剪映升级 schema 变化 | 中高 | 沿用未知字段保留 + 版本探测；本轮不新增 schema 写入路径 |
| 记忆库体积增长 | 低 | export 分项目导出、P1 增加按时间清理 |
| 变体同质化 | 中 | 默认顺序轮换参数池，后续加随机种子与去重检查（M3） |
| 预览与 apply 行为不一致 | 高 | 复用同一执行路径（同一函数），单测断言 after 一致性 |
| 导入损坏数据污染记忆 | 中 | 先校验再写入、原子替换、导入前自动备份 |

**附录 A：开发约束清单**
- 只改动/新增 `scripts/mile/`、`scripts/mile_cli.py`、`tests/`、`docs/`、`rules/`、`SKILL.md`、`README.md`、`CHANGELOG.md`；
- 不修改 `scripts/vendor/`（第三方库保持原样）；
- 新增模块零第三方依赖；错误码风格与既有 `MileError` 一致；
- 全部新增功能配单测（unittest 风格）与文档条目。

**附录 B：与 v1.0.0 improvement-plan 的对应关系**
- F1 ← improvement-plan P1-2（differences preview）
- F2 ← P1-3（风格档案/profile）
- F3 ← P1-3（记忆治理/负反馈）部分
- F4 ← P1-5（批量变体 MVP）
- F5 ← P1 验收标准（测试/零依赖守护）

> 本文档为策划与开发依据；开发顺序建议 F5（先立测试基线）→ F1 → F3 → F2 → F4。