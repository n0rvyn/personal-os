---
title: B1 — Character Bible station restoration (handoff seed)
date: 2026-06-15
status: done — implemented 2026-06-16 (step 6 bible-distill station; 312 pytest green; live no-TTS e2e still PENDING MiniMax)
origin: /plugin-master audit (2026-06-15), finding B1; investigation confirmed Phase-1 regression
owner_next_session: COMPLETE — see lib/runner.py `_bible_distill_step` + lib/pipeline.py `bible-distill` step + test_runner.py `test_bible_distill_*` / `test_finalize_broadcast_resolver_*`; dev-guide acceptance corrected. Remaining: live no-TTS e2e (MiniMax-gated) must confirm finalize(12)/broadcast(13) resolve the REAL state/character-bible.md, not the base-persona fallback.
---

# B1 — Restore the Character Bible distill station

## TL;DR for the next session
The Character Bible (voice-unification source) is **documented, whitelisted, and required by Phase-1 acceptance — but the coded pipeline never produces it.** Add the missing `bible-distill` station to `lib/pipeline.py` + a custom runner executor in `lib/runner.py` that runs `gather_corpus` → dispatches `bible-distiller` (corpus-only, isolated) → `write_bible(state_dir, …)`, fail-soft with a minimal-bible fallback. Add tests. Sync the folded docs. **Do NOT re-derive — everything you need is below with file:line anchors (verified 2026-06-15).**

## The bug (verified evidence)
- `bible` appears in `lib/pipeline.py` ONLY at `AGENT_WHITELIST` (`pipeline.py:136`) and as a `character-bible.md` **input** to step 12 finalize (`pipeline.py:371`) and step 13 broadcast-rewrite (`pipeline.py:402`). **There is no `bible-distill` step** in `_build_steps()`.
- `lib/runner.py` never calls `write_bible` / `gather_corpus` and never dispatches `bible-distiller` (grep-confirmed). So `character-bible.md` is **never produced at runtime**.
- Steps 12/13 read it as an input → the file is absent → they fall back to base persona → **voice-unification is silently lost on every run** (no halt, no error).
- This is a **Phase-1 regression with a false-green**: the Phase-1 plan required `character-bible.md` to exist after a run (`docs/06-plans/2026-06-14-phase1-code-runner-plan.md:336`), the dev-guide treats it as **step 6, fail-soft** (`…big-track-redesign-dev-guide.md:24`), and dev-guide **acceptance #29 was checked ✅** ("bible/corpus artifact 必然存在") — but no station produces it. The feature was dropped when the prose pipeline became a coded DAG. It is NOT an intentional cut (CLAUDE.md still carries the "Character Bible distiller (step 6) MUST be isolated" invariant).

## What already exists (do NOT rebuild)
- `agents/bible-distiller.md` — the persona. Contract (verified): input = ONLY the corpus (`lib.bible.gather_corpus` output); MUST NOT see episodes/news/cards (isolation); output = ONE Character Bible markdown with 4 sections (世界观 / 偏执主题 / 口头习惯 / 演化中的立场); empty corpus → minimal bible (base persona 卞旸), no fabrication.
- `lib/bible.py` (verified signatures):
  - `bible_path(output_dir) -> Path` (`:40`) → `<output_dir>/character-bible.md`
  - `gather_corpus(subjective_dir, *, byte_cap, max_files) -> {text, included, dropped}` (`:56`) — reads `subjective_dir` notes, recency-sorted, skips binary/oversized.
  - `write_bible(output_dir, text) -> Path` (`:173`) — atomic overwrite.
- **Read side is ALREADY wired to state_dir** (done in Phase 4): `lib/runner.py:1624` — the input resolver special-cases `character-bible.md` → `bible_path(state_dir)` when present, scratch fallback otherwise. **So the WRITE side MUST also write to `state_dir`** (`config.vault.state_dir` / `_subdir(ctx, "state")`) to match. SKILL.md:215 spec was updated to `{output_dir}/state/character-bible.md`.

## Restoration plan (concrete)

### Task 1 — `lib/pipeline.py`: add the `bible-distill` station
- Insert a step between `magnitude` (5b, `pipeline.py:249`) / `assemble-briefs` (`:269`) and `drafts` (7, `:292`). Recommended position: step 6, after `assemble-briefs`, before `drafts` (matches SKILL.md "step 6"; the bible is isolated/independent so exact slot only needs to be **before finalize step 12**).
- Step dict: `{"name": "bible-distill", "kind": "agent" (custom-executor — see Task 2), "agent": "bible-distiller", "inputs": ["corpus"] (conceptual — NOT a scratch file), "artifact": "character-bible.md", "gate": [{"fn": "check_artifact"}], "fail_soft": True, "parallel": None, "retry": None, "skip_when": None}`.
- `bible-distiller` is already in `AGENT_WHITELIST` (`:136`) — no whitelist change needed.

### Task 2 — `lib/runner.py`: custom executor (the generic agent path will NOT work)
Why custom: (a) input is `gather_corpus(subjective_dir)` from config — OUTSIDE scratch, and the generic input-resolver would point the persona at scratch; (b) isolation requires feeding ONLY the corpus (the generic resolver would leak other inputs); (c) output must land in `state_dir` (persistent), not scratch.
- Precedent to copy: `_scorecard_step` (`runner.py:1381`) — a custom executor branched in `_execute_step` (see the `if name == "scorecard": return _scorecard_step(...)` dispatch ~`runner.py:1039`). Add an analogous `if name == "bible-distill": return _bible_distill_step(step, ctx, dispatch_fn)`.
- `_bible_distill_step` logic:
  1. `corpus = gather_corpus(config.vault.subjective_dir, byte_cap=<pick>, max_files=<pick>)["text"]` (read caps from existing usage / SKILL.md; subjective_dir via `ctx["config"].vault.subjective_dir`).
  2. Dispatch `bible-distiller` fed the corpus inline in the prompt (do NOT use the generic input resolver). Tell it to write `character-bible.md` to scratch. Reuse `_run_dispatch` with a corpus-only prompt.
  3. Read the agent's scratch `character-bible.md`; `write_bible(_subdir(ctx, "state"), text)` → `state_dir/character-bible.md`.
  4. **fail-soft**: on dispatch failure / empty / exception, `write_bible(state_dir, MINIMAL_BIBLE)` (base-persona 卞旸 voice) so the artifact ALWAYS lands. Dev-guide rule: degrade is legal, total-miss → halt; here we never total-miss.
  5. Gate: confirm `bible_path(state_dir)` exists (the executor can do this directly, returning None on success). `fail_soft=True` keeps a dispatch miss from halting.
- The `state_dir` is created by config (Phase 4); `_subdir(ctx, "state")` resolves it.

### Task 3 — tests (`lib/tests/test_runner.py` or a new `test_bible_station.py`)
- Station lands `character-bible.md` in `state_dir` after a (mocked-dispatch) run.
- Isolation: assert the dispatched prompt for `bible-distiller` contains ONLY corpus text — no episode/news/card content (mirror the coveredground-distiller isolation test style).
- fail-soft: inject a dispatch failure → a MINIMAL bible still lands in `state_dir` (artifact never fully missing), run does NOT halt.
- Use the existing `_set_out` helper (Phase-4) so `state_dir` exists in the stub config.

### Task 4 — docs (the folded L2/L3/L5 from the audit)
- `skills/podcast/SKILL.md`: re-add the `bible-distill` row to the per-step **contract table** (~L600-626, currently jumps past it); confirm the step-6 prose matches the implemented slot.
- `README.md`: add `liangchen`, `scorecard`, `coveredground-distiller`, **`bible-distiller`** to the agent list (currently lists only 7/11); fix the "six"/"seven" persona-count inconsistency to a single accurate statement (or make it count-free/role-based like CLAUDE.md now is).
- `.claude-plugin/plugin.json:4` + `marketplace.json` podcast-studio entry: sync the stale "6-persona" wording (make role-based, not a brittle count).

## Landmines / invariants (respect these)
- **Isolation (CLAUDE.md hard invariant):** bible-distiller sees ONLY `gather_corpus` output — never episodes/cards/news/material. History: a leaky distill made "obsessions" = episode topics (霍尔木兹/苏伊士) and homogenized every show.
- **Write to `state_dir`, not output_dir root or scratch** — must match the Phase-4 read special-case (`runner.py:1624`) and SKILL.md:215.
- **fail-soft, minimal-on-failure** — never halt the daily run on a bible miss; always land at least a minimal bible.
- **`write_bible` raises if the dir is missing** — `state_dir` is config-created, fine; but the minimal-bible fallback path must still target an existing `state_dir`.
- Bible output is **markdown**, not JSON (unlike the structured judges).

## Verification (real-green, not simple-green)
- Unit: Tasks 3 tests green + full `python3 -m pytest lib/tests/ -q` (currently 306 passed — keep it green).
- Real path: a no-TTS e2e (sandbox config `Content/Podcasts/config-e2e-sandbox-phase2.yaml`) → confirm `state/character-bible.md` is produced with non-trivial content, and that finalize (12) / broadcast (13) prompts now resolve the real bible (not the scratch fallback). ⚠️ Heads-up: live e2e depends on MiniMax throughput — the qianzhongshu scoring station was timing out / exit-1 the evening of 2026-06-15 (orthogonal infra); resume helper at `/tmp/p4_resume.py` if still relevant, else fresh run.
- Update dev-guide acceptance #29 to a TRUE green (it was false-green) — or note the correction.

## Pointers
- Audit report (full findings, incl. C1 trigger + L1 dead-tools decisions still open): `.claude/reviews/plugin-master-audit-2026-06-15.md`
- Dev-guide (all 4 phases ✅; finalize ✅): `docs/06-plans/2026-06-14-big-track-redesign-dev-guide.md`
- Phase-1 plan (the regressed source-of-truth for the bible station): `docs/06-plans/2026-06-14-phase1-code-runner-plan.md`
- State: `.claude/dev-workflow-state.json` (phase_step: finalized)
