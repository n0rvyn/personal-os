#!/bin/bash
# domain-intel SessionStart hook
# Checks if CWD is an initialized domain-intel directory and reports unread insight count

# If CWD doesn't have config.yaml, this isn't a domain-intel directory — silent exit
if [ ! -f "./config.yaml" ]; then
  exit 0
fi

# Check state file for last scan
state_file="./state.yaml"
last_scan="never"
if [ -f "$state_file" ]; then
  last_scan=$(grep '^last_scan:' "$state_file" | sed 's/last_scan: *//' | tr -d '"')
fi

if [ "$last_scan" = "never" ]; then
  echo "[domain-intel] Ready but no scans yet. Run /scan to start collecting."
  exit 0
fi

# Count unread insights — check both the legacy {WD}/insights/ and the new IEF dir
# (ief_output_dir in config.yaml overrides; default is {exchange_dir}/domain-intel/)
legacy_dir="./insights"
ief_dir=""
if [ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "${CLAUDE_PLUGIN_ROOT}/scripts/personal_os_config.py" ]; then
  override=$(grep -E '^ief_output_dir:' config.yaml 2>/dev/null | sed 's/ief_output_dir: *//' | tr -d '"' | head -1)
  if [ -n "$override" ]; then
    ief_dir=$(eval echo "$override")
  else
    exchange_dir=$(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/personal_os_config.py" --get exchange_dir 2>/dev/null)
    if [ -n "$exchange_dir" ]; then
      ief_dir="${exchange_dir}/domain-intel"
    fi
  fi
fi

unread_count=0
if [ -d "$legacy_dir" ]; then
  n=$(grep -rl 'read: false' "$legacy_dir/" 2>/dev/null | wc -l | tr -d ' ')
  unread_count=$((unread_count + n))
fi
if [ -n "$ief_dir" ] && [ -d "$ief_dir" ]; then
  n=$(grep -rl 'read: false' "$ief_dir/" 2>/dev/null | wc -l | tr -d ' ')
  unread_count=$((unread_count + n))
fi

if [ "$unread_count" = 0 ] && [ ! -d "$legacy_dir" ] && [ -z "$ief_dir" ]; then
  exit 0
fi

if [ "$unread_count" -gt 0 ]; then
  echo "[domain-intel] $unread_count unread insight(s). Last scan: $last_scan. Use /intel for a briefing."
fi
