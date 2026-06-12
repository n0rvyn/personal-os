#!/usr/bin/env bash
# cleanup.sh — operator-side TTS temp-file cleanup.
#
# Identifies stale TTS staging dirs (tts-batch-*) in TMPDIR.
#
# NEVER deletes audio files (.mp3 .wav .flac .pcm .m4a) anywhere, even inside
# staging dirs. Audio is an artifact, not temp.
#
# Usage:
#   cleanup.sh [--older-than DAYS] [--dry-run|--apply] [--scope all|staging]
#
# Defaults: --older-than 7, --dry-run, --scope all
#
# Exit codes:
#   0  success (dry-run completed or apply finished)
#   1  argument-parse error (invalid DAYS, invalid scope, unknown flag)
#
# Env overrides (for testing — avoids touching real /tmp):
#   TTS_CLEANUP_TMPDIR      scan root for staging dirs (default: ${TMPDIR:-/tmp})

set -euo pipefail

# --- Defaults ---
days=7
mode=dry-run
scope=all

# --- Argument parsing ---
while [[ $# -gt 0 ]]; do
  case "$1" in
    --older-than)
      days="$2"; shift 2;;
    --dry-run)
      mode=dry-run; shift;;
    --apply)
      mode=apply; shift;;
    --scope)
      scope="$2"; shift 2;;
    -h|--help)
      cat <<EOF
Usage: $(basename "$0") [--older-than DAYS] [--dry-run|--apply] [--scope all|staging]

Defaults: --older-than 7, --dry-run, --scope all

Scans for stale TTS staging dirs and either lists (dry-run) or deletes (apply) them.
NEVER deletes audio files (.mp3 .wav .flac .pcm .m4a) anywhere.

Scan root (env-overridable for testing):
  TTS_CLEANUP_TMPDIR      staging dirs scan root  (default: \${TMPDIR:-/tmp})

Exit codes:
  0  success
  1  argument error
EOF
      exit 0;;
    *)
      echo "cleanup: unknown flag: $1" >&2; exit 1;;
  esac
done

# --- Validate arguments ---
[[ "$days" =~ ^[1-9][0-9]*$ ]] || { echo "cleanup: --older-than must be a positive integer (got '$days')" >&2; exit 1; }
case "$scope" in
  all|staging) ;;
  *) echo "cleanup: --scope must be all|staging (got '$scope')" >&2; exit 1;;
esac

# --- Scan root (env-overridable) ---
scan_tmpdir="${TTS_CLEANUP_TMPDIR:-${TMPDIR:-/tmp}}"

# --- Collect candidates ---
candidates=()

# Stage A — staging directories under TMPDIR (single root, no wildcard parent)
if [[ "$scope" == "all" || "$scope" == "staging" ]]; then
  while IFS= read -r -d '' path; do
    # Confirm it's actually a directory (not a symlink resolving elsewhere)
    [[ -d "$path" && ! -L "$path" ]] || continue
    # Confirm basename matches literal glob pattern (defense-in-depth)
    bn="$(basename "$path")"
    [[ "$bn" == tts-batch-* ]] || continue
    candidates+=("$path")
  done < <(find "$scan_tmpdir" -maxdepth 3 -type d -name 'tts-batch-*' -mtime "+$days" -print0 2>/dev/null)
fi

# --- Safety filter + output ---
total_size=0
safe_candidates=()

for candidate in "${candidates[@]}"; do
  # Refuse any path that is itself an audio file
  case "$candidate" in
    *.mp3|*.wav|*.flac|*.pcm|*.m4a)
      echo "skip (audio artifact, never deleted): $candidate" >&2
      continue
      ;;
  esac
  safe_candidates+=("$candidate")
done

if [[ ${#safe_candidates[@]} -eq 0 ]]; then
  echo "no stale TTS temp files found (older-than=${days}d, scope=$scope)"
  exit 0
fi

# --- Compute sizes and format output ---
if [[ "$mode" == "dry-run" ]]; then
  echo "would delete (dry-run, run with --apply to commit):"
else
  echo "deleting:"
fi

for candidate in "${safe_candidates[@]}"; do
  if [[ -d "$candidate" ]]; then
    size="$(du -sh "$candidate" 2>/dev/null | cut -f1 || echo '?')"
    mtime="$(stat -f '%Sm' -t '%Y-%m-%d' "$candidate" 2>/dev/null || stat --format='%y' "$candidate" 2>/dev/null | cut -d' ' -f1 || echo '?')"
  else
    size_bytes="$(stat -f '%z' "$candidate" 2>/dev/null || stat --format='%s' "$candidate" 2>/dev/null || echo 0)"
    # Convert to human readable
    if [[ "$size_bytes" -ge 1048576 ]]; then
      size="$(echo "scale=1; $size_bytes/1048576" | bc)M"
    elif [[ "$size_bytes" -ge 1024 ]]; then
      size="$(echo "scale=1; $size_bytes/1024" | bc)K"
    else
      size="${size_bytes}B"
    fi
    mtime="$(stat -f '%Sm' -t '%Y-%m-%d' "$candidate" 2>/dev/null || stat --format='%y' "$candidate" 2>/dev/null | cut -d' ' -f1 || echo '?')"
  fi

  if [[ "$mode" == "apply" ]]; then
    if rm -rf "$candidate" 2>/dev/null; then
      echo "  deleted: $candidate ($size, modified $mtime)"
    else
      echo "  ERROR: failed to delete $candidate" >&2
    fi
  else
    echo "  $candidate ($size, modified $mtime)"
  fi
done

echo "total: ${#safe_candidates[@]} candidates"
