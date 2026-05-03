---
name: scan
description: "Use when the user says 'scan', 'collect intel', 'run scan', or when invoked by cron. Orchestrates the full domain intelligence pipeline: collect from sources, filter duplicates, analyze insights, detect convergence signals, store results. Primary cron target."
model: sonnet
user-invocable: true
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
  - Write
---

## Overview

Pipeline orchestrator for domain-intel. Reads config, dispatches collection and analysis agents, applies 3-tier filtering, stores results, and detects cross-source convergence signals.

Uses sonnet because the 3-tier filter requires precise arithmetic (Jaccard similarity, weighted keyword scoring) and convergence signal detection requires topic clustering — haiku is unreliable for these.

Designed for **automated cron execution** — minimal output, no interactive prompts, fail-safe.

### Cron Mode Detection

If the invocation prompt contains `[cron]`:
- Set `CRON_MODE = true`
- All parameters from config (no AskUserQuestion)
- Pre-collect failures are logged but never block the pipeline

## Process

### Step 0: Resolve Working Directory

```
Bash(command="pwd")
```

Store the result as `WD`. **All file paths in this skill are relative to `WD`** — prefix every `./` path with `{WD}/` when calling Read, Write, Glob, or Grep. Bash commands can use relative paths as-is.

### Step 1: Load Config

1. Read `{WD}/config.yaml`
   - If file does not exist → output `[domain-intel] Not initialized. Run /intel setup in this directory.` → **stop**

2. Extract from config:
   - `domains[]` — each with name
   - `sources.github` — enabled flag, languages, min_stars
   - `sources.rss[]` — list of {name, url}
   - `sources.official[]` — list of {name, url, paths[]}
   - `sources.external[]` — list of {name, path, pre_collect (optional)}
   - `sources.producthunt` — enabled flag, client_id, client_secret, topics[]
   - `scan.max_items_per_source` (default: 20)
   - `scan.significance_threshold` (default: 2)
   - `scan.auto_digest` (default: false)
   - `scan.auto_intel_sync` (default: false) — when true, invoke `/intel-sync` after Step 8.5. Resolution order: config.yaml `scan.auto_intel_sync` > env `AUTO_INTEL_SYNC` (truthy values: `1`, `true`, `yes`) > default false. Requires `pkos` plugin loaded for the role.
   - `ief_output_dir` (optional; top-level field) — profile override for IEF output; see Step 1.5

3. Read `{WD}/LENS.md` if it exists:
   - Parse YAML frontmatter → extract `figures[]` and `companies[]`
   - Extract the markdown body (everything after frontmatter) → store as `lens_context`
   - Extract "What I Don't Care About" items from body → use as additional blacklist terms in Tier 3
   - If LENS.md does not exist → proceed without it (scan works without LENS)

4. Get today's date and month:
   ```
   date +%Y-%m-%d
   date +%Y-%m
   ```

5. Resolve IEF output directory (precedence: profile `ief_output_dir` in `config.yaml` → `{exchange_dir}/domain-intel/`):

   ```bash
   IEF_DIR=$(python3 -c "
   import yaml, os
   from pathlib import Path
   cfg = yaml.safe_load(open('config.yaml')) or {}
   override = cfg.get('ief_output_dir', '').strip()
   if override:
       print(str(Path(os.path.expanduser(override)).resolve()))
   else:
       import subprocess
       ex = subprocess.check_output(['python3', os.environ['CLAUDE_PLUGIN_ROOT'] + '/scripts/personal_os_config.py', '--get', 'exchange_dir']).decode().strip()
       print(f'{ex}/domain-intel')
   ")
   mkdir -p "$IEF_DIR/{YYYY-MM}"
   ```

   Store the resolved absolute path as `IEF_DIR`. **All newly produced IEF insight writes in this scan land under `{IEF_DIR}/{YYYY-MM}/`**. The legacy `{WD}/insights/` location remains readable for pre-migration data but is not written to by this step.

6. Resolve `scan.auto_intel_sync` (env var fallback, mirrors auto_digest pattern):
   ```bash
   # Read auto_intel_sync from config.yaml or fall back to env var AUTO_INTEL_SYNC
   AUTO_INTEL_SYNC_CFG=$(yq '.scan.auto_intel_sync // ""' "${WD}/config.yaml" 2>/dev/null || echo "")
   if [[ -n "${AUTO_INTEL_SYNC_CFG}" ]]; then
     AUTO_INTEL_SYNC="${AUTO_INTEL_SYNC_CFG}"
   fi
   # Normalize: 1 / true / yes (any case) → "true"; everything else → "false"
   case "${AUTO_INTEL_SYNC,,}" in
     1|true|yes) AUTO_INTEL_SYNC="true" ;;
     *) AUTO_INTEL_SYNC="false" ;;
   esac
   ```

7. Read `{WD}/state.yaml` if it exists (for stats tracking).

### Step 1.5: Pre-collect External Sources

If `sources.external[]` is empty or not defined → skip to Step 2.

For each external source that has a `pre_collect` field:
1. Log: `[domain-intel] Pre-collecting: {name} via {pre_collect}`
2. Invoke the skill specified in `pre_collect` (e.g., `/youtube-scan`)
   - If in `[cron]` mode: append `[cron]` to the skill invocation
3. If the skill fails or is unavailable → log warning: `[domain-intel] Pre-collect failed for {name}: {reason}. Continuing without it.` → continue to next external source
4. Pre-collect is best-effort: failures never block the main scan pipeline

### Step 2: Dispatch source-scanner

Dispatch the `source-scanner` agent with:
- **sources**: the full sources block from config
- **domains**: all domain entries (name only — scanner uses these for search queries)
- **figures**: from LENS.md frontmatter (or empty list if no LENS.md)
- **companies**: from LENS.md frontmatter (or empty list if no LENS.md)
- **date**: today's date
- **max_items_per_source**: from config
- **rss_feeds**: list of URLs from `sources.rss[].url` (for source signal detection)
- **browser_fallback**: from config `scan.browser_fallback` (default: false)
- **fallback_script_path**: (only if browser_fallback is true) resolve via `Bash(command="echo ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_rendered.py")` and pass the absolute path. If `CLAUDE_PLUGIN_ROOT` is empty, set `browser_fallback` to false and log a warning.
- **fetch_script_path**: resolve via `Bash(command="echo ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_url.py")` and pass the absolute path. This is always required — it replaces WebFetch for all page fetching (with explicit timeout control).
- **producthunt_script_path**: resolve via `Bash(command="echo ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_producthunt.py")` and pass the absolute path.
- **producthunt_config**: from config `sources.producthunt` (if present). Pass the full block: `enabled`, `client_id`, `client_secret`, `topics[]`. If the section is missing or `enabled` is false, omit this input.

Wait for completion. The agent returns:
```yaml
items:
  - url, title, source, snippet, metadata, collected_at
failed_sources:
  - url, source_type, error
source_signals:
  - type, value, reason
stats:
  github: N, producthunt: N, rss: N, official: N, figure: N, company: N, failed: N, total: N
```

Save `source_signals` for merging in Step 6.5.

If total items == 0 AND `sources.external[]` is empty or not defined → output `[domain-intel] Scan complete — no items collected. Check source configuration.` → update state → **stop**

If total items == 0 AND `sources.external[]` is defined → log `[domain-intel] No items from built-in sources. Proceeding to external import.` → skip Steps 3-5 → jump to Step 5.5.

Step 2.5: SimHash Near-Dup Detection
--------------------------------------

After the source-scanner returns the flat `items[]` list, run SimHash near-duplicate detection **before** any source-type grouping (per DP-003: dedup on the flat list to catch cross-source duplicates — e.g., the same release announced via RSS + GitHub + official simultaneously).

Resolve the script path:
```
Bash(command="echo ${CLAUDE_PLUGIN_ROOT}/scripts/simhash_dedup.py")
```

For each item in `items[]`:
1. Compute weighted fingerprint: `python3 {simhash_script} fingerprint --title "{item.title}" --content "{item.snippet}"`
2. Check against the persistent seen store: `python3 {simhash_script} check --fp {fp} --state-dir ~/.personal-os`
   - If `is_seen` returns True (Hamming distance <= 3 to any stored fp): drop the item, add to `simhash_dropped[]` with reason `"near-dup of {existing_id}"`
   - If `is_seen` returns False: add fingerprint to seen store: `python3 {simhash_script} add --id {item_id} --fp {fp} --state-dir ~/.personal-os`
3. Track: `after_simhash = N` (count of items remaining after SimHash dedup)

**SimHash dedup rationale:** Computed over the flat fetched-items list before source-type grouping so that cross-source near-dups (same release announced via multiple feeds simultaneously) are collapsed before they enter parallel-dispatch groups. This is the Lumina pattern and prevents redundant analysis of near-identical content from different sources.

Items dropped by SimHash accumulate into the eventual `dropped[]` return with reason `"near-dup of {existing_id}"`.

#### BM25 Corpus Build (pre-dedup)

Before applying filters, build the BM25 corpus from all post-SimHash items so keyword relevance scores are available throughout Step 3 and Step 4:

Resolve the script path:
```
Bash(command="echo ${CLAUDE_PLUGIN_ROOT}/scripts/bm25_relevance.py")
```

1. Write all post-SimHash item text to a temp file for corpus building:
   ```
   Bash(command="python3 -c \"import json; items=[{...}]; open('/tmp/bm25_corpus.json','w').write(json.dumps([{'id':i['id'],'text':(i.get('title','')+' '+i.get('snippet','')).strip()} for i in items]))\"")
   ```
2. Build corpus and compute per-item keyword relevance scores against `domain_intel.keywords` from config (default: empty list → skip BM25):
   ```
   Bash(command="python3 {bm25_script} --corpus /tmp/bm25_corpus.json --keywords \"{keywords joined by space}\" --output /tmp/bm25_scores.json")
   ```
3. Load scores into a lookup: `scores[id] = keyword_relevance (0.0-1.0)`
4. Attach `keyword_relevance` to each item in the pipeline: `item.keyword_relevance = scores.get(item.id, 0.0)`
5. If no keywords configured or corpus is empty: skip BM25, set `keyword_relevance = 0` for all items

**BM25 rationale:** In-memory per-scan; corpus rebuilt per scan is cheap (DP-A4). Complement gate: items pass Stage 1 if `confidence >= threshold OR keyword_relevance >= 0.7` (DP-A5).

#### Tier 1: URL Deduplication

For each item, normalize the URL:
- Strip protocol (http:// or https://)
- Lowercase the hostname
- Remove trailing slash
- Remove query parameters matching `utm_*`, `ref=`, `source=`

**Regex-escape the normalized URL** before using as Grep pattern: replace `.` with `\\.`, `+` with `\\+`, `?` with `\\?`, `[` with `\\[`, `]` with `\\]`.

Check if the normalized URL exists in **recent** insight files only (current month + previous month). Search BOTH the new IEF output dir AND the legacy `{WD}/insights/` so pre-migration and post-migration files are both considered:
```
Grep(pattern="{escaped_url}", path="{IEF_DIR}/{YYYY-MM}/", output_mode="files_with_matches", head_limit=1)
Grep(pattern="{escaped_url}", path="{WD}/insights/{YYYY-MM}/", output_mode="files_with_matches", head_limit=1)
```
If current day is within the first 7 days of the month, also check previous month (only if it exists):
```
Glob(pattern="{IEF_DIR}/{PREV-YYYY-MM}/*.md", head_limit=1)
Glob(pattern="{WD}/insights/{PREV-YYYY-MM}/*.md", head_limit=1)
```
If either glob returns results:
```
Grep(pattern="{escaped_url}", path="{IEF_DIR}/{PREV-YYYY-MM}/", output_mode="files_with_matches", head_limit=1)
Grep(pattern="{escaped_url}", path="{WD}/insights/{PREV-YYYY-MM}/", output_mode="files_with_matches", head_limit=1)
```

Remove items whose URL already exists. Track: `after_url_dedup = N`

#### Tier 2: Title Deduplication

Get titles from recent insight files (current month + previous month if applicable). Read both new and legacy dirs:
```
Grep(pattern="^title:", path="{IEF_DIR}/{YYYY-MM}/", output_mode="content")
Grep(pattern="^title:", path="{WD}/insights/{YYYY-MM}/", output_mode="content")
```
If within first 7 days and `{PREV-YYYY-MM}` subdirs exist (checked via Glob in Tier 1), also check previous month.

For each remaining item, compare its title against existing titles:
- Lowercase both titles
- Split into words, remove common stop words (the, a, an, is, of, for, in, on, to, and, with)
- Calculate word overlap: `|intersection| / |union|`
- If overlap > 0.80 → mark as duplicate

Remove duplicates. Track: `after_title_dedup = N`

#### Tier 3: Relevance Scoring

For each remaining item, compute relevance score using domain names and LENS context:

```
score = 0

# Domain name matching — item title/snippet matches a configured domain name
For each domain in domains:
  if domain.name appears in item.title OR item.snippet (case-insensitive):
    score += 1

# LENS "What I Don't Care About" blacklist (if LENS.md exists)
For each anti_interest extracted from LENS.md body:
  if anti_interest appears in item.title OR item.snippet (case-insensitive):
    score -= 3

# Source-type baseline — figure, company, and producthunt items have inherent relevance:
# figure/company were explicitly requested via LENS.md;
# producthunt items were already topic-filtered by the API script
if item.source == "figure" OR item.source == "company" OR item.source == "producthunt":
  score += 1
```

Drop items with score <= 0. Sort remaining by score descending.

Take top N items where N = min(`max_items_per_source` * number of enabled source types, **30**).

Hard cap at 30 total items to stay within agent turn budgets.

Track: `after_keyword = N`

#### ContentDiff: Official Sources Only

After Tier 3 filtering, apply content-change detection to `official` source items only. This prevents re-analyzing unchanged changelog/blog pages.

Resolve the script path:
```
Bash(command="echo ${CLAUDE_PLUGIN_ROOT}/scripts/content_diff_store.py")
```

For each item with `source == "official"`:
1. Fetch the page content (or use the snippet if the source-scanner already collected it)
2. Check for changes: `python3 {diff_script} --check --url "{item.url}" --content "{content}" --state-dir ~/.personal-os`
   - If `change_type == "unchanged"`: drop the item, add to `diff_dropped[]` with reason `"no-content-change"`
   - If `change_type == "content_updated"`: replace the item's content/snippet with the `added_lines` only so the analyzer sees just the delta
   - If `change_type == "new_content"`: proceed unchanged
3. Track: `after_content_diff = N` (count of official items remaining after ContentDiff; track separately from the main tier counts)

**ContentDiff rationale:** Official sources (changelogs, blogs) are re-scraped on every scan; most return unchanged content. ContentDiff detects unchanged pages and drops them before analysis, saving LLM tokens and eliminating noise from stale content.

Items dropped by ContentDiff accumulate into the eventual `dropped[]` return with reason `"no-content-change"`.

### Step 4: Dispatch insight-analyzer (Two-Stage)

Group filtered items by source type (github, producthunt, rss, official, figure, company).

For each non-empty group, dispatch one `insight-analyzer` agent with:
- **items**: the filtered items for that source type (each item includes `keyword_relevance` attached in the BM25 corpus build step above)
- **keyword_relevance**: (optional, default 0) the average or representative BM25 keyword relevance score (0.0-1.0) for this group, computed by the scan pipeline. If BM25 is not enabled, omit this field.
- **source_type**: github | producthunt | rss | official | figure | company
- **domains**: domain definitions from config
- **significance_threshold**: from config
- **date**: today's date
- **lens_context**: the LENS.md body content (or omit if no LENS.md)

Dispatch all groups **in parallel** (multiple Agent tool calls in one message).

Wait for all to complete. If a single analyzer fails, log the failure and continue with the results from the others.

**Two-stage screening:** The insight-analyzer applies Stage 1 (quick screen) to each item using numeric `confidence` + the passed `keyword_relevance` score via the `screen_gate.py` complement gate. Items below threshold (`confidence < 0.6 AND keyword_relevance < 0.7`) are emitted as `dropped[]` entries with `reason: "low-confidence-screen"`. Only items passing Stage 1 proceed to Stage 2 (deep analysis). This means the `dropped[]` returned by the analyzer may include both Stage 1 screen failures and Stage 2 rejections — both land in the unified `dropped[]` array.

Each successful analyzer returns:
```yaml
insights:
  - id, source, url, title, significance, tags, category, domain,
    problem, technology, insight, difference, selection_reason
dropped:
  - url, reason   # reason may be "low-confidence-screen" or Stage 2 rejection text
```

### Step 5: Store Insights

Merge results from all analyzers. For each insight with `significance >= significance_threshold`:

1. Verify the ID doesn't collide with existing files. If it does, increment the sequence number.

2. Write insight file to `{IEF_DIR}/{YYYY-MM}/{id}.md` (the IEF output dir resolved in Step 1.5):

```markdown
---
id: {id}
source: {source}
url: "{url}"
title: "{title}"
significance: {N}
tags: [{tags joined by comma}]
category: {category}
domain: {domain}
date: {YYYY-MM-DD}
read: false
---

# {title}

**Problem:** {problem}

**Technology:** {technology}

**Insight:** {insight}

**Difference:** {difference}

---

*Selection reason: {selection_reason}*
```

Track: `stored = N`

### Step 5.5: Import External Insights

If `sources.external[]` is empty or not defined → skip to Step 6.

For each external source in `sources.external[]`:
1. Resolve `~` in path to absolute path: `Bash(command="echo {path}")`
2. Glob: `{resolved_path}/*.md`
   - If path doesn't exist or no files found → log: `[domain-intel] External source {name}: no files at {path}` → skip
3. For each `.md` file:
   a. Read and parse YAML frontmatter
   b. Validate required fields: `id`, `source`, `url`, `title`, `significance`, `date`
      - If missing required fields → log warning, delete the source file, skip
   c. Check URL deduplication against already-stored insights (same Tier 1 logic)
      - If duplicate → delete the source file, skip
   d. Check significance >= `scan.significance_threshold`
      - If below threshold → delete the source file, skip
   e. Extract month from the file's `date` field (not current scan month): `{file_YYYY-MM}`
      - `Bash(command="mkdir -p {IEF_DIR}/{file_YYYY-MM}")`
   f. Copy file to `{IEF_DIR}/{file_YYYY-MM}/{id}.md`
      - If ID collides with existing file → increment sequence number in ID
   g. Delete the source file (consumed)
4. Track: `imported = N`
5. Log: `[domain-intel] Imported {N} external insights from {name}`

Include imported insights in the pool for Step 6 (Convergence Signal Detection) and Step 6.5 (Lens Signal Collection).

### Step 6: Convergence Signal Detection

Read all insights stored today (from Step 5 and Step 5.5 combined). Glob `{IEF_DIR}/*/` AND `{WD}/insights/*/` for files with today's date prefix `{YYYY-MM-DD}-*` (legacy dir is included so that profiles that override `ief_output_dir` back to `{WD}/insights/` still behave consistently).

Group by normalized topic:
- Extract the primary tag (first tag) + category as topic key
- Also group by similar problem descriptions: two insights are similar if their `problem` fields share 2+ non-stop-words (using the same stop word list as Tier 2)

For each topic that appears across 2+ different source types (e.g., github + rss):

Write a convergence signal file to `{IEF_DIR}/{YYYY-MM}/{YYYY-MM-DD}-convergence.md`:

```markdown
---
id: {YYYY-MM-DD}-convergence
type: signal
date: {YYYY-MM-DD}
---

# Convergence Signals — {YYYY-MM-DD}

| Topic | Sources | Insight IDs | Summary |
|-------|---------|-------------|---------|
| {topic} | {source1}, {source2} | {id1}, {id2} | {1-sentence cross-source synthesis} |
```

If no convergence detected, skip this file. Track: `convergence_signals = N`

### Step 6.5: Lens Signal Collection

Skip this step if LENS.md does not exist.

Check today's stored insights for evolution signals — topics or entities that appear frequently but aren't reflected in LENS.md.

1. **New interest detection**: Extract all tags from today's insights with significance >= 4. If any tag appears 3+ times but is NOT mentioned in LENS.md "What I Care About" section → record as `new-interest` signal.

2. **New figure detection**: Scan `problem`, `technology`, `insight`, and `difference` fields across today's insights. Look for capitalized multi-word names that appear to reference a person (e.g., "Andrej Karpathy", "Tim Cook"). Exclude known technical terms (framework names, language names, domain names). If a person name appears in 2+ insights and is NOT in LENS.md `figures[]` frontmatter → record as `new-figure` signal. This is best-effort detection; false negatives are acceptable.

3. **New company detection**: Same field scan as above. Look for capitalized names that appear to reference an organization or company (e.g., "Mistral AI", "Hugging Face"). If an organization name appears in 2+ insights and is NOT in LENS.md `companies[]` frontmatter → record as `new-company` signal. Best-effort; false negatives acceptable.

4. **New RSS detection**: Group today's stored insights by URL domain (extract hostname from `url` field). If 3+ insights with significance >= 4 share the same URL domain, and that domain is NOT in `sources.rss[].url` or `sources.official[].url` → record as `suggest-rss` signal with value = the domain URL.

5. **New domain detection**: Group today's insights by primary tag (first tag). If a tag appears on 3+ insights but does NOT match any `domains[].name` (case-insensitive) → record as `suggest-domain` signal.

6. **Merge source-scanner signals**: Append any `source_signals` returned by the source-scanner in Step 2 (suggest-rss, suggest-official-path).

7. Append all signals to `{WD}/.lens-signals.yaml`:
   ```yaml
   - date: YYYY-MM-DD
     type: new-interest  # or new-figure, new-company, suggest-rss, suggest-official-path, suggest-domain
     value: "{tag, name, or URL}"
     evidence: [insight IDs or source description]
   ```

   If `{WD}/.lens-signals.yaml` doesn't exist, create it. If it does, append to the existing list.

Track: `lens_signals = N`

### Step 7: Update State

Write `{WD}/state.yaml`:

```yaml
last_scan: "{YYYY-MM-DD}T{HH:MM:SS}"
total_insights: {previous_total + stored + imported}
total_scans: {previous_scans + 1}
last_scan_stats:
  collected: {raw items from scanner}
  after_simhash: {N}          # after SimHash near-dup dedup
  after_url_dedup: {N}
  after_title_dedup: {N}
  after_keyword: {N}
  after_content_diff: {N}      # after ContentDiff (official sources only)
  screen_dropped: {N}          # dropped by Stage 1 low-confidence-screen gate
  analyzed: {sent to analyzers}
  stored: {above threshold}
  imported: {N}  # external insights imported
  convergence_signals: {N}
  lens_signals: {N}
  failed_sources: {N}
```

### Step 8: Report

Output a `--scan-stats` summary showing counts at each pipeline stage:

```
[domain-intel] Scan complete — {YYYY-MM-DD}
--scan-stats
  fetched:         {collected}
  simhash-dropped: {collected - after_simhash}
  url-dedup-dropped: {after_simhash - after_url_dedup}
  title-dedup-dropped: {after_url_dedup - after_title_dedup}
  unchanged-dropped: {after_keyword - after_content_diff}  (official sources, ContentDiff)
  screen-dropped:   {screen_dropped}  (Stage 1 low-confidence gate)
  final IEF:        {stored}
  Analyzed:         {analyzed}
  Convergence signals: {N}
  By domain: {domain1}: {N}, {domain2}: {N}
  Failed sources: {N}
```

If failed_sources > 0, list them.

If imported > 0, include in summary:
```
  External imports: {source1}: {N}, {source2}: {N}
```

If lens_signals > 0, append:
```
  LENS evolution signals: {N} (run /intel evolve to review)
```

### Step 8.5: Auto-Digest

If `scan.auto_digest` is `false` or not defined → skip.

If `stored + imported == 0` → skip (nothing new to digest).

Invoke `/digest` for today's date (daily mode).
- If in `[cron]` mode: append `[cron]` to the invocation
- If digest succeeds → output: `[domain-intel] Auto-digest generated. See digests/ directory.`
- If digest fails → log warning: `[domain-intel] Auto-digest failed: {reason}`. Do not fail the scan.

### Step 8.6: Auto Intel-Sync (cross-plugin)

If `$AUTO_INTEL_SYNC != "true"` → skip.

If `stored + imported == 0` → skip (nothing new to sync).

Invoke `/intel-sync` (the pkos plugin skill that imports new IEF insights into the PKOS Obsidian vault).
- This step requires the executing role to have BOTH `domain-intel` AND `pkos` plugins available.
  If `/intel-sync` is not found, log warning: `[domain-intel] Auto intel-sync requested but pkos:intel-sync not available. Skipping. Make sure the role has both plugins or use the standalone pkos-intel-sync template.` and proceed without failing.
- If in `[cron]` mode: append `[cron]` to the invocation
- If intel-sync succeeds → output: `[domain-intel] Auto intel-sync complete. See PKOS vault.`
- If intel-sync fails → log warning: `[domain-intel] Auto intel-sync failed: {reason}`. Do not fail the scan.

**Ordering note:** Step 8.6 runs AFTER Step 8.5 — auto-digest first (so the digest sees today's pre-sync state), then intel-sync. This mirrors the ordering established by the existing `pkos-domain-scan` → `pkos-intel-sync` chain (sync runs after scan, not before digest).

## Error Handling

- If source-scanner returns 0 items: report and stop gracefully
- If all insight-analyzers return 0 insights above threshold: report "no significant insights found" and stop
- If a single analyzer fails: report the failure, continue with others
- Never leave state.yaml in an inconsistent state — write it as the last step
