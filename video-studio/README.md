# video-studio

personal-os fleet-member video production plugin. A Claude-driven multi-persona pipeline that takes structured training-science source docs and produces 6–10 minute landscape knowledge videos (适合微信视频号). Two skills cover the full lifecycle: `/video-plan` drafts and locks a syllabus (大纲), `/video-make` picks the next pending episode and renders it to `{seq}-{slug}.mp4` (named to match its episode directory).

Backed by ffmpeg (libass build required for subtitle burning), Doubao(默认)/MiniMax dual-provider for image/video/TTS, a faithfulness gate (忠实门) that traces every script beat back to a source anchor, and a storyboard judge (分镜判官) with a hard video-budget guard.

**Fleet member** — video-studio participates in the personal-os marketplace. Drop a config file, set your API keys, point `FFMPEG_BIN` at a libass-enabled build, and run.

## Install

### Prerequisites

**1. ffmpeg with libass (required for subtitle burning)**

The system ffmpeg does not include libass. You need a static build:

```bash
# Example — download a static libass-enabled build to ~/.local/ffmpeg-libass/
# (source: https://johnvansickle.com/ffmpeg/ or compile from source)
mkdir -p ~/.local/ffmpeg-libass
# ... place ffmpeg and ffprobe binaries there ...

# Export in your shell rc:
export FFMPEG_BIN=~/.local/ffmpeg-libass/ffmpeg
export FFPROBE_BIN=~/.local/ffmpeg-libass/ffprobe
```

The `/video-make` entry point checks for libass at startup. If your ffmpeg lacks libass, it prints a clear error and exits before rendering begins.

**2. 字幕字体（subtitle fonts）**

Install the subtitle font on your system before rendering. The default font is **PingFang SC** (macOS built-in). On Linux, install a CJK font and set `render.subtitle_font` in config accordingly.

**3. Python dependency**

```bash
pip install -r video-studio/requirements.txt
```

**4. Install plugin**

```
/plugin install video-studio@personal-os
```

## Skills

### `/video-plan` — Stage 0 大纲规划

Drafts and locks a syllabus for a new course book.

1. Collect snapshot docs from `video-studio/syllabus/source/<book>/*.md` — these are your Runetic training-science authority snapshots (pace tables, science summaries, etc.).
2. Dispatch `planner` persona (`agents/planner.md`, runs via `claude -p --model sonnet`) to draft `syllabus_draft.yaml` — each episode gets a title, source doc refs, and a traceability status (✅ direct science / ⚠️ engineering estimate).
3. Show you the draft for review — add, delete, or reorder episodes.
4. On approval, run `python -c "from lib.syllabus import promote_draft; promote_draft(...)"` to validate the draft and write `syllabus/{book}.yaml` + initialize `cursor.yaml`. After this step, `/video-make` auto-consumes episodes in order.

### `/video-make` — Stage 1 出片

Renders the next pending episode end-to-end.

```bash
# Prerequisite: FFMPEG_BIN and FFPROBE_BIN must point at the libass build
export FFMPEG_BIN=~/.local/ffmpeg-libass/ffmpeg
export FFPROBE_BIN=~/.local/ffmpeg-libass/ffprobe

python -m lib.runner --config ~/.video-studio/config.yaml
```

Internally: preflight libass check → `select_next` (reads cursor + syllabus) → `prepare_render` (digest-writer script + 忠实门 + director storyboard + 分镜判官 budget gate + camera movements) → `run_pipeline` (media gen + ffmpeg composite) → `episodes/{seq}-{slug}/{seq}-{slug}.mp4` + provenance.

## Provider switch (Doubao / MiniMax)

Set `MEDIA_PROVIDER=doubao` (default) or `MEDIA_PROVIDER=minimax` in your shell, or set `media.provider` in config.

| Provider | Image model | Video model | TTS |
|---|---|---|---|
| doubao (default) | Seedream | Seedance | MiniMax TTS via `audio.voice_id` |
| minimax | MiniMax image | MiniMax video | MiniMax TTS |

## Config

Create `~/.video-studio/config.yaml` (see `config.example.yaml` for the full schema):

```yaml
render:
  width: 1920
  height: 1080
  fps: 30
  subtitle_font: PingFang SC

audio:
  voice_id: male-qn-jingying-jingpin   # MiniMax voice ID
  bgm_duck_db: 18.0

media:
  provider: doubao    # doubao | minimax

ffmpeg:
  # Must be a libass-enabled build
  ffmpeg_bin: ~/.local/ffmpeg-libass/ffmpeg
  ffprobe_bin: ~/.local/ffmpeg-libass/ffprobe

output_dir: /path/to/video-studio-output   # MUST exist on disk
```

**API credentials — shell env, not config:**

```bash
# Doubao / 火山方舟
export ARK_API_KEY=...

# MiniMax
export MINIMAX_API_KEY=...
export MINIMAX_GROUP_ID=...
```

## What lives where

- `skills/video-plan/` — `/video-plan` skill (Stage 0 大纲规划, planner dispatch + promote_draft).
- `skills/video-make/` — `/video-make` skill (Stage 1 出片, thin shell over `python -m lib.runner`).
- `agents/planner.md` — Stage 0 syllabus planner persona.
- `agents/digest-writer.md` — Stage 1 script writer + faithfulness-gate host.
- `agents/director.md` — Stage 1 storyboard + camera movement director.
- `lib/pipeline.py` — pipeline topology (the authoritative step table; skills are thin shells over this, not over prose).
- `lib/runner.py` — Stage 1 CLI entry point (`python -m lib.runner`).
- `lib/syllabus.py` — syllabus/cursor load + save + `promote_draft`.
- `lib/wiring.py` — persona dispatch wiring + `assemble` (ffmpeg composite).
- `lib/preflight.py` — `check_ffmpeg_libass` (libass detection, called at runner startup).
- `lib/ledger.py` — faithfulness gate (忠实门): validate every beat has a source anchor.
- `lib/provenance.py` — provenance record per episode.
- `lib/cards.py` — on-screen card (标题卡/数据卡/引用卡) compositor.
- `lib/grade.py` — color grading presets (栏目冷蓝晨光 default).
- `evals/video_full_e2e.py` — full Stage 0→Stage 1 end-to-end eval (caches paid media via `dry=True`, live script/faithfulness/storyboard/synthesis).
- `SPIKE_FINDINGS.md` — API hard-facts from the original spike (S2V, TTS, ffmpeg quirks).

## Faithfulness gate (忠实门)

Every script beat must trace back to a source anchor from the episode's doc refs. The gate runs after script generation and before storyboarding. A beat that cannot be anchored halts the pipeline with a clear error listing the ungrounded claims. Fix the script or tighten the source docs, then re-run.

## Fleet membership

video-studio is a personal-os fleet member. All LLM dispatch runs through `claude -p --model sonnet` (CC plugin model; never switched to direct API). Configure `~/.video-studio/config.yaml` + API key env vars, install a libass ffmpeg, and run.
