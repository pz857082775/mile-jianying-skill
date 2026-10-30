# Minimal Command SOP

用最少命令完成一次剪辑。先判断是"改已有草稿"还是"从素材新建"。

## A. 修改已有草稿（最短路径）

```powershell
# 1. 看时间线（同时确认草稿根目录可用）
python .agent\skills\mile-jianying-skill\scripts\mile_cli.py timeline "DraftName" --pretty

# 2. 找到目标片段
python .agent\skills\mile-jianying-skill\scripts\mile_cli.py find "DraftName" --text "关键词"

# 3. 精准修改（示例：裁掉前 2 秒并前移）
python .agent\skills\mile-jianying-skill\scripts\mile_cli.py edit "DraftName" `
  --op trim --target '{"ref":"SEGV1"}' --param start=2s --param ripple=true

# 4. 体检
python .agent\skills\mile-jianying-skill\scripts\mile_cli.py qa "DraftName" --fix
```

多步需求：把操作写进 `plan.json`，用 `plan` 校验、`apply` 应用（见 `rules/smart-edit.md`）。

## B. 从素材新建

### Step 1: 最小环境检查（最多 2 条）

```powershell
python --version
Test-Path "$env:LOCALAPPDATA\JianyingPro\User Data\Projects\com.lveditor.draft"
```

路径不同则设置 `JY_PROJECTS_ROOT` 后继续。

### Step 2: 素材检查（1 条）

```powershell
Get-ChildItem .agent\skills\mile-jianying-skill\assets -File
```

除非素材查找失败，不要递归扫描整个工作区。

### Step 3: 生成

优先用组装命令（自动套用历史风格）：

```powershell
python .agent\skills\mile-jianying-skill\scripts\mile_cli.py assemble a.mp4 b.mp4 `
  --project "新片" --captions captions.srt --bgm bgm.mp3 --intent "主题 时长 比例"
```

> 没有 ffprobe 时加 `--clip-duration 3s`，否则无法探测视频时长会返回 `DURATION_UNKNOWN`。

需要云端素材/TTS/特效时，生成**一个** `JyProject` 脚本放在工作区根目录，只用确定性 API：

- `JyProject(...)`
- `add_media_safe(...)`
- `add_cloud_media(...)` / `add_cloud_music(...)`
- `add_text_simple(...)` / `add_narrated_subtitles(...)`
- `save()`

### Step 4: 单次执行

```powershell
python simple_edit.py
```

失败时按具体错误修补一次脚本，再运行一次。

### Step 5: 验收（必做）

- 草稿目录存在
- 保存成功
- 至少一个 video 片段
- BGM 在 audio 轨（若使用）
- 有旁白时有字幕
- `mile_cli.py qa` 无 error

## 错误处理

- `MileError`：按 `code` 处理（见 `rules/precise-edit.md`）。
- `SegmentOverlap`：把片段移到轨道末尾或换轨。
- 素材缺失：切换已知本地素材或有效云端 ID。
- 路径失败：设置 `JY_SKILL_ROOT` 后重跑 bootstrap。

## 反模式（避免）

- 对全工作区反复 `Get-ChildItem -Recurse`
- 临时探查文件（`*_dir.txt`、`output.txt`），除非明确调试
- 首次成功生成前反复搜索样式/特效
- 未看时间线就按序号改片段
