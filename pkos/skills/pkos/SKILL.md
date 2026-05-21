---
name: pkos
description: "Use when the user says 'pkos', 'knowledge base', '知识库', 'what's in my inbox', 'what do I know about', or wants to interact with their personal knowledge system. Unified entry point for status, query, ingest, and review."
user-invocable: true
model: sonnet
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
  - Write
  - WebFetch
  - Skill(pkos:harvest)
  - Skill(pkos:ingest-exchange)
  - Skill(pkos:getnote-intel)
  - Skill(pkos:getnote-import)
  - Skill(pkos:migrate)
  - Skill(pkos:lint)
---

Unified human interface for PKOS. Routes user intent to the appropriate subsystem. This is the ONLY user-facing entry point — all other PKOS skills are internal (cron/event-triggered).

## Arguments

Parse from user input (natural language routing):

- No args / `status` → Status Dashboard
- `<question or topic>` → Knowledge Query
- `ingest <url|text>` → Manual Ingest
- `ingest-exchange [--producer NAME] [--intent INTENT] [--dry-run]` → Producer Exchange Ingest
- `review` → Today's Wiki Changes
- `lint` → Latest Health Report
- `notion-links [--apply]` → Audit or repair Notion Obsidian links
- `intel [getnote]` → Get笔记 Intelligence Feed
- `getnote-import [--dry-run] [--limit N]` → Offline Get笔记 export backfill

## Routes

### Route: Status Dashboard (default)

Show PKOS system status. Collect data in parallel:

**Inbox count:**
```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/collect-inbox.sh all
```

**Recent vault activity (last 24h):**
```bash
find ~/Obsidian/PKOS/{10-Knowledge,20-Ideas,50-References,80-MOCs} -name "*.md" -mtime -1 2>/dev/null | wc -l | tr -d '[:space:]'
```

**Latest lint health score (if exists):**
```
Glob(pattern="lint-*.md", path="~/Obsidian/PKOS/70-Reviews")
```
Read the most recent one, extract the Summary section.

**Latest digest (if exists):**
```
Glob(pattern="*.md", path="~/Obsidian/PKOS/60-Digests")
```
Read the most recent one's Quick Stats section.

Present:
```
PKOS Status
  Inbox: {N} items pending
  Last 24h: +{N} notes, +{N} MOC updates
  Health: {score from latest lint, or "no lint data"}
  Latest digest: {date}
```

### Route: Knowledge Query

When user provides a topic or question:

1. Search vault frontmatter for topic matches:
   ```
   Grep(pattern="tags:.*{keyword}", path="~/Obsidian/PKOS", output_mode="files_with_matches", head_limit=10)
   ```

2. Search vault content:
   ```
   Grep(pattern="{keyword1}|{keyword2}", path="~/Obsidian/PKOS/{10-Knowledge,20-Ideas,50-References,80-MOCs}", output_mode="files_with_matches", head_limit=10)
   ```

3. Check MOCs first — if an 80-MOCs/ file matches, read it (it contains synthesized knowledge):
   ```
   Glob(pattern="**/*{keyword}*", path="~/Obsidian/PKOS/80-MOCs")
   ```

4. Read the top 5 most relevant files (MOCs first, then by match count).

5. Synthesize an answer citing specific notes. Format:
   ```
   {Synthesized answer}

   Sources:
   - [[note-title]] (10-Knowledge/) — {key point}
   - [[moc-title]] (80-MOCs/) — {overview}
   ```

6. If the answer is substantial and novel (user confirms), offer to save as a new note via vault write.

### Route: Manual Ingest

When user says `ingest <url|text>`:

1. If URL detected: fetch content via WebFetch
2. Create a temporary inbox item and invoke the inbox skill internally:
   - Classification via `pkos:inbox-processor` agent
   - Route to Obsidian + Notion
   - Trigger ripple compilation via `pkos:ripple-compiler` agent
3. Report what was created and what MOCs were updated.

### Route: Producer Exchange Ingest

When user says `ingest-exchange`, `exchange ingest`, `ingest product-lens exchange`, or `导入 exchange`:

Invoke the `ingest-exchange` skill.

Supported forms:
- `/pkos ingest-exchange`
- `/pkos ingest-exchange --producer product-lens`
- `/pkos ingest-exchange --producer product-lens --intent reprioritize`
- `/pkos ingest-exchange --dry-run`

Use this route when another plugin has already written structured artifacts into `~/Obsidian/PKOS/.exchange/` and PKOS now needs to convert them into canonical vault notes.

### Route: Review

Show today's wiki changes:

1. Find all files modified today:
   ```bash
   find ~/Obsidian/PKOS -name "*.md" -mtime -1 -newer ~/Obsidian/PKOS/.state/last-review-marker 2>/dev/null
   ```

2. Categorize changes:
   - New notes (10/20/50)
   - MOC updates (80-MOCs)
   - Digest generated (60-Digests)
   - Lint reports (70-Reviews)

3. For MOC updates, show a brief diff (what was added).

4. Update review marker:
   ```bash
   touch ~/Obsidian/PKOS/.state/last-review-marker
   ```

### Route: Lint

Show the latest lint report:

1. Find the most recent lint report:
   ```
   Glob(pattern="lint-*.md", path="~/Obsidian/PKOS/70-Reviews")
   ```

2. If found: read and display the summary + high-severity items.
3. If not found: report "No lint data. Lint runs automatically every Sunday, or invoke internally."

### Route: Notion Links

Trigger: user says "notion links", "repair notion links", "notion obsidian links", "PKOS Hub links", "修复 Notion Obsidian 链接".

Audit only:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/pkos/scripts/audit_notion_obsidian_links.py \
  --hub-page-id 32a1bde4-ddac-808b-bbca-db3641449bdf
```

Repair:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/pkos/scripts/audit_notion_obsidian_links.py \
  --hub-page-id 32a1bde4-ddac-808b-bbca-db3641449bdf \
  --apply
```

Rules:
- require `NOTION_TOKEN` from the environment; do not write tokens to config
- default mode reports only
- `--apply` updates only `repairable` findings with a unique local note match
- unresolved, missing-file, ambiguous, and outside-vault findings remain report-only

### Route: Get笔记 Intelligence Feed

Trigger: user says "intel getnote", "getnote intel", "刷新博主"

Invoke `getnote-intel` skill with optional `--source blogger|live`.

### Route: harvest

Trigger: user says "harvest", "scan projects", "import knowledge", "收割"

Invoke the `harvest` skill:
- No args → full harvest across all projects
- `--dry-run` → preview only
- `--project {name}` → single project
- `--force` → re-import all
- `--skip-ripple` → skip MOC compilation (faster for bulk)

### Route: migrate

Trigger: user says "migrate", "import vault", "迁移", "migrate --scan-only"

Invoke the `migrate` skill:
- `--scan-only` (or `--dry-run`) → scan the source vault and present the migration
  report (destinations, discard list, review candidates) without writing files
- `--source-name {name}` → use a named source from `migrate-sources.yaml`
- `--source-vault {path}` → specify source vault path directly
- `--force` → relocate the prior run's output to `.trash/migrate-prior-run/`, then
  re-migrate everything cleanly (the correct first real run — the prior migrate broke)
- `--resume` → skip notes already migrated (by content hash)

To add a new source vault, edit `~/Obsidian/PKOS/.state/migrate-sources.yaml`.

### Route: getnote-import

Trigger: user says "getnote-import", "import getnote export", "导入 Get笔记 导出",
"getnote 回填", "回填 Get笔记"

Invoke the `getnote-import` skill — a one-shot offline backfill of a getnote HTML
export archive into the vault (the API is rate-limited, so a full history is
exported from the getnote app and ingested offline):
- `--dry-run` → parse and report routing without writing (run this first)
- `--limit N` → process at most N notes (staged run)
- `--export-dir DIR` → override the export archive path

Distinct from `intel getnote` (ongoing blogger feed) and the `inbox` skill's
getnote source (incremental capture) — this route is the bulk historical backfill.
