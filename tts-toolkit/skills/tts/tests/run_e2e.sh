#!/usr/bin/env bash
# run_e2e.sh — Live E2E smoke test for the synth pipeline.
#
# Default fixture is small (`fixtures/podcast-smoke.md`, a few hundred chars)
# so this can run on every dev cycle without draining the daily quota. The
# full 5471-char fixture is opt-in via E2E_FIXTURE override for occasional
# production-scale parity checks.
#
# Vendor is derived from the voice-id prefix (volc-* → volcengine, mm-* →
# minimax). Set E2E_VOICE to switch providers.
#
# Env overrides:
#   E2E_FIXTURE        — path to input markdown (default: smoke fixture)
#   E2E_VOICE          — voice id; prefix determines vendor (legacy path only)
#   E2E_OUTPUT         — output mp3 path
#   E2E_AUTO=1         — run synth-auto.sh (quota pre-flight + vendor fallback)
#                        instead of the legacy explicit quota-check + synth
#   SKIP_QUOTA_CHECK=1 — skip provider quota preflight (use when a vendor's
#                        quota API is broken; the run still consumes real chars
#                        but the size is bounded by the fixture)
#
# Exit codes:
#   0  VALIDATED (real audio, magic bytes OK, duration/size OK)
#   1  fixture missing / unreadable
#   2  bad magic bytes (not MP3)
#   3  duration too short (ffprobe path)
#   4  bad file type (fallback path)
#   5  file unexpectedly small for fixture size (fallback path)

set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SYNTH="$HERE/../scripts/synth.sh"
QUOTA_CHECK="$HERE/../scripts/quota_check.sh"
FIXTURE="${E2E_FIXTURE:-$HERE/fixtures/podcast-smoke.md}"
OUT="${E2E_OUTPUT:-/tmp/tts-e2e-$(date +%Y%m%d-%H%M%S).mp3}"
VOICE="${E2E_VOICE:-mm-Chinese (Mandarin)_Radio_Host}"

# Derive vendor from voice prefix — single source of truth is the voice id.
case "$VOICE" in
  volc-*) VENDOR=volcengine ;;
  mm-*)   VENDOR=minimax ;;
  *)
    echo "unsupported voice prefix in E2E_VOICE: $VOICE" >&2
    echo "supported: volc-* (Volcengine), mm-* (MiniMax)" >&2
    exit 1
    ;;
esac

# Validate fixture
[[ -r "$FIXTURE" ]] || { echo "fixture unreadable: $FIXTURE"; exit 1; }
CHARS=$(wc -m < "$FIXTURE")
echo "fixture: $FIXTURE"
echo "fixture chars: $CHARS"
echo "vendor: $VENDOR"
[[ "$CHARS" -ge 100 ]] || { echo "fixture too short ($CHARS chars, need >= 100)"; exit 1; }

if [[ "${E2E_AUTO:-0}" == "1" ]]; then
  # Exercise synth-auto.sh end to end: pre-flight quota across the vendor pool,
  # vendor selection, then synthesis. Vendor is chosen by synth-auto, not E2E_VOICE.
  echo "--- synth-auto (quota-aware orchestration) ---"
  echo "output: $OUT"
  bash "$HERE/../scripts/synth-auto.sh" --input "$FIXTURE" --output "$OUT" --concurrency 3
else
  # Legacy path: explicit quota pre-check, then single-vendor synth.
  # SKIP_QUOTA_CHECK=1 bypasses when a vendor's quota API is broken; fixture size
  # already bounds the actual consumption.
  if [[ "${SKIP_QUOTA_CHECK:-0}" != "1" ]]; then
    echo "--- quota pre-check ---"
    bash "$QUOTA_CHECK" check --vendor "$VENDOR" --required-chars "$CHARS" --reserve-pct 30 || {
      echo "quota pre-check failed — aborting to preserve daily budget" >&2
      exit 1
    }
  else
    echo "--- quota pre-check skipped (SKIP_QUOTA_CHECK=1) ---"
  fi
  echo "--- synthesizing ---"
  echo "output: $OUT"
  echo "voice: $VOICE"
  bash "$SYNTH" --input "$FIXTURE" --voice "$VOICE" --output "$OUT" --concurrency 3
fi

# Validate magic byte
echo "--- validating ---"
first_bytes=$(xxd -p -l 4 "$OUT")
case "$first_bytes" in
  fffb*|fffa*|fff3*|fff2*|4944*)  # MP3 sync / ID3 magic
    ;;
  *)
    echo "BAD MAGIC: $first_bytes (expected fffb/fffa/fff3/fff2/4944)" >&2
    exit 2
    ;;
esac

# Validate duration or size — thresholds scale with fixture size.
# Chinese TTS rate ≈ 12 chars/s → conservative 0.05 s/char floor;
# mp3 at 128 kbps → ~16 KB/s → conservative 8 KB/s floor.
min_dur=$(( CHARS / 20 ))                # 0.05 s/char
[[ "$min_dur" -lt 5 ]] && min_dur=5      # absolute floor 5s — anything less means audio is broken
min_size=$(( CHARS * 8000 / 20 ))        # 8 KB/s × min_dur (in seconds), in bytes
[[ "$min_size" -lt 40000 ]] && min_size=40000

if command -v ffprobe >/dev/null 2>&1; then
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$OUT" 2>/dev/null)
  dur_int=${dur%.*}
  [[ "$dur_int" -ge "$min_dur" ]] || { echo "DURATION TOO SHORT: ${dur}s (expected >= ${min_dur}s for ${CHARS}-char input)" >&2; exit 3; }
  echo "VALIDATED: $OUT, ${dur}s, magic $first_bytes (fixture=${CHARS} chars, vendor=${VENDOR})"
else
  # Fallback: file command + scaled size check
  file "$OUT" | grep -qE "MPEG|Audio" || { echo "BAD FILE TYPE: $(file "$OUT")" >&2; exit 4; }
  size=$(wc -c < "$OUT")
  [[ "$size" -ge "$min_size" ]] || { echo "FILE TOO SMALL: ${size} bytes (expected >= ${min_size} for ${CHARS}-char input)" >&2; exit 5; }
  echo "VALIDATED: $OUT, ${size} bytes, magic $first_bytes (ffprobe unavailable, used size check)"
fi

# Cleanup check: confirm cleanup.sh does NOT list the mp3 output as a candidate
CLEANUP="$HERE/../scripts/cleanup.sh"
if [[ -x "$CLEANUP" ]]; then
  echo "--- cleanup.sh safety check ---"
  cleanup_out=$(bash "$CLEANUP" --dry-run 2>&1 || true)
  if echo "$cleanup_out" | grep -q "$OUT"; then
    echo "WARNING: cleanup.sh dry-run listed the E2E output mp3 — audio-safety filter may be broken" >&2
  else
    echo "cleanup.sh safety check: E2E output mp3 not listed (correct)"
  fi
fi
