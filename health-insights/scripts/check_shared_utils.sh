#!/usr/bin/env bash
# Verifies that health-insights shared-utils scripts are present locally.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MISSING=0
for f in scripts/mongo_query.py scripts/mongo_insert.py scripts/notion_api.py; do
  if [ ! -f "$SCRIPT_DIR/../$f" ]; then
    echo "[health-insights] Missing helper: $SCRIPT_DIR/../$f" >&2
    MISSING=1
  fi
done
if [ "$MISSING" -ne 0 ]; then
  echo "[health-insights] Missing required helper scripts — ensure health-insights is fully installed" >&2
  exit 1
fi
echo "[health-insights] shared-utils OK"
exit 0
