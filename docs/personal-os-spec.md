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
