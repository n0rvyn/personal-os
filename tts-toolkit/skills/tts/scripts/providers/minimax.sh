#!/usr/bin/env bash
# MiniMax TTS provider — skeleton, NOT YET IMPLEMENTED.
#
# Quirks to handle when implementing (see ../references/provider-quirks.md):
#   - `.data.audio` is HEX-encoded, not base64. Decode with `xxd -r -p` or Python bytes.fromhex.
#   - Daily quota: 19000 chars; check before submitting long-form runs.
#   - Endpoint: https://api.minimax.chat/v1/t2a_v2
#   - Auth: standard `Authorization: Bearer <key>` (no semicolon, unlike Volcengine).
#   - Body shape differs from Volcengine — see API doc.
#
# Args (positional):
#   $1 text, $2 voice_id (stripped of `mm-` prefix), $3 output, $4 speed, $5 rate
#
# Env (required when implemented):
#   MINIMAX_GROUP_ID, MINIMAX_API_KEY
#
# Exit code 2: not implemented.

set -euo pipefail
echo "minimax: provider not yet implemented in tts-toolkit 0.1.0 — see issue #237 follow-up" >&2
exit 2
