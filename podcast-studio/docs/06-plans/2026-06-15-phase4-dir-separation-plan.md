---
type: plan
status: active
contract_version: 2
tags: [podcast-studio, directory-layout, config, output-dir, refactor]
refs: [docs/06-plans/2026-06-14-big-track-redesign-dev-guide.md]
---

# Phase 4 — Output-dir Directory Separation Implementation Plan

**Goal:** Split the single flat `vault.output_dir` into `episodes/` (listener artifacts), `state/` (continuity state), and `reports/` (scorecards), and move the config file out of the artifacts directory.

**Architecture:** The three subdirs are **derived** from `output_dir` (no new YAML keys) and auto-created during config resolution. The existing path helpers (`episode_paths`/`stance_path`/`store_path`/`bible_path`/`_throughline_path`) are unchanged — they already take a dir arg and realpath-guard against it; the runner is changed to pass the right subdir at each call site. `topic_log.yaml`/`source_log.jsonl` (vendored-prep boundary) and `.scratch-*` (transient) stay at root. Config moves to the documented default `~/.podcast-studio/config.yaml`.

**Tech Stack:** Python 3.13, pytest, dataclasses, pathlib.

**Design doc:** docs/06-plans/2026-06-14-big-track-redesign-dev-guide.md § phase-4

**Crystal file:** none

**Bug diagnosis:** not applicable

**Threat model:** not applicable — no attacker-influenced inputs; paths are config-derived under an already-validated dir (the realpath guards in store_path/bible_path/_throughline_path/episode_paths remain, guarding against `..` escape from the passed subdir).

**Pre-flight risks:**
- Load-bearing readers `load_cards` (stance.py:265), `load_obsessions` (throughline), `gather_recent_bodies` (magnitude.py:98) and the distiller published-`.md` glob (runner.py:1586) read `output_dir` DIRECTLY — if episodes move to `episodes/` but these readers are not re-pointed, they silently return empty sets (no error). Every reader must be swapped in the same task as the writers.
- `topic_log.yaml` is written by BOTH the runner (`_topic_log_step` :646) and the VENDORED `podcast-studio-prep` (orchestrator.py:509 defaults to `output_dir/topic_log.yaml`). Moving it desyncs prep's topic-dedup from the runner. → kept at root this phase (CLAUDE.md forbids hand-editing vendored code).

---

## Impact Map

**User path:** None directly user-facing at runtime; changes the on-disk layout the user browses in `Content/Podcasts/` (episodes/state/reports subfolders instead of one flat dir).
**Data path:** config `output_dir` → derived `episodes_dir`/`state_dir`/`reports_dir` → pipeline writes artifacts/state/reports into the respective subdir; continuity readers read back from the same subdirs next run.
**Shared surfaces:** `lib/config.py` (VaultConfig contract), `lib/runner.py` (all write/read call sites), CLAUDE.md § Scope wording.
**Existing consumers:** runner pipeline stations; the post-publish distiller; the continuity read; the vendored prep (topic_log — kept at root, NOT changed).
**Must remain unchanged:** path-helper signatures + their realpath guards; scratch lifecycle (make_scratch/resume/cleanup keyed off output_dir); topic_log/source_log location; the magnitude/stance ignore regexes; the no-confidence-number / append-only stance invariants.
**Regression checks:** full `pytest lib/tests/` green; a fresh-config dry check that each helper path lands in the intended subdir; continuity round-trip (write a card to episodes/, load_cards(episodes_dir) finds it).

---

<!-- section: task-1-tests keywords: config, vaultconfig, episodes-dir -->
### Task 1-tests: config.py derived-subdir resolution tests

**Maps to Impact Map:** Data path, Shared surfaces

**Files:**
- Modify: `lib/tests/test_config.py`

**Expected outcome:** A loaded config exposes `episodes_dir`/`state_dir`/`reports_dir` that resolve to `<output_dir>/episodes|state|reports`, exist on disk after load, and the existing `output_dir` fail-closed validation is unchanged.

**Non-goals:** Changing the YAML schema (no new required keys); validating subdir existence as fail-closed (they are auto-created).

**Touched surface:** test_config.py.

**Regression shield:** Keep the existing `output_dir`/`subjective_dir`/`news_dir` fail-closed tests intact and passing.

**Task Contract:**
- Expected behavior: When the pipeline loads its config, it knows three distinct folders — one for episodes, one for continuity state, one for reports — all under the configured output folder, and they're ready to write into.
- Automated verify: `python3 -m pytest lib/tests/test_config.py -q` — the new tests FAIL (AttributeError: VaultConfig has no `episodes_dir`) before impl.
- Real path verify: `python3 -m lib.config --validate <a real config>` after impl resolves and prints ok.
- Manual/device verify: none.

**Steps:**
1. Add `test_vault_subdirs_derived_and_created`: build a temp config with a valid `output_dir`, `load_config`, assert `cfg.vault.episodes_dir == str(Path(output_dir)/'episodes')` (and state/reports), and that all three dirs `.exists()` and `.is_dir()`.
2. Add `test_output_dir_still_fail_closed`: a missing `output_dir` still raises `ConfigError` naming `vault.output_dir` (regression).
3. Run: `python3 -m pytest lib/tests/test_config.py -q` → expect FAIL on the new derived-dir test (attribute missing).

**Verify:**
Run: `python3 -m pytest lib/tests/test_config.py::test_vault_subdirs_derived_and_created -q`
Expected: fails pre-impl with AttributeError/`episodes_dir`.
<!-- /section -->

<!-- section: task-1-impl keywords: config, vaultconfig, derived-dirs -->
### Task 1-impl: config.py add derived episodes/state/reports dirs

**Depends on:** Task 1-tests

**Maps to Impact Map:** Data path, Shared surfaces

**Files:**
- Modify: `lib/config.py:34-42` (VaultConfig dataclass), `lib/config.py:203-224` (_resolve_vault)

**Expected outcome:** `VaultConfig` carries `episodes_dir`/`state_dir`/`reports_dir`; `_resolve_vault` computes them as `output_dir/episodes|state|reports`, `mkdir(parents=True, exist_ok=True)`, and stores the resolved strings.

**Non-goals:** Adding YAML keys; changing `output_dir`'s fail-closed behavior; touching `tts`/`exchange_dir` resolution.

**Touched surface:** lib/config.py.

**Regression shield:** Do not modify the test files written in Task 1-tests. Keep `REQUIRED_VAULT_KEYS` and the existence loop (`:203-213`) intact — only ADD the derived-dir computation after it.

**Task Contract:**
- Expected behavior: same as Task 1-tests (config now provides the three folders).
- Automated verify: `python3 -m pytest lib/tests/test_config.py -q` → all pass.
- Real path verify: `python3 -m lib.config --validate <config>` → `ok`.
- Manual/device verify: none.

**Steps:**
1. Add `episodes_dir: str`, `state_dir: str`, `reports_dir: str` to `VaultConfig` (after `output_dir`, before optional `root`).
2. In `_resolve_vault`, after the `REQUIRED_VAULT_KEYS` existence loop, compute `out = Path(resolved["output_dir"])`; for name in (episodes/state/reports): `d = out / name; d.mkdir(parents=True, exist_ok=True); resolved[name+"_dir"] = str(d)`.
3. Pass the three into `VaultConfig(...)`.
4. Run: `python3 -m pytest lib/tests/test_config.py -q` → all green.

**Verify:**
Run: `python3 -m pytest lib/tests/test_config.py -q`
Expected: all pass; `python3 -c "from lib.config import load_config"` import clean.
<!-- /section -->

<!-- section: task-2-tests keywords: runner, episodes, state, reports, integration -->
### Task 2-tests: runner integration asserts subdir landing

**Maps to Impact Map:** Data path, Existing consumers, Regression checks

**Files:**
- Modify: `lib/tests/test_runner.py` — `_make_config_stub` (:207-213) **and** the 14 sites that set `cfg.vault.output_dir = ...`; the integration tests at :1362, :1497, :1505, :1950, :2041 + their topic_log pre-stage helpers

**Expected outcome:** The runner integration tests (mocked dispatch, no MiniMax) assert: published `.md`/`.stance.yaml` land in `episodes/`, covered-ground store in `state/`, scorecard.md in `reports/`, topic_log stays at root, and the continuity read finds prior cards in `episodes/`. The config stub exposes the 3 subdirs so the runner doesn't TypeError at entry.

**Non-goals:** Changing the helper unit tests (test_episode/test_stance/test_bible/test_coveredground/test_throughline stay dir-relative and green — do NOT touch them).

**Touched surface:** test_runner.py.

**Regression shield:** Keep topic_log pre-stage at `output_dir/topic_log.yaml` (root) — assert it is NOT moved.

**CRITICAL (must do FIRST in this task — else every integration test TypeErrors at runner entry):** `_make_config_stub` (:207-213) is a `MagicMock`; after Task 1-impl the runner reads `cfg.vault.episodes_dir`/`state_dir`/`reports_dir`, which a MagicMock auto-creates as MagicMocks → `Path(MagicMock())` raises TypeError. Add a helper `_set_out(cfg, out)` that sets `cfg.vault.output_dir=str(out)` + `episodes_dir=str(out/'episodes')` + `state_dir=str(out/'state')` + `reports_dir=str(out/'reports')` and `mkdir`s them; route `_make_config_stub` and ALL 14 `cfg.vault.output_dir = ...` sites through it.

**Task Contract:**
- Expected behavior: A full mocked no-TTS run drops the episode + stance card under episodes/, the memory store under state/, and the scorecard under reports/, while topic_log stays at the top.
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q` — the updated subdir-landing assertions FAIL before impl (publish still goes to root).
- Real path verify: covered by the post-impl pytest + the Task 4 dry check.
- Manual/device verify: none.

**Steps:**
0. **(do first)** Add `_set_out(cfg, out)` helper + route `_make_config_stub` and all 14 `cfg.vault.output_dir = ` sites through it (see CRITICAL above). After this, run the FULL existing `test_runner.py` and confirm it still passes BEFORE adding new asserts — this isolates the stub fix from the layout asserts.
1. Update `output_dir.glob("{date}-*.md")` / `output_dir / "...stance.yaml"` / `output_dir / "...scorecard.md"` assertions to `episodes_dir`/`reports_dir` (resolve via the run's config or `Path(output_dir)/'episodes'` etc.).
2. Update `cg_store_path(output_dir)` expectation to `state_dir`; add an assert that `bible_path(state_dir)` is where character-bible lands (if a test exercises the bible step).
3. Add an explicit assert that `output_dir/'topic_log.yaml'` still exists (boundary kept).
4. Run: `python3 -m pytest lib/tests/test_runner.py -q` → expect FAILs on the subdir-landing asserts (NOT TypeErrors — step 0 fixed those).

**Verify:**
Run: `python3 -m pytest lib/tests/test_runner.py -q`
Expected: fails pre-impl on episodes/state/reports landing assertions.
<!-- /section -->

<!-- section: task-2-impl keywords: runner, ctx, output-dir, subdirs -->
### Task 2-impl: thread episodes/state/reports through the runner

**Depends on:** Task 2-tests

**Maps to Impact Map:** Data path, Existing consumers, Must remain unchanged

**Files:**
- Modify: `lib/runner.py` (ctx setup in `run_pipeline` ~:1687-1712; the dir-determining call sites below; the **gate dispatch** at :285 + :295; the **input resolver** ~:1568-1597)
- Modify: `skills/podcast/SKILL.md:215` (the canonical bible write-path SPEC — `write_bible(...)` to `{output_dir}/character-bible.md`; update to `state_dir`)
- Note: `write_bible` is NOT called by the coded runner today (grep: only in tests); the bible is a pre-existing READ-ONLY input in the current pipeline. So the WRITE-side fix is the SKILL.md spec line + migration moving the existing file; there is no runtime bible-write executable to repoint.

**Expected outcome:** The runner adds `episodes_dir`/`state_dir`/`reports_dir` to `ctx` (from config) and uses them at every write/read site; scratch + topic_log stay at `output_dir`.

**Non-goals:** Changing path-helper signatures; moving scratch or topic_log; touching the magnitude/stance ignore regexes.

**Touched surface:** lib/runner.py, agents/bible-distiller.md (conditional).

**Regression shield:** Do not modify the test files from Task 2-tests. Re-grep `output_dir` in runner.py after editing to confirm only scratch/topic_log/scratch-glob references remain on the root.

**Task Contract:**
- Expected behavior: same as Task 2-tests.
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q` → all pass.
- Real path verify: Task 4 dry check confirms real config produces the right subdir paths.
- Manual/device verify: deferred optional no-TTS e2e (pipeline already proven green this session).

**Steps:**
1. In `run_pipeline`, after resolving config, set `ctx["episodes_dir"]=Path(cfg.vault.episodes_dir)`, `ctx["state_dir"]=...`, `ctx["reports_dir"]=...`.
2. Swap `output_dir`→subdir at the code call sites: `stance_card_exists` :167 (episodes), `load_cards` :397 (episodes), `load_obsessions` :405 (state), `episode_paths` :588 (episodes), `write_card` :771 (episodes), `load_store` :490/:731/:1209/:1431 (state), `write_store` :1213 (state), scorecard.md write :1497 (reports), distiller `.md` glob :1586 (episodes).
3. **Gate dispatch (S1-1):** the 3a stance-card gates are dispatched generically at **:285** (`check_stance_card_absent`) and **:295** (`check_stance_card`) via `gate_fn(ctx["output_dir"], ctx["date"], ctx["show"])` — change BOTH to pass `ctx["episodes_dir"]` (cards now live in episodes/). Verify a re-run after a publish detects the existing card in episodes/ (else fail-fast 3a misses it → overwrite).
4. **character-bible.md (S1-3):** it has NO input-resolver special-case (the resolver at ~:1568-1597 only special-cases coveredground-distill's `published.md` + `covered-ground.yaml`). Add a special-case so steps that read `character-bible.md` (pipeline inputs :371/:402) resolve to `state_dir/character-bible.md` via `bible_path(state_dir)`, AND ensure the bible-distiller step's WRITE target is `bible_path(state_dir)` (trace where the bible artifact path is set — runner dispatch artifact and/or `agents/bible-distiller.md`). Confirm with a grep that no `character-bible` path resolves to output_dir root or bare scratch after the change.
5. Re-grep `gather_recent_bodies` (magnitude:98) / `save_obsessions` (throughline) call sites; point bodies→episodes, obsessions→state.
6. Leave `topic_log` :646 and `make_scratch`/`_resolve_scratch_dir`/`_scratch_is_under`/`cleanup_scratch` on `output_dir`.
7. Run: `python3 -m pytest lib/tests/test_runner.py lib/tests/test_coveredground.py lib/tests/test_throughline.py lib/tests/test_bible.py -q` → green.

**Verify:**
Run: `python3 -m pytest lib/tests/test_runner.py -q`
Expected: all pass; `grep -nE "output_dir" lib/runner.py` shows root usage only at scratch/topic_log sites.
<!-- /section -->

<!-- section: task-3-migration keywords: migration, config, output-dir -->
### Task 3: migrate existing production files + move config out

**Maps to Impact Map:** Data path, Must remain unchanged

**Files:**
- Create: `tools/migrate-phase4-layout.sh` (one-shot, reversible; not part of the shipped pipeline)

**Expected outcome:** A script that moves existing `output_dir` files into `episodes/`/`state/`/`reports/` and relocates the in-vault `config.yaml` to `~/.podcast-studio/config.yaml`, printing exactly what moved (so it's reversible).

**Non-goals:** Touching topic_log.yaml/source_log.jsonl/x_banner.png (stay at root); deleting anything.

**Ordering (S2-2):** This migration MUST run once before the first post-Phase-4 pipeline run on the production vault — otherwise `load_cards(episodes_dir)`/`gather_recent_bodies(episodes_dir)` find nothing (existing cards/bodies still at root) and the next episode loses all continuity silently. Run order: Task 1-impl + Task 2-impl landed and pytest green → Task 3 migration → only then a live run.

**Touched surface:** the production vault dir (real user data — run only after confirmation).

**Regression shield:** Script is idempotent (skips files already in a subdir) and prints an undo hint; does not overwrite existing destination files.

**⚠️ No test:** one-shot ops script over real user data; verified by listing the resulting layout, not a unit test.

**Task Contract:**
- Expected behavior: After running once, the user's `Content/Podcasts/` shows episodes/state/reports subfolders with the prior files inside, the config no longer sits next to episodes, and the next run's continuity still finds history.
- Automated verify: N/A (ops script). Post-run: `ls <output_dir>/episodes <output_dir>/state <output_dir>/reports` shows the moved files; `ls ~/.podcast-studio/config.yaml` exists.
- Real path verify: load config → `load_cards(episodes_dir)` returns the migrated stance cards (continuity intact).
- Manual/device verify: user eyeballs the new folder layout.

**Steps:**
1. Script reads `output_dir` (arg or from config), `mkdir -p episodes state reports`.
2. `mv {date}-*.md {date}-*.mp3 {date}-*.stance.yaml → episodes/`; `mv character-bible.md covered-ground.yaml throughline.yaml → state/` (each guarded by existence); `mv {date}-*.scorecard.md → reports/`.
3. `mv <output_dir>/config.yaml ~/.podcast-studio/config.yaml` (only if the latter doesn't already exist; else print a warning and skip).
4. Print a summary of every move + an undo one-liner.
5. Verify: run the script against the real `Content/Podcasts/`, then `python3 -c "from lib.config import load_config; from lib.stance import load_cards; c=load_config(); print(len(load_cards(c.vault.episodes_dir)),'cards found')"` → non-zero.

**Verify:**
Run: `bash tools/migrate-phase4-layout.sh <output_dir>` (after user confirms), then `ls <output_dir>/episodes <output_dir>/state <output_dir>/reports`
Expected: prior files now under the subdirs; config at `~/.podcast-studio/config.yaml`.
<!-- /section -->

<!-- section: task-4-docs keywords: claude-md, config-example, docs -->
### Task 4: document the new layout

**Maps to Impact Map:** Shared surfaces

**Files:**
- Modify: `config.example.yaml`, `CLAUDE.md` (§"Config is the single source of truth" + §Scope)

**Expected outcome:** Docs describe the episodes/state/reports layout, that the three subdirs are derived+auto-created, that config lives outside `output_dir` (default `~/.podcast-studio/config.yaml`), and the §Scope "three co-named artifacts to `vault.output_dir`" wording becomes "to `vault.output_dir/episodes/`".

**Non-goals:** Changing any runtime behavior.

**Touched surface:** config.example.yaml, CLAUDE.md.

**⚠️ No test:** docs-only edit; verified by grep.

**Task Contract:**
- Expected behavior: A reader of CLAUDE.md/config.example understands where each artifact type lands and where config goes.
- Automated verify: `grep -n "episodes/" CLAUDE.md` and `grep -n "reports/" CLAUDE.md` return the new wording.
- Real path verify: N/A.
- Manual/device verify: none.

**Steps:**
1. In CLAUDE.md, update the §Scope line to reference `vault.output_dir/episodes/`; add a short layout note (episodes/state/reports derived + auto-created; config outside output_dir).
2. Add a layout comment block to `config.example.yaml`.
3. Verify: `grep -nE "episodes/|state/|reports/" CLAUDE.md config.example.yaml`.

**Verify:**
Run: `grep -nE "episodes/|state/|reports/" CLAUDE.md config.example.yaml`
Expected: the new layout wording present.
<!-- /section -->

## Decisions
None. (Layout = full split, confirmed by user this session; topic_log/scratch-stay-at-root and config→`~/.podcast-studio/config.yaml` are determined by the vendored-prep boundary + the documented default config location, stated inline above.)

---
## Verification
- **Verdict:** Approved
- **Date:** 2026-06-15
- **Verifier:** dev-workflow:plan-verifier (sonnet), 2 cycles. Cycle 1 → 3 must-revise (gate-dispatch ctx swap, character-bible read+write, test-stub MagicMock→Path TypeError) + migration-ordering advisory — all applied. Cycle 2 → gate / stub / migration-ordering VERIFIED CLEAN; the residual bible-WRITE item was resolved by direct code inspection (grep: `write_bible` is test-only, the coded runner has no bible-write step, `SKILL.md:215` is the doc spec line — Task 2-impl Files now names it; read-resolver→state_dir + migration cover the rest).
- Reports: `.claude/reviews/plan-verifier-2026-06-15-phase4.md`, `.claude/reviews/plan-verifier-2026-06-15-phase4-dir-separation.md`
