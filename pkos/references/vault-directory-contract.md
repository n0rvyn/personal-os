# Vault Directory Contract — Framework Template

> This is the **framework template**. The live instance lives in the user's vault at
> `<vault>/99-System/10-Directory-Contract.md` and **takes precedence**. A fork copies
> this template into their vault's `99-System/` and adapts it.

The contract is the single source of truth for what each Obsidian vault directory
holds. Every ingestion path routes content by it; every recall reads content by it.
Ingestion/recall logic consults the contract — not the `source` frontmatter field,
not ad-hoc keyword guesses.

## Content types

`type:` frontmatter is the canonical classifier; each type has one home directory.

| `type` | Home | Holds |
|--------|------|-------|
| `knowledge` | `10-Knowledge/` | Neutral, verifiable knowledge — facts, how-tos, technical insight |
| `idea` | `20-Ideas/` | The user's own ideas + reflective viewpoints (subdivided — see below) |
| `reference` | `50-References/` | External content saved as-is — article/book/video notes, intel captures |
| `person` | `40-People/` | Key-people profiles |
| `project` | `30-Projects/` | Active project notes |
| `podcast` | `90-Podcasts/` | One archived note per generated podcast episode |

Other directories: `00-Inbox/` (manual capture), `60-Digests/`, `70-Reviews/`,
`80-MOCs/`, `99-System/` (system docs incl. the contract).

## 20-Ideas subdivision

`20-Ideas/` = 想法与观点, split two ways:
- `20-Ideas/产品想法/` — things to BUILD (product/feature concepts)
- `20-Ideas/观点心得/` — things the user THINKS (reading reflections, stances)

Both carry `type: idea`; the subdirectory is the finer split.

## getnote split rule

A getnote capture with both an excerpt (摘抄) and the user's reflection (个人思考) is
split at ingestion: excerpt → `50-References/`, reflection → `20-Ideas/观点心得/`.

## Recall contract

- `self_past_candidates` ← `20-Ideas/观点心得/` + `90-Podcasts/` (past on-record stances)
- `cross_domain_candidates` ← `10-Knowledge/` + `20-Ideas/`, bucketed by domain
- `50-References/` is supporting context, never a stance source
