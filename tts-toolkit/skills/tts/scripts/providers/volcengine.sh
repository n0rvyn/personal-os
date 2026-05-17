#!/usr/bin/env bash
# Volcengine (Doubao) TTS provider.
# Quirks (see ../references/provider-quirks.md):
#   - Auth header uses a SEMICOLON: "Bearer;<token>" (not "Bearer <token>")
#   - Audio payload returned in `.data` is base64 (NOT hex like MiniMax)
#   - Voice must be ¥0-authorized in the console first or returns 3001
#   - Per-call text limit ~1024 UTF-8 bytes (~280 chars Chinese); caller must pre-chunk
#   - X-Api-Resource-Id (required by doc): seed-tts-1.0 / seed-tts-1.0-concurr / seed-tts-2.0 / seed-icl-2.0.
#     Voice ID and Resource-Id must be paired correctly — see references/voice-catalog.md.
#
# Args (positional):
#   $1 text       — raw text (≤ ~280 chars)
#   $2 voice_id   — stripped of the `volc-` prefix by caller
#   $3 output     — absolute path to write the mp3
#   $4 speed      — speed_ratio (default 1.0)
#   $5 rate       — output sample rate (default 24000)
#
# Env:
#   VOLC_TTS_APPID, VOLC_TTS_TOKEN (required)
#   VOLC_TTS_CLUSTER    (default volcano_tts)
#   VOLC_TTS_RESOURCE_ID (default seed-tts-1.0)
#
# Exit codes: 0 success / 2 missing env / 3 API error

set -euo pipefail

text="${1:?text required}"
voice="${2:?voice required}"
output="${3:?output required}"
speed="${4:-1.0}"
rate="${5:-24000}"

if [[ -z "${VOLC_TTS_APPID:-}" ]]; then
    echo "volcengine: VOLC_TTS_APPID env not set" >&2
    exit 2
fi
if [[ -z "${VOLC_TTS_TOKEN:-}" ]]; then
    echo "volcengine: VOLC_TTS_TOKEN env not set" >&2
    exit 2
fi
cluster="${VOLC_TTS_CLUSTER:-volcano_tts}"
resource_id="${VOLC_TTS_RESOURCE_ID:-seed-tts-1.0}"

endpoint="https://openspeech.bytedance.com/api/v1/tts"
reqid="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')"

# Build JSON body with python3 to avoid shell-quoting hazards on Chinese text + newlines.
body="$(python3 -c "
import json, sys
print(json.dumps({
    'app': {'appid': '$VOLC_TTS_APPID', 'token': '$VOLC_TTS_TOKEN', 'cluster': '$cluster'},
    'user': {'uid': 'tts-toolkit'},
    'audio': {'voice_type': '$voice', 'encoding': 'mp3', 'speed_ratio': float('$speed'), 'rate': int('$rate')},
    'request': {'reqid': '$reqid', 'text': sys.stdin.read(), 'operation': 'query'}
}))
" <<<"$text")"

response="$(curl -sS \
  -H "Authorization: Bearer;${VOLC_TTS_TOKEN}" \
  -H "Content-Type: application/json" \
  -H "X-Api-Resource-Id: ${resource_id}" \
  --data-binary "$body" \
  --max-time 60 \
  "$endpoint")" || {
    echo "volcengine: curl failed (network/timeout)" >&2
    exit 3
}

# Decode-and-validate via python3 — one parse, clear error, base64 → bytes.
python3 - "$response" "$output" <<'PYEOF'
import sys, json, base64
resp_raw, out = sys.argv[1], sys.argv[2]
try:
    d = json.loads(resp_raw)
except json.JSONDecodeError as e:
    sys.stderr.write(f"volcengine: non-JSON response: {resp_raw[:300]}\n")
    sys.exit(3)
code = d.get("code")
if code != 3000:
    sys.stderr.write(f"volcengine: API error code={code} message={d.get('message')!r} reqid={d.get('reqid')}\n")
    sys.exit(3)
audio_b64 = d.get("data")
if not audio_b64:
    sys.stderr.write(f"volcengine: missing audio data in success response: {resp_raw[:300]}\n")
    sys.exit(3)
with open(out, "wb") as f:
    f.write(base64.b64decode(audio_b64))
PYEOF
