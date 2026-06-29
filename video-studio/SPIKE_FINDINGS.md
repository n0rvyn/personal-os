# video-studio Spike — Findings (2026-06-29)

Spike goal: prove the riskiest chain end-to-end (文案 beats → 图/视频/图表 → 配音 → 字幕 → ffmpeg 合成 → 可播 16:9 1080p mp4) and surface every external-dependency 坑 before Stage 1.

**Verdict: chain validated end-to-end with REAL assets, including real S2V.** Acceptance UX-001 (1920×1080, narration-driven duration, drift 0.15s) and UX-003 (BGM ≥18 dB below narration; measured 31.5 dB) both PASS.

## Live-verified API contracts (MiniMax, host = api.minimaxi.com)

These were wrong in the first cut and are now fixed in `lib/`. All verified against the live API on 2026-06-29 — **do not "correct" back to a `data`-wrapper assumption.**

| Call | Endpoint | Real response shape |
|---|---|---|
| TTS | `POST /v1/t2a_v2` | `data.audio` (hex mp3) + **top-level** `extra_info.audio_length` (ms). `extra_info` is NOT under `data`. |
| Image | `POST /v1/image_generation` | `data.image_urls[0]`; subject_reference uses `image_file: <url>` (scalar). |
| S2V create | `POST /v1/video_generation` | model **`S2V-01`** (Hailuo T2V/I2V models reject subject_reference: `2013 ... does not support Subject-Reference-Video mode`). Payload subject_reference uses `image: [<url>]` (**array**, unlike image-01's `image_file`). Response: **top-level** `task_id` (no `data`). |
| S2V query | `GET /v1/query/video_generation?task_id=` | **top-level** `status` / `file_id` / `video_width` / `video_height`. Statuses seen: `Preparing` → `Processing` → `Success`. `file_id` empty until Success. |
| S2V retrieve | `GET /v1/files/retrieve?file_id=` | `file.download_url` (under `file`, NOT `data`); 9h-valid signed OSS URL. |

**S2V-01 output is 720p (1280×720)** even when `resolution: "1080P"` is requested — the param is effectively ignored. ffmpeg upscales to 1920×1080 in `video_segment_cmd`, so the pipeline still produces 1080p, but do not assume native 1080p from S2V-01. S2V preserved character identity acceptably (same coach as the reference image, in motion).

## Environment prerequisite (hard)

- **System ffmpeg (brew 8.1) lacks libass** → cannot burn hard subtitles (`subtitles` filter absent; no drawtext/freetype either). Hard subs are required for 视频号.
- Fix used: a libass-enabled static ffmpeg (evermeet 8.1.2: libass + libfreetype + libharfbuzz). libass uses the macOS **coretext** font provider, so `force_style=FontName=PingFang SC` resolves natively (no fontconfig needed). Chinese renders correctly.
- Both `spike_run.py` and `spike_acceptance.py` honor `FFMPEG_BIN` / `FFPROBE_BIN` env overrides. **Stage 1 must ship/locate a libass-enabled ffmpeg** and wire it through config.

## Costs

- Image (image-01) + TTS (speech-02-hd) calls: cheap, used freely.
- Video (S2V-01, 21/week budget): **1 successful generation consumed** (the instrumented probe). Earlier failed submits (wrong model `2013`, and the unparseable-response attempt) — the `2013` was rejected pre-task (0 credit); the unparseable one may have created an orphan task (≤1 credit). Net: ~1–2 of 21.

## Reuse hooks (credit-free re-runs)

`spike_run.py` accepts `SPIKE_REF_PNG`+`SPIKE_REF_URL` (reuse a paid character_ref) and `SPIKE_S2V_CLIP` (drop in a paid S2V clip) so the full pipeline can be re-run/iterated without re-spending video credit. Useful for Stage 1 dev.

## Locked decisions confirmed by the spike

ffmpeg engine ✓ · narration-driven timeline (per-beat audio_length) ✓ · character consistency via subject_reference (still + S2V, same person) ✓ · charts code-rendered (matplotlib, no AI) ✓ · BGM hard-ducked + measured (not prompt-based) ✓ · S2V graceful fallback to still ✓ (exercised live).
