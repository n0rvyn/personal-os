---
name: migrate
description: "Migrate an external Obsidian vault into PKOS, faithful to the vault directory contract. Rebuilds the migrate capability after a prior broken run; fixes titles, nested routing, domain tags, and WeChat production routing. Triggered via /pkos migrate."
model: sonnet
allowed-tools:
  - Read
  - Bash
  - Glob
  - Grep
---

## Overview

Import an external Obsidian vault into PKOS. The configured source is `99-Obsidian`
(`~/Obsidian/PKOS/.state/migrate-sources.yaml`).

A prior migration ran broken: it slug-flattened source category names directly under
`10-Knowledge/`, captured titles as the frontmatter delimiter (`title: '---'`), wrote
no domain tags, and left a whole source directory (`Linux SRE/`, 384 notes) unwritten.
This skill replaces that capability and repairs the damage.

## Arguments

- `--source-name NAME` — a named source from `migrate-sources.yaml` (default `99-Obsidian`).
- `--source-vault PATH` — a source vault path directly.
- `--scan-only` (`--dry-run`) — report routing + discards without writing. Run first.
- `--force` — relocate the prior broken run's output to `.trash/migrate-prior-run/`,
  then re-migrate everything cleanly. This is the correct first real run here.
- `--resume` — skip notes already migrated (by content hash).

## Process

### Step 1: Scan

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/migrate/scripts/migrate.py" \
  --source-name 99-Obsidian --scan-only
```

Present to the user:
- destination breakdown (each source category nests under its type's home dir);
- the **DISCARDED** list — notes judged empty or mojibake-garbled. The user must
  eyeball this list; discard is auto but moves to `.trash/`, never deletes;
- the **review candidates** — short, unstructured notes. These are still migrated;
  the flag just marks them for an optional later quality pass.

### Step 2: Migrate

The prior run left damage, so the first real run uses `--force` — it relocates ALL
prior-migration output (everything carrying a `migrated_from` frontmatter field, not
just what `migrate-state.yaml` recorded) to `.trash/migrate-prior-run/`, then
re-migrates cleanly:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/migrate/scripts/migrate.py" \
  --source-name 99-Obsidian --force
```

A later top-up of new source notes uses `--resume` instead of `--force`. The run
writes a judgment queue at `~/Obsidian/PKOS/.state/migrate-judgment-queue.jsonl`.

### Step 3: LLM value judgment

The migration in Step 2 is mechanical (routing, titles, tags). It auto-discards only
empty and mojibake notes. This step applies the content-value judgment — read every
migrated note and discard the ones with no reusable knowledge.

Process `migrate-judgment-queue.jsonl` (one JSON object per line: `vault_path`,
`title`, `excerpt`). Read it in batches (≈100 lines per `Read`) so context stays
bounded. For each note, judge against this rubric:

- **keep** — any reusable knowledge: a command/config snippet, a how-to, a concept
  note, a reference, a genuine reflection. Short is fine — a one-line command is
  valuable. When unsure, **keep** (discard is for clear cases only).
- **discard** — no reusable value: a test/scratch note, a meaningless fragment, a
  truncated capture with nothing usable, accidental or pure-noise content, a
  near-duplicate stub.

Collect the `vault_path` of every discard verdict into a plain-text file, one path
per line:

```bash
# write the discard list to this path as you judge
DISCARDS=~/Obsidian/PKOS/.state/migrate-discards.txt
```

### Step 4: Apply discards

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/skills/migrate/scripts/migrate.py" \
  --apply-discards ~/Obsidian/PKOS/.state/migrate-discards.txt
```

This moves the judged-low-value notes to `.trash/migrate-discarded/` and drops them
from `migrate-state.yaml`. Nothing is deleted.

### Step 5: Report

Present the final summary: migrated count, auto-discarded (empty/mojibake),
LLM-discarded (low value), destination breakdown, and the `.trash/` locations
(`migrate-prior-run/`, `migrate-discarded/`).

## Routing

Faithful to `99-System/10-Directory-Contract.md`. A source category becomes a NESTED
slug directory under the type's home — never a flat top-level directory.

| Source | vault destination | `type` |
| --- | --- | --- |
| `<Category>/note.md` (knowledge) | `10-Knowledge/<category-slug>/note.md` | `knowledge` |
| `Project/`, `WorkSpace/` | `30-Projects/<category-slug>/` | `project` |
| `WeChat/Channel/<series>/` | `90-Productions/WeChat/<series>/` | `production` |
| `WeChat/Official Account/` | `90-Productions/WeChat/公众号随笔/` | `production` |

WeChat content is the user's own published articles/courses — it routes to the
`90-Productions/` archive as `type: production`, not reference or knowledge. Every
migrated note gets a `<category-slug>` tag so the cross-domain classifier can bucket
it; `migrated_from: 99-Obsidian` frontmatter records provenance.

## Discard policy

Two layers, both move to `.trash/migrate-discarded/` — never delete:

1. **Mechanical (Step 2)** — `migrate.py` auto-discards only **empty** notes (no text
   after frontmatter) and **mojibake** (CJK decoded through the wrong codec). A short
   note — a one-line command, a config snippet — is valid knowledge and is migrated.
2. **LLM value judgment (Step 3)** — every migrated note is read and discarded if it
   carries no reusable knowledge (test/scratch, meaningless fragment, truncated
   noise). Conservative: when unsure, keep.

## Notes

- Title is taken from the file NAME (cleaned), never from a `---` line.
- Ripple/MOC compilation is not run here — a bulk migration would thrash MOCs; run
  `lint` afterwards instead.
- Migration rules reference: `references/migration-rules.md`.
