# session-reflect

AI collaboration coach: analyzes your Claude Code and Codex sessions to help you improve prompting, workflow, and AI collaboration skills.

## Skill

### /reflect
Analyze recent sessions and get coaching feedback.
```
/reflect                  # today's sessions
/reflect --days 7         # weekly reflection
/reflect --profile        # view/update your collaboration profile
/reflect --project myapp  # filter by project
/reflect --task-trace abc123  # show the linked task chain for one session
/reflect --backfill --full    # run full historical backfill
/reflect --baselines          # show current baseline metrics
/reflect --rebaseline --plugin dev-workflow  # recompute one plugin's baselines
```

## What It Analyzes

- **Prompt quality**: vague instructions, missing context, unclear goals — with concrete rewrite suggestions
- **Process maturity**: skipping exploration, no verification, correction loops
- **Correction patterns**: recurring types of AI redirections and how to prevent them
- **Emotion signals**: frustration triggers, satisfaction patterns
- **Growth over time**: behavioral changes across reflections

## Data Sources

- Claude Code sessions: `~/.claude/projects/*/*.jsonl`
- Codex sessions: `~/.codex/sessions/YYYY/MM/DD/*.jsonl`
- /insights facets: `~/.claude/usage-data/facets/*.json` (optional enrichment)

## Storage

- SQLite data: `~/.claude/session-reflect/sessions.db`
- Reflections: `~/.claude/session-reflect/reflections/{date}.md`
- User profile: `~/.claude/session-reflect/profile.yaml`
- Analyzed sessions: `~/.claude/session-reflect/analyzed_sessions.json`
- IEF export: `{exchange_dir}/session-reflect/{YYYY-MM}/` (see shared config below)

## Skills

- **`reflect`** — Coaching analysis on recent sessions. Optional `--session-report-json PATH` enriches output with structured data from session-report.
- **`full-session-review`** — Single-command orchestrator: runs `claude-plugins-official/session-report` then `reflect --session-report-json`. Hard dependency on session-report being installed.

## Recommended Chain

For daily reviews, invoke `session-reflect:full-session-review` — it handles the session-report HTML render + enriched coaching markdown in one chain. To run only the coaching analysis (without the HTML report), invoke `session-reflect:reflect` directly.

## Configuration

Copy `references/session-reflect.local.md.example` to `~/.claude/session-reflect.local.md` and customize.

### session-reflect shared config keys

`session-reflect` reads two optional keys from `~/.claude/personal-os.yaml` (the shared personal-os config, loaded via `scripts/personal_os_config.py`):

| Key | Default | Description |
|-----|---------|-------------|
| `session_reflect.output_dir` | `~/.claude/session-reflect/reflections/` | Directory for coaching feedback markdown files |
| `session_reflect.session_report_json_path` | `/tmp/session-report.json` | Path to session-report's JSON output (consumed by `--session-report-json`) |

Example `~/.claude/personal-os.yaml` snippet:

```yaml
session_reflect:
  output_dir: ~/my-custom/reflections
  session_report_json_path: /tmp/my-session-report.json
```

These keys are optional — omit them to use defaults. The `--get` CLI also supports dotted access:

```bash
python3 scripts/personal_os_config.py --get session_reflect.output_dir
```

## Architecture

```
Session JSONL + /insights facets
  → Python scripts (parse + plugin telemetry extraction)
  → session-parser agent (enrich + ai behavior audit)
  → SQLite persistence (`sessions`, `plugin_events`, `ai_behavior_audit`, ...)
  → coach agent (coaching feedback) / profiler agent (user profile)
  → growth-tracker agent (cross-time comparison)
  → reflections/{date}.md + profile.yaml
```

- **Scripts**: Python stdlib only, no external dependencies
- **Agents**: session-parser (sonnet), coach (sonnet), profiler (sonnet), growth-tracker (sonnet)
- **Hook**: SessionEnd auto-summarization

## Phase 3 Operations

Scan plugin commits into `plugin_changes`:
```bash
python3 session-reflect/scripts/scan_plugin_changes.py --since 2026-01-01
```

Query a linked task chain from sqlite:
```bash
python3 session-reflect/scripts/sessions_db.py --query task-trace --session-id abc123
```

Check a before/after metric window for one plugin change:
```bash
python3 session-reflect/scripts/sessions_db.py \
  --query before-after \
  --plugin dev-workflow \
  --component verify-plan \
  --commit-hash 9f88532 \
  --metric-name correction_rate \
  --window-days 14
```

`/reflect` can also show a short unfinished-session hint when the latest analyzed session links back to an earlier `interrupted` or `failed` session.

## Phase 4 Operations

Run a full backfill with report output:
```bash
python3 session-reflect/scripts/backfill.py --full
```

Query current baselines from sqlite:
```bash
python3 session-reflect/scripts/sessions_db.py --query baselines --window-days 60 --plugin dev-workflow
```

Recompute baselines without re-parsing sessions:
```bash
python3 session-reflect/scripts/compute_baselines.py --window 60d --plugin dev-workflow --replace-existing
```

## Triggerable Tasks

| Task | Entry | Suggested Cadence | Reads | Writes |
|------|-------|-------------------|-------|--------|
| Daily reflection | `/reflect --days 1` | Daily | `~/.claude/projects/*/*.jsonl`, `~/.codex/sessions/` | `~/.claude/session-reflect/sessions.db`, `~/.claude/session-reflect/reflections/` |
| Weekly reflection | `/reflect --days 7` | Weekly | Same as above | Same as above |
| Session backfill | `/reflect --backfill --full` | One-time | Same as above | `sessions.db`, `{exchange_dir}/session-reflect/` |

Users wire these to Adam Templates (cron or event) or to host-level cron per their preference.

## Shared Config

session-reflect reads `~/.claude/personal-os.yaml` for IEF export directory. See [personal-os shared config spec](../../docs/personal-os-spec.md) for details.
