#!/usr/bin/env bash
# MiniMax TTS provider.
#
# Quirks (see ../references/provider-quirks.md):
#   - `.data.audio` is HEX-encoded, not base64. Decoded with Python bytes.fromhex().
#   - Endpoint: https://api.minimaxi.com/v1/t2a_v2  (NOTE: minimaxi.com, NOT minimax.chat)
#   - Auth: standard "Authorization: Bearer <key>" (no semicolon, unlike Volcengine).
#   - Body: JSON with model, text, stream: false, voice_setting, audio_setting.
#
# Args (positional):
#   $1 text, $2 voice_id (stripped of `mm-` prefix by caller), $3 output, $4 speed, $5 rate
#
# Env:
#   MINIMAX_API_KEY      (required)
#   MINIMAX_API_HOST     (optional, default https://api.minimaxi.com)
#   MINIMAX_MODEL        (optional, default speech-2.8-hd)
#   MINIMAX_TIMEOUT_SEC  (optional, default 60) — per-chunk curl timeout
#
# Exit codes: 0 success / 2 missing env / 3 API or decode error

set -euo pipefail

text="${1:?text required}"
voice="${2:?voice required}"
output="${3:?output required}"
speed="${4:-1.0}"
rate="${5:-24000}"

[[ -z "${MINIMAX_API_KEY:-}" ]] && { echo "minimax: MINIMAX_API_KEY env not set" >&2; exit 2; }

API_HOST="${MINIMAX_API_HOST:-https://api.minimaxi.com}"
MODEL="${MINIMAX_MODEL:-speech-2.8-hd}"

# Build JSON body via env-var passing into python3.
# NEVER shell-interpolate voice IDs into JSON — they contain parentheses, spaces, etc.
body="$(
  MM_TEXT="$text" MM_VOICE="$voice" MM_MODEL="$MODEL" MM_SPEED="$speed" MM_RATE="$rate" \
  python3 -c "
import json, os
print(json.dumps({
    'model': os.environ['MM_MODEL'],
    'text': os.environ['MM_TEXT'],
    'stream': False,
    'voice_setting': {
        'voice_id': os.environ['MM_VOICE'],
        'speed': float(os.environ['MM_SPEED']),
        'volume': 1.0,
        'pitch': 0,
    },
    'audio_setting': {
        'sample_rate': int(os.environ['MM_RATE']),
        'bitrate': 128000,
        'format': 'mp3',
        'channel': 1,
    },
}))
")"

response="$(curl -sS -X POST "$API_HOST/v1/t2a_v2" \
  -H "Authorization: Bearer ${MINIMAX_API_KEY}" \
  -H "Content-Type: application/json" \
  --max-time "${MINIMAX_TIMEOUT_SEC:-60}" \
  --data-binary "$body")"

# Decode and validate via python3:
#   - Check base_resp.status_code; non-zero → exit 3.
#   - Decode .data.audio (hex string) → binary.
#   - Validate first bytes are MP3 magic (\xff\xfb/\xff\xfa/\xff\xf3) or ID3.
#   - On magic mismatch: unlink output file, exit 3.
# Write response to a temp file to avoid OS ARG_MAX limit on large hex payloads.
resp_file="$(mktemp -t minimax-resp-XXXXXX)"
printf '%s' "$response" > "$resp_file"
MM_OUTPUT="$output" MM_RESP_FILE="$resp_file" python3 - <<'PYEOF'
import sys, json, os

resp_raw = open(os.environ['MM_RESP_FILE']).read()
out = os.environ['MM_OUTPUT']

try:
    d = json.loads(resp_raw)
except json.JSONDecodeError:
    sys.stderr.write(f"minimax: non-JSON response: {resp_raw[:300]}\n")
    sys.exit(3)

base_resp = d.get("base_resp", {})
code = base_resp.get("status_code", -1)
if code != 0:
    msg = base_resp.get("status_msg", "unknown")
    sys.stderr.write(f"minimax: API error code={code} message={msg}\n")
    sys.exit(3)

audio_data = d.get("data", {})
audio_hex = audio_data.get("audio") if isinstance(audio_data, dict) else None
if not audio_hex:
    sys.stderr.write(f"minimax: missing .data.audio in success response\n")
    sys.exit(3)

try:
    audio_bytes = bytes.fromhex(audio_hex)
except ValueError as e:
    sys.stderr.write(f"minimax: hex decode error: {e}\n")
    sys.exit(3)

# Validate MP3 magic bytes
mp3_syncs = (b'\xff\xfb', b'\xff\xfa', b'\xff\xf3', b'\xff\xf2')
id3_magic = b'ID3'
first3 = audio_bytes[:3]
first2 = audio_bytes[:2]
is_mp3 = any(first2 == s for s in mp3_syncs) or first3 == id3_magic
if not is_mp3:
    first4_hex = audio_bytes[:4].hex()
    sys.stderr.write(f"minimax: decoded bytes not MP3 (first 4 hex bytes: {first4_hex})\n")
    if os.path.exists(out):
        os.unlink(out)
    sys.exit(3)

with open(out, 'wb') as f:
    f.write(audio_bytes)

sys.exit(0)
PYEOF
_py_exit=$?
rm -f "$resp_file"
exit $_py_exit
