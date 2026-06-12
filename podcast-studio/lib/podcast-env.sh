#!/usr/bin/env bash
# podcast-env.sh — TTS config→env shim for the podcast-studio plugin.
#
# Reads the podcast-studio config (lib/config.py) and exports the tts
# provider / host_voice as env vars the vendored tts scripts consume.
# Credential env vars (VOLC_TTS_*, MINIMAX_API_KEY, MM_*) are passed
# through from the parent environment — the shim never reads or prints
# them.
#
# Threat model: the shim NEVER `eval`s config content. Config values are
# round-tripped through a tmp env file written by Python (with the file
# mode set so other users cannot read), then sourced with `set -a; .` to
# export into the caller's shell.
#
# Usage:
#   source lib/podcast-env.sh
#   # Now $PODCAST_TTS_PROVIDER / $PODCAST_HOST_VOICE are exported.
#
# Resolution order for the config path (handled by lib/config.py):
#   1. PODCAST_STUDIO_CONFIG env var
#   2. ~/.podcast-studio/config.yaml
set -euo pipefail

# Resolve the plugin's lib/ dir relative to this script (no matter where
# the caller sources it from).
_SHIM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_PLUGIN_ROOT="$(cd "$_SHIM_DIR/.." && pwd)"

# Build a tmp env file with mode 0600; have Python write KEY=value pairs
# (quoted) into it. We do NOT pipe Python output into a `read` loop and
# we do NOT `eval` anything from Python.
_ENVDIR="$(mktemp -d -t podcast-env.XXXXXX)"
trap 'rm -rf "$_ENVDIR"' EXIT
_ENVFILE="$_ENVDIR/tts.env"
(umask 077 && : > "$_ENVFILE")

PODCAST_STUDIO_CONFIG="${PODCAST_STUDIO_CONFIG:-}" PODCAST_STUDIO_ROOT="$_PLUGIN_ROOT" python3 - "$_ENVFILE" <<'PYEOF'
import os
import sys
from pathlib import Path

env_path = Path(sys.argv[1])
plugin_root = Path(os.environ["PODCAST_STUDIO_ROOT"])
sys.path.insert(0, str(plugin_root))

from lib.config import load_config  # type: ignore[import-not-found]

try:
    cfg = load_config()
except Exception as e:
    print(f"podcast-env.sh: failed to load podcast-studio config: {e}", file=sys.stderr)
    sys.exit(1)

# Write KEY=value lines, single-quote the value defensively. A value
# containing a single-quote is unlikely in the config schema (paths +
# voice names) but we still guard against it by escaping.
def _shell_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"

with env_path.open("w", encoding="utf-8") as f:
    f.write(f"PODCAST_TTS_PROVIDER={_shell_quote(cfg.tts.provider)}\n")
    f.write(f"PODCAST_HOST_VOICE={_shell_quote(cfg.tts.host_voice)}\n")
PYEOF

# Source the env file: `set -a` auto-exports every assignment.
set -a
# shellcheck disable=SC1090
. "$_ENVFILE"
set +a

# The trap (set up above) removes $_ENVDIR on EXIT. Internal vars are
# left in scope but prefixed with `_` to avoid colliding with the
# caller's namespace.
