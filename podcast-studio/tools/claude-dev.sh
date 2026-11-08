#!/usr/bin/env bash
# Load this existing plugin's canonical source in place, using Claude's supported
# development loader. It overrides the same-named installed plugin for this
# process only; no install/cache/marketplace/credential files are changed.
set -euo pipefail
entry_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
script_path="${BASH_SOURCE[0]}"
while [[ -L "$script_path" ]]; do
  script_dir="$(cd -- "$(dirname -- "$script_path")" && pwd -P)"
  link_target="$(readlink -- "$script_path")"
  if [[ "$link_target" = /* ]]; then script_path="$link_target";
  else script_path="$script_dir/$link_target"; fi
done
plugin_root="$(cd -- "$(dirname -- "$script_path")/.." && pwd -P)"
claude_command="${CLAUDE_BIN:-claude}"
if ! command -v "$claude_command" >/dev/null 2>&1; then
  if [[ -x "$HOME/.local/bin/claude" ]]; then claude_command="$HOME/.local/bin/claude";
  else printf '%s\n' 'Claude CLI not found; set CLAUDE_BIN to its executable.' >&2; exit 127; fi
fi
# A project-local link anchors both Claude's project settings and its existing config.
if [[ -f "$entry_dir/config.yaml" ]]; then cd -- "$entry_dir"; fi
# Preserve an explicit config selection. When launched from the established
# podcast project, use that project's config rather than inventing vault paths.
if [[ -z "${PODCAST_STUDIO_CONFIG:-}" && -f "$PWD/config.yaml" ]]; then
  export PODCAST_STUDIO_CONFIG="$PWD/config.yaml"
fi
exec "$claude_command" --plugin-dir "$plugin_root" "$@"
