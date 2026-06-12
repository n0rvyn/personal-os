# Vendored: tts-toolkit

This tree is a vendored copy of the `tts-toolkit` plugin, kept verbatim so
the podcast-studio plugin is self-contained and does not require the
personal-os marketplace to be installed.

## Source

- **Origin repo:** `personal-os` (SAME repo since 2026-06-12 migration; marketplace at `~/Code/Skills/personal-os`)
- **Source path:** `~/Code/Skills/personal-os/tts-toolkit` (upstream skill dir is `skills/tts/`)
- **Upstream version:** 0.4.0 (in sync with upstream)
- **Vendored at:** 2026-06-08; relocated into personal-os 2026-06-12
- **De-vendor candidate** — upstream is the same version; this copy differs only by the `TTS_LEDGER_DIR` default + a dropped Adam `.bak` cleanup, both env-overridable. Migration dev-guide Phase 6 plans to de-vendor (call the `tts` skill by name + set `TTS_LEDGER_DIR` in `lib/podcast-env.sh`). Kept vendored until then.

## Re-vendor procedure

1. From the personal-os repo, copy the source tree (byte-faithful):
   ```
   # NOTE: same-repo since migration. Upstream skill dir is `tts`; the vendored copy is renamed `podcast-studio-tts`.
   cp -R ~/Code/Skills/personal-os/tts-toolkit/skills/tts/scripts \
         ~/Code/Skills/personal-os/podcast-studio/skills/podcast-studio-tts/scripts
   cp -R ~/Code/Skills/personal-os/tts-toolkit/skills/tts/references \
         ~/Code/Skills/personal-os/podcast-studio/skills/podcast-studio-tts/references
   cp -R ~/Code/Skills/personal-os/tts-toolkit/skills/tts/tests \
         ~/Code/Skills/personal-os/podcast-studio/skills/podcast-studio-tts/tests
   cp ~/Code/Skills/personal-os/tts-toolkit/skills/tts/SKILL.md \
      ~/Code/Skills/personal-os/podcast-studio/skills/podcast-studio-tts/SKILL.md
   ```
2. Bump the upstream version in this file if the personal-os plugin version
   in `.claude-plugin/plugin.json` (tts-toolkit entry) has changed.
3. No rewire needed — tts-toolkit reads credentials from env (`VOLC_IAM_*` /
   `MM_*`), and the env shim (`lib/podcast-env.sh`) feeds it the
   non-credential config (`PODCAST_TTS_PROVIDER`, `PODCAST_HOST_VOICE`).
4. Run the vendored bats tests to confirm parity:
   ```
   cd podcast-studio/skills/podcast-studio-tts && bats tests/
   ```
   (`tests/run_e2e.sh` is a live keyed test — do NOT use it as the
   automated gate.)

## What is NOT vendored (intentional)

- `__pycache__/` and `.pytest_cache/` from the upstream repo — build
  artifacts, not source.
- `.claude-plugin/` plugin manifest from upstream — the podcast-studio
  marketplace/plugin manifest is the active one; the vendored skill is a
  pure library, not a separately installable plugin.

## Modification policy

Once vendored, edits to the scripts in this tree should be avoided in
favor of fixing them upstream and re-vendoring. This copy is intentionally
byte-faithful; the env shim (`lib/podcast-env.sh`) is the only place
where tts-toolkit behavior is shaped by the podcast-studio config.
