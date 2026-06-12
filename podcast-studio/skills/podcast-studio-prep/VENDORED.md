# Vendored: podcast-prep

This tree is a vendored copy of the `podcast-prep` plugin, kept verbatim so
the podcast-studio plugin is self-contained and does not require the
personal-os marketplace to be installed.

## Source

- **Origin repo:** `personal-os` (SAME repo since 2026-06-12 migration; marketplace at `~/Code/Skills/personal-os`)
- **Source path:** `~/Code/Skills/personal-os/podcast-prep` (upstream skill dir is `skills/prep/`)
- **Upstream version:** 0.8.0 (upstream now at 0.10.0 — re-vendor pending, see migration dev-guide Phase 6)
- **Vendored at:** 2026-06-08; relocated into personal-os 2026-06-12
- **Still vendored after co-location** — do NOT replace with a direct dependency on `../../podcast-prep`. Rationale:
  1. **Config patch** — this copy carries the `_resolve_vault_root` rewire (Task 4-impl of the Phase 1 plan) that the upstream `podcast-prep` does not have. A direct dependency would not apply this patch and would re-introduce the broken vault-root resolution.
  2. **Upstream drift to 0.10.0** — the upstream `podcast-prep` is now at 0.10.0 (Phase 6 port-back brought `vault_contract.py` to upstream). A direct dependency would skip the documented re-vendor + patch cycle and could silently pull in 0.10.0 semantics before the vendored copy is updated.
  3. **Same-repo co-location doesn't change the calculus** — `podcast-prep` and `podcast-studio` are sibling plugins in the personal-os marketplace; vendoring prep into podcast-studio keeps the latter's runtime self-contained for the prep stage, matching how the personal-os fleet is intended to compose.

## Re-vendor procedure

The re-vendor flow is: pull byte-faithful from upstream `podcast-prep` (in
this same personal-os repo), then re-apply the local patches documented
below. Do NOT hand-edit vendored scripts in place — fix upstream and
re-vendor, except for the documented patches that only this copy carries.

1. From the personal-os repo, copy the source tree (byte-faithful) from the
   upstream `podcast-prep` plugin (in this same repo, since the 2026-06-12
   migration):
   ```
   # Upstream skill dir is `prep`; the vendored copy is renamed `podcast-studio-prep`.
   cp -R ~/Code/Skills/personal-os/podcast-prep/scripts \
         ~/Code/Skills/personal-os/podcast-studio/skills/podcast-studio-prep/scripts
   cp ~/Code/Skills/personal-os/podcast-prep/skills/prep/SKILL.md \
      ~/Code/Skills/personal-os/podcast-studio/skills/podcast-studio-prep/SKILL.md
   cp ~/Code/Skills/personal-os/podcast-prep/references/quality-rubric.md \
      ~/Code/Skills/personal-os/podcast-studio/skills/podcast-studio-prep/references/quality-rubric.md
   ```
2. Bump the upstream version in this file to match the new
   `podcast-prep` version in `podcast-prep/.claude-plugin/plugin.json`.
3. Re-apply the documented local patch — the vault-root resolution rewire
   (Task 4-impl of the Phase 1 plan). See the `_resolve_vault_root` function
   in `scripts/orchestrator.py` and the `--topic-log` argparse default in
   the same file.
4. Run the vendored tests to confirm parity:
   ```
   cd podcast-studio/skills/podcast-studio-prep && python3 -m pytest scripts/ -q
   ```

## What is NOT vendored (intentional)

- `__pycache__/` and `.pytest_cache/` from the upstream repo — build
  artifacts, not source.
- `.claude-plugin/` plugin manifest from upstream — the podcast-studio
  marketplace/plugin manifest is the active one; the vendored skill is a
  pure library, not a separately installable plugin.

## Modification policy

Once vendored, edits to the scripts in this tree should be avoided in
favor of fixing them upstream and re-vendoring. The only deliberate
modification in this copy is the vault-root resolution rewire (Task
4-impl of the Phase 1 plan), which the re-vendor procedure above is
expected to re-apply.
