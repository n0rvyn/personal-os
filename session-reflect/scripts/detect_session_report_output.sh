#!/usr/bin/env bash
# Find newest session-report-*.html in CWD (or specified dir). Echo its absolute path.
# Exit non-zero if none found.
set -e
DIR="${1:-$PWD}"
NEWEST=$(ls -t "$DIR"/session-report-*.html 2>/dev/null | head -1 || true)
if [ -z "$NEWEST" ]; then
  echo "ERROR: no session-report-*.html found in $DIR" >&2
  exit 1
fi
# Resolve to absolute path
if command -v realpath >/dev/null 2>&1; then
  realpath "$NEWEST"
else
  # macOS without realpath
  ( cd "$(dirname "$NEWEST")" && echo "$PWD/$(basename "$NEWEST")" )
fi
