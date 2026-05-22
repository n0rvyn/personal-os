---
name: tts
description: "Synthesize speech with a single command across Volcengine (Doubao) and MiniMax. Voice-id prefix routes the backend: volc-* → Volcengine, mm-* → MiniMax. Provides single-shot synth, long-text batched synth with auto-chunk + ffmpeg merge, and per-provider quota check. Caller never writes vendor-specific curl. Use when generating podcast audio, character voiceovers, or any TTS output from a Role script."
allowed-tools:
  - Bash
  - Read
  - Write
---

## When to use

Invoke this skill instead of writing per-vendor curl/auth/encoding logic in a Role workspace script. Examples:

- Daily podcast audio (long markdown → mp3)
- Single voice line for a character (周杰伦, 达芬奇, ...)
- Cross-vendor fallback when one quota is exhausted

Do not use this skill for:

- Pure text chunking with no synthesis — use `text-to-segments` (#238).
- Audio post-processing (EQ, normalization) — use ffmpeg directly or a dedicated audio skill.

## API

### `synth` — single chunk

```bash
tts-toolkit/skills/tts/scripts/synth.sh \
  --text "你好测试" \
  --voice volc-zh_male_M392_conversation_wvae_bigtts \
  --output out.mp3 \
  [--speed 1.0 --rate 24000]
```

Behaviour: routes to backend by voice prefix, writes one mp3, exits 0 on success.

### `synth-batch` — long text → single merged mp3

```bash
tts-toolkit/skills/tts/scripts/synth.sh \
  --input transcript.md \
  --voice volc-zh_male_M392_conversation_wvae_bigtts \
  --output podcast.mp3 \
  [--max-chars 280]
```

Behaviour: strips markdown, chunks on paragraph/sentence boundary (≤ `max-chars`), synthesizes each chunk, then ffmpeg-concats into the final output.

- **Transient 1002 rate-limit** on a chunk is retried in place (bounded, env `TTS_RATELIMIT_RETRIES`, default 3, growing wait) — a passing RPM blip does NOT abort the run.
- **Resumable staging**: the staging dir is named deterministically from input+voice+params, and each chunk is written atomically (`.partial`→rename). An interrupted run (or an outer step-retry) re-runs `synth.sh` with the same input and **resumes** — already-synthesized chunks are skipped, not re-billed.
- A non-rate-limit chunk failure still aborts the run.

### `synth-auto` — quota-aware: pick a vendor that can finish, then synthesize

```bash
tts-toolkit/skills/tts/scripts/synth-auto.sh \
  --input transcript.md \
  --output podcast.mp3 \
  [--reserve-pct 25] [--concurrency 3] [--vendor-pool minimax,volc-2.0,volc-1.0]
```

Behaviour: estimates the WHOLE job's character count (chunks once, generic format), then walks the vendor pool in priority order (default **minimax → volc-2.0 → volc-1.0**) calling `quota_check` for each. The FIRST vendor with enough quota for the entire job is selected and synthesizes all of it. If NO vendor has enough, it exits **4 before synthesizing a single character** — never a half-made podcast, never a half-spent budget. Use this (not raw `synth-batch`) for any unattended long-form run.

Exit codes: `0` success · `1` arg error · `4` no vendor has enough quota (decided pre-synthesis) · other = propagated from `synth.sh`.

### Volcengine 1.0 / 2.0 — separate models, separate quota

`seed-tts-1.0` and `seed-tts-2.0` are different models billed as separate products. UsageMonitoring cannot split them, so `providers/volcengine.sh` writes every successful call's input char count to a per-tier local ledger (`${TTS_LEDGER_DIR:-~/.tts-toolkit/ledger}/volc-usage.log`), and `quota_check.sh check --vendor volcengine --tier 1.0|2.0` reads that ledger for an accurate per-tier `used_today`. Per-tier budgets: env `VOLC_TTS_DAILY_BUDGET_V1` / `_V2`.

### Voice resolution

See [references/voice-catalog.md](references/voice-catalog.md) for the list of verified voices and cross-vendor equivalents.

LLM workflow: pick a voice from the catalog first; do NOT invent a `voice_id` and hope it works — unverified IDs return `3001 resource not granted` (Volcengine) or `1004 voice unauthorized` (MiniMax).

### Provider quirks

See [references/provider-quirks.md](references/provider-quirks.md) for documented pitfalls (hex vs base64 audio encoding, semicolon auth header, etc.).

## Process

### Step 1 — Check voice prefix

`synth.sh` parses `--voice` and dispatches to `providers/{volcengine,minimax}.sh`. Unknown prefix → fail fast with supported-prefix list. No silent fallback.

### Step 2 — Provider-specific call

Each provider script handles its own auth header format, request body shape, and response decoding. Output: a single chunk's mp3 written to the requested path (single mode) or a numbered staging path (batch mode).

### Step 3 — Batch concat (batch mode only)

`scripts/merge.sh` runs `ffmpeg -f concat -safe 0 -i <list> -c copy <output>` over the staging dir. Concat-demuxer is byte-level — only works because each provider returns mp3 with consistent codec/bitrate within a single run.

## Exit codes

- 0: success
- 1: argument error (missing `--text`/`--voice`/`--output`, unknown prefix)
- 2: provider auth/config missing (env var not set)
- 3: provider API error (3001 not authorized, 1004 voice unauthorized, etc. — original response included in stderr)
- 4: ffmpeg concat error (batch mode)

## Acceptance test

Single-chunk Volcengine call (requires `VOLC_TTS_APPID` + `VOLC_TTS_TOKEN`):

```bash
scripts/synth.sh \
  --text "你好测试" \
  --voice volc-zh_male_M392_conversation_wvae_bigtts \
  --output /tmp/tts-smoke.mp3
file /tmp/tts-smoke.mp3   # expect: "MPEG ADTS, layer III"
```

MiniMax single-chunk: TODO — provider script is a skeleton, returns exit code 2 until implemented.
