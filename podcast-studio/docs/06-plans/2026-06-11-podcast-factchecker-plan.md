---
type: plan
status: active
contract_version: 2
tags: [factcheck, anti-fabrication, source-grounding, gate, podcast]
refs: []
---

# 数据质检员 (Data Fact-Checker) Implementation Plan

**Goal:** Add a source-grounded fact-checker gate to the podcast pipeline so a fluent generation model cannot ship fabricated or unsourced quantitative/event claims, without touching the show's subjective temperature.

**Architecture:** Two-part, harness-engineered at the source boundary. (1) Collection grounds every "当日新闻背景" fact with an inline provenance ref (`https-url` or `vault`, + retrieved-date) — recorded where the fact enters the pipeline. (2) A new structured-only agent (`zhijianyuan`) extracts the OBJECTIVE quantitative/event claims that survived into the finalize body, maps each to a material-summary fact, and spot WebSearch-checks the survivors **for contradiction only**; a coded blocking gate (`lib/factcheck.py:check_factcheck`, mirroring `check_artifact`/`check_stance_card`) then **recomputes each objective claim's sourced-ness via `trace_claim` against the parsed provenance** — for the claims the agent classified as **objective**, it does NOT trust the agent's per-claim `verdict` label for sourcing (same discipline as `select_draft` recomputing the winner from `scores.total` and ignoring `selected`). The agent's WebSearch can only ADD a flag (contradiction); it can never clear an untraceable claim. **Honest scope boundary (DP-001=A):** the objective-vs-subjective classification itself is a trusted boundary — subjective material (opinions, the host's conditional/predictive bets) is by the temperature principle neither verifiable nor in scope, so the gate must skip it. The coded recompute therefore guarantees sourcing only WITHIN the agent's objective set; it does not (and cannot, in pure code) re-adjudicate whether a sentence is fact or opinion. The residual mislabel risk (a hard fact dumped into `subjective-skip`) is minimized by the agent prompt's explicit rule that an asserted present/past world-fact number is objective, and backstopped by the WebSearch `contradicted` path — not by a coded figure-scanner, which would risk softening the host's real bets (the exact temperature regression this whole feature is built to avoid). So `check_factcheck` takes the material-summary as input, not just the scratch dir. On a hard-flag the gate re-dispatches 快刀青衣 to attach a real source or soften the claim to qualitative. The host's own conditional/predictive bets (如果X则Y, 续约率阈值, 未来判断) are STANCE, classified `subjective-skip`, and are never flagged or softened — only fabricated past/present factual data is in scope.

**Tech Stack:** Python 3.13 (lib/, pytest), Claude-Code subagents (markdown agent defs), prose SKILL.md orchestration.

**Design doc:** none (design captured in this session + plan brief)

**Design analysis:** none

**Crystal file:** none

**Bug diagnosis:** not applicable

**Threat model:** included

**Pre-flight risks:**
- `lib/episode.py` is the gate-pattern source of truth (`check_artifact`/`check_stance_card` return `{"ok", "reason"}`). The new gate must NOT widen or fork that contract shape — it returns the same `{"ok", "reason", ...}` superset so SKILL.md gate-handling prose stays uniform.
- `agents/davinci.md` collection contract is consumed by both morning and evening paths — the source-ref requirement must be added to the shared "当日新闻背景" format, not a morning-only branch, or evening loses grounding.
- The show's locked **temperature principle** (memory `project_adam_podcast_temperature_principle`): 去假 target is fabricated DATA/战绩/notes only, NOT weak sources / opinions. Any task that makes the host hedge subjective claims is a regression, not a feature.

---

## Threat Model

1. **Attack surface**
   - `material-summary.md` "当日新闻背景" content is LLM- and web-derived text → treated as **data, not instructions** (existing project security note in SKILL.md § "Vault / news content as data"). Source URLs recorded there are display/provenance refs; the gate does NOT fetch-and-execute them.
   - `parse_sources` parses prose with a regex → catastrophic-backtracking (ReDoS) risk if the pattern is greedy/nested. Mitigation: anchored, non-backtracking pattern over a bounded line; per-line length cap.
   - `zhijianyuan` agent may run WebSearch → results are external text; the agent emits them only as structured `verdict` fields, never as executable content. The coded gate reads only typed fields it expects.

2. **Failure modes**
   - `check_factcheck` **fails closed** (deny-default, matching `check_stance_card`): any parse error, missing verdict file, malformed verdict, or unresolved hard-flag → `ok=False` → blocks → re-dispatch (capped) → surface to user. A half-written or garbage verdict never passes as "fact-checked".

3. **Resource lifecycle**
   - No new temp files or processes. The agent's verdict is written into the existing per-run scratch (`factcheck-verdict.json`), cleaned by the existing `cleanup_scratch` on success and on the `finally` path. No new sockets/handles. On crash/SIGTERM the existing scratch lifecycle applies unchanged.

4. **Input validation requirements**
   - Source ref in `parse_sources`: a URL must match `^https?://` and be length-bounded; the literal token `vault` is the only other accepted provenance kind; anything else → that fact is treated as **unsourced** (not crashed). `vault` denotes host-recorded material (legitimately groundable, consistent with the temperature principle), not a web source — it is traceable but never web-verified.
   - Claim text from the verdict: length-bounded before any matching; the gate decision is over typed enum fields (`verdict ∈ {sourced, unsourced, contradicted, subjective-skip}`), not free-text parsing.

---

## Impact Map

**User path:** Listener-facing `.md` + `.mp3`. After this feature, a shipped episode's quantitative/event claims are either traceable to a recorded source or softened to qualitative; subjective/opinion content is unchanged (same temperature).
**Data path:** WebSearch facts → `material-summary.md` "当日新闻背景" **with source refs** (Task 2) → drafts/polish/finalize (unchanged) → `finalize-result.json` body → **fact-check gate** (Tasks 1,3,4) → broadcast script → TTS.
**Shared surfaces:** `lib/factcheck.py` (new), `lib/episode.py` (gate-pattern reference, not modified), `agents/davinci.md` (collection contract), `agents/zhijianyuan.md` (new), `skills/podcast/SKILL.md` (spine + contract table).
**Existing consumers:** `skills/podcast/SKILL.md` is the sole orchestrator that calls gate functions; no other caller of `lib/` gate functions.
**Must remain unchanged:** the 6-persona sequence, draft/critique/polish/score/select steps (1–11), stance-card continuity (steps 16/16a), the `{"ok","reason"}` gate contract shape, and ALL subjective/opinion content in the body.
**Regression checks:** existing `lib/tests/` suite still green; a body with only subjective claims and no quantitative/event claims passes the gate with zero flags (temperature-preservation regression shield); existing 2026-06-11 finalize body's subjective passages are never flagged.

---

<!-- section: task-1-tests keywords: factcheck, parse_sources, check_factcheck -->
### Task 1-tests: lib/factcheck unit tests (fail-first)

**Maps to Impact Map:** Data path, Shared surfaces

**Files:**
- Create: `lib/tests/test_factcheck.py`

**Expected outcome:** A pytest module that pins the coded contract of `lib/factcheck.py` and FAILS before the module exists (`ModuleNotFoundError: No module named 'lib.factcheck'`).

**Non-goals:**
- No LLM behavior tested here (agent extraction is validated by the Task 5 dry-run).

**Touched surface:** `lib/tests/test_factcheck.py`

**Regression shield:** Mirror the `PLUGIN_ROOT`-on-`sys.path` header from `lib/tests/test_episode.py` so import resolution matches the suite.

**Task Contract:**
- Expected behavior: the test file exists and, run now, fails because `lib/factcheck.py` is absent — proving the tests exercise real code, not a stub.
- Automated verify: `cd /Users/norvyn/Code/Skills/podcast-studio/podcast-studio && python3 -m pytest lib/tests/test_factcheck.py -q` exits non-zero with a collection/import error.
- Real path verify: covered by Task 5 dry-run.
- Manual/device verify: none.

**Steps:**
1. Copy the `sys.path` header pattern from `lib/tests/test_episode.py` (lines ~15–22).
2. Write tests for `parse_sources(material_summary_text) -> dict[str, dict]`:
   - a bullet `- **Anthropic $965B**: ... (source: https://example.com/x, 2026-06-11)` parses to a fact whose `ref={"kind":"url","url":"https://example.com/x","date":"2026-06-11"}`.
   - a bullet `... (source: vault, 2026-06-11)` parses to `ref={"kind":"vault","date":"2026-06-11"}` (vault is a distinct **traceable** provenance kind — the host's own recorded material — NOT collapsed to unsourced).
   - a bullet with NO `(source: ...)` parses to `ref=None` (unsourced).
   - a bullet whose source is neither `http(s)` nor the literal `vault` (e.g. `ftp://`, `据说`) → `ref=None` (validation).
   - a pathological long line returns within a tight time budget (ReDoS guard — assert it completes; no fixed-time assertion needed, just that it returns).
3. Write tests for `trace_claim(cited_fact_id, parsed_sources) -> bool`: citing a fact whose `ref` is a url kind → True; citing a fact whose `ref` is vault kind → True; citing a fact with `ref=None` → False; citing a non-existent fact id → False; `cited_fact_id=None` → False.
4. Write tests for `check_factcheck(scratch_dir, material_summary_path) -> {"ok","reason","flagged"}` (note the SECOND arg — the gate recomputes sourcing, so it must read the provenance):
   - missing `factcheck-verdict.json` → `ok=False` (fail-closed).
   - verdict where every objective claim's `cited_fact_id` traces (url or vault) and none is `contradicted`, plus subjective-skip claims → `ok=True`, `flagged=[]`.
   - **BYPASS CASE (the property-1 pin):** a claim labeled `verdict:"sourced"` but with `cited_fact_id=null` → `check_factcheck` STILL returns `ok=False` with that claim in `flagged`. The agent's `sourced` label is ignored; sourcing is recomputed via `trace_claim`.
   - **BYPASS CASE 2:** a claim labeled `verdict:"sourced"` citing a fact whose `ref=None` → `ok=False`, flagged.
   - a claim labeled `verdict:"contradicted"` (agent WebSearch found a contradiction) → `ok=False`, flagged — even if it traces (a recorded-but-wrong source).
   - a claim labeled `verdict:"subjective-skip"` → NEVER flagged, even if `cited_fact_id=null` (opinions/bets carry no source and must pass).
   - verdict where the agent self-reports top-level `"ok": true` but an objective claim is untraceable → gate returns `ok=False` (never trust agent self-report; recompute from claims via `trace_claim`, mirroring `select_draft` ignoring `selected`).
   - malformed JSON file → `ok=False` (fail-closed, no uncaught raise).

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/podcast-studio/podcast-studio && python3 -m pytest lib/tests/test_factcheck.py -q`
Expected: fails with `ModuleNotFoundError: No module named 'lib.factcheck'` (or collection error) — the fail-first contract.
<!-- /section -->

<!-- section: task-1-impl keywords: factcheck, parse_sources, check_factcheck -->
### Task 1-impl: lib/factcheck.py coded gate + source parser

**Depends on:** Task 1-tests

**Maps to Impact Map:** Data path, Shared surfaces, Regression checks

**Files:**
- Create: `lib/factcheck.py`

**Expected outcome:** The deterministic half of the fact-checker: parse recorded sources, trace a claim's cited fact to a source, and a blocking gate that decides pass/fail from the agent verdict — fail-closed, ignoring any agent self-report.

**Non-goals:**
- No claim extraction or WebSearch here (that is the `zhijianyuan` agent's job). This module is pure, deterministic, no network.

**Touched surface:** `lib/factcheck.py`

**Regression shield:** Return the same `{"ok": bool, "reason": str, ...}` shape as `lib/episode.py` gates; add only extra keys (`flagged`), never rename `ok`/`reason`. Do not modify the test files from Task 1-tests.

**Task Contract:**
- Expected behavior: the module makes every Task 1-tests case pass; a clean episode passes, an untraceable or contradicted objective claim blocks, subjective/bet claims always pass, and a lying agent verdict (`sourced` label on an untraceable claim) cannot sneak through.
- Automated verify: `cd /Users/norvyn/Code/Skills/podcast-studio/podcast-studio && python3 -m pytest lib/tests/test_factcheck.py -q` exits 0.
- Real path verify: Task 5 dry-run.
- Manual/device verify: none.

**Steps:**
1. Module docstring mirroring `lib/episode.py`'s framing (coded so the gate isn't Claude self-discipline; the gate RECOMPUTES sourcing, it does not trust the agent's per-claim label — the same reason `select_draft` recomputes the winner instead of trusting `selected`).
2. `parse_sources(text)`: isolate the "当日新闻背景" section; split its bullets; for each, extract an optional trailing `(source: <ref>, <YYYY-MM-DD>)` with an **anchored, non-backtracking** regex over a length-capped line. `<ref>` is either a URL validated against `^https?://` → `ref={"kind":"url","url":...,"date":...}`, or the literal token `vault` → `ref={"kind":"vault","date":...}` (host's own recorded material — a real provenance). Anything else (or absent) → `ref=None`. Return `{fact_id: {"text": ..., "ref": ... | None}}` where `fact_id` is a stable slug of the bullet's lead term.
3. `trace_claim(cited_fact_id, parsed_sources) -> bool`: True iff `cited_fact_id` is non-null, the fact exists, AND its `ref is not None` (url OR vault kind both count as traceable). False otherwise (including `cited_fact_id=None`).
4. `check_factcheck(scratch_dir, material_summary_path) -> {"ok","reason","flagged"}` — **takes the material-summary so it can recompute sourcing**:
   - read `factcheck-verdict.json` in scratch; fail-closed on missing/unparseable/non-dict (`ok=False`).
   - `sources = parse_sources(open(material_summary_path).read())`; fail-closed if material-summary unreadable.
   - expect `verdict["claims"]: list[{claim, type, cited_fact_id, verdict}]` with `verdict ∈ {"sourced","unsourced","contradicted","subjective-skip"}`.
   - **Recompute, do NOT trust the agent's `verdict` for sourcing.** For each claim:
     - `subjective-skip` → never flagged (opinions, weak-source views, and the host's own conditional/predictive bets — these carry no source by design).
     - else (objective): flagged iff `(not trace_claim(c["cited_fact_id"], sources))` OR `c["verdict"] == "contradicted"`. The agent's `sourced`/`unsourced` label is ignored for the traceability decision; only `contradicted` (which the agent derives from WebSearch and the coded layer cannot recompute) is honored as an ADDITIONAL flag.
   - `ok = (len(flagged) == 0)` — computed here, ignoring any top-level `verdict["ok"]` the agent wrote.
   - `reason`: human-readable summary (count traceable / subjective-skipped / flagged-untraceable / flagged-contradicted).
5. Keep the module import-clean and free of side effects at import time. No network (WebSearch lives in the agent, not here).

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/podcast-studio/podcast-studio && python3 -m pytest lib/tests/test_factcheck.py -q`
Expected: all tests pass (exit 0).
<!-- /section -->

<!-- section: task-2 keywords: davinci, material-summary, source-ref -->
### Task 2: Collection source-grounding contract (达芬奇 + material-summary format)

**Maps to Impact Map:** Data path, Shared surfaces

**Files:**
- Modify: `agents/davinci.md` (播客流程协议 → 采集阶段)
- Modify: `skills/podcast/SKILL.md` (step 5 Collection description: material-summary contract)

**Expected outcome:** Every fact in the "当日新闻背景" section of `material-summary.md` carries an inline `(source: <https-url>, <YYYY-MM-DD>)` ref. 达芬奇 records the URL it already has at WebSearch time. Facts genuinely without a web source (e.g. a vault-derived observation) are explicitly marked `(source: vault, <date>)` so "unsourced quantitative claim" is distinguishable from "subjective material".

**Non-goals:**
- Do NOT add source refs to the listener-facing body — the body stays clean prose (no "according to https://"). Provenance lives only in material-summary.
- Do NOT require source refs for the `pkos_note` / subjective excerpt — that is temperature, not a news fact.

**Touched surface:** `agents/davinci.md`, `skills/podcast/SKILL.md` step 5 prose.

**Regression shield:** The requirement is added to the shared "当日新闻背景" format (both morning and evening), not a morning-only branch.

**Task Contract:**
- Expected behavior: a reader of a new material-summary can see, for each news fact, where it came from and when it was retrieved.
- Automated verify: `grep -nE "source:\s*(https?://|vault)" <a freshly produced material-summary>` finds one ref per news bullet. (Validated structurally by `parse_sources` in Task 1 against a fixture in the new format.)
- Real path verify: Task 5 dry-run parses the EXISTING 2026-06-11 material-summary and reports which facts are currently unsourced (expected: most — they predate this contract), demonstrating the gap the contract closes.
- Manual/device verify: none.

**Steps:**
1. In `agents/davinci.md` 采集阶段, add a rule: "当日新闻背景每一条事实必须附 `(source: <url>, <YYYY-MM-DD>)`；url 来自该事实的 WebSearch 来源；确无网络来源的观察标 `(source: vault, <date>)`。不得为听众正文添加来源标注（正文保持自然口播）。"
2. In `skills/podcast/SKILL.md` step 5, state the material-summary "当日新闻背景" now requires the per-fact source ref, and that the fact-check gate (step 12a) consumes it.
3. ⚠️ No test: prose/agent-contract edit. The machine-readable half (parse_sources over this format) is covered by Task 1-tests with a format fixture.

**Verify:**
Run: `grep -nE "source:" agents/davinci.md skills/podcast/SKILL.md`
Expected: the new source-ref requirement appears in both the agent contract and the skill step 5.
<!-- /section -->

<!-- section: task-3 keywords: zhijianyuan, agent, structured-only -->
### Task 3: zhijianyuan fact-checker agent (structured-only)

**Maps to Impact Map:** Data path, Shared surfaces, Must remain unchanged (temperature)

**Files:**
- Create: `agents/zhijianyuan.md`

**Expected outcome:** A structured-only subagent that reads `finalize-result.json` body + `material-summary.md`, extracts ONLY objective quantitative/event claims, classifies subjective material as `subjective-skip` (untouched), maps each objective claim to a material-summary `cited_fact_id`, spot WebSearch-verifies survivors, and emits strict JSON `factcheck-verdict.json`.

**Non-goals:**
- Does NOT rewrite the body (like 钱钟书, it is a judge not a writer; no speakAs, no first-person, no narrative voice).
- Does NOT flag, hedge, soften, or comment on subjective/opinion/weak-source content — those are emitted as `subjective-skip` and left exactly as written.

**Touched surface:** `agents/zhijianyuan.md`

**Regression shield:** Frontmatter and discipline mirror `agents/qianzhongshu.md` (structured-only, no speakAs) plus `tools: WebSearch, WebFetch` for spot-verification. Explicit constraint line: subjective material is out of scope and must never be flagged.

**Task Contract:**
- Expected behavior: given a script, the agent produces a verdict that lists each hard fact with a source mapping and a verdict, and never touches the host's opinions.
- Automated verify: N/A — agent prompt (config). Behavior validated by the Task 5 dry-run against real data.
- Real path verify: Task 5.
- Manual/device verify: none.

**Steps:**
1. Frontmatter: `name: zhijianyuan`, `tools: [Read, Write, Bash, WebSearch, WebFetch, Agent]`.
2. Role: "数据质检员" — objective fact accuracy + sourcing ONLY. Explicitly distinct from 老黑 (老黑 = argument falsifiability/counter-evidence; 质检员 = data accuracy/provenance).
3. Output contract (strict JSON, no code fences, no prose), written to the scratch path the skill passes:
   ```json
   {
     "claims": [
       {"claim": "<原文事实片段>", "type": "number|event|temporal|research-citation",
        "cited_fact_id": "<material-summary 事实 slug 或 null>",
        "verdict": "sourced|unsourced|contradicted|subjective-skip",
        "note": "<一句话依据>"}
     ]
   }
   ```
4. Hard rules in the prompt:
   - Extract ONLY objective claims asserting a PAST/PRESENT fact about the world: quantitative figures (估值/营收/基准分/百分比/日期), named events ("WWDC 开放第三方"), temporal-factual assertions ("上周发布X"), cited research conclusions. Numbers may be written in CJK form ("九百六十五亿"、"八百五十亿") — extract these too, not only Arabic digits.
   - **subjective-skip (NEVER verify, NEVER flag)** — emit these as `subjective-skip`: first-person feelings, opinions, value judgments, "朋友说"、二手印象、弱来源观点, AND — critically — **the host's own conditional/predictive bets and stance thresholds** ("如果续约率超过 90%…"、"如果 IPO 推迟或续约率低于 80%…"、"我押注…"、"到 2027 年…"). A number inside a forward-looking bet or a hypothetical threshold is a STANCE parameter, not a factual claim about the present world — it is temperature, not data. Only a number asserting what IS/WAS true needs sourcing.
   - **Disambiguation rule (load-bearing — the gate trusts this classification, DP-001=A):** decide by the MARKER, deterministically:
     - figure governed by a forward/conditional marker (如果…/到20XX/超过…则/我押/可观测信号/未来/将) → `subjective-skip` (it is a bet/stance — neither verifiable nor in scope).
     - bare present/past world-assertion with no such marker (估值是X / 年化营收X / 基准分X / 已发布X) → `objective` (classify it objective even if it sits in an otherwise opinionated sentence; do NOT hide it in `subjective-skip`).
     Rationale for keying on the marker rather than an "unsure→objective" default: a misclassified bet has no material-summary fact → it would flag → re-dispatch would SOFTEN it = the property-4 regression. So when a figure carries any forward/conditional marker, it MUST be `subjective-skip`. This host's bets always carry such markers ("如果续约率超过 90%", "到 2026 年底", "我押"), so the marker test is reliable for this voice.
   - For each objective claim, find the matching "当日新闻背景" fact and put its slug in `cited_fact_id`; if none matches → `cited_fact_id: null`.
   - WebSearch is for **contradiction detection only**: spot-check objective claims that carry a number or named event; if a web source contradicts the claim → `verdict:"contradicted"`. You do NOT certify a claim as `sourced` — sourcing is decided by the coded gate from `cited_fact_id` + recorded provenance, not by you. Set `verdict` to your best read, but know the gate recomputes sourcing and only honors your `contradicted` signal.
   - Never invent a source. Never rewrite the body. Never念出后台把手名 (vault/PKOS/brief/GetNote) — though this agent emits JSON only, never listener text.
5. ⚠️ No test: agent prompt. Validated by Task 5 (which asserts the 06-11 conditional bets land in `subjective-skip`).

**Verify:**
Run: `test -f agents/zhijianyuan.md && grep -nE "subjective-skip|sourced|unsourced|contradicted" agents/zhijianyuan.md`
Expected: the four verdict enum values and the subjective-skip carve-out are present.
<!-- /section -->

<!-- section: task-4 keywords: SKILL, step-12a, gate-wiring -->
### Task 4: SKILL.md wiring — step 12a gate + contract table + re-dispatch loop

**Maps to Impact Map:** User path, Data path, Shared surfaces, Existing consumers

**Files:**
- Modify: `skills/podcast/SKILL.md` (insert step 12a in the deterministic spine; add per-step contract table row; reference the coded gate)

**Expected outcome:** The pipeline runs the fact-check gate between finalize (step 12) and broadcast (step 13). On a hard-flag it re-dispatches 快刀青衣 to attach a source or soften the claim; capped retries; on persistent failure it surfaces to the user (no silent partial), matching the existing per-step gate discipline.

**Non-goals:**
- Do NOT reorder or alter steps 1–12 or 13–17.
- Do NOT make the gate advisory — it is blocking like `check_artifact`.

**Touched surface:** `skills/podcast/SKILL.md` deterministic spine + contract table.

**Regression shield:** Reuse the existing gate-handling prose pattern ("re-dispatch same inputs, cap 1 retry, second miss → surface to user"). The gate decision comes from `lib/factcheck.check_factcheck`, not agent self-report.

**Task Contract:**
- Expected behavior: a run that produces an unsourced number gets caught before TTS and is re-finalized; a clean run flows straight through with no listener-visible change.
- Automated verify: N/A — skill prose (config). Integration validated by Task 5.
- Real path verify: Task 5.
- Manual/device verify: none.

**Steps:**
1. Insert **step 12a (Fact-check gate — 质检员 + coded gate, blocking)** after step 12:
   - dispatch `agents/zhijianyuan.md` consuming `finalize-result.json` `body` + `material-summary.md`; it writes `factcheck-verdict.json` to scratch.
   - call `from lib.factcheck import check_factcheck; check_factcheck(scratch, material_summary_path)` — pass BOTH the scratch dir and the material-summary path (the gate recomputes sourcing from the recorded provenance, per Task 1-impl step 4).
   - if `ok=False`: re-dispatch 快刀青衣 (step-12 finalize) with the `flagged` claims + instruction to attach a recorded source OR soften each flagged claim to qualitative (no number/no asserted event); re-run 12a. Cap at 1 retry.
   - on second miss: surface `flagged` to the user as a hard去假 gap and STOP (no silent ship), same discipline as the artifact gate.
   - explicit note: `flagged` by construction contains ONLY objective claims (subjective-skip is never flagged, Task 1-impl step 4). The re-dispatch therefore can never touch an opinion or a conditional/predictive bet — the temperature-principle guarantee is enforced by the gate, not by 快刀青衣's discretion.
2. Add a row to the per-step contract table: `| 12a | zhijianyuan + factcheck | finalize body + material-summary | factcheck-verdict.json | check_factcheck(scratch, material-summary) — ok iff every objective claim traces & none contradicted; subjective-skip never flagged |`.
3. Update the "Out of scope" / phase note if needed to record the fact-checker as in-pipeline.

**Verify:**
Run: `grep -nE "12a|check_factcheck|zhijianyuan" skills/podcast/SKILL.md`
Expected: step 12a, the coded gate call, and the agent appear in the spine and the contract table.
<!-- /section -->

<!-- section: task-5 keywords: dry-run, verification, 2026-06-11-scratch -->
### Task 5: Verification dry-run against the existing 2026-06-11 scratch

**Maps to Impact Map:** Regression checks, User path

**Files:**
- (no source files; this is the feature's real-path verification)

**Expected outcome:** Demonstrate end-to-end against the REAL 2026-06-11 artifacts (verified content, not assumed): (a) `parse_sources` over the EXISTING (pre-contract) material-summary reports its news facts as `ref=None` (the bullets predate Task 2's source-ref contract); (b) a `zhijianyuan` dispatch over the EXISTING `finalize-result.json` body produces a verdict where the body's actual factual claims — the **CJK-spelled** valuations "九百六十五亿"(Anthropic) / "八百五十亿"(OpenAI) / "四百七十亿"(年化营收), the "差距约 13%", and the "32K token" spec — are flagged `unsourced` (they cite no sourced material-summary fact in the old summary); AND the body's conditional/predictive bets "续约率超过 90%" / "续约率低于 80%" / "增长超过 50%" plus the opinion passages ("怪物养在体内", "情感信任 才真正拥有你") are `subjective-skip` and **never flagged**.

**Non-goals:**
- Not a full-suite run (that is `test-changes`' job). This is a targeted real-data verification of THIS gate.
- Does not modify the existing scratch artifacts.

**Touched surface:** read-only against `/Users/norvyn/Obsidian/PKOS/90-Productions/Podcasts/.scratch-2026-06-11-morning/`.

**Regression shield:** Confirms the temperature-preservation property on the HARD real case — the 90%/80%/50% conditional bets (numeric, but stance) must be `subjective-skip`, not just the obvious feelings. Zero subjective/bet passages flagged.

**Task Contract:**
- Expected behavior: the dry-run prints, for the real 06-11 episode, which hard facts would be blocked AND confirms the host's bets and opinions were left untouched.
- Automated verify: the `parse_sources` command below prints the real unsourced news facts (non-empty); the `zhijianyuan`+`check_factcheck` dry-run verdict is inspected to confirm (i) the CJK valuations / 13% / 32K are in `flagged`, (ii) the 90%/80%/50% bets are `subjective-skip` and NOT in `flagged`.
- Real path verify: this task IS the real-path verify for Tasks 1–4.
- Manual/device verify: read the printed verdict; confirm property-2 + property-4 on the conditional bets specifically.

**Steps:**
1. `parse_sources` over the existing material-summary; print the `ref=None` fact list (expected non-empty: the WWDC / Anthropic-$965B / GPT-5.4 bullets, which carry no `(source: ...)` in the pre-contract summary).
2. Dispatch `agents/zhijianyuan.md` over the existing `finalize-result.json` body + material-summary; write `factcheck-verdict.json` to a TEMP scratch copy (not the original). Confirm the agent extracted the CJK-spelled valuations (not just Arabic digits).
3. Run `check_factcheck(temp_scratch, <existing material-summary path>)`; print `ok`, `flagged`, and the list of `subjective-skip` claims.
4. Confirm by inspection, BOTH directions:
   - every `flagged` item is an objective past/present fact (the CJK valuations / 13% / 32K token) — property 1 & 2;
   - every conditional/predictive bet (90%/80%/50%) and every opinion is `subjective-skip` and absent from `flagged` — property 4. If ANY bet or opinion is flagged → the `zhijianyuan` carve-out (Task 3 step 4) is too aggressive; tighten and re-run before proceeding.

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/podcast-studio/podcast-studio && python3 -c "import sys; sys.path.insert(0,'.'); from lib.factcheck import parse_sources; t=open('/Users/norvyn/Obsidian/PKOS/90-Productions/Podcasts/.scratch-2026-06-11-morning/material-summary.md',encoding='utf-8').read(); s=parse_sources(t); print('unsourced facts:', [k for k,v in s.items() if v.get('ref') is None])"`
Expected: prints a non-empty list of unsourced facts (the pre-contract news bullets), proving the parser + the grounding gap are real.
<!-- /section -->

## Decisions

### [DP-001] Scope of the coded recompute vs the trusted fact/opinion classification (resolved)

**Context:** The coded gate recomputes sourcing only for claims the agent classified as objective; the objective-vs-`subjective-skip` classification is itself made by the agent. A fabricated fact mislabeled `subjective-skip` would skip the recompute.
**Options:**
- A: Narrow the property-1 guarantee honestly — sourcing is recomputed within the agent's objective set; classification is a trusted boundary (subjective material is neither verifiable nor in scope). Mitigate misclassification with an explicit prompt disambiguation rule + the WebSearch `contradicted` backstop. Zero temperature risk.
- B/C: Add a coded figure-scanner that re-routes hard numbers found inside `subjective-skip` back to the recompute. Tighter去假 floor, but risks softening the host's real conditional bets — the exact temperature regression this feature exists to prevent.
**Chosen:** A — per the user (2026-06-11): subjective attitude neither should be nor can be verified; do not build a code heuristic that risks touching real bets. Encoded as the marker-based disambiguation rule in Task 3 step 4 and the honest scope boundary in Architecture.

(Other design choices were fixed by the session brief and stated inline: WebSearch in `agents/zhijianyuan.md` runs for **contradiction detection only**, not to certify sourcing — sourcing is decided by the coded `trace_claim`; re-dispatch on flag goes to 快刀青衣 re-finalize, not a separate patch agent.)

---
## Verification
- **Verdict:** Approved (cycle 2: all 5 must-revise items CLOSED; NEW-1 resolved via DP-001=A)
- **Date:** 2026-06-11
