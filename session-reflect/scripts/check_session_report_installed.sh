#!/usr/bin/env bash
# Exit 0 if claude-plugins-official/session-report is installed; non-zero otherwise.
set -e
PLUGIN_ROOT="$HOME/.claude/plugins/cache/claude-plugins-official/session-report"
if [ ! -d "$PLUGIN_ROOT" ]; then
  echo "ERROR: claude-plugins-official/session-report is not installed." >&2
  echo "Install it via Claude Code's plugin marketplace, then rerun:" >&2
  echo "  /plugin marketplace add claude-plugins-official" >&2
  echo "  /plugin install session-report@claude-plugins-official" >&2
  exit 1
fi
# Verify SKILL.md exists in the most recent version directory.
# Path layout: $PLUGIN_ROOT/<version-hash>/skills/session-report/SKILL.md (depth 4 from PLUGIN_ROOT).
# Use -maxdepth 5 to tolerate any reasonable cache layout variation.
SKILL=$(find "$PLUGIN_ROOT" -maxdepth 5 -name SKILL.md -path '*/session-report/SKILL.md' 2>/dev/null | head -1)
if [ -z "$SKILL" ]; then
  echo "ERROR: session-report directory exists but SKILL.md not found at expected location." >&2
  echo "Try reinstalling: /plugin install session-report@claude-plugins-official" >&2
  exit 2
fi
echo "$SKILL"
exit 0
