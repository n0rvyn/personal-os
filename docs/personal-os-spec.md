# Personal-OS Specification

**Version**: 1.0
**Date**: 2026-04-17

Personal-OS is a collection of Claude Code plugins for personal data intelligence: health, reflection, domain research, video curation, and knowledge management.

## Shared Config Contract

Personal-OS plugins read `~/.claude/personal-os.yaml` for exchange and scratch directory paths.

**File**: `~/.claude/personal-os.yaml`

```yaml
exchange_dir: ~/Obsidian/PKOS/.exchange    # IEF artifacts land here
scratch_dir:  ~/.personal-os/scratch        # transient files (replaces /tmp)
```

Both paths are expanded to absolute paths at load time. Callers receive resolved strings, not tilde-prefixed ones.

## IEF Exchange Directory Convention

All Personal-OS plugins producing IEF (Insight Exchange Format) files write to `{exchange_dir}/{producer}/` (e.g. `{exchange_dir}/youtube-scout/`, `{exchange_dir}/domain-intel/`). Consumer plugins read from the same directory.

## No `/tmp` for Data

Do not use `/tmp/` for plugin-owned persistent or transient data. Use `scratch_dir` instead:
- Temporary processing files
- Intermediate outputs before final write
- Any file that would otherwise land in `/tmp`

User source data (e.g., `~/Downloads/apple_health_export/export.xml`) is not plugin-owned and may live wherever the user placed it.

## No Adam Webhooks

Personal-OS plugins do NOT:
- POST to `/webhooks/events`
- Register as Adam signal sources
- Subscribe to Adam event streams

Plugins are pure data-processing pipelines. Users wire them to Adam via Role/Template/Trigger config in each plugin's README "Triggerable Tasks" section. This is a user-level decision, not a plugin capability.

## Adam Runtime Independence

Personal-OS plugins run under Claude Code, Codex, or Adam Role equally. They do not depend on Adam's runtime. Adam works without Personal-OS plugins installed. Personal-OS plugins work without Adam running.

## First-Run Behavior

On first invocation of any Personal-OS plugin, the shared config loader (`scripts/personal_os_config.py`) creates `~/.claude/personal-os.yaml` and `mkdir -p` both `exchange_dir` and `scratch_dir`:

- **Interactive first run**: prompts for paths (Enter accepts defaults)
- **Non-interactive first run** (e.g., Adam cron task): uses defaults silently without prompting

**Escape hatch**: users who do NOT want auto-creation of `~/Obsidian/PKOS/.exchange` should manually create `~/.claude/personal-os.yaml` with their preferred paths BEFORE running any Personal-OS plugin for the first time.

```yaml
# Example manual override
exchange_dir: /path/to/your/exchange
scratch_dir:  /path/to/your/scratch
```

## Project-root config (Cowork)

A project-root anchored config (`personal-os.yaml`) is the preferred layout for Cowork (multi-plugin workspace) users. Single superset file, namespace-divided.

**File**: `{project-root}/personal-os.yaml`

```yaml
exchange_dir: ./.exchange          # shared across the plugin fleet (project-relative OK)
scratch_dir:  ./.scratch           # shared across the plugin fleet (project-relative OK)
vault:                              # podcast plugin namespace
  subjective_dir: ./vault/subjective
  news_dir:       ./vault/news
  output_dir:     ./vault/output
  root:           ./vault
tts:                                # podcast plugin namespace
  provider:    minimax
  host_voice:  female-shaonv
```

**Keys:**
- **Flat (fleet-shared, top-level):** `exchange_dir`, `scratch_dir`. Both are expanded to absolute paths at load time.
- **Namespace blocks (plugin-specific):** `vault:`, `tts:` (and any other plugin-owned namespace). Returned as-is, not expanded.

**Resolution order (DP-003=C):**
1. `PERSONAL_OS_ROOT` env var → load `{env}/personal-os.yaml`. Trusted; no sentinel check.
2. Bounded cwd-walk: starting at `Path.cwd()`, walk up `Path.cwd().parents` looking for `personal-os.yaml`. Each candidate must parse as a YAML dict and contain at least one of `exchange_dir` / `scratch_dir` / `vault` / `tts` (sentinel check — prevents unrelated `personal-os.yaml` files on the cwd chain from hijacking resolution). Skip on parse failure or missing sentinel keys.
3. Home fallback: `~/.claude/personal-os.yaml` (the legacy single-user layout, still supported).

**Backwards compatibility:** home single-user mode is still supported. When no env is set and no project-root marker is found, the resolver falls back to `~/.claude/personal-os.yaml` and `load_config()` output is byte-identical to the pre-Phase-2 implementation. Consumers see zero behavioral change.

**Credentials:** TTS / API credentials remain in shell env only (e.g. `MINIMAX_API_KEY`, `VOLC_TTS_*`). They are never written to the YAML.

## Triggerable Tasks

Each plugin README must include a "Triggerable Tasks" section documenting how to wire the plugin to Adam Templates (cron or event) or host-level cron.

**Format template**:

```markdown
## Triggerable Tasks

| Task | Entry | Suggested Cadence | Reads | Writes |
|------|-------|-------------------|-------|--------|
| Daily health ingest | `/health ingest <path>` | Daily 07:00 | `~/Downloads/apple_health_export/export.xml` | MongoDB Atlas |
| ... | ... | ... | ... | ... |

Users wire these to Adam Templates (cron or event) or to host-level cron per their preference.
```

## Plugin Architecture

Each plugin is self-contained:
- Includes its own copy of `scripts/personal_os_config.py` and `scripts/personal_os_config.sh` (duplicated, not shared via cross-marketplace import)
- Manages its own storage roots via the shared config
- No cross-plugin runtime dependencies between Personal-OS plugins

## Shared Config Helper Reference

**Python** (`scripts/personal_os_config.py`): provides `load_config()` returning a dict with `exchange_dir` and `scratch_dir` as absolute paths.

**Shell** (`scripts/personal_os_config.sh`): sources `personal_os_config.py` and exports `personal_os_dir()` function for bash callers.

CLI access: `python3 scripts/personal_os_config.py --get exchange_dir` prints a single resolved path.
