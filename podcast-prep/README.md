# podcast-prep — ⛔ DEPRECATED (2026-09-22)

**Superseded by `podcast-studio/skills/podcast-studio-prep/`**, which began as a
vendored copy of this plugin and has since become a strict superset of it. No
live caller invokes this plugin. Work on the fork, not here.

Comparison that closed it out (2026-09-22): the fork leads on every differing
file (`orchestrator.py` 601 vs 531, `test_orchestrator.py` 1045 vs 911) and
carries three files this tree lacks (`exchange_ief.py`, `test_exchange_ief.py`,
`test_vault_root_resolution.py`). This tree's only exclusive content is 47 lines
of comment rewording plus the pre-patch `_resolve_vault_root` the fork
deliberately replaced. Details in
[`../podcast-studio/skills/podcast-studio-prep/VENDORED.md`](../podcast-studio/skills/podcast-studio-prep/VENDORED.md).

The runtime data directory named `podcast-prep` (`topic_log.yaml`,
`source_log.jsonl`) is **data, not this plugin** — it is untouched by this
deprecation and the fork reads the same schema.

The tree is kept for history; it is not deleted and stays installable for
anyone already depending on it.

---

Daily podcast prep orchestrator: topic dedup, angle rotation, MinHash text dedup,
PKOS serendipity, contrarian source picker. Part of the [personal-os](../) marketplace.

See `skills/prep/SKILL.md` for the user-facing skill contract.

## Status

Phase 2 of the 2026-05-19 podcast-dedup-quality engineering. See
[docs/06-plans/2026-05-19-podcast-dedup-quality-dev-guide.md](../docs/06-plans/2026-05-19-podcast-dedup-quality-dev-guide.md).
