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

19000 characters per day across all voices on the standard plan. Single long-form runs (e.g. a 5000-char podcast) consume a quarter of the daily budget. Quota check before submitting is essential — see `quota_check.sh` (TODO).

### Auth is standard Bearer

`Authorization: Bearer <key>` with a SPACE (unlike Volcengine). Don't copy the Volcengine semicolon trick into MiniMax.

### Endpoint

`https://api.minimax.chat/v1/t2a_v2` for synthesis. Note `t2a_v2` (text-to-audio v2), not the older `text_to_audio_v1` path.

## Common to both

### Network resilience

Wrap each call in a retry-on-network-error loop (NOT retry on auth/quota errors — those are deterministic). The 2026-05-17 run used 0.5s gentle gap between chunks; that proved sufficient.

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
