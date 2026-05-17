# Voice catalog

Verified voice IDs grouped by vendor, with scene tags and cross-vendor equivalents. LLMs picking a voice for a Role should consult this file first — inventing IDs returns 3001/1004 from the vendor.

## Volcengine (Doubao) — `volc-*`

Verified 2026-05-17 (Volcengine v1/tts).

| Voice ID | Scene | Quota / auth |
| --- | --- | --- |
| `zh_male_M392_conversation_wvae_bigtts` | Podcast, conversational male, mature warm tone — used for the 2026-05-17 26-chunk 5324-char podcast run | ¥0 authorized on test account; daily quota tied to TOS |

Voices NOT verified on this account (request via Volcengine console before use):

| Voice ID | Notes |
| --- | --- |
| `BV001_streaming` | Default sample voice; returned 3001 unauthorized on first attempt — must be ¥0 ordered in console |

## MiniMax — `mm-*`

Skeleton — provider not yet implemented; catalog will be filled when the MiniMax script ships.

Reference (from minimax-multimodal-toolkit, NOT yet verified through tts-toolkit):

| Voice ID | Scene |
| --- | --- |
| `male-qn-qingse` | Young male, clear narration |
| `Chinese (Mandarin)_Refined_Lady` | Refined female, professional reading |

## Cross-vendor equivalents

When quota_check signals a vendor is over-budget, the caller may swap voice with this mapping:

| Scene | Volcengine | MiniMax |
| --- | --- | --- |
| Mature warm male / podcast host | `volc-zh_male_M392_conversation_wvae_bigtts` | TODO — pick from MiniMax catalog after empirical verification |

## How to verify a new voice

1. Order ¥0 in the Volcengine TTS console (or grant in MiniMax dashboard) for the target voice.
2. Run a 3-character smoke test via `synth.sh --text "你好啊" --voice <prefix-id> --output /tmp/smoke.mp3`.
3. Listen / `file` check the output — `file` should report `MPEG ADTS, layer III`.
4. If OK, add a row to the table above with the scene tag and date verified.
