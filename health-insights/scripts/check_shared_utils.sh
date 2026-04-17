#!/usr/bin/env bash
# Verifies that indie-toolkit:shared-utils is installed, since health-insights
# agents depend on its mongo_query.py and notion_api.py helpers.
SHARED_UTILS="$HOME/.claude/plugins/marketplaces/indie-toolkit/shared-utils"
MISSING=0
for f in scripts/mongo_query.py scripts/mongo_insert.py skills/notion-with-api/scripts/notion_api.py; do
  if [ ! -f "$SHARED_UTILS/$f" ]; then
    echo "[health-insights] Missing dependency: $SHARED_UTILS/$f" >&2
    MISSING=1
  fi
done
if [ "$MISSING" -ne 0 ]; then
  echo "[health-insights] Install indie-toolkit:shared-utils — /plugin install shared-utils@indie-toolkit" >&2
  exit 1
fi
echo "[health-insights] shared-utils OK"
exit 0
