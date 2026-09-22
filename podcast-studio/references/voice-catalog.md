<!-- snapshot from tts-toolkit/skills/tts/references/voice-catalog.md — re-sync when upstream voices change -->
# Voice catalog

Verified voice IDs grouped by vendor, with scene tags and cross-vendor equivalents. LLMs picking a voice for a Role should consult this file first — inventing IDs returns 3001/1004 from the vendor.

## Volcengine (Doubao) — `volc-*`

Verified 2026-05-17 (Volcengine v1/tts).

Voice + Resource-Id are paired — calling a 2.0 voice with `seed-tts-1.0` returns '音色未授权', and vice versa. Pick the row's `Resource-Id` value to set `VOLC_TTS_RESOURCE_ID` when invoking synth.sh.

| Voice ID | Scene | Resource-Id | Quota / auth |
| --- | --- | --- | --- |
| `zh_male_M392_conversation_wvae_bigtts` | Podcast, conversational male, mature warm tone — used for the 2026-05-17 26-chunk 5324-char podcast run | `seed-tts-1.0` | ¥0 authorized on test account; daily quota tied to TOS |
| `zh_male_yuanboxiaoshu_uranus_bigtts` | Podcast host / 通用场景 male, "渊博小叔 2.0" — knowledgeable mature tone, supports 情感变化 | `seed-tts-2.0` | smoke-tested 2026-05-22 via `providers/volcengine.sh`; 2.0 字符版 trial pack |

1.0 and 2.0 are **separate billing products / separate quota pools** — `seed-tts-2.0` bills as "语音合成2.0字符版", `seed-tts-1.0` as "语音合成1.0字符版". `quota_check.sh --tier 1.0|2.0` checks them independently via the per-tier local ledger.

Voices NOT verified on this account (request via Volcengine console before use):

| Voice ID | Notes |
| --- | --- |
| `BV001_streaming` | Default sample voice; returned 3001 unauthorized on first attempt — must be ¥0 ordered in console |

## MiniMax — `mm-*` — 已验证可用

Verified 2026-05-17 via 3-char smoke test through `providers/minimax.sh` against `api.minimaxi.com` with `speech-2.8-hd` model (unless noted otherwise).

MiniMax model variants — HD = best quality (longform podcast, audiobook), Turbo = faster + lower latency (short clips, chat). Override per-call with `--model` flag or `MINIMAX_MODEL` env.

| Voice ID | Scene | Recommended model | Notes |
| --- | --- | --- | --- |
| `mm-Chinese (Mandarin)_Radio_Host` | Podcast male host, mature warm tone | `speech-2.8-hd` | Best for podcast-style narration |
| `mm-Chinese (Mandarin)_Reliable_Executive` | Sophisticated business male | `speech-2.8-hd` | Clear, authoritative tone |
| `mm-audiobook_female_1` | Narrator, audiobook female, clear pacing | `speech-2.8-hd` | HD for longform quality |
| `mm-Chinese (Mandarin)_News_Anchor` | News female anchor, authoritative | `speech-2.8-turbo` | Turbo OK for short news clips |
| `mm-male-qn-qingse` | Young male, clear narration | `speech-2.8-hd` | The 周杰伦 Role's previous default |

## Local (offline, on-device) — `local-*` — 已验证可用

Verified 2026-09-22 on an **Intel** Mac (x86_64, macOS 15.8) through
`providers/local.sh`. Engine: sherpa-onnx 1.13.8 + VITS-zh-ll, running entirely
on this machine — no key, no network, no quota, no bill. This is the floor the
pool falls back to when every cloud vendor is exhausted or a key expired.
Install once with `scripts/fetch-local-model.sh` (113 MB download → 142 MB under
`~/.tts-toolkit/models/vits-zh-ll`, plus a private venv).

One model, five Mandarin speakers — the sid is the only thing that varies, so
switching between them costs nothing.

| Voice ID | Notes (post-gain peak on the same 180-char podcast excerpt) |
| --- | --- |
| `local-vits-zh-ll-sid0` | Default. Even, neutral pacing — peak 0.605 |
| `local-vits-zh-ll-sid1` | peak 0.876 |
| `local-vits-zh-ll-sid2` | peak 0.898 |
| `local-vits-zh-ll-sid3` | Loudest of the five; the limiter does real work here — peak 0.970 |
| `local-vits-zh-ll-sid4` | peak 0.830 |

Override which one the pool uses: `TTS_LOCAL_VOICE=vits-zh-ll-sid3`.

**Measured here** (2026-09-22, Intel x86_64, 4 onnx threads): single-process
RTF 0.17; through the real `synth-auto` path (concurrency 2, chunker +
per-chunk model load + ffmpeg merge) a 795-char markdown produced 35.7 s of
audio in 11.6 s wall. A ~6000-char episode lands near 90 s.

⚠️ **Two limitations. Both real, neither a bug:**

1. **The lexicon is Chinese-only and drops Latin words silently.** "Swift",
   "Apple", "TTS", "ffmpeg" are simply not spoken — sherpa prints `Ignore OOV`
   and carries on. `local_tts.py` counts them and warns on stderr so the
   omission surfaces in a pipeline log instead of only in the finished audio.
   On an English-heavy script this voice changes the meaning of sentences.
2. **16 kHz, and audibly below the cloud voices.** A fallback, not a peer. The
   ×2.1 gain + soft-knee limiter (ported from ArtLens's measured calibration —
   this model runs ~6 dB under everything else) brings its LOUDNESS to parity.
   Nothing brings its fidelity there.

## Cross-vendor equivalents

When quota_check signals a vendor is over-budget, the caller may swap voice with this mapping:

| Scene | Volcengine 2.0 (`seed-tts-2.0`) | Volcengine 1.0 (`seed-tts-1.0`) | MiniMax | Local (offline) |
| --- | --- | --- | --- | --- |
| Mature warm male / podcast host | `volc-zh_male_yuanboxiaoshu_uranus_bigtts` | `volc-zh_male_M392_conversation_wvae_bigtts` | `mm-Chinese (Mandarin)_Radio_Host` | `local-vits-zh-ll-sid0` |
| Narration / audiobook female | TODO empirical | TODO empirical | `mm-audiobook_female_1` | — (5 个 sid 的性别/音色未逐个听辨，见上表) |
| News / announcement female | TODO empirical | TODO empirical | `mm-Chinese (Mandarin)_News_Anchor` | — (5 个 sid 的性别/音色未逐个听辨，见上表) |

`synth-auto.sh` uses this row's voices as its default vendor pool
(priority: MiniMax → Volcengine 2.0 → Volcengine 1.0 → local).

## How to add a MiniMax voice

1. Confirm the voice ID is listed in the MiniMax console or official catalog.
2. Run a 3-character smoke test: `synth.sh --text "你好啊" --voice "mm-<id>" --output /tmp/smoke.mp3`.
3. Check the output: `file /tmp/smoke.mp3` should report `MPEG ADTS, layer III`.
4. If verified: add a row to the "已验证可用" table above with the scene tag, recommended model, and the date verified.
5. If failed with error code 1004 (voice unauthorized): add to "候选未验证" with the failure reason. The voice may require explicit console authorization.

## How to verify a new Volcengine voice

1. Order ¥0 in the Volcengine TTS console (or grant in MiniMax dashboard) for the target voice.
2. Run a 3-character smoke test via `synth.sh --text "你好啊" --voice <prefix-id> --output /tmp/smoke.mp3`.
3. Listen / `file` check the output — `file` should report `MPEG ADTS, layer III`.
4. If OK, add a row to the table above with the scene tag, correct `Resource-Id`, and date verified.
