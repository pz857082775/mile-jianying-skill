---
name: memory
description: 剪辑记忆：自动记录每次剪辑、按需求/结构/风格召回历史经验，并用于风格复用。
metadata:
  tags: memory, style, recall, feedback
---

# 剪辑记忆

目标：**同类视频第二次剪辑时，直接复用上一次成功的节奏、字幕与画面风格。**

## 1. 记录什么

每次 `apply` / `edit` / `assemble` 保存成功后自动写入一条会话记忆：

| 字段 | 内容 |
|------|------|
| `intent` | 本次需求描述（`--intent`） |
| `tags` | 主题/形式标签（`--tags`，逗号分隔） |
| `notes` | 备注（`--notes`） |
| `rating` | 自评分 1-5（`--rating`） |
| `outcome` | 结果状态，如 `draft_saved` / `applied` |
| `fingerprint` | 草稿内容指纹（去重合并用） |
| `structure` | 结构签名：轨道数、片段数、时长、字幕密度、素材类型分布 |
| `style` | 风格摘要：画布、帧率、平均镜头时长、字幕字号/颜色/描边、转场类型、BGM 量级 |
| `context.ops` | 本次实际执行的操作序列 |

去重规则：同一项目、同一草稿指纹在 30 分钟内重复保存会**合并**为一条，更新 `updated_at` 与上下文，而不是新增记录。

存储位置：默认用户配置目录下的 `mile-jianying-skill/memory.jsonl`，可用 `MILE_MEMORY_DIR` 覆盖；`MILE_MEMORY_AUTO=0` 可全局关闭自动记录。

## 2. 召回

```bash
# 按需求描述召回最相似的 3 条
python <SKILL_ROOT>/scripts/mile_cli.py memory --action recall \
  --intent "科技产品 30 秒竖屏，快节奏" --k 3 --pretty

# 同时拿一份参考草稿做结构/风格比对
python <SKILL_ROOT>/scripts/mile_cli.py memory --action recall \
  --draft "上一条同类视频" --intent "同样的节奏" --k 5 --pretty
```

相似度打分：

- 有文本需求时：`0.40×文本 + 0.25×结构 + 0.15×风格 + 0.20×时效`
- 仅给参考草稿时：`0.45×结构 + 0.25×风格 + 0.30×时效`
- 历史评分会带来 ±0.05 的修正，正向反馈优先。
- **负反馈降权（1.1.0）**：`outcome=rejected` 的记录总分 ×0.5；`outcome=accepted` 的记录总分 ×1.1。被拒绝过的风格会在召回中明显下沉。

返回的 `style` 是把 Top-K 记忆合并后的可直接使用风格档案；`advice` 给出可读建议。

## 3. 在组装中使用

```bash
# 默认自动召回历史风格
python <SKILL_ROOT>/scripts/mile_cli.py assemble a.mp4 b.mp4 \
  --project "新产品-竖屏" --captions captions.srt --bgm bgm.mp3 \
  --intent "科技产品 30 秒竖屏"

# 指定参考草稿
python <SKILL_ROOT>/scripts/mile_cli.py assemble ... --style-from "上一条同类视频"

# 显式覆盖风格（JSON 文件或内联 JSON）
python <SKILL_ROOT>/scripts/mile_cli.py assemble ... --style '{"font_size":9,"transition":"叠化"}'

# 完全不用记忆
python <SKILL_ROOT>/scripts/mile_cli.py assemble ... --no-memory-style
```

## 4. 反馈与遗忘

```bash
# 用户认可：打 5 分，后续同类任务权重更高
python <SKILL_ROOT>/scripts/mile_cli.py memory --action feedback --id <id> --rating 5 --notes "节奏合适"

# 用户不满意：低分或标注失败原因
python <SKILL_ROOT>/scripts/mile_cli.py memory --action feedback --id <id> --rating 1 --outcome rejected

# 删除失效/错误记忆
python <SKILL_ROOT>/scripts/mile_cli.py memory --action forget --id <id>

# 统计与列表
python <SKILL_ROOT>/scripts/mile_cli.py memory --action stats
python <SKILL_ROOT>/scripts/mile_cli.py memory --action list --limit 20 --tag 竖屏
```

## 5. 使用纪律

- **先召回再动手**：同类项目开始前先 `recall`，把 `style` 作为默认值，而不是每次从零猜。
- **intent 要具体**：写清题材、时长、比例、节奏与风格关键词，召回质量取决于它。
- **不记录敏感信息**：`--notes` 不要写用户隐私或密钥。
- **结果导向**：只有用户确认满意后才给高分；失败案例保留并标注 `outcome`，同样有参考价值。
- **单次关闭**：批量实验或测试时加 `--no-remember`，避免污染长期记忆。

## 6. 记忆迁移与治理（1.1.0）

```bash
# 全量导出 / 按项目导出 / 按时间导出（换机迁移或备份）
python <SKILL_ROOT>/scripts/mile_cli.py memory --action export --out memories.json
python <SKILL_ROOT>/scripts/mile_cli.py memory --action export --out tech.json --project 科技
python <SKILL_ROOT>/scripts/mile_cli.py memory --action export --out this-year.json --since 2026-01-01

# 导入（默认 merge 按 id 去重；--overwrite 更新已存在的；--mode replace 清空后导入并自动备份原库）
python <SKILL_ROOT>/scripts/mile_cli.py memory --action import --source memories.json
python <SKILL_ROOT>/scripts/mile_cli.py memory --action import --source memories.json --mode replace
```

规则：

- 导入文件必须先通过格式校验（`kind: mile-memory`，100MB 上限），损坏文件报 `IMPORT_BAD_FORMAT` 且**不污染现有记忆库**。
- `replace` 模式导入前自动把原库备份为 `sessions.backup-<时间>.jsonl`。
- 导出内容包含记忆原文（intent/notes 等），注意不要在公开链路里传播敏感项目。

## 7. 系列风格档案（1.1.0）

把「记忆里的常用风格」固化成**可命名的档案**，一个账号/栏目一套，供 `assemble` 直接套用，比自动召回更稳定可控：

```bash
# 从参考草稿提取风格，建立「科技评论」档案
python <SKILL_ROOT>/scripts/mile_cli.py profile --action create --name 科技评论 --draft "上周那期"

# 列表 / 查看 / 更新 / 删除
python <SKILL_ROOT>/scripts/mile_cli.py profile --action list
python <SKILL_ROOT>/scripts/mile_cli.py profile --action show --name 科技评论
python <SKILL_ROOT>/scripts/mile_cli.py profile --action update --name 科技评论 --tags "科技,栏目" --notes "品牌标准"
python <SKILL_ROOT>/scripts/mile_cli.py profile --action delete --name 科技评论

# 导出 / 导入（团队共享、换机迁移）
python <SKILL_ROOT>/scripts/mile_cli.py profile --action export --name 科技评论 --out profile.json
python <SKILL_ROOT>/scripts/mile_cli.py profile --action import --source profile.json
```

规则：

- **档案优先级**：`assemble --style <档案JSON/内联>` > `profile 档案中的风格` > 记忆自动召回 > 默认。显式指定的档案不会被自动召回覆盖。
- 同名创建默认拒绝（`PROFILE_EXISTS`）；`--force` 覆盖时版本号自增，旧版自动备份到 `profiles/versions/`。
- 档案风格可手动更新（深合并：只改你想改的字段，其他保留），也可从新草稿重新提取。
- 档案文件是本地明文 JSON（`MILE_MEMORY_DIR/profiles/`），导出时注意敏感样式信息与字体版权标注。
