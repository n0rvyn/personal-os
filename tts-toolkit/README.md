# tts-toolkit

Unified TTS skill for personal-os. Wraps Volcengine (Doubao) and MiniMax behind a single voice-id-prefix routing convention so Role scripts no longer hand-write per-vendor curl pipelines.

## Status (0.1.0 — ship candidate)

| Component | Status |
| --- | --- |
| Plugin manifest | done |
| `skills/tts/SKILL.md` API surface | done |
| Volcengine provider (`volc-*`) | done — V1 sync, `seed-tts-1.0` resource, base64 decode, semicolon bearer quirk |
| MiniMax provider (`mm-*`) | done — `t2a_v2`, HEX decode with magic-byte validation chain |
| Voice catalog | done — 1 Volcengine + 5 MiniMax verified + cross-vendor equivalents |
| Provider quirks doc | done — credential separation, version pinning, AppID requirement, info-leak warnings |
| Chunker (provider-aware char limits) | done — 26-chunk podcast verified |
| Quota check (`quota_check.sh`) | done — MiniMax via `token_plan/remains`, Volcengine via `UsageMonitoring` |
| End-to-end smoke test (`run_e2e.sh`) | done — small fixture by default, vendor auto-detected from voice prefix |
| Cleanup (`cleanup.sh`) | done — staging-dir + bak-file pruning with mp3 safety filter |
| BATS test coverage | 10/10 passing (test_quota_check + test_synth + test_providers + test_cleanup) |
| Multi-voice segments | out of scope for 0.1; depends on personal-os/text-to-segments (#238) |
| Role integration (周杰伦 generate_voice.sh) | done — Role workspace script delegates to this toolkit's `synth.sh` |

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
