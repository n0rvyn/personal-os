# Vault Directory Contract (podcast-studio recall)

`podcast-studio` recall (`cross_domain` / `self_past`) reads notes from specific
Obsidian vault directories. This file documents the **default** layout it expects.

> **The live vault contract takes precedence.** If your vault has its own
> `<vault.root>/99-System/10-Directory-Contract.md`, podcast-studio reads the recall
> directories from *that* file at runtime (`skills/podcast-studio-prep/scripts/vault_contract.py`).
> This document is only the fallback default used when no such file exists. There is
> **no cross-plugin dependency** — recall consults the vault's own data, not another plugin.

## Config

Set `vault.root` in `~/.podcast-studio/config.yaml` to your Obsidian/PKOS vault root
(e.g. `~/Obsidian/PKOS`). Recall and the directory contract resolve relative to it.
When `vault.root` is unset, recall falls back to `vault.subjective_dir`.

## Default recall directories (the fallback)

Notes are classified by their location under the vault root:

| Recall channel | Reads from | Holds |
|----------------|-----------|-------|
| `self_past_candidates` | `20-Ideas/观点心得/` + `90-Productions/Podcasts/` | the user's recorded viewpoints + past on-record podcast stances — what 达芬奇 debates against |
| `cross_domain_candidates` | `10-Knowledge/` + `20-Ideas/` | neutral knowledge + the user's ideas, bucketed by domain for cross-domain synthesis |

Notes need a `created:` frontmatter date to be eligible. `50-References/` (external
content saved as-is) is supporting context, never a stance source, and is not read by
recall.

## Live contract format (parsed at runtime)

If present, `<vault.root>/99-System/10-Directory-Contract.md` is parsed for its
`## Recall contract` section. The reader extracts backtick-wrapped directory paths
from the `self_past_candidates ← …` and `cross_domain_candidates ← …` bullets, dropping
any directory after a `NOT` / `never` exclusion marker. On a missing file or unparseable
section it falls back to the defaults above — recall never crashes on a malformed doc.
