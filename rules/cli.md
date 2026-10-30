---
name: cli-tools
description: 命令行工具：统一剪辑 CLI、草稿检查、素材搜索、导出与环境诊断。
metadata:
  tags: cli, diagnostics, drafts, export
---

# CLI 工具

## 1) 统一剪辑 CLI（首选）

`scripts/mile_cli.py` 是日常剪辑的默认入口，所有子命令共享统一 JSON 信封与退出码。

```bash
python <SKILL_ROOT>/scripts/mile_cli.py doctor
python <SKILL_ROOT>/scripts/mile_cli.py drafts --detail
python <SKILL_ROOT>/scripts/mile_cli.py timeline "DraftName" --pretty
python <SKILL_ROOT>/scripts/mile_cli.py find "DraftName" --text "欢迎" --role caption
python <SKILL_ROOT>/scripts/mile_cli.py plan  "DraftName" --plan plan.json
python <SKILL_ROOT>/scripts/mile_cli.py apply "DraftName" --plan plan.json
python <SKILL_ROOT>/scripts/mile_cli.py edit  "DraftName" --op trim --target '{"ref":"SEGV1"}' --param end=3s --param ripple=true
python <SKILL_ROOT>/scripts/mile_cli.py qa    "DraftName" --fix
python <SKILL_ROOT>/scripts/mile_cli.py undo  "DraftName"
python <SKILL_ROOT>/scripts/mile_cli.py backups "DraftName"
python <SKILL_ROOT>/scripts/mile_cli.py srt   "DraftName" --out captions.srt
python <SKILL_ROOT>/scripts/mile_cli.py smart "DraftName" "把BGM音量调到30%，开头删掉1秒"
python <SKILL_ROOT>/scripts/mile_cli.py smart "DraftName" "把BGM音量调到30%" --apply
python <SKILL_ROOT>/scripts/mile_cli.py subtitles "DraftName" --split-max 8
python <SKILL_ROOT>/scripts/mile_cli.py subtitles "DraftName" --split-max 8 --apply
python <SKILL_ROOT>/scripts/mile_cli.py recommend "DraftName" --tags "轻快,日常"
python <SKILL_ROOT>/scripts/mile_cli.py recommend "DraftName" --transitions --audio bgm.mp3
python <SKILL_ROOT>/scripts/mile_cli.py export "DraftName" --out out.mp4
python <SKILL_ROOT>/scripts/mile_cli.py assemble a.mp4 b.mp4 --project "新片"
python <SKILL_ROOT>/scripts/mile_cli.py memory --action recall --intent "竖屏 快节奏"
```

契约：

- 成功：`{"ok": true, "code": "OK", "reason": "成功", "data": {...}}`，退出码 0。
- 失败：`{"ok": false, "code": "<稳定错误码>", "reason": "<原因>", "details": {...}}`，退出码 2。
- 时间默认秒，支持 `500ms` / `1m2s` / `00:12`。
- 记忆类命令默认写入；`--no-remember` 关闭单次记录。
- 通用参数：`--root` 覆盖草稿根目录、`--pretty` 美化输出、`--debug` 输出 traceback。

子命令速查：

| 子命令 | 用途 |
|--------|------|
| `version` / `doctor` | 版本与环境自检 |
| `drafts` | 列出草稿（`--detail` 附时长/画布/轨道摘要） |
| `timeline` | 时间线模型（轨道、片段、ref、角色、已挂载的转场） |
| `find` | 按 ref/轨道/时间/文字/角色/素材类型查找片段 |
| `plan` | 校验结构化计划，不保存 |
| `apply` | 事务化应用计划 |
| `edit` | 单片段精准编辑；支持滤镜、视频/人物特效、蒙版、入/出/组合动画与挂载素材移除 |
| `qa` | 质量检查与安全修复 |
| `undo` / `backups` | 撤销与备份列表 |
| `srt` | 导出字幕 SRT |
| `smart` | 中文指令 → 意图/计划/字幕整理/推荐/质检（默认预览，`--apply` 落盘） |
| `subtitles` | 字幕纠错、术语保护、断句与时长重分配（默认预览） |
| `recommend` | BGM 候选（曲库元数据排序）与转场建议（镜头时长/空隙/BPM 规则） |
| `analyze` | 只读证据分析：媒体信号（黑帧/静帧/音频）、时间线观察与能力矩阵；`--media` + `--scenes` 附带镜头边界 |
| `decide` | 只读剪辑决策：证据 → 可解释建议（风格/节奏/转场/字幕保护）+ 候选 `plan_ops`，可 `--plan-out` 导出计划 |
| `export` | 调剪映 GUI 导出视频 |
| `assemble` | 按素材与历史风格组装草稿 |
| `memory` | 记忆列表/召回/详情/遗忘/反馈/统计 |

## 2) 草稿检查（只读）

`draft_inspector.py` 适合快速查看原始草稿：

```bash
python <SKILL_ROOT>/scripts/draft_inspector.py list --limit 20
python <SKILL_ROOT>/scripts/draft_inspector.py summary --name "DraftName"
python <SKILL_ROOT>/scripts/draft_inspector.py show --name "DraftName" --kind content --json
```

- `--root` 覆盖草稿根目录，`--path` 按绝对路径查看。
- 结构化编辑请优先用 `mile_cli.py timeline`，它返回语义化片段模型。

## 3) 环境诊断

```bash
python <SKILL_ROOT>/scripts/api_validator.py --json
```

## 4) 素材搜索

```bash
python <SKILL_ROOT>/scripts/asset_search.py "复古" -c filters
python <SKILL_ROOT>/scripts/asset_search.py "雾化" -c transitions
```

## 5) 导出

```bash
python <SKILL_ROOT>/scripts/auto_exporter.py "DraftName" "output.mp4" --res 1080 --fps 60
python <SKILL_ROOT>/scripts/mile_cli.py export "DraftName" --out output.mp4
```
