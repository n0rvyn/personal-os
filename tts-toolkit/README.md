# tts-toolkit

Unified TTS skill for personal-os. Wraps Volcengine (Doubao) and MiniMax behind a single voice-id-prefix routing convention so Role scripts no longer hand-write per-vendor curl pipelines.

## Status (0.1.0 — initial scaffold)

| Component | Status |
| --- | --- |
| Plugin manifest | done |
| `skills/tts/SKILL.md` API surface | done |
| Volcengine provider (`volc-*`) | done — proven on 2026-05-17 podcast (26 chunks, 5324 chars) |
| MiniMax provider (`mm-*`) | skeleton with TODO; not wired |
| Voice catalog | seeded with 2026-05-17 verified voices |
| Provider quirks doc | done (6 known pitfalls) |
| Quota check | not started |
| Multi-voice segments | not started — depends on personal-os/text-to-segments (#238) |
| Role integration (周杰伦 generate_voice.sh) | not started |

## Routing convention

Voice ID prefix selects the backend:

| Prefix | Backend | Example |
| --- | --- | --- |
| `volc-*` | Volcengine `/api/v1/tts` | `volc-zh_male_M392_conversation_wvae_bigtts` |
| `mm-*` | MiniMax `/v1/t2a_v2` (TODO) | `mm-male-qn-qingse` |

Unrecognized prefix → `synth.sh` exits non-zero with a list of supported prefixes.

## Configuration

Env vars consumed by providers:

| Vendor | Vars |
| --- | --- |
| Volcengine | `VOLC_TTS_APPID`, `VOLC_TTS_TOKEN`, `VOLC_TTS_CLUSTER` (default `volcano_tts`) |
| MiniMax | `MINIMAX_GROUP_ID`, `MINIMAX_API_KEY` (placeholder) |

Each provider script fails fast if its required vars are missing.

## Follow-ups tracked

- [#237 issue](https://github.com/n0rvyn/adam/issues/237) acceptance criteria not yet fully covered. Outstanding: MiniMax provider, quota check, 周杰伦 Role integration.
- [#238 issue](https://github.com/n0rvyn/adam/issues/238) text-to-segments — chunker.py belongs there; this plugin shells out to it.
