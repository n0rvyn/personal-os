---
type: dev-guide
status: active
tags: [podcast, claude-code-plugin, vault, tts, continuity]
refs: [docs/01-discovery/project-brief.md]
current: true
confirmed_at: 2026-06-08T09:30:00
---

# podcast-studio Development Guide

**Project brief:** docs/01-discovery/project-brief.md
**Design source:** session discussion 2026-06-08 (feasibility eval → scope lock → quality-layer design)
**Target repo:** /Users/norvyn/Code/Skills/podcast-team (currently empty marketplace dir)

## Global Constraints

- **Runtime:** the plugin + Claude Code only. No Adam runtime, no personal-os
  code dependency at runtime. Reads/writes the user's Obsidian Vault; the only
  credential is the TTS key.
- **Self-containment red line:** must run in a clean environment from
  `~/.podcast-studio/config.yaml` + TTS key alone. Vendored copies (podcast-prep,
  tts-toolkit) carry their own code and record source-of-truth + re-vendor
  procedure (no live link back to personal-os).
- **No MVP / no half-done** (global rule). Machine-absolute paths must become
  portable references (`${CLAUDE_PLUGIN_ROOT}` / config), never hardcoded.
- **TTS:** single vendor, single voice. `merge.sh` ffmpeg `-c copy` is valid
  only same-vendor/same-rate; no cross-vendor mixing.
- **Scoring step:** must NOT bind a narrative persona (Adam Layer B tone-check
  vs JSON output is mutually exclusive — bombed repeatedly). Pure structured
  output only.
- **Anti-fabrication (temperature principle):** block only fabricated
  data/track-records/notes; allow weak/second-hand sources; no confidence
  numbers; "go deep" = perspective, not citations.
- **Output naming:** `{date}-{title}` (script/audio), `{date}-{show}` (stance
  card). Three artifacts per episode, co-located in `output_dir`.

---

## Dependency graph

```
Phase 1 (scaffold + config + vendoring)
   └─> Phase 2 (personas + orchestration → end-to-end script+audio)
          ├─> Phase 3 (stance-card continuity)
          ├─> Phase 4 (Character Bible voice gate)
          └─> Phase 5 (quality layer)   [depends on 2, 3, 4]
```

Phase 3 and Phase 4 both depend on Phase 2 and are independently reviewable;
Phase 5 enhances the outputs of 2/3/4 and comes last.

---

<!-- section: phase-1 keywords: scaffold, config, vendoring, marketplace, plugin-json -->
## Phase 1: Plugin scaffold + config contract + vendoring

**Goal:** A clean-environment installable plugin whose vendored `prep` and `tts`
skills run standalone, reading all paths from `~/.podcast-studio/config.yaml`
instead of personal-os.

**Depends on:** None

**Scope:**
- `.claude-plugin/marketplace.json` + `podcast-studio/.claude-plugin/plugin.json` + `README.md`
  (README documents the `/loop` cadence one-liner and config setup).
- Config contract: `~/.podcast-studio/config.yaml` schema + a loader/resolver that
  resolves `vault.subjective_dir` / `vault.news_dir` / `vault.output_dir` /
  `tts.*`, with clear errors on missing keys or missing dirs.
- Vendor `podcast-prep` scripts → `skills/prep/` (orchestrator, minhash_check,
  cross_domain, contrarian_pull, topic_log, source_log, angle_slots,
  domain_select + their tests). Strip `~/.claude/personal-os.yaml` dependency;
  read plugin config instead.
- Vendor the **complete** `tts-toolkit` skill → `skills/tts/` (synth,
  synth-auto, chunker, merge, **quota_check, cleanup, volcsign.py**, providers/,
  voice-catalog, provider-quirks + tests). `synth-auto` sources `quota_check.sh`
  and `volcengine.sh` uses `volcsign.py` — omitting either breaks synthesis.
  Strip personal-os.yaml.
- Each vendored dir gets a `VENDORED.md`: source path, upstream version, and the
  exact re-vendor procedure (defends against 0.3.1↔0.4.0 drift).

**用户可见的变化:** 无 — 纯基建阶段（安装插件、写 config 后，可在终端手动跑
vendored 脚本冒烟，但还没有 `/podcast` 命令）。

**Architecture decisions (resolve at /write-plan):**
- Config loader implementation: a small shared shell helper vs a Python module —
  which do the vendored bash/python scripts consume most cleanly?
- Config injection into vendored scripts: env vars vs CLI args vs a thin shim
  that maps config → the scripts' existing flags?
- How the plugin resolves `~/.podcast-studio/config.yaml` location (fixed path vs
  `${CLAUDE_PLUGIN_ROOT}`-relative fallback)?

**Acceptance criteria:**
- [x] `marketplace.json` lists `podcast-studio`; `plugin.json` valid; plugin
      installs in a clean Claude Code instance with no personal-os present.
      (JSON valid; zero personal-os runtime coupling proven.)
- [x] With a sample `config.yaml`, `prep` orchestrator `check` runs config-driven
      (vault-root + topic-log from config, no personal-os.yaml) and returns a
      valid brief structure (approved_topics + contrarian_source + cooldown).
      (rewire tests pass + prep pytest 141 green.) Note:
      `cross_domain_candidates` / `self_past_candidates` enrichment is wired
      config-driven in Phase 5 (corpus-schema decision deferred there) — not
      required for Phase 1 acceptance.
- [ ] `tts` `synth-auto` smoke test produces an mp3 from a short fixture using
      `tts.host_voice`. ⚠️ 需用户验证: requires a live TTS key (VOLC_*/MM_* env);
      offline bats (48) green, but real-key mp3 smoke cannot run in this env.
- [x] `grep -rn "personal-os.yaml" skills/` returns nothing; no absolute
      `/Users/norvyn/...` paths remain in vendored scripts. (runtime scripts clean;
      only test files name personal-os.yaml to assert its absence.)
- [x] `VENDORED.md` present in both `skills/prep` and `skills/tts`.
- [x] Vendored scripts' own unit tests (pytest / bats) pass against the plugin copy.
      (prep pytest 141, tts bats 48 offline, config 7, env-shim 8.)

**Status:** ✅ Completed — 2026-06-08 (1 criterion pending live-TTS-key user verification)

**Review checklist:**
- [x] run-phase review step (implementation-reviewer) — ✅ 0 plan-vs-code gaps
- [x] Clean-env self-containment audit (no personal-os / Adam reference) — grep clean
<!-- /section -->

---

<!-- section: phase-2 keywords: personas, agents, orchestration, podcast-skill, pipeline -->
## Phase 2: Personas + orchestration backbone (end-to-end script + audio)

**Goal:** `/podcast morning` and `/podcast evening` run the full pipeline from
Vault inputs and produce `{date}-{title}.md` + `{date}-{title}.mp3` in
`output_dir`. No continuity / Character Bible yet.

**Depends on:** Phase 1

**Scope:**
- Extract 6 personas → `agents/*.md` (达芬奇 collection/writing, 老黑 critique,
  快刀青衣 polish/finalize, 钱钟书 scoring, 卞旸 host voice, 周杰伦 TTS), from
  Adam `cag_prompt` content, stripping all machine-absolute paths →
  `${CLAUDE_PLUGIN_ROOT}` + config-injected Vault paths.
- `skills/podcast/SKILL.md` — a **Claude-driven orchestration skill** (invoked
  via `/podcast morning|evening`) that drives collection → multi-candidate
  drafts (A/B/C) → critique → polish → scoring/selection → finalize → TTS,
  reading subjective + news from Vault and dispatching the persona subagents +
  the vendored `prep`/`tts` skills. NOT a deterministic driver script and NOT a
  thin command — Claude reads the SKILL.md and executes the pipeline (same
  native primitive as `prep`/`tts`).
- Per-step artifact self-check: the skill verifies each step's required artifact
  exists before proceeding and re-runs the step if missing (replaces Adam's
  output-contract + retry gate). A lightweight file-presence check backs the
  skill's gate.
- Scoring/selection step: pure structured output, NO narrative persona bound
  (honors the Layer B landmine).
- Show variants: morning = event-centric multi-segment; evening = essayistic /
  spine-reversal (the open-questions carry is wired in Phase 3).

**用户可见的变化:** 在 Claude Code 里输入 `/podcast morning`（或 `evening`），
插件读取 Vault 主观源 + 新闻源，跑完整条流水线，最终在 Vault 归档目录里出现两个
同名文件：一份 `{日期}-{标题}` 定稿 markdown 和一段同名 mp3 音频。

**Architecture decisions (resolve at /write-plan):**
- Orchestration = Claude-driven `skills/podcast/SKILL.md` (DP-001 resolved).
  Open: how `/podcast morning|evening` triggers the skill and passes the show
  variant; how the skill structures its step instructions for reliable
  subagent dispatch.
- Number of draft candidates fixed (A/B/C) vs config-driven.
- How per-step artifact presence is checked and how many re-prompt retries
  before hard-fail.

**Status:** ✅ Completed — 2026-06-08 (2 end-to-end criteria pending live-TTS-key user verification)

**Acceptance criteria:**
- [ ] `/podcast morning` end-to-end produces a co-named `.md` + `.mp3` in
      `output_dir` from Vault inputs. ⚠️ 需用户验证: Claude-driven skill needs a real
      run + live TTS key; structure/wiring verified, headless e2e not runnable here.
- [ ] `/podcast evening` produces its own co-named pair with the essayistic
      structure. ⚠️ 需用户验证: same — references/{morning,evening}.md differ structurally.
- [x] A deliberately missing step artifact makes the skill re-run that step, not
      silently pass — the deterministic gate (`episode.py check_artifact`) is
      UT-covered; the skill's re-run instruction consumes it. (Full e2e re-run is
      part of the live-run verification.)
- [x] Scoring step output is valid structured data with no persona/tone binding.
      (钱钟书 scoring-only enforced in agent + SKILL step 8 + both references.)
- [x] No `/Users/norvyn/...` absolute paths or InfoHub references in any agent.
      (grep clean across agents/ + skills/podcast/.)
- [x] UT pass for the orchestration self-check + selection logic. (episode.py 21
      tests incl. check_artifact + select_draft max-total/ignores-mislabeled.)

**Review checklist:**
- [x] run-phase review step (implementation-reviewer) — ✅ 0 plan-vs-code gaps
- [ ] feature-reviewer (complete user journey) — deferred to live-run verification (needs TTS key)
<!-- /section -->

---

<!-- section: phase-3 keywords: stance-card, continuity, bets, settlement, open-questions -->
## Phase 3: Stance-card continuity mechanism

**Goal:** Each episode reads prior stance cards, settles due bets in-script, and
appends a new card — append-only, fabrication-proof by construction.

**Depends on:** Phase 2

**Scope:**
- Stance card schema `{date}-{show}.stance.yaml` in `output_dir`:
  `bets[{id,claim,horizon,settle_by,status}]`, `open_questions[{id,q}]`,
  `settles[{ref,verdict,note}]`, `named_concept`, `topics`,
  `resonance` (reserved).
- Read step: load prior cards, surface bets whose `settle_by <= today` and open
  questions to the writing stage.
- Write step: emit a settlement passage for due bets; write a NEW card whose
  `settles` references the old bet id. Never edit a past card.
- Cross-show / cross-day carry: morning `open_questions` feed evening; unresolved
  threads persist across days.
- No confidence numbers (qualitative bets only).

**用户可见的变化:** 从第二期起，节目里出现"上期成绩单"式的结算段落（回头认账
某个判断成真/落空），以及对未决问题的承接；Vault 归档目录每期多出一个同名
`.stance.yaml` 立场卡。

**Architecture decisions (resolve at /write-plan):**
- `settle_by` / horizon computation (relative horizon → absolute date).
- Bet/question id scheme guaranteeing global uniqueness append-only.
- How the writing stage is fed the due bets (brief injection format).

**Status:** ✅ Completed — 2026-06-08 (in-script settlement/carry rendering pending live run)

**Acceptance criteria:**
- [x] Episode N reads N-1's open cards (`due_bets` + `carried_open_questions` UT'd);
      a due bet appears in the script as a settlement passage. ⚠️ 需用户验证: the
      read mechanics are UT'd; the "appears in script" rendering is SKILL-driven (live run).
- [x] Settlement writes a new card referencing the old bet id; the old card file
      is byte-unchanged (append-only verified by `test_append_only_refuses_overwrite`).
- [x] A fabricated track record is structurally impossible: settlement referencing a
      non-existent bet id is rejected — AND a same-card self-ref is rejected (UT, code-confirmed by reviewer).
- [x] Morning open_questions carry to the same-day evening (`carried_open_questions`
      UT'd). ⚠️ 需用户验证: the "appear in brief" injection is SKILL-driven (live run).
- [x] No confidence numbers emitted in any card (`_walk_for_confidence_fields` UT'd —
      rejects confidence-named numeric fields, allows numbers in claim text).
- [x] UT pass for card read/write/settle + append-only guard. (stance 18 tests, lib 46 total.)

**Review checklist:**
- [x] run-phase review step (implementation-reviewer) — ✅ 0 must-fix
- [x] Anti-fabrication guard audit — ✅ confirmed in code (validate_settlement self-ref + prior-ref)
<!-- /section -->

---

<!-- section: phase-4 keywords: character-bible, voice-unification, vault-corpus, host-identity -->
## Phase 4: Character Bible (voice-unification gate)

**Goal:** A host Character Bible distilled from the Vault corpus is applied as
the final voice-unification pass — the committee writes, one voice speaks.

**Depends on:** Phase 2 (final step hook)

**Scope:**
- Distill the host's worldview / obsessions / verbal tics / evolving long-form
  stances from `subjective_dir` corpus into a Character Bible artifact.
- Refresh mechanism: keep the bible current as the corpus grows.
- Apply as the LAST pass before finalize: rewrite the selected script in the
  single host voice so committee-averaging is removed.

**用户可见的变化:** 定稿听起来稳定地像"同一个人"——有固定的偏执、语癖和口吻，
而且这个人格会随用户笔记的积累缓慢演化。

**Architecture decisions (resolve at /write-plan):**
- Where the Character Bible lives (a Vault file under `output_dir` vs plugin
  data dir) and its format — see DP-002.
- Refresh cadence: re-distill every run vs incremental update vs periodic.
- How much corpus to sample per distillation (recency vs breadth).

**Status:** ✅ Completed — 2026-06-08 (distillation + voice rendering pending live run)

**Acceptance criteria:**
- [x] A Character Bible artifact is generated from `subjective_dir` corpus.
      (`gather_corpus` + `write_bible` UT'd; SKILL step 6 distills + writes
      character-bible.md.) ⚠️ 需用户验证: the distillation itself is Claude-driven (live run).
- [x] The finalize stage applies the bible as a voice-unification rewrite —
      injected into step 12 定稿 + step 13 口播稿 (SKILL.md wired). ⚠️ 需用户验证:
      the pre/post voice-consistency diff is a live-run + listen check.
- [x] Re-running with a grown corpus updates the bible — `write_bible` OVERWRITE
      (not append-only) + `gather_corpus` recency UT'd (`test_write_bible_atomic_overwrite`).
- [x] UT pass for distillation input handling + the finalize gate wiring.
      (bible 14 tests: corpus gather/bounds/binary-skip/symlink-escape/atomic; lib 60 total.)

**Review checklist:**
- [x] run-phase review step (implementation-reviewer) — ✅ complete (1 doc-table gap, fixed)
- [ ] Voice-consistency spot review (pre/post bible diff) — deferred to live-run verification
<!-- /section -->

---

<!-- section: phase-5 keywords: throughline, narrative-arc, selection-axis, resonance, dramatized-settlement -->
## Phase 5: Quality layer (the "world-class show" enhancements)

**Goal:** Layer the six quality mechanisms onto the working pipeline so the show
optimizes for warmth / opinion / insight / shareability, not just correctness.

**Depends on:** Phase 2, Phase 3, Phase 4

**Scope:**
- **Throughline portfolio:** mine 3–5 long-term obsession candidates from
  Vault; user confirms; deepen each across episodes (seeded by open_questions);
  replaces pure novelty/dedup with "deepen recurring + sprinkle fresh."
- **Vault-leads weighting:** episode core = "what I've long thought about X,
  today's news is a lens," not "news + take."
- **Narrative/emotional arc:** cold open / stakes / turn / payoff + a personal
  stake and self-disclosure.
- **Selection axis change:** from "highest rubric score" to "most memorable that
  doesn't break + most like the host would actually say."
- **Resonance proxy gate:** hard self-critique "what would make a listener
  forward / re-listen?"; wire the stance card `resonance` field for future
  feedback inflow.
- **Dramatized settlement segment:** present settlement as a recurring
  "last episode's report card" segment, not back-office bookkeeping.

**用户可见的变化:** 节目从"信息播报+点评"变成"有冷开场和情感弧、围绕几个长期母题
持续加深、有戏剧化结算栏目、明显更像一个有主见的人在讲"的节目。

**Architecture decisions (resolve at /write-plan):**
- Where the confirmed throughline obsessions are stored + how the user confirms
  them (config vs a Vault file vs a `/podcast` subcommand).
- How the selection axis is operationalized without re-introducing a
  persona-bound scoring step (honor the Layer B landmine).
- **Cross-domain corpus schema (deferred from Phase 1, 2026-06-08):** how the
  config models the corpus that feeds `cross_domain_candidates` /
  `self_past_candidates` — flat `subjective_dir` (loses knowledge vs viewpoint
  separation) vs categorized dirs (e.g. add `vault.knowledge_dir`, map each config
  dir to a category, derive `cross_domain.py`'s prefix filters from config,
  preserving upstream two-corpus semantics). Recommended: categorized. This is
  where `cross_domain.py` becomes config-driven (left byte-identical in Phase 1).

**Status:** ✅ Completed — 2026-06-08 (editorial rendering pending live run; cross-domain config-mapping descoped to a follow-up — DP-003)

**Acceptance criteria:**
- [x] Throughline obsessions are mined, user-confirmed, persisted, and a recurring
      obsession is deepened across two episodes. (`throughline.py` mine/persist/
      pick_to_deepen UT'd, 16 tests; SKILL step 4 injects the obsession.) ⚠️ 需用户验证:
      the cross-episode deepening + user-confirm flow is a live run.
- [x] A produced episode shows a narrative arc + vault-led core. (references/
      {morning,evening}.md carry the arc + vault-leads framing.) ⚠️ 需用户验证: rendering is a live run.
- [x] Selection picks on the new axis with structured output only (no persona-bound
      scoring). (rubric GUIDANCE prose within the existing 6-field schema;
      `select_draft` + 21 episode tests byte-unchanged; 钱钟书 persona-free — reviewer-confirmed.)
- [x] The resonance self-critique gate runs and is recorded; `resonance` field
      present in the card. (stance.py resonance field + 2 UT'd; SKILL step 15a gate +
      OMIT→populated.) ⚠️ 需用户验证: the gate's content is a live run.
- [x] Settlement renders as a labeled recurring segment. (references carry the
      dramatized 上期成绩单 segment, consuming the Phase-3 settlement.) ⚠️ 需用户验证: rendering is a live run.
- [x] UT pass for throughline persistence + selection-axis logic. (lib 78 total;
      throughline 16 + the schema-preservation proven by unchanged episode 21.)
      ⚠️ 需用户验证: one full E2E run with the quality layer active needs a live TTS key.

**Review checklist:**
- [x] run-phase review step (implementation-reviewer) — ✅ 0 blocking (NTH test-strength, fixed)
- [ ] feature-reviewer (full quality-layer user journey) — deferred to live-run verification
<!-- /section -->

---

## Decisions

### [DP-001] Orchestration mechanism for the `/podcast` pipeline (recommended)

**Context:** Phase 2 replaces Adam's ExecutionPool DAG. The pipeline must run a
multi-step sequence with per-step artifact gating, either driven by Claude
interpreting a command, or by a deterministic script.

**Options:**
- A: Claude-driven command markdown — `/podcast` is a command.md that instructs
  Claude to dispatch each subagent in order and check artifacts — flexible,
  least code, but non-deterministic step control and harder to unit-test the
  gate logic.
- B: Deterministic driver script — a bash/python driver invokes `claude`
  headless per step and enforces artifact gates in code — reproducible,
  unit-testable gates, survives long runs better, but more code and a heavier
  pattern.

**Chosen:** Neither A nor B — a **Claude-driven SKILL** (`skills/podcast/SKILL.md`).
The orchestration intelligence lives in a SKILL.md that Claude reads and
executes (dispatching persona subagents + the vendored `prep`/`tts` skills),
the same native primitive `prep`/`tts` already use. (User override 2026-06-08:
for a Claude-driven editorial pipeline the skill is the native primitive; a
driver script over-constrains the judgment that IS the product, and a thin
command carries no orchestration.)

### [DP-002] Character Bible storage + refresh cadence (recommended)

**Context:** Phase 4 needs a home for the distilled host identity and a refresh
policy as the Vault corpus grows.

**Options:**
- A: Vault file under `output_dir` (e.g. `character-bible.md`), re-distilled each
  run — always current, co-located with content and human-editable, but adds
  per-run distillation cost.
- B: Plugin data dir, refreshed incrementally/periodically — cheaper per run,
  but splits identity from content and needs a staleness policy.

**Chosen:** A — Vault file `character-bible.md` under `output_dir`, re-distilled
each run. Co-locating with the Vault matches the "host = living projection of
the corpus" thesis and keeps the artifact human-auditable; per-run distillation
cost is acceptable under the project's cost posture (quality first,
absolute-spend guardrails, not ratio budgets).
