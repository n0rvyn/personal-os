# portfolio-lens

Indie project portfolio management: scan, progress pulse, verdict refresh, reprioritize. Produces structured exchange artifacts for PKOS ingestion.

## Quick Start

```
/portfolio-scan              # Scan ~/Code for active projects, emit exchange artifacts
/project-progress-pulse     # Observable progress facts for one or more projects
/repo-reprioritize          # Convert recent signals into focus/maintain/freeze/stop decisions
/recent-feature-review      # Judge recently built features or commit slices
/verdict-refresh            # Check whether an earlier conclusion still holds
```

## Skills

| Skill | Description |
|-------|-------------|
| `/portfolio-scan` | Periodic root-level portfolio scan; publishes exchange artifacts for PKOS |
| `/project-progress-pulse` | Per-project observable progress scan with normalized states |
| `/repo-reprioritize` | Converts recent signals into focus/maintain/freeze/stop decisions |
| `/recent-feature-review` | Reviews recent commit windows and feature slices |
| `/verdict-refresh` | Re-checks older conclusions against new evidence |

## Agents

| Agent | Purpose |
|-------|---------|
| `ingress-publisher` | Formats PKOS exchange artifacts without choosing final vault destinations |
| `repo-activity-scanner` | Gathers repo facts only: activity, tests, docs, TODO density, shipping clues |
| `feature-change-clusterer` | Groups recent changes into likely feature slices |
| `verdict-delta-analyzer` | Compares old verdict reasoning with new evidence |

## Architecture

```
portfolio-scan / project-progress-pulse / repo-reprioritize
  → repo-activity-scanner (per target)
  → ingress-publisher
      → publish_exchange.py
          → {exchange_dir}/product-lens/{intent}/
              → PKOS ingest-exchange skill
                  → canonical PKOS vault note
```

Exchange artifacts are consumed by `pkos:ingest-exchange` for canonical vault note creation.

## Configuration

Exchange artifact output uses the shared Personal-OS config (`~/.claude/personal-os.yaml`):

```yaml
# ~/.claude/personal-os.yaml
exchange_dir: ~/Obsidian/PKOS/.exchange
scratch_dir: ~/.personal-os/scratch
```

The `publish_exchange.py` script defaults to `{exchange_dir}/product-lens/` as its `--exchange-root`.

## Triggerable Tasks

| Task | Entry | Suggested Cadence | Reads | Writes |
|------|-------|-------------------|-------|--------|
| Portfolio scan | `/portfolio-scan` | Weekly (Sunday) | `~/Code/` | `{exchange_dir}/product-lens/portfolio-scan/` |
| Progress pulse | `/project-progress-pulse` | Daily or on demand | `~/Code/{project}/` | `{exchange_dir}/product-lens/progress-pulse/` |
| Reprioritize | `/repo-reprioritize` | Bi-weekly or on demand | `~/Code/` + PKOS verdict notes | `{exchange_dir}/product-lens/reprioritize/` |
| Feature review | `/recent-feature-review` | On demand | `~/Code/{project}/.git/` | `{exchange_dir}/product-lens/recent-feature-review/` |
| Verdict refresh | `/verdict-refresh` | Monthly or on demand | PKOS verdict notes | `{exchange_dir}/product-lens/verdict-refresh/` |

Wire these to Adam Templates (cron or event) or host-level cron per your preference.

## Smoke Test

```bash
# Verify config helper
python3 scripts/personal_os_config.py --get exchange_dir

# Verify no /tmp or hardcoded paths in runtime files
grep -rE '/tmp/|~/\.pkos|~/\.youtube-scout' scripts/ agents/ skills/ 2>/dev/null
```
