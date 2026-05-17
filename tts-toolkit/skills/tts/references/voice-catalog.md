# Voice catalog

Verified voice IDs grouped by vendor, with scene tags and cross-vendor equivalents. LLMs picking a voice for a Role should consult this file first — inventing IDs returns 3001/1004 from the vendor.

## Volcengine (Doubao) — `volc-*`

Verified 2026-05-17 (Volcengine v1/tts).

Voice + Resource-Id are paired — calling a 2.0 voice with `seed-tts-1.0` returns '音色未授权', and vice versa. Pick the row's `Resource-Id` value to set `VOLC_TTS_RESOURCE_ID` when invoking synth.sh.

| Voice ID | Scene | Resource-Id | Quota / auth |
| --- | --- | --- | --- |
| `zh_male_M392_conversation_wvae_bigtts` | Podcast, conversational male, mature warm tone — used for the 2026-05-17 26-chunk 5324-char podcast run | `seed-tts-1.0` | ¥0 authorized on test account; daily quota tied to TOS |

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

## Cross-vendor equivalents

When quota_check signals a vendor is over-budget, the caller may swap voice with this mapping:

| Scene | Volcengine | MiniMax |
| --- | --- | --- |
| Mature warm male / podcast host | `volc-zh_male_M392_conversation_wvae_bigtts` | `mm-Chinese (Mandarin)_Radio_Host` |
| Narration / audiobook female | TODO empirical | `mm-audiobook_female_1` |
| News / announcement female | TODO empirical | `mm-Chinese (Mandarin)_News_Anchor` |

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
