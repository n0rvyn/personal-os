# Insight Exchange Format (IEF)

Cross-plugin protocol for exchanging producer-to-consumer data. Any plugin can produce or consume IEF files.

Two payload shapes share the same envelope:
- **Insight** (`category` ∈ analytical set): analyzed intelligence (articles, videos, research) — original IEF use case.
- **Directive** (`category: directive`): producer-to-consumer action requests (e.g., product-lens → pkos "reprioritize project X", "update verdict Y"). Body replaces the Problem/Technology/Insight/Difference template with the decision payload defined by the producer.

## File Format

IEF files are Markdown with YAML frontmatter. Required fields:

```yaml
---
id: "{YYYY-MM-DD}-{source}-{NNN}"    # Unique ID: date + source name + sequence
source: "{source_name}"                # Producer identifier (e.g., youtube, podcast, product-lens)
url: "{original_url}"                  # Canonical URL or source ref (commit hash, notion page, repo path for directives)
title: "{title}"                       # Human-readable title
significance: {1-5}                    # Importance / confidence score (integer)
tags: [{keyword1}, {keyword2}]         # 3-5 lowercase hyphenated keywords
category: "{category}"                 # One of: framework, tool, library, platform, pattern, ecosystem, security, performance, ai-ml, devex, business, community, directive
domain: "{domain}"                     # Knowledge domain (e.g., ai-ml, ios-development, product-strategy)
date: {YYYY-MM-DD}                     # Production date
read: false                            # Consumption flag (consumer sets to true)
---

# {title}

**Problem:** {what question or gap this addresses}

**Technology:** {tools, frameworks, methods involved}

**Insight:** {single most valuable takeaway}

**Difference:** {what makes this perspective unique}

---

*Selection reason: {why this was selected for export}*
```

For `category: directive` the body replaces the Insight template with:

```markdown
# {title}

**Intent:** {what the producer wants the consumer to do}

**Decision:** {concrete action, e.g., `reprioritize:project-x`, `verdict-update:y`}

**Confidence:** {0.0-1.0}

**Context:** {evidence / reasoning backing the directive}

**Targets:** {consumer-scoped identifiers the directive acts on}
```

## Naming Convention

- File name: `{id}.md` (e.g., `2026-04-05-youtube-001.md`)
- ID format: `{YYYY-MM-DD}-{source}-{NNN}` where NNN is zero-padded sequence

## Exchange Directory Convention

- Producer writes to `{exchange_dir}/{source}/` (e.g., `{exchange_dir}/youtube-scout/`, `{exchange_dir}/domain-intel/`)
- Consumer reads from the same directory via `sources.external[].path` in its config
- Consumer either deletes or archives files after successful import (producer must not assume files persist)
- Archive option: consumer may `mv` to its own tracked location (e.g., `{exchange_dir}/{consumer}/ingested/YYYY-MM/`) instead of deleting — convention is to move out of the producer's export directory so producer's next scan does not re-see it

The `exchange_dir` value is loaded from `~/.claude/personal-os.yaml` (see `docs/personal-os-spec.md`).

## Producer Responsibilities

- Write well-formed IEF files with all required fields
- Ensure `id` uniqueness within a single export batch
- Only export items above a configurable quality threshold

## Consumer Responsibilities

- Validate required fields before import
- Deduplicate against existing items (URL-based for insights, id-based for directives)
- Apply own significance threshold
- Remove source files from the producer export directory after import (delete or move to a consumer-owned archive)
- Gracefully handle missing/malformed files

## Pre-collect Convention

Consumers may optionally trigger producers before importing:
```yaml
scan:
  external:
    - name: YouTube Scout
      path: {exchange_dir}/youtube-scout
      pre_collect: /youtube-scan    # Skill to invoke before import
```
Pre-collect is best-effort: failures do not block the consumer pipeline.

## Extended Fields

Producers may add source-specific fields to frontmatter (e.g., `channel`, `duration`, `weighted_total` for YouTube). Consumers must ignore unknown fields.
