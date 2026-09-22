# podcast-studio-prep — formerly vendored from `podcast-prep`

**Status as of 2026-09-22: this tree is the source of truth.** The upstream
`podcast-prep` plugin it was vendored from is deprecated and has no live
caller. Nothing needs to be pulled from it, and nothing needs to be ported
back to it. Edit this tree directly.

## Why the vendoring relationship ended

The relationship inverted. This copy was vendored from `podcast-prep` @ 0.8.0
on 2026-06-08 and the plan was to keep re-vendoring from upstream. Instead the
fork kept moving and upstream stopped (upstream's last content change was
2026-06-12). A file-by-file comparison on 2026-09-22:

| file | upstream `podcast-prep` | this tree |
|---|---|---|
| `orchestrator.py` | 531 | **601** |
| `test_orchestrator.py` | 911 | **1045** |
| `source_log.py` | 94 | **105** |
| `test_source_log.py` | 119 | **144** |
| `topic_log.py` | 108 | **109** |
| `cross_domain.py` | 566 | 565 |
| `exchange_ief.py` + `test_exchange_ief.py` | absent | **present** |
| `test_vault_root_resolution.py` | absent | **present** |

Upstream-exclusive content totals 47 lines across four files, and all of it is
either comment rewording or the **old** `_resolve_vault_root` implementation
that this copy deliberately patched out (the vault-root rewire, Task 4-impl of
the Phase 1 plan). `vault_contract.py` is byte-identical in both — the Phase 6
port-back completed, and the fork has moved on since.

So the three reasons the previous version of this file gave for staying
vendored are all void: the config patch is no longer at risk (there is nothing
to re-vendor over it), the "upstream drift to 0.10.0" is backwards (upstream is
behind), and the co-location argument is moot now that upstream is retired.

## History

- **Origin:** `podcast-prep` plugin, `personal-os` marketplace
- **Vendored at:** 2026-06-08 from upstream 0.8.0
- **Relocated** into personal-os alongside upstream: 2026-06-12
- **Upstream deprecated:** 2026-09-22 (`marketplace.json`: `"deprecated": true`,
  `"replacedBy": "podcast-studio/podcast-studio-prep"`)

## Runtime data is separate

The prep runtime writes `topic_log.yaml` / `source_log.jsonl` under a directory
literally named `podcast-prep` (legacy layout: `{exchange_dir}/podcast-prep/`;
current layout: the `output_dir` root resolved from
`~/.podcast-studio/config.yaml`). That **directory name is data, not the
plugin** — retiring the upstream plugin does not touch it, and this tree reads
the same schema.

## Modification policy

Edit the scripts here directly. There is no upstream to fix instead, and no
re-vendor cycle to respect. Run the tests after changes:

```
cd podcast-studio/skills/podcast-studio-prep && python3 -m pytest scripts/ -q
```

## What was never vendored (unchanged)

- `__pycache__/` and `.pytest_cache/` — build artifacts, not source.
- Upstream's `.claude-plugin/` manifest — this skill is a library inside the
  podcast-studio plugin, not separately installable.
