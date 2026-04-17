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

## Configuration

Copy `references/session-reflect.local.md.example` to `~/.claude/session-reflect.local.md` and customize.

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
