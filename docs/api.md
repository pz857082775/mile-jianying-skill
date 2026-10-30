# API Reference

This document is the canonical API index for this skill.

## Python Wrapper (`JyProject`)

Bootstrap:

```python
import os
import sys
skill_root = os.getenv("JY_SKILL_ROOT", r"<SKILL_ROOT>")
sys.path.insert(0, os.path.join(skill_root, "scripts"))
from jy_wrapper import JyProject
```

### Constructor

```python
JyProject(project_name: str, width: int = 1920, height: int = 1080, drafts_root: str | None = None, overwrite: bool = True)
```

### Core Lifecycle

- `save()`: persist and patch draft content.
- `get_track_duration(track_name: str) -> int`: timeline end in microseconds.

### Media APIs

- `add_media_safe(media_path, start_time=None, duration=None, track_name=None, source_start=0, **kwargs)`
- `add_audio_safe(media_path, start_time=None, duration=None, track_name="AudioTrack", **kwargs)`
- `add_clip(media_path, source_start, duration, target_start=None, track_name="VideoTrack", **kwargs)`
- `add_cloud_media(query, start_time=None, duration=None, track_name=None)`
- `add_cloud_music(query, start_time=None, duration=None, name=None, duration_s=None)`

### Text / Voice APIs

- `add_text_simple(text, start_time=None, duration="3s", track_name="Subtitles", **kwargs)`
- `add_tts_intelligent(text, speaker="zh_male_huoli", start_time=None, track_name="AudioTrack")`
- `add_narrated_subtitles(text, speaker="zh_female_xiaopengyou", start_time=None, track_name="Subtitles")`
- `add_ai_voiceover(prompt, start_time=None, speaker="ICL_zh_female_manbo_jianying", speed=1.2, sentence_gap="100ms", voice_track="AI VoiceOver", subtitle_track="AI Subtitles", model="deepseek-r1", need_reasoning=True, writer_credentials=None, writer_timeout=120.0)`
- `add_ai_voiceover_async(...)`: async equivalent.

`add_ai_voiceover(...)` directly calls JianYing's `smart_text_v2` writer with deep reasoning,
generates sentence-level `曼波讲故事` audio through SAMI, applies real `1.2x` FFmpeg speed
conversion, and adds duration-matched audio and subtitles only after every asset is valid.
It has no UI automation fallback and is verified against JianYing Pro `10.7.0.14095`.

```python
result = project.add_ai_voiceover(
    "写一段30秒口播稿，主题是普通人为什么越来越难存钱。",
    start_time="0s",
)
print(result["text"], result["reasoning"])
```

`add_text_simple(..., **kwargs)` supports `style`, `border`, `clip_settings`, `font`, `background`, `shadow`.
Use `clip_settings=draft.ClipSettings(transform_y=-0.8)` for subtitle bottom position.
Do not pass `transform_y` directly as a top-level arg.

### VFX / Transition APIs

- `add_effect_simple(effect_name, start_time=None, duration="3s", track_name="EffectTrack")`
- `add_transition_simple(transition_name, video_segment=None, duration="1s", track_name=None)`
- `add_web_asset_safe(html_path, start_time=None, duration="5s", track_name="WebVfxTrack", output_dir=None)`

### Export API

```python
result = project.export_video("out.mp4", resolution="1080", fps="30")
```

Returns:

```json
{
  "status": "success",
  "output": "C:/path/out.mp4",
  "size": 11045382,
  "duration": 4.71,
  "width": 2046,
  "height": 1080,
  "codec": "h264",
  "verified": "ffmpeg"
}
```

- `output` is the real file path after Jianying finishes (handles 5.9's same-name folder layout).
- `verified` is `ffprobe` or `ffmpeg`; the ffmpeg fallback decodes one frame and also extracts `duration` / `width` / `height` / `codec` / `fps`. A failed check returns `status="error"`.
- Set `MILE_FFMPEG` / `MILE_FFPROBE`, or `JIANYING_HOME`, when the binaries are not on PATH; the skill also probes standard install directories and the running Jianying process directory.

### Sticker API

- `add_sticker_at(image_path, start_time, duration="500ms", track_name="StickerTrack", scale=0.08, transform_x=None, transform_y=None, *, size_mode="scale", width_px=None, height_px=None)`

`scale` is a multiple of the "fit to canvas" size. Use `size_mode="pixel"` with exactly one of `width_px` / `height_px` for an exact on-screen size.

## Minimal End-to-End Example

```python
import os
import sys

skill_root = r"<SKILL_ROOT>"
sys.path.insert(0, os.path.join(skill_root, "scripts"))
from jy_wrapper import JyProject

project = JyProject("API_Demo", overwrite=True)
project.add_media_safe(os.path.join(skill_root, "assets", "video.mp4"), "0s", "5s", "VideoTrack")
bgm = project.add_cloud_media("7546546694282676275", "0s", "8s", "BGM_Track")
if bgm:
    bgm.volume = 0.6
project.add_narrated_subtitles("这是一个 API 文档示例。", start_time="0.5s")
project.save()
```

## Deterministic Draft Plan (`mile.plan` / `mile.ops`)

`compile_plan(data, plan)` validates a structured operation list without writing. `apply_plan(draft, plan, dry_run=False, backup=True)` applies every operation in memory, rolls back on failure, runs duration refresh, and saves one backup-backed transaction on success.

Validated JianYing 5.9 video operations:

- `set_filter`: `name`, `intensity` (0-100), `keep_old`; writes `materials.effects` with the vendored `Filter.export_json()` schema.
- `add_effect`: `name`, optional numeric `params` list (0-100), `keep_old`; writes scene or character effects to `materials.video_effects`.
- `set_mask`: `name`, normalized pixel center and size/feather/invert options; writes `materials.masks`.
- `add_animation`: `name`, `kind` (`in`, `out`, `group`), optional duration; writes `materials.material_animations` and replaces the same animation kind.
- `add_text_animation`: `name`, `kind` (`in`, `out`, `loop`), optional duration; text segments only — the entry keeps `material_type="sticker"` / `panel=""` exactly as real JianYing 5.9 drafts store caption animations.
- `add_keyframes`: `property` (`scale`/`x`/`y`/`rotation`/`alpha`/`volume`/`scale_x`/`scale_y`) and `frames` (`[{"at": seconds, "value": number}]`); merges into the segment's `common_keyframes` sorted by `time_offset`. `scale` writes `KFTypeScaleX` with `uniform_scale.on=true`, matching the real 5.9 draft schema (verified against shipped drafts); per-axis scale flips `uniform_scale.on=false`; out-of-range times are clamped to the segment end with a warning.
- `remove_keyframes`: clears one property or all keyframe lists on the segment.
- `add_media`: `path`, optional `start` (append to track end when omitted), `duration`, `source_start`, `track`, `volume`, `scale`/`x`/`y`/`rotation`/`alpha`, `name`; video/photo materials land in `materials.videos` using the vendored `VideoMaterial` field template (photo stores the 3h placeholder duration like real drafts, clip length comes from the segment). `track` values `pip`/`画中画`/`sticker`/`贴纸` create-or-reuse a `flag=2` overlay track. Photos default to 3 s; unknown video duration without probe raises `DURATION_UNKNOWN`; audio files must use `add_audio` (`BAD_ARGS`).
- `set_text` decoration (JianYing 5.9 verified): `border={"color", "width"(0-100 JianYing display units; stored raw as width/100*0.2), "alpha"}`, `shadow={"color", "alpha", "distance"(0-100), "angle"(-180~180), "feather"(0-1)}` and `background={"color", "style"(1|2), "alpha", "round_radius", "height", "width", "horizontal_offset", "vertical_offset"}`; pass `False` for any of them to remove. Style entries land in `content.styles[i].strokes` / `.shadows` with the real-draft key set (shadow uses `feather`, not `diffuse`), and the material mirrors `border_*`, `has_shadow`/`shadow_*` and `background_*`. Colors accept `#rgb`/`#rrggbb` or RGB arrays; named colors are not parsed (`BAD_COLOR`).
- `remove_material`: removes `filter`, `effect`, `mask`, `animation`, or `transition` references and garbage-collects unreferenced materials.

These operations are deterministic schema adapters, not claims of model-level visual understanding. Use `mile.analyze` / `mile.decide` for explainable signal-based recommendations; unverified capabilities remain reported as unavailable or warnings.

## Analysis & Adapters Layer (`mile.analysis` / `mile.adapters`)

Read-only evidence layer. Every result carries `method`, `available` and honest limitation
notes; nothing here claims semantic video understanding, and missing dependencies degrade to
stable error codes instead of fabricated output.

- `capability_status()` / `list_capabilities()` — per-backend availability with tiers
  (`signal` T0/T1, `scene` T1/T2, `pyscenedetect` T2, `faster_whisper` T3, `ocr` T2/T3,
  `sam2` T3) and missing-dependency lists.
- `analyze_media(path, *, analyze_audio_flag=True, analyze_scenes=False)` — ffmpeg/numpy
  signals: duration, audio BPM/energy (delegates to `mile.recommend.analyze_audio`), black
  and freeze frames; `analyze_scenes=True` adds shot boundaries from the scene adapter.
- `analyze_timeline_signals(timeline)` — draft-side observations (main shot lengths,
  caption coverage, BGM volume, voice presence).
- `mile.adapters.detect_scenes(path, *, threshold=0.4, min_scene_len_s=0.1)` — shot
  boundaries; prefers [PySceneDetect](https://github.com/Breakthrough/PySceneDetect)
  (`method="pyscenedetect"`) and degrades to the ffmpeg `select=gt(scene,t)` signal
  (`method="ffmpeg-scene-signal"`); with neither available raises
  `MileError("SCENE_DETECT_UNAVAILABLE", ...)` including an `install_hint`. Returns
  `boundaries_s`, contiguous `scenes` intervals and a `note` that these are difference
  signals, not content understanding.
- `mile.adapters.scene_backend()` — the backend the adapter would use right now:
  `"pyscenedetect"` / `"ffmpeg-scene-signal"` / `"none"`.
- `mile.adapters.measure_loudness(path, *, target_lufs=-16.0)` — integrated loudness
  `I` (LUFS) and `LRA` measured by ffmpeg `ebur128` (T1, no extra dependency). Returns
  `integrated_lufs`, `deviation_lu`, `suggested_gain_db` and a clamped `suggested_volume`
  when audible; values at/below the -70 LUFS absolute gate are reported as `silent` with no
  gain suggestion instead of guessing. Error codes: `FILE_NOT_FOUND`, `BAD_RANGE`,
  `FFMPEG_MISSING`, `LOUDNESS_FAILED`. `analyze_media` audio evidence carries it under
  `audio.loudness`; CLI `decide --audio` attaches it and emits a `review-loudness` decision
  (manual review only — whole-file loudness does not localize to segments, so no plan op).
- `mile.decide.decide_timeline(timeline, *, intent, tags, audio, timeline_signals)` —
  explainable style/pace/transition decisions with `confidence`, `risk`, `manual_review`
  and candidate `plan_ops` consumable by `mile.plan`.

CLI: `mile_cli.py analyze "<draft>" [--media FILE --no-audio --scenes]` and
`mile_cli.py decide "<draft>" [--intent ... --tags ... --audio FILE] [--plan-out plan.json]`
are read-only and never touch the draft.

## Smart Layer (`mile.intent` / `mile.subtitles` / `mile.recommend` / `mile.smart`)

These modules are rule/signal based. They never claim model-level understanding: results
carry `confidence`, `method`, `note`, `clarify` and `warnings` so the Agent can decide
whether to ask the user instead of guessing.

Bootstrap:

```python
import os, sys
skill_root = os.getenv("JY_SKILL_ROOT", r"<SKILL_ROOT>")
sys.path.insert(0, os.path.join(skill_root, "scripts"))
from mile.draft import load_draft
from mile.timeline import build_timeline
```

### Intent (`mile.intent`)

- `parse_intent(text, timeline=None) -> dict`

Returns `{"text", "confidence", "actions", "plan", "clarify", "warnings", "unsupported", "tags", "summary"}`.
`plan["ops"]` is directly consumable by `mile.plan.compile_plan` / `apply_plan`. When `plan` is
`None` or `clarify` is non-empty, ask the user instead of applying.

Feature slots (JianYing 5.9): `滤镜` → `set_filter`, `特效` → `add_effect`, `蒙版` → `set_mask`,
`入场/出场/组合动画` → `add_animation`, `字幕/文字/标题/花字动画` → `add_text_animation` (text
segments only; `循环` selects `kind=loop`, and a video target clarifies), `去掉/移除…` →
`remove_material`. Names are taken verbatim
from quoted or bare text and validated by the vendored enums at apply time; a missing or mismatched
name yields `FILTER_NOT_FOUND` / `EFFECT_NOT_FOUND` / `MASK_NOT_FOUND` / `ANIMATION_NOT_FOUND` /
`TEXT_ANIMATION_NOT_FOUND` with
`suggestions`, and an unquoted name lowers the action's confidence. Zoom keyframes parse from
`从T1到T2把画面放大到N倍` / `在T秒放大到N倍` into `add_keyframes` with two frames (start value
assumed `1.0`, surfaced as a warning; missing times or values clarify). Easing curves beyond
linear interpolation, color grading, beauty and reverse playback remain explicitly unsupported
and surface in `warnings`.

- `extract_target(text)`, `extract_numbers(text)`, `INTENT_TAGS`

Clause separators: `，` `。` `；` `！` `？` and `然后` / `接着` / `随后` / `并且` / `同时` / `并` / `再`.
`timeline` is required for boundary-dependent requests (`开头/结尾`, `所有转场`, `保留前 N 秒`).

### Subtitles (`mile.subtitles`)

- `polish_text(text, *, glossary=None, rules=None, punctuation=True, spaces=True) -> dict`
  Returns `{"original", "corrected", "changed", "changes", "protected"}`. Numbers, URLs, emails,
  `#topics` and `@mentions` are masked before rules run.
- `correct_text(text, **kwargs) -> str`
- `load_glossary(path)` — file (`.json` object/array or plain text with `wrong -> right` lines),
  dict, or a term list; returns `{"protect": [...], "replace": {...}}`.
- `split_caption(text, max_chars=28, min_chars=2) -> list[str]`
- `layout_caption(text, line_chars=14, max_lines=2) -> str`
- `build_captions(captions, *, max_chars=24, line_chars=None, max_lines=2, glossary=None, rules=None, correct=True, min_duration=0.5) -> list[dict]`
  Times are seconds; durations are re-allocated by character count.
- `to_srt(captions) -> str` / `parse_srt(text) -> list[dict]`
- `correct_timeline(timeline, *, glossary=None, roles=("caption", "title"), max_chars=None, rules=None) -> dict`
  Read-only report: `{"checked", "changed", "items"}` where each item has `ref`, `original`,
  `corrected`, `changes`, `protected`, `parts`.
- `asr_available() -> bool` / `transcribe(path, ...)` — optional `faster-whisper` backend;
  raises `MileError("ASR_UNAVAILABLE", ...)` when the backend is missing and
  `FILE_NOT_FOUND` when the media path does not exist.

### Recommendations (`mile.recommend`)

- `music_library_path()` / `load_music_library(path=None) -> list[dict]`
- `analyze_audio(path, *, sample_rate=22050) -> dict`
  ffmpeg/WAV decode plus numpy onset autocorrelation. Fields: `bpm`, `bpm_confidence`,
  `bpm_method`, `energy_db`, `dynamic_range_db`, `silence_ratio`, `brightness_hz`, `mood`.
  Error codes: `FFMPEG_MISSING`, `AUDIO_DECODE_FAILED`, `AUDIO_EMPTY`, `AUDIO_UNSUPPORTED`.
- `rank_bgm(library=None, *, duration_s=None, mood=None, tags=None, limit=5) -> dict`
  `method="metadata-rules"`; scores come from title/category/duration only.
- `recommend_transitions(timeline, *, bpm=None, max_items=30) -> dict`
  `method="duration-rules"`; gap > 100 ms and shots < 1.2 s are reported as `transition="无"`.

### Orchestration (`mile.smart`)

- `run_smart(draft, instruction=None, *, apply=False, polish=False, split_max=None, line_chars=None, max_lines=2, recommend=None, audio=None, library=None, limit=5, tags=None, glossary=None, roles=("caption", "title"), backup=True, remember=True, rating=None, notes=None) -> dict`
  One pass: parse intent → non-edit actions (polish/recommend/transcribe) → combined plan →
  single transactional save (subtitle rewrites and plan ops are written together) → QA → memory.
  `apply=False` is a pure preview; `clarify` short-circuits before anything is executed.
- `run_subtitles(draft, *, apply=False, split_max=None, line_chars=None, max_lines=2, glossary=None, roles=("caption", "title"), backup=True) -> dict`
- `apply_subtitle_fixes(data, items, *, split_max=None, line_chars=None, max_lines=2) -> dict`
  Returns `{"changed", "split", "skipped"}`. Segments that share one text material are skipped
  (rewriting the shared material would silently change other captions); splits clone the style
  into independent segments. Any failure rolls back the in-memory draft.

## v1.1 Layer (`mile.preview` / `mile.profile` / `mile.variants` / `mile.memory` governance)

### Preview (`mile.preview`)

- `preview_plan(data, plan) -> dict` — runs the plan on an **in-memory copy** (same
  execution path as `apply_plan` via `plan._execute_ops`) and returns a structured
  before/after diff. Never writes to disk, never records memory.
  Returns `{draft, preview: True, saved: False, intent, tags, before, after,
  diff: {meta, segments, ops}, risks, warnings}`.
- Segment diff is keyed by segment `id` with three states: `added` / `removed` /
  `changed` (the latter lists changed `fields` plus before/after values; times are in
  seconds).
- `risks` reuses `quality.run_checks` on the previewed in-memory draft and keeps only
  `error`-level issues.
- CLI: `mile_cli.py plan "<draft>" --plan plan.json --preview` and
  `mile_cli.py apply "<draft>" --plan plan.json --preview` (apply + preview never saves).

### Style Profiles (`mile.profile`)

Named, versioned style bundles (caption font size/color/position, transitions, BGM
volume, shot pace, canvas). Stored in `MILE_MEMORY_DIR/profiles/` (or `MILE_PROFILE_DIR`).

- `create_profile(name, *, draft_path=None, style=None, tags=None, notes=None, force=False) -> dict`
  — extracts style from a reference draft (`memory.extract_style`) or takes explicit JSON.
  Duplicate names raise `PROFILE_EXISTS` unless `force=True` (version auto-bumps, old
  version backed up under `profiles/versions/`).
- `list_profiles() -> list[dict]` / `get_profile(name) -> dict` (raises
  `PROFILE_NOT_FOUND`) / `resolve_style(name) -> dict` (style only, for `assemble --style`).
- `update_profile(name, *, style=None, tags=None, notes=None, draft_path=None) -> dict` —
  style updates are **deep-merged** (nested keys like `caption.color` merge, not replace).
- `delete_profile(name) -> dict` / `export_profile(name, out) -> dict` /
  `import_profile(source, *, force=False) -> dict`.
- CLI: `mile_cli.py profile --action create|list|show|update|delete|export|import`.

### Memory Governance (`mile.memory`)

- `export_memory(out, *, project=None, since=None) -> dict` — exports a JSON package
  `{kind: "mile-memory", version, exported_at, count, records[]}`; filters by project or
  since-date; atomic write; raises `EXPORT_FAILED` on failure.
- `import_memory(source, *, mode="merge"|"replace", overwrite=False) -> dict` — validates
  the whole file first (`IMPORT_BAD_FORMAT` on malformed input, 100 MB cap); `merge` de-dupes
  by id (skip by default, `overwrite=True` updates); `replace` wipes then imports and backs
  up the old library first. Returns `{mode, added, updated, skipped, backup}`.
- Negative feedback learning: `recommend_style` now multiplies score by `0.5` for
  `outcome="rejected"` records and `1.1` for `outcome="accepted"` (on top of rating-based
  adjustment).
- CLI: `mile_cli.py memory --action export --out mem.json [--project X --since Y]` /
  `import --source mem.json [--mode merge|replace --overwrite]`.

### Variants (`mile.variants`)

Matrix-account / multi-platform / A-B test batcher.

- `variants(project, clips, *, root=None, width, height, fps, count=3, names=None,
  openings=None, transitions=None, bgms=None, ratios=None, captions=None, bgm_volume,
  clip_duration=None, transition_duration=None, style=None, remember=True, intent, tags,
  notes, force=False, dry_run=False) -> dict`
  — deterministically cycles parameter pools to produce `{project}_v1.._vN` independent
  drafts, each QA-checked and remembered with `variant_of:<project>` / `variant_index:<N>` tags.
  - Ratios accept `16:9 / 9:16 / 1:1 / 4:5 / 3:4 / 21:9` or explicit `WxH`.
  - Without explicit `names`, at least one pool must contain ≥2 options, otherwise
    `BAD_ARGS` (guarantees structural difference between variants).
  - Single-item failures are collected in `failures` and do not abort others; all-fail
    raises `VARIANTS_FAILED`.
  - Returns `{project, requested, created, failed, items[], failures[], dry_run}`.
- CLI: `mile_cli.py variants --project <name> 素材... [--count N --openings ... --transitions ... --bgms ... --ratios ... --projects ...]`.

## CLI Contract

Common `--json` output:

```json
{
  "ok": true,
  "code": "ok",
  "reason": "",
  "data": {}
}
```

Exit codes:

- `0`: success
- `1`: runtime/infra failure
- `2`: invalid input or strict precondition failure

Key scripts:

- `scripts/api_validator.py`
- `scripts/asset_search.py`
- `scripts/build_cloud_music_library.py`
- `scripts/auto_exporter.py`
- `scripts/draft_inspector.py`

`analyze` and `decide` CLI commands expose capability status and explainable style/pace/transition decisions; they do not claim semantic video understanding.

### Draft Inspector CLI

```bash
python <SKILL_ROOT>/scripts/draft_inspector.py list --limit 20
python <SKILL_ROOT>/scripts/draft_inspector.py summary --name "DraftName"
python <SKILL_ROOT>/scripts/draft_inspector.py show --name "DraftName" --kind content --json
```
