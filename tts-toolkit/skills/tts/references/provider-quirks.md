# Provider quirks

Every pitfall encountered while making the 2026-05-17 podcast TTS work, plus differences between vendors. Read before changing any provider script.

## Volcengine (Doubao)

### Auth header uses a semicolon

```
Authorization: Bearer;${TOKEN}
```

Note the SEMICOLON between `Bearer` and the token. Standard `Bearer <token>` (space) returns an auth error. This is non-RFC and easy to miss.

### Audio is base64

`response.data` is base64-encoded mp3 bytes. Decode with `base64 -d` / Python `base64.b64decode` / `openssl base64 -d`. Do NOT use `xxd -r -p` here — that's the MiniMax encoding (see below).

### Voice authorization required

Any voice you call must be ¥0 ordered in the Volcengine console first. Even default sample voices like `BV001_streaming` return `3001 resource not granted` until ordered.

### Per-call text limit

Volcengine accepts ≤ 1024 UTF-8 bytes per request. For Chinese (3 bytes/char) that's ~340 characters. Practical safe limit: **280 characters** with markdown stripped. Caller MUST chunk longer texts.

### Cluster value

Set `app.cluster: "volcano_tts"` (default). Other cluster names are documented but `volcano_tts` is what works for the v1/tts endpoint.

## MiniMax

### Audio is hex, not base64

`response.data.audio` is HEX-encoded. Use `xxd -r -p` or Python `bytes.fromhex` to decode. The Volcengine pattern (`base64 -d`) returns a white-noise file — exactly the 2026-05-17 incident on 周杰伦 Role's `generate_voice.sh`.

### Daily character quota

19000 characters per day across all voices on the standard plan. Single long-form runs (e.g. a 5000-char podcast) consume a quarter of the daily budget. Quota check before submitting is essential — see `quota_check.sh`.

### Audio decode

`response.data.audio` is HEX-encoded. Implemented in `providers/minimax.sh` per the official MiniMax encoding contract — see code for the validation chain. The first 3–4 bytes must be `\xff\xfb` / `\xff\xfa` / `\xff\xf3` (MPEG layer 3 sync) or `ID3` (ID3 tag header); mismatch is rejected with exit 3 and the partial output unlinked.

### Auth is standard Bearer

`Authorization: Bearer <key>` with a SPACE (unlike Volcengine). Don't copy the Volcengine semicolon trick into MiniMax.

### Endpoint

`https://api.minimaxi.com/v1/t2a_v2` for synthesis. Note `t2a_v2` (text-to-audio v2), not the older `text_to_audio_v1` path.

## Common to both

### Network resilience

Wrap each call in a retry-on-network-error loop (NOT retry on auth/quota errors — those are deterministic). The 2026-05-17 run used a 0.5s gentle gap between chunks at concurrency 3; that completed jobs but kept brushing MiniMax's `speech-2.8-hd` per-minute ceiling, and on 2026-06-03 MiniMax sent an rpm pre-warning email. Default lowered to concurrency 2 + 1.0s inter-batch gap to pull margin away from the rpm limit.

### Encoding consistency for ffmpeg concat

The `ffmpeg -f concat` demuxer concatenates mp3 streams byte-level — it requires every chunk share codec + bitrate + sample rate. Easy when all chunks come from the same provider in the same run with identical `--rate`. Mixing vendors mid-run (vendor swap) breaks this — re-encode via `-c:a libmp3lame` instead of `-c copy`.

### Speak natural prose, not markdown

TTS engines say literal `asterisk` for `*`, `hash` for `#`, etc. Strip markdown BEFORE chunking:
- code fences (entire block)
- inline backticks
- heading markers (keep text)
- bold/italic
- horizontal rules
- HTML comments
- list bullets
- collapse blank lines

`scripts/synth.sh` does this strip inline.

## Credential separation

Volcengine uses **two different credential pairs** depending on what you're doing:

| Credential pair | Where to get it | What it authorizes |
| --- | --- | --- |
| `VOLC_TTS_APPID` + `VOLC_TTS_TOKEN` | Volcengine **speech console** ([console.volcengine.com/speech](https://console.volcengine.com/speech)) per service | TTS / ASR calls on `openspeech.bytedance.com/api/v1/tts` |
| `VOLC_IAM_ACCESS_KEY_ID` + `VOLC_IAM_SECRET_ACCESS_KEY` | Volcengine **IAM 访问控制 console** ([console.volcengine.com/iam](https://console.volcengine.com/iam)) per IAM identity | Service management APIs on `open.volcengineapi.com`, including `UsageMonitoring` and `QuotaMonitoring` |

The `_IAM_` infix is a hard-learned naming requirement after a 2026-05-17 incident where an env file held a 32-char token under `VOLC_ACCESS_KEY_ID` (the legacy unprefixed name). That token was actually a speech-service value and produced misleading `InvalidAccessKey` errors against the IAM API. Real IAM access keys start with `AKLT` and run ~44 chars; the `_IAM_` infix in the env name forces visual disambiguation. Legacy `VOLC_ACCESS_KEY_ID` / `VOLC_SECRET_ACCESS_KEY` are still honored as fallback by `volcsign.py` and `quota_check.sh` for back-compat, but new env files should use the IAM-prefixed names exclusively.

The IAM identity must also have `speech_saas_prod:UsageMonitoring` and `speech_saas_prod:QuotaMonitoring` (and the read-only friends in that family) granted in IAM Console → policies → attach to the user/role behind the AK. Creating the policy alone is not sufficient; attach + propagation (≤ ~1 min) are required.

**Operator security rule**: Do not run `quota_check.sh` or `volcsign.py` under `bash -x` / `set -x` / `set -v`. Bash's trace will print every line of the calling script INCLUDING the env-export lines that set `VOLC_IAM_SECRET_ACCESS_KEY`, leaking the secret to whatever sink xtrace targets (stderr by default, `BASH_XTRACEFD` if set). This is a caller-side hygiene rule — the script cannot defend against it at runtime.

**Vendor info-leak observation**: Volcengine's `InvalidAccessKey` error message echoes the AK string back inside `[…]` in the response Message field. Anything that logs the raw response (CI logs, ticket attachments) will leak the AK. `volcsign.py:131-139` already prints only `{Code, Message}` and strips raw headers/body; downstream consumers should redact `\[[A-Za-z0-9_\-/+=]{20,}\]` patterns before persisting Volc error messages.

## Resource-Id requirement (Volcengine)

Volcengine's V1 TTS endpoint documents `X-Api-Resource-Id` as a REQUIRED HTTP header. In practice the endpoint has a backward-compat fallback that serves the legacy 1.0 model when the header is missing, which is how the 2026-05-17 baseline worked without it. **Do not rely on that fallback** — it is undocumented behavior that may be removed. `providers/volcengine.sh` MUST send the header in every request:

```bash
-H "X-Api-Resource-Id: ${VOLC_TTS_RESOURCE_ID:-seed-tts-1.0}"
```

Resource-Id values:

| Value | Model | Billing | Voice authorization scope |
| --- | --- | --- | --- |
| `seed-tts-1.0` | 豆包语音合成 1.0 (Seed-TTS 1.0) | 1.0 字符版 | 1.0 era voices (e.g. `zh_male_M392_conversation_wvae_bigtts`) |
| `seed-tts-1.0-concurr` | 豆包语音合成 1.0 | 1.0 并发版 | 1.0 era voices |
| `seed-tts-2.0` | 豆包语音合成 2.0 (Seed-TTS 2.0, released 2025.09) | 2.0 字符版 | 2.0 era voices (14 new; trustworthy, role-play, etc.) |
| `seed-icl-2.0` | 豆包声音复刻 2.0 | 复刻 2.0 字符版 | Voice clone (out of scope for tts-toolkit v0.1) |

Voice and Resource-Id are paired: a 2.0 voice ID with `seed-tts-1.0` returns "音色未授权"; a 1.0 voice ID with `seed-tts-2.0` returns the same. The voice-catalog table's "Resource-Id" column documents which value pairs with each voice.

## Operator hygiene — cleaning up TTS temp files

Staging dirs (`tts-batch-*` in `$TMPDIR`) are intentionally kept on failure for forensic inspection (see Threat Model in the plan). After a few weeks of TTS work with occasional failed runs, `$TMPDIR` accumulates stale staging dirs and `~/.adam/roles/*/scripts/tts/` accumulates `.bak.<timestamp>` backup files.

Run `scripts/cleanup.sh --dry-run` to inspect; `scripts/cleanup.sh --apply` to delete temp files older than 7 days.

The cleanup tool will NEVER delete a `.mp3` file (or any audio extension) anywhere. If you want to delete generated podcasts, do it manually.

## Out-of-scope variants (v0.2 candidates)

Future implementers should pick up from:

- **Volcengine V3 HTTP SSE**: `https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse` — one-shot text, streamed audio events. Different request shape (no `app.cluster`; uses event IDs 351 / 352 / 151 / 152 / 153).
- **Volcengine V3 WebSocket unidirectional**: `wss://openspeech.bytedance.com/api/v3/tts/unidirectional/stream` — same payload as SSE, WebSocket transport. Lower latency via connection reuse.
- **Volcengine V3 WebSocket bidirectional**: `wss://openspeech.bytedance.com/api/v3/tts/bidirection` — real-time interactive, streaming text-in + streaming audio-out. Used for voice-clone seed-icl-2.0 product.
- **Volcengine QuotaMonitoring API**: `POST /?Action=QuotaMonitoring&Version=2025-05-21` at `https://open.volcengineapi.com` with HMAC-SHA256 signature. Returns rate-limit state (QuotaType ∈ `qps` / `concurrency` / `qpm` / `tpm`), NOT character-package balance. Out of scope for v0.1 (which only checks char-budget).

  **Note**: `UsageMonitoring` (the action `quota_check.sh` actually uses for char-budget tracking) lives at the **same** `Version=2025-05-21`, NOT the older `2021-03-01` that `volcsign.py` was originally pinned to. The body MUST include `ResourceID` (e.g., `volc.service_type.10029` for 大模型语音合成) AND `AppID` (the 10-digit speech APPID — required even though the official 2026-05 doc spec doesn't list it; omitting returns `403 UnauthorizedRequest.AppID: Service unavailable. Please contact dev team.` which misleads operators into thinking the IAM policy is at fault). A working body for the `default` project:

  ```json
  {
    "ProjectName": "default",
    "ResourceID": "volc.service_type.10029",
    "AppID": "<10-digit VOLC_TTS_APPID>",
    "Mode": "daily",
    "UsageType": "text_words",
    "Start": "YYYY-MM-DD",
    "End": "YYYY-MM-DD"
  }
  ```

  Returns `Result.UsageMonitoring[].Value` as a float of consumed chars on that day. See `quota_check.sh:131-141` for the implementation.
- **MiniMax official podcast API**: `https://github.com/MiniMax-OpenPlatform/minimax_aipodcast` — multi-voice dialog synthesis. Different from `t2a_v2`; purpose-built for the podcast use case the 周杰伦 Role currently approximates with single-voice batched chunks.
- **Volcengine 语音播客大模型** (Adam issue #241): auto-generates 双人对话 podcast from a topic. WebSocket binary protocol, token-based billing, completely different shape from TTS. Not nestable into the current `synth.sh` model.

This subsection is documentation only; v0.1 ships single-vendor V1 (Volcengine) + sync `t2a_v2` (MiniMax).
