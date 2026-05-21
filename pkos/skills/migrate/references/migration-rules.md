# Migration Rules

Reference for `migrate.py`.

## Title

Taken from the **file name** stem, cleaned of leading/trailing markdown noise
(`**`, `#`, whitespace). Never from a frontmatter `---` line or a bold-wrapped first
body line — that is what produced `title: '---'` in the prior broken run. Source
notes mostly have no frontmatter (≈21 of 1135), so the filename is the reliable title.

## Routing

A source category directory becomes a NESTED slug directory under the destination
type's home — `Linux SRE/x.md` → `10-Knowledge/linux-sre/x.md`. The prior run wrote a
flat top-level `linux-sre/`, which is wrong.

| Source path | type | destination |
| --- | --- | --- |
| `<Category>/...` (generic) | `knowledge` | `10-Knowledge/<category-slug>/` |
| `Project/...`, `WorkSpace/...` | `project` | `30-Projects/<category-slug>/` |
| `WeChat/Channel/<series>/...` | `production` | `90-Productions/WeChat/<series>/` |
| `WeChat/Official Account/...` | `production` | `90-Productions/WeChat/公众号随笔/` |

`WeChat/Channel/丹尼尔斯路步方程式` is normalized to `丹尼尔斯跑步方程式` (a source typo).

WeChat routing is handled in code and overrides whatever `migrate-sources.yaml`
classification rules say — the user's own published work is production-archive
content, never reference/knowledge.

## Tags

Every migrated note gets a `<category-slug>` tag (e.g. `linux-sre`) plus any tags the
source note already declared in frontmatter. The category slug is the signal the
cross-domain classifier uses to bucket the note by domain.

## Value judgment (discard policy)

A note is **discarded** (moved to `.trash/migrate-discarded/`, never deleted) only if:

- it is **empty** — no real content after frontmatter; or
- it is **mojibake** — a high density of Latin-1 supplement characters with little
  real CJK, i.e. CJK text decoded through the wrong codec.

A short note is **not** discarded. A one-line command or a config snippet is valid
knowledge. Code-block content counts toward "is this note empty?". A short,
unstructured note is migrated and flagged `review` for an optional later quality pass.

## Prior-run cleanup (`--force`)

`--force` reads `migrate-state.yaml`, relocates every `vault_path` it recorded to
`.trash/migrate-prior-run/`, removes the now-empty directories left under
`10-Knowledge/` etc., resets the state, and re-migrates everything cleanly.
