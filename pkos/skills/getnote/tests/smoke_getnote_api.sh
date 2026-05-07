#!/usr/bin/env bash
# Opt-in real GetNote API smoke checks. This script creates and deletes one note.
set -euo pipefail

if [[ "${RUN_GETNOTE_SMOKE:-}" != "1" ]]; then
  echo "SKIP: set RUN_GETNOTE_SMOKE=1 with GETNOTE_API_KEY and GETNOTE_CLIENT_ID"
  exit 0
fi

if [[ -z "${GETNOTE_API_KEY:-}" || -z "${GETNOTE_CLIENT_ID:-}" ]]; then
  echo "SKIP: missing credentials"
  exit 0
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GETNOTE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
GETNOTE_SH="${GETNOTE_DIR}/scripts/getnote.sh"
GETNOTE_PY="${GETNOTE_DIR}/scripts/getnote.py"

NOTE_ID=""

cleanup() {
  local exit_code=$?
  if [[ -n "$NOTE_ID" ]]; then
    if bash "$GETNOTE_SH" delete_note "$NOTE_ID" >/dev/null; then
      echo "OK: deleted smoke note ${NOTE_ID}"
    else
      echo "ERROR: failed to delete smoke note ${NOTE_ID}" >&2
      exit_code=1
    fi
  fi
  exit "$exit_code"
}
trap cleanup EXIT

TITLE="PKOS GetNote smoke $(date -u +%Y%m%dT%H%M%SZ)-$$"
CONTENT="Created by pkos/skills/getnote/tests/smoke_getnote_api.sh"

SAVE_RESPONSE="$(bash "$GETNOTE_SH" save_note "$TITLE" "$CONTENT")"
NOTE_ID="$(printf "%s" "$SAVE_RESPONSE" | python3 "$GETNOTE_PY" parse-save-response)"
if [[ -z "$NOTE_ID" ]]; then
  echo "ERROR: save_note did not return data.note_id" >&2
  exit 1
fi
echo "OK: created smoke note ${NOTE_ID}"

LIST_RESPONSE="$(bash "$GETNOTE_SH" list_notes 0)"
LIST_RESPONSE_JSON="$LIST_RESPONSE" python3 - "$TITLE" <<'PY'
import json
import os
import sys

title = sys.argv[1]
payload = json.loads(os.environ["LIST_RESPONSE_JSON"])
if not isinstance(payload, dict) or payload.get("success") is not True:
    raise SystemExit("ERROR: list_notes did not return success:true")

data = payload.get("data", {})
notes = data.get("notes", []) if isinstance(data, dict) else []
found = any(isinstance(note, dict) and note.get("title") == title for note in notes)
if found:
    print("OK: list_notes returned the smoke note")
else:
    print("OK: list_notes returned success:true")
PY

bash "$GETNOTE_SH" add_tags "$NOTE_ID" "pkos-smoke" >/dev/null
echo "OK: added tag pkos-smoke"

SHARE_RESPONSE="$(bash "$GETNOTE_SH" share_note "$NOTE_ID")"
SHARE_RESPONSE_JSON="$SHARE_RESPONSE" python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["SHARE_RESPONSE_JSON"])
data = payload.get("data", payload) if isinstance(payload, dict) else {}
share_url = data.get("share_url") if isinstance(data, dict) else ""
if not share_url:
    raise SystemExit("ERROR: share_note did not return data.share_url")
print("OK: share_url exists")
PY

echo "PASS: GetNote real API smoke checks passed"
