# pkos — Personal Knowledge Operating System

Automated knowledge compilation system for Obsidian vaults. Ingests, links, evolves, and surfaces your knowledge.

## Quick Start

```
/pkos                  # Status dashboard
/pkos {topic}          # Query your knowledge base
/pkos ingest {url}     # Ingest a URL into your vault
/pkos ingest-exchange  # Convert producer exchange artifacts into canonical PKOS notes
/pkos review           # Today's wiki changes
/pkos lint             # Latest health report
/pkos notion-links     # Audit Notion links back to Obsidian notes
/podcast-transcript --type daily  # Create a deduped TTS-ready podcast transcript
```

## Required Configuration

Copy into `~/.claude/personal-os.yaml`:

```yaml
# pkos plugin config
pkos:
  notion_databases:
    inbox: "<your-inbox-db-uuid>"    # Notion DB for inbox items
    pipeline: "<your-pipeline-db-uuid>"  # Notion DB for exchange pipeline entries (optional)

# Standard Personal-OS paths (also used by other plugins)
exchange_dir: "~/Obsidian/PKOS/.exchange"
scratch_dir:  "~/.personal-os/scratch"
```

Keys under `pkos.notion_databases` are looked up at runtime by skill scripts via:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/personal_os_config.py --get pkos.notion_databases.inbox
```

## Skills

| Skill | Trigger | Description |
|-------|---------|-------------|
| `/pkos` | Auto (natural language routing) | Unified entry point: status, query, ingest, review |
| `/harvest` | "harvest", "scan projects", "收割" | Import knowledge from ~/Code/Projects/\*/docs/ |
| `/intel-sync` | Internal | Import insights from domain-intel IEF exports |
| `/digest` | Internal (cron) | Generate daily/weekly digest reports |
| `/podcast-transcript` | Manual or scheduled | Produces a TTS-ready transcript and owns its dedup state |
| `/signal` | Internal (cron) | Cross-source signal aggregation for weekly review |
| `/inbox` | Internal | Process captured items: classify, route, ripple |
| `/ingest-exchange` | Internal | Convert `.exchange/` artifacts from producer plugins into canonical PKOS notes |
| `/lint` | Internal (cron, Sundays) | Wiki health check: orphans, broken links, frontmatter |
| `/pkos notion-links` | Manual or cron | Audit and repair Notion properties that should open Obsidian notes |
| `/evolve` | Internal | Generate LENS/FOCUS profile updates |
| `/vault` | Internal | Obsidian vault operations (atomic writes, state management) |
| `/serendipity` | Internal | Cross-domain connection discovery |
| `/kb-bridge` | Internal | Export PKOS knowledge to external systems |

## Agents

| Agent | Purpose |
|-------|---------|
| inbox-processor | Classify, extract metadata, route inbox items to Obsidian + Notion |
| ripple-compiler | Propagate new note knowledge across MOCs, add cross-references, update entity pages |
| digest-writer | Compose daily/weekly digest content from pipeline data |
| signal-aggregator | Cross-source pattern detection and trend synthesis |
| wiki-linter | Detect orphan notes, broken wikilinks, frontmatter issues |
| graph-analyzer | Analyze vault as knowledge graph for serendipity discovery |
| knowledge-prefetch | Search vault for notes related to a topic |

## Architecture

```
Inbox (captured items: URLs, voice, text)
  → inbox-processor (classify + route)
      → Obsidian write
      → Notion write (optional)
      → ripple-compiler (propagate to MOCs)
          → MOC updates / creation
          → Cross-reference additions
          → Entity page updates
          → ripple-log.yaml

Harvest (~/Code/Projects/*/docs/)
  → scan + parse
  → inbox-processor (per-file)
  → ripple-compiler (batch)

Intel Sync (IEF imports from domain-intel)
  → inbox-processor (IEF → inbox item)
  → ripple-compiler

Producer Exchange (structured artifacts from plugins such as product-lens)
  → ingest-exchange (validate + normalize + place)
  → canonical vault note
  → downstream summary projection

Cron (daily/weekly)
  → signal-aggregator (weekly) → signal report
  → digest-writer (daily/weekly) → digest file
  → lint (Sundays) → health report
```

## Vault Directory Structure

PKOS uses a numbered folder structure:

```
~/Obsidian/PKOS/
├── 10-Knowledge/     # Permanent notes (atomic concepts)
├── 20-Ideas/         # Transient ideas and drafts
├── 30-Projects/      # Project-specific notes
├── 40-People/        # Person/entity pages
├── 50-References/     # Reference material (articles, papers)
├── 60-Digests/       # Generated digest reports
├── 70-Reviews/       # Health reports, signal reports
├── 80-MOCs/          # Map of Contents (synthesized topics)
├── 90-Inbox/         # Pending items not yet processed
├── .state/           # Internal state (ripple-log.yaml, last-review-marker)
└── .claude/          # Config and scripts
```

## Configuration

Edit `~/.claude/pkos/config.yaml`:

```yaml
vault:
  path: ~/Obsidian/PKOS

notion:
  enabled: true
  database_id: your-notion-database-id

notion_link_audit:
  hub_page_id: 32a1bde4-ddac-808b-bbca-db3641449bdf
  database_ids: []
  link_properties:
    - obsidian_link
    - source_note_path

harvest:
  projects_path: ~/Code/Projects
  docs_pattern: "**/docs/**/*.md"

migrate:
  sources: ~/.claude/pkos/.state/migrate-sources.yaml
```

## Migrate Skill

Import an external Obsidian vault:

```
/pkos migrate                           # Interactive migrate
/pkos migrate --scan-only               # Preview without writing
/pkos migrate --source-vault /path/to/vault
/pkos migrate --source-name github-notes
/pkos migrate --force                   # Re-import all
/pkos migrate --resume                  # Resume from interruption
```

## Cron Setup

```
# Daily digest at 9am
CronCreate(cron="0 9 * * *", prompt="cd ~/Obsidian/PKOS && /digest [cron]")

# Weekly signal aggregation Sundays at 10am
CronCreate(cron="0 10 * * 0", prompt="cd ~/Obsidian/PKOS && /signal [cron]")

# Wiki health check Sundays at 11am
CronCreate(cron="0 11 * * 0", prompt="cd ~/Obsidian/PKOS && /lint [cron]")
```

Note: Cron jobs auto-expire after 7 days. Recreate in new sessions.

## Triggerable Tasks

| Task | Entry | Suggested Cadence | Reads | Writes |
|------|-------|-------------------|-------|--------|
| Exchange ingest | `/pkos ingest-exchange` | Daily 08:30 | `{exchange_dir}/*/` | `~/Obsidian/PKOS/` canonical notes, `.state/exchange-ingest.yaml` |
| Intel sync | `/intel-sync` | Daily 09:00 (after domain-intel/session-reflect export) | domain-intel + session-reflect IEF under `{exchange_dir}/` | `~/Obsidian/PKOS/.state/imported-insights.yaml`, vault notes |
| Daily digest | `/digest [cron]` | Daily 09:00 | `~/Obsidian/PKOS/` recent notes | `~/Obsidian/PKOS/10-Knowledge/digests/` |
| Podcast transcript | `/podcast-transcript --type daily` | Daily after exchange producers finish | `{exchange_dir}/domain-intel/`, `{exchange_dir}/session-reflect/`, `{exchange_dir}/product-lens/`, PKOS vault notes | `~/Obsidian/PKOS/60-Digests/Podcast/`, `~/Obsidian/PKOS/.state/podcast-transcript/` |
| Weekly signals | `/signal [cron]` | Sundays 10:00 | `~/Obsidian/PKOS/` cross-source signals | `~/Obsidian/PKOS/10-Knowledge/signals/` |
| Wiki lint | `/lint [cron]` | Sundays 11:00 | `~/Obsidian/PKOS/` wikilinks + frontmatter | `~/Obsidian/PKOS/` lint report |
| Notion link audit | `/pkos notion-links` | Daily 23:00 | Notion PKOS Hub DBs, `~/Obsidian/PKOS/` | report only by default; `--apply` updates repairable Notion fields |
| Harvest project notes | `/harvest` | Weekly | `~/Code/Projects/*/docs/` | `~/Obsidian/PKOS/` canonical notes |

Users wire these to Adam Templates (cron or event) or to host-level cron per their preference.

## Inbox Processing

When `/pkos ingest <url>` or harvest finds new content:

1. **Classify** — inbox-processor determines type (article, video, podcast, tweet)
2. **Extract** — pulls title, summary, key quotes, tags
3. **Route** — writes to appropriate folder (10-Knowledge, 50-References, etc.)
4. **Ripple** — ripple-compiler propagates knowledge to relevant MOCs, adds cross-references

## Producer Exchange Flow

Some producer plugins do not write final vault notes directly. They publish structured artifacts into:

```text
~/Obsidian/PKOS/.exchange/{producer}/
```

PKOS then ingests those artifacts:

1. **Validate** — confirm schema and required fields
2. **Normalize** — map producer intent to canonical PKOS note type
3. **Place** — choose final folder and frontmatter
4. **Project** — optionally mark downstream summary sync as pending

Current intended producer:
- `product-lens`

## Product Lens Notion Projection

`product-lens` does not write to Notion directly. The flow is:

1. `product-lens` publishes an exchange artifact
2. `ingest-exchange` writes the canonical PKOS note
3. `sync_product_lens_notion.py` projects summary fields to Notion

Required config in `~/.claude/pkos/config.yaml`:

```yaml
product_lens_notion:
  enabled: true
  database_id: 3401bde4-ddac-8143-80aa-d65ca05ff26c
```

Current verified target:
- Workspace: `Knowledge Base`
- Database: `Product Lens Summary DB`
- Database ID: `3401bde4-ddac-8143-80aa-d65ca05ff26c`

Trigger rule:
- Artifact must request projection with `notion_sync_requested: true`
- In normal use this comes from `product-lens/scripts/publish_exchange.py --sync-notion`

Live commands (using `{scratch_dir}/pkos-test/` from `~/.claude/personal-os.yaml`):

```bash
SCRATCH=$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/personal_os_config.py --get scratch_dir)
EXCHANGE_ROOT="$SCRATCH/pkos-test/.exchange/product-lens"
VAULT_ROOT="$SCRATCH/pkos-test"

python3 product-lens/scripts/publish_exchange.py \
  --intent repo_reprioritize \
  --decision focus \
  --project AppA \
  --risk "Main blocker is still demand validation." \
  --reason "Recent progress is coherent and user-facing." \
  --action "Keep the current focus for one more review window." \
  --evidence "Recent commits stayed on the core path." \
  --exchange-root "$EXCHANGE_ROOT" \
  --sync-notion

python3 pkos/skills/ingest-exchange/scripts/ingest_exchange.py \
  --source "$EXCHANGE_ROOT/reprioritize/<file>.md" \
  --vault-root "$VAULT_ROOT" \
  --sync-notion
```

Verified result:
- `AppA Verdict` row exists in the database
- `AppA Smart Tagging Feature Review` row exists in the database

## Notion Obsidian Link Audit

PKOS keeps Obsidian as the source of truth and Notion as a projection. Run the audit to detect Notion fields that no longer open a local Obsidian note.

Audit only:

```bash
NOTION_TOKEN=... python3 pkos/skills/pkos/scripts/audit_notion_obsidian_links.py \
  --hub-page-id 32a1bde4-ddac-808b-bbca-db3641449bdf
```

Repair unique matches:

```bash
NOTION_TOKEN=... python3 pkos/skills/pkos/scripts/audit_notion_obsidian_links.py \
  --hub-page-id 32a1bde4-ddac-808b-bbca-db3641449bdf \
  --apply
```

The script updates only `repairable` findings. It reports unresolved, ambiguous, missing-file, and outside-vault findings without changing them.

## Code Organization

Scripts are organized by ownership:

- `pkos/scripts/` — plugin-shared scripts (used across multiple skills or invoked by Adam templates directly). Examples: `personal_os_config.py`, `voice-transcribe.sh`, `collect-inbox.sh`.
- `pkos/skills/{name}/scripts/` — skill-owned scripts (only invoked from that skill's SKILL.md). Example: `skills/pkos/scripts/audit_notion_obsidian_links.py` (owned by the pkos skill's Notion Links route).

## MOC (Map of Contents)

MOCs are synthesized topic pages in 80-MOCs/. They aggregate notes on a topic into:
- **Overview**: 2-3 sentence synthesis citing specific notes
- **Notes**: List of related notes with one-line summaries
- **Contradictions & Open Questions**: Detected conflicts between notes
- **Related MOCs**: Links to overlapping MOCs

MOCs are auto-created when a topic has 3+ notes without one.

## Hooks

- **SessionStart**: Reports inbox count and recent vault activity if CWD is the vault directory
