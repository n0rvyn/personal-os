---
type: plan
status: active
contract_version: 2
tags: [paper-digest, paperline, arxiv, fact-ledger, isolation]
refs:
  - docs/06-plans/2026-06-18-paper-digest-show-dev-guide.md
  - docs/06-plans/2026-06-18-paper-digest-show-design.md
  - docs/11-crystals/2026-06-18-paper-digest-show-crystal.md
  - docs/02-architecture/ubiquitous-language.md
---

# Phase 2 — 论文采集侧 (Paper Collection Side) Implementation Plan

**Goal:** Stand up the 论文线 (paper line) collection skeleton so it can autonomously
pick one paper from arXiv, fetch its **full text**, and extract an anchored 论文事实账
(paper fact-ledger) — validated on a real arXiv paper, producing no listener episode yet.

**Architecture:** New line on the line-agnostic engine P1 extracted. Three new collection
stations live in `lib/paperline/` (discovery / fetch / ledger), driven by two new personas
in `agents/papers/` (选题判官 curator + 事实账 ledger-writer). A new `lib/pipeline_papers.py`
holds the **collection-only** topology (config → scratch → discovery → curator → fetch →
ledger); generation/publish stations are added incrementally in P3/P4, not declared-but-dead
here. `lib/lines.py` gains a `PAPER_LINE` bundle and a `"papers"` registry entry. The
"two lines don't fight" firewall test (`test_line_isolation.py`) is activated paper-side
against **exactly four** opinion-only modules (stance / coveredground / magnitude / bible).
Mechanism choices were **resolved from real arXiv samples** (2026-06-18, see header below),
not guessed.

**Tech Stack:** Python 3 (stdlib `urllib` + `xml.etree` for arXiv API/Atom; `subprocess`
for `pdftotext`); pytest (offline fixtures from staged real samples); persona dispatch via
headless `claude -p` (`lib/dispatch.py`).

**Design doc:** docs/06-plans/2026-06-18-paper-digest-show-design.md
**Design analysis:** none
**Crystal file:** docs/11-crystals/2026-06-18-paper-digest-show-crystal.md (D-005, D-007, D-008, D-015, D-017)
**Bug diagnosis:** not applicable
**Threat model:** included

**Resolved-from-samples mechanism choices** (D-017 — validated on real arXiv 2026-06-18, samples staged `.claude/p2-samples/`; these are NOT decision points, the data determined them):
- **Discovery = arXiv API (Atom)**, code-fetches candidates → agent selects. Endpoint
  `https://export.arxiv.org/api/query?search_query=cat:{cat}&sortBy=submittedDate&sortOrder=descending&max_results=N`.
  Per-entry fields confirmed present: `id` (→ arxiv_id), `title`, `summary` (abstract),
  `published`/`updated`, `arxiv:primary_category`, multi `category`, `arxiv:comment`,
  `author/name`, PDF link via `<link rel="related" type="application/pdf">`. (HTTP/80 returns
  empty reply → HTTPS only. The API exposes **no** full-text HTML link — must construct it.)
- **Full text = HTML primary → PDF fallback.** Primary `https://arxiv.org/html/{id}` →
  available iff http 200 AND body contains `ltx_abstract`/`ltx_title_document`; unavailable
  signal = http 404 + body "HTML is not available". Fallback `https://arxiv.org/pdf/{id}` →
  `pdftotext` (poppler; present at `/usr/local/bin/pdftotext`, **not** a declared dep — Task 8
  declares it).
- **No HF Daily Papers in v1.** Discovery stays arXiv-API-only; freshness via `submittedDate`
  sort. Heat signal is a future optional source (design "实验室博客/顶会留作以后可配" + D-006).
  Rationale: acceptance #1 only needs "select 1 from real candidates"; one proven external
  dep before adding a second. Recorded here, not deferred silently.

**Pre-flight risks:**
- `lib/pipeline.py::validate_pipeline` reads a module-global `AGENT_WHITELIST` (opinion personas
  only). The paper topology needs its own whitelist; parameterizing `validate_pipeline(steps, whitelist=AGENT_WHITELIST)`
  with a default keeps opinion behavior byte-identical (Task 6 — guarded by the zero-change regression check).
- `config.resolve()` always validates `vault.*` (REQUIRED_VAULT_KEYS). Adding `papers.*` as a
  **required** top-level key would break every existing opinion config = zero-change violation.
  → `papers.*` is OPTIONAL at resolve time (type-validated when present, like `vault.voice_corpus_dir`);
  the paper line's config station requires it fail-closed at use (Task 1).
- `factcheck` is a SHARED module (P3 faithfulness gate reuses its claim→trace skeleton). Do NOT
  add it to the paper-side firewall list, and do NOT make P2's `paperline` import it (the ledger
  anchor check is self-contained substring matching). Firewall list = the four opinion-only
  modules ONLY (Task 7).

---

## Impact Map

**User path:** None user-visible this phase (背后采集, no episode produced — dev-guide
"用户可见的变化: 无"). The only observable artifact is a `paper-ledger.json` during validation.

**Data path:** arXiv API (Atom XML) → candidate list → 选题判官 picks 1 arxiv_id →
`arxiv.org/html|pdf/{id}` → full text → 事实账 extraction → `paper-ledger.json` (problem /
method / key_results / limitations, each with a verbatim original-text anchor).

**Shared surfaces:** `lib/config.py` (new optional `papers` section), `lib/lines.py` (new
`PAPER_LINE` + registry entry), `lib/pipeline.py::validate_pipeline` (optional `whitelist`
param, default-preserving), `lib/tests/test_line_isolation.py` (activate paper-side test),
`CLAUDE.md` + `requirements`/docs (declare `pdftotext`). New isolated surfaces:
`lib/pipeline_papers.py`, `lib/paperline/*`, `agents/papers/*`.

**Existing consumers:** `lib.runner` imports `get_line` from `lib.lines`; `load_pipeline`
callers for `morning`/`evening`; every existing config consumer of `config.resolve()`.

**Must remain unchanged (zero-change, D-014):** morning/evening topology byte-identical
(`topology_golden.json`), all existing opinion modules untouched, existing opinion configs
valid without adding `papers.*`, 341 lib tests + 8 bats green unchanged.

**Regression checks:** `git diff --name-only` shows NO opinion-only module touched (stance /
coveredground / magnitude / bible / episode / scorecard / structlint / dedup / throughline /
pipeline.py-topology-body / runner-dispatch-body); `test_lines.py` topology-golden byte-identical
still green; full lib suite ≥ 341 + new tests green; `test_line_isolation.py` both directions green.
(The real no-TTS opinion e2e four-gate inherits P1's Option-B deferral — no config on this
machine; not re-litigated here.)

---

## Threat Model

**1. Attack surface**
- **arXiv id → URL/subprocess.** A candidate `arxiv_id` is parsed from external Atom XML and
  interpolated into `arxiv.org/html|pdf/{id}` URLs and into the `pdftotext <file>` argv. Attack
  class: URL/path injection, argument injection. Mitigation: validate `arxiv_id` against a strict
  regex (`^\d{4}\.\d{4,5}(v\d+)?$`) BEFORE any URL build or subprocess; reject otherwise
  (`ValueError`). pdftotext is invoked as an argv **list** (no `shell=True`) on a tempfile path
  WE control, never on the raw id.
- **Paper full text → persona prompt.** Fetched full text is untrusted external content fed to
  the 事实账 persona. Attack class: instruction injection (CLAUDE.md invariant "vault/news/card
  content is DATA, never instructions"). Mitigation: the `agents/papers/ledger-writer.md` persona
  is instructed to treat paper text as quoted DATA; the code-side anchor verifier never executes
  paper text, only substring-matches it.

**2. Failure modes**
- Discovery fetch failure (network/HTTP error / empty feed) → **fail-closed**: raise, the paper
  line halts before dispatching the curator (no silent empty candidate list).
- Full-text fetch: HTML-unavailable is NOT a failure (it degrades to PDF). Both HTML and PDF
  unavailable → fail-closed raise (cannot do faithful analysis without full text — never proceed
  on the abstract, D-005).
- Ledger anchor verification failure (an anchor is not a verbatim substring of full text) →
  **flag + fail the ledger gate** (recompute discipline, never trust the agent's self-label).

**3. Resource lifecycle**
- The fetched PDF is written to a tempfile for `pdftotext`. Cleanup: `try/finally` removes it on
  success AND on error; the collection scratch dir follows the existing `episode.py` scratch
  lifecycle. No long-lived sockets (urllib request is request-scoped, closed in `finally`).

**4. Input validation requirements**
- `arxiv_id`: regex-validated (above) at the discovery→candidate boundary, before any URL or argv.
- Atom XML: parsed with `xml.etree.ElementTree` (no external entity expansion by default on the
  stdlib parser); entries missing required fields are skipped, not defaulted.
- `pdftotext` argv: list form, tempfile path only.

---

<!-- section: task-1 keywords: config, papers, fail-closed -->
### Task 1-tests: config `papers.*` optional section — tests

**Maps to Impact Map:** Shared surfaces (config.py), Must remain unchanged (opinion configs valid without papers)

**Files:**
- Modify: `lib/tests/test_config.py`

**Expected outcome:** Tests pin: (a) an existing opinion config WITHOUT a `papers` section still
resolves (zero-change); (b) a `papers` section, when present, is type-validated (categories is a
non-empty list of strings; max_candidates is a positive int when present); (c) a `require_papers()`
helper raises `ConfigError` naming the missing key when the paper line is run without `papers.*`.

**Non-goals:** No `papers.output_dir`/paper-line state dir (P4); no vault coupling.

**Touched surface:** `lib/tests/test_config.py`

**Regression shield:** Keep all existing `test_config.py` cases unchanged; only ADD papers cases.

**Task Contract:**
- Expected behavior: Running the paper line without a `papers` config section fails with a clear
  "missing required key: papers.categories"-style error; a normal morning/evening config keeps
  working untouched.
- Automated verify: `python3 -m pytest lib/tests/test_config.py -q` — new papers cases FAIL with
  `AttributeError`/`ImportError` (PapersConfig / require_papers not yet defined). State this FAIL
  signal in Steps.
- Real path verify: covered by Task 9 e2e (real config drives a real run).
- Manual/device verify: none.

**Steps:**
1. Add `test_papers_section_absent_opinion_config_still_resolves()` — resolve a minimal vault+tts
   config (existing fixture pattern) with NO papers key; assert it returns a config object (no raise).
2. Add `test_papers_section_present_type_validated()` — categories must be non-empty list[str];
   bad shapes raise `ConfigError`.
3. Add `test_require_papers_raises_when_absent()` — call the new `config.require_papers(cfg)` (or
   `cfg.papers` accessor) on a papers-less config; assert `ConfigError` naming `papers.categories`.
4. Run the command; confirm the new cases FAIL (symbols undefined) — this is the expected pre-impl red.

**Verify:**
Run: `python3 -m pytest lib/tests/test_config.py -q 2>&1 | tail -5`
Expected: existing cases pass; the 3 new papers cases ERROR/FAIL on undefined `PapersConfig`/`require_papers`.
<!-- /section -->

<!-- section: task-1-impl keywords: config, papers, PapersConfig -->
### Task 1-impl: config `papers.*` optional section — implementation

**Depends on:** Task 1-tests
**Crystal ref:** D-005 (configurable arXiv categories)

**Maps to Impact Map:** Shared surfaces (config.py), Must remain unchanged (zero-change for opinion configs)

**Files:**
- Modify: `lib/config.py`

**Expected outcome:** `config.resolve()` parses an OPTIONAL top-level `papers` section into a
`PapersConfig` (categories: list[str]; max_candidates: int default e.g. 60) when present, leaving
it `None` when absent (opinion configs unaffected). A `require_papers(cfg)` helper returns the
`PapersConfig` or raises `ConfigError("missing required key: papers.categories")` — fail-closed at
the paper-line use site, not at resolve time.

**Non-goals:** No paper output/state dir (P4); `vault.*` requirements untouched.

**Touched surface:** `lib/config.py`

**Regression shield:** Do not modify the test files from Task 1-tests (test tampering). Do not add
`papers` to any `REQUIRED_*_KEYS`; existing opinion configs must resolve unchanged.

**Task Contract:**
- Expected behavior: same as Task 1-tests.
- Automated verify: `python3 -m pytest lib/tests/test_config.py -q` exits 0 (all cases pass).
- Real path verify: Task 9.
- Manual/device verify: none.

**Steps:**
1. Add `@dataclass class PapersConfig` (`categories: tuple[str, ...]`, `max_candidates: int`).
2. In `resolve()`, after the tts block, parse optional `papers` (mirror the `vault.voice_corpus_dir`
   optional pattern): when key absent → `papers=None`; when present → type-validate categories
   (non-empty list[str]) and max_candidates (positive int, default 60), else `ConfigError`.
3. Add module-level `def require_papers(cfg) -> PapersConfig:` raising `ConfigError("missing required
   key: papers.categories")` when `cfg.papers is None`.
4. Run the suite; confirm green.

**Verify:**
Run: `python3 -m pytest lib/tests/test_config.py -q 2>&1 | tail -3`
Expected: all pass (existing + 3 new).
<!-- /section -->

<!-- section: task-2 keywords: discovery, arxiv, atom -->
### Task 2-tests: arXiv discovery + Atom parse — tests

**Maps to Impact Map:** Data path (arXiv API → candidate list)

**Files:**
- Create: `lib/tests/test_paperline_discovery.py`
- Create: `lib/tests/fixtures/arxiv-api-sample.xml` (trimmed from `.claude/p2-samples/arxiv-api-cs.CL-sample.xml`)

**Expected outcome:** Tests pin the Atom parser against a REAL staged sample: each candidate dict
has `arxiv_id` (e.g. `2606.19341v1`, parsed from `<id>.../abs/{id}`), `title`, `summary`,
`published`, `primary_category`, `categories` (list), `pdf_url`. Malformed/missing-id entries are
skipped, not defaulted. `fetch_candidates(categories, max_results, fetcher=...)` injects the HTTP
fetcher (offline test passes the fixture bytes).

**Non-goals:** No live network in tests; no selection logic (that's the curator persona).

**Touched surface:** new test + fixture.

**Regression shield:** Fixture is a real arXiv sample — do not hand-edit field values.

**Task Contract:**
- Expected behavior: Given a real arXiv Atom feed, the system yields a clean candidate list a judge
  can read; junk entries drop out.
- Automated verify: `python3 -m pytest lib/tests/test_paperline_discovery.py -q` — FAILS with
  `ModuleNotFoundError: lib.paperline.discovery`.
- Real path verify: Task 9 hits the live API.
- Manual/device verify: none.

**Steps:**
1. Stage the fixture: copy `.claude/p2-samples/arxiv-api-cs.CL-sample.xml` → fixture (trim to 2 entries).
2. `test_parse_atom_fields()`: parse fixture → assert the 7 fields per candidate + arxiv_id regex.
3. `test_parse_skips_entry_missing_id()`: feed a mutated entry with no `<id>` → it is skipped.
4. `test_fetch_candidates_uses_injected_fetcher()`: pass a fake fetcher returning fixture bytes;
   assert candidate count + that the built query URL contains `cat:` + `sortBy=submittedDate`.
5. Run; confirm FAIL on missing module.

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_discovery.py -q 2>&1 | tail -5`
Expected: collection/run FAILS — `lib.paperline.discovery` not found.
<!-- /section -->

<!-- section: task-2-impl keywords: discovery, arxiv, urllib -->
### Task 2-impl: arXiv discovery + Atom parse — implementation

**Depends on:** Task 2-tests
**Crystal ref:** D-005, D-007 (candidates from arXiv; selection is the curator's, not here)

**Maps to Impact Map:** Data path

**Files:**
- Create: `lib/paperline/__init__.py`
- Create: `lib/paperline/discovery.py`

**Expected outcome:** `discovery.py` exposes `parse_atom(xml_bytes) -> list[dict]` and
`fetch_candidates(categories, *, max_results=60, fetcher=_https_get) -> list[dict]`. URL built
over HTTPS to `export.arxiv.org/api/query`; `fetcher` default does a request-scoped `urllib`
GET (closed in `finally`). `arxiv_id` validated `^\d{4}\.\d{4,5}(v\d+)?$` (Threat Model); invalid
or field-missing entries skipped.

**Non-goals:** No selection, no full-text, no HF heat source.

**Touched surface:** new isolated module — imports NOTHING from opinion line.

**Regression shield:** Do not modify Task 2-tests files. `lib/paperline/*` must not import
stance/coveredground/magnitude/bible (Task 7 enforces).

**Task Contract:**
- Expected behavior: same as Task 2-tests.
- Automated verify: `python3 -m pytest lib/tests/test_paperline_discovery.py -q` exits 0.
- Real path verify: Task 9.
- Manual/device verify: none.

**Steps:**
1. Create `lib/paperline/__init__.py` (empty package marker).
2. `parse_atom`: `xml.etree.ElementTree` with Atom + arxiv namespaces; per entry extract the 7
   fields; validate arxiv_id regex; skip on missing id/title/summary.
3. `fetch_candidates`: build query (urlencode `search_query=cat:{c}`, `sortBy=submittedDate`,
   `sortOrder=descending`, `max_results`), call `fetcher(url)`, `parse_atom`. `_https_get` uses
   `urllib.request` with a timeout, `try/finally` close.
4. Run; confirm green.

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_discovery.py -q 2>&1 | tail -3`
Expected: all pass.
<!-- /section -->

<!-- section: task-3 keywords: fetch, fulltext, html, pdf, pdftotext -->
### Task 3-tests: full-text fetch (HTML primary → PDF fallback) — tests

**Maps to Impact Map:** Data path (full text), Threat model (id validation, subprocess)

**Files:**
- Create: `lib/tests/test_paperline_fetch.py`
- Create: `lib/tests/fixtures/arxiv-html-available.html` (from `.claude/p2-samples/arxiv-2606.19341-html-head.html`)
- Create: `lib/tests/fixtures/arxiv-html-unavailable.html` (from `.claude/p2-samples/arxiv-no-html-404.html`)
- Create: `lib/tests/fixtures/arxiv-pdftotext.txt` (from `.claude/p2-samples/arxiv-2606.19341-pdftotext.txt`, trimmed)

**Expected outcome:** Tests pin: (a) HTML-available detection = http 200 + `ltx_abstract`/`ltx_title_document`
present → HTML text extracted; (b) HTML-unavailable = http 404 / markers absent → PDF fallback taken;
(c) PDF path runs `pdftotext` (injected runner) and returns its text; (d) invalid arxiv_id raises
`ValueError` before any fetch; (e) both-unavailable raises (fail-closed). All via injected
fetcher/subprocess — offline.

**Non-goals:** No live network; no ledger extraction.

**Touched surface:** new test + 3 fixtures.

**Task Contract:**
- Expected behavior: The fetcher always returns real full text when the paper has either HTML or a
  PDF; it refuses to proceed on an abstract or a bad id.
- Automated verify: `python3 -m pytest lib/tests/test_paperline_fetch.py -q` — FAILS, `lib.paperline.fetch` missing.
- Real path verify: Task 9 (real paper, real fallback).
- Manual/device verify: none.

**Steps:**
1. Stage the 3 fixtures from `.claude/p2-samples/`.
2. `test_html_available_extracts()`: fake fetcher returns (200, available-html) → result method == "html",
   text non-empty.
3. `test_html_unavailable_falls_back_to_pdf()`: fetcher returns (404, unavailable-html) for html url,
   (200, pdf-bytes) for pdf url; fake pdftotext runner returns fixture txt → method == "pdf".
4. `test_invalid_id_rejected()`: `fetch_fulltext("../etc/passwd", ...)` raises `ValueError`.
5. `test_both_unavailable_raises()`: html 404 + pdf 404 → raises.
6. Run; confirm FAIL on missing module.

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_fetch.py -q 2>&1 | tail -5`
Expected: FAILS — `lib.paperline.fetch` not found.
<!-- /section -->

<!-- section: task-3-impl keywords: fetch, fulltext, fallback, subprocess -->
### Task 3-impl: full-text fetch — implementation

**Depends on:** Task 3-tests
**Crystal ref:** D-005 (抓全文做原文分析, not abstract / not secondary)

**Maps to Impact Map:** Data path, Threat model

**Files:**
- Create: `lib/paperline/fetch.py`

**Expected outcome:** `fetch_fulltext(arxiv_id, *, fetcher=_https_get, pdftotext=_run_pdftotext)
-> dict {method: "html"|"pdf", text: str, source_url: str}`. arxiv_id regex-validated first.
Try `arxiv.org/html/{id}`: if 200 AND contains `ltx_abstract`/`ltx_title_document` → strip tags to
text. Else fetch `arxiv.org/pdf/{id}` to a tempfile, run `pdftotext` (argv list, `try/finally`
unlink). Both unavailable → raise. HTML→text extraction keeps paragraph order (stdlib
`html.parser` or a minimal tag-strip; no new dep).

**Non-goals:** No ledger; no caching.

**Touched surface:** new isolated module.

**Regression shield:** Do not modify Task 3-tests files. No opinion-line imports.

**Task Contract:**
- Expected behavior: same as Task 3-tests.
- Automated verify: `python3 -m pytest lib/tests/test_paperline_fetch.py -q` exits 0.
- Real path verify: Task 9.
- Manual/device verify: none.

**Steps:**
1. `_HTML_MARKERS = ("ltx_abstract", "ltx_title_document")`; `_ID_RE` regex.
2. `fetch_fulltext`: validate id; try html; detect; extract or fall back to pdf→pdftotext; raise if both fail.
3. `_run_pdftotext(pdf_path) -> str`: `subprocess.run(["pdftotext", pdf_path, "-"], ...)` capture stdout
   (or write `-` to stdout); fail-closed on nonzero.
4. Run; confirm green.

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_fetch.py -q 2>&1 | tail -3`
Expected: all pass.
<!-- /section -->

<!-- section: task-4 keywords: ledger, anchor, faithfulness, recompute -->
### Task 4-tests: paper fact-ledger schema + anchor verification — tests

**Maps to Impact Map:** Data path (ledger), Threat model (recompute, don't trust agent)

**Files:**
- Create: `lib/tests/test_paperline_ledger.py`

**Expected outcome:** Tests pin: (a) a valid ledger (problem / method / key_results / limitations,
each entry `{text, anchor}`, key_results also `{metric?, value?}`) round-trips through schema
validation; (b) `verify_anchors(ledger, fulltext)` PASSES when every anchor is a verbatim substring
of fulltext; (c) FLAGS the entry when an anchor is NOT a substring (fabricated/paraphrased) — the
recompute gate, never trusting an agent self-label; (d) a ledger missing any of the four sections
is rejected.

**Non-goals:** No LLM extraction here (that's the persona, Task 5 + Task 9); this is the code gate.

**Touched surface:** new test.

**Task Contract:**
- Expected behavior: A ledger whose claims are all traceable to the paper passes; a ledger with an
  invented number is caught.
- Automated verify: `python3 -m pytest lib/tests/test_paperline_ledger.py -q` — FAILS, `lib.paperline.ledger` missing.
- Real path verify: Task 9 (real ledger from a real paper).
- Manual/device verify: none.

**Steps:**
1. `test_valid_ledger_schema_ok()`: minimal complete ledger validates.
2. `test_missing_section_rejected()`: drop `limitations` → raises/returns invalid.
3. `test_verify_anchors_pass()`: anchors are exact substrings of a sample fulltext → all pass.
4. `test_verify_anchors_flags_fabricated()`: one anchor not in fulltext → flagged with its locator.
5. Run; confirm FAIL on missing module.

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_ledger.py -q 2>&1 | tail -5`
Expected: FAILS — `lib.paperline.ledger` not found.
<!-- /section -->

<!-- section: task-4-impl keywords: ledger, schema, verify-anchors -->
### Task 4-impl: paper fact-ledger schema + anchor verification — implementation

**Depends on:** Task 4-tests
**Crystal ref:** D-008 (anchored fact-ledger as the faithfulness baseline)

**Maps to Impact Map:** Data path, Threat model

**Files:**
- Create: `lib/paperline/ledger.py`

**Expected outcome:** `ledger.py` exposes the schema (`validate_ledger(d) -> None` raising on a
missing/empty section among problem/method/key_results/limitations, each entry needing non-empty
`text` + `anchor`) and `verify_anchors(ledger, fulltext) -> dict {ok: bool, flagged: list}`
recomputing each anchor as a verbatim substring of fulltext (normalize only whitespace, not content).
Self-contained — does NOT import `factcheck` (P3 may, P2 does not), keeping the firewall trivially clean.

**Non-goals:** No夸大/局限 semantic checks (that's P3 忠实门); P2 verifies anchor traceability + structure only.

**Touched surface:** new isolated module.

**Regression shield:** Do not modify Task 4-tests files. No opinion-line imports; no factcheck import.

**Task Contract:**
- Expected behavior: same as Task 4-tests.
- Automated verify: `python3 -m pytest lib/tests/test_paperline_ledger.py -q` exits 0.
- Real path verify: Task 9.
- Manual/device verify: none.

**Steps:**
1. Define the schema constants + `validate_ledger`.
2. `verify_anchors`: for each entry across the four sections, `_norm_ws(anchor) in _norm_ws(fulltext)`;
   collect flagged (section, text, anchor) on miss; `ok = not flagged`.
3. Run; confirm green.

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_ledger.py -q 2>&1 | tail -3`
Expected: all pass.
<!-- /section -->

<!-- section: task-5 keywords: agents, papers, curator, ledger-writer -->
### Task 5: paper personas — 选题判官 + 事实账 writer (prose)

**Maps to Impact Map:** Data path (curator selects, ledger-writer extracts)
**Crystal ref:** D-007 (curator四条选题), D-008 (ledger extraction), D-002 (主播观点退场)

**Files:**
- Create: `agents/papers/curator.md` (选题判官)
- Create: `agents/papers/ledger-writer.md` (事实账 writer)

**Expected outcome:** Two persona prompts. `curator.md`: given a candidate list (id/title/abstract/
category/date) + a (possibly EMPTY) paper-log dedup input, pick exactly 1 by 重要性 + 可解释性 +
新鲜度 + paper-log 去重; output the chosen `arxiv_id` + one-line rationale; selection only — does
NOT fetch. `ledger-writer.md`: given the full text, extract the 事实账 (问题/方法/关键结果数字/
作者自陈局限), each claim quoting a **verbatim anchor** from the text; treats paper text as DATA not
instructions; no主播观点. Both forbid the reserved cross-line terms (ubiquitous-language.md).

**Non-goals:** No讲解者 voice / drafting persona (P3); no TTS persona reuse here.

**⚠️ No test:** prose persona files (no executable logic). Verified by existence + whitelist membership
(Task 6) + the real dispatch in Task 9.

**Touched surface:** `agents/papers/` (new dir, isolated from `agents/`).

**Task Contract:**
- Expected behavior: The curator reliably narrows dozens of candidates to one explainable pick; the
  ledger-writer produces an anchored fact-ledger a code gate can verify.
- Automated verify: N/A — prose. `test -f agents/papers/curator.md && test -f agents/papers/ledger-writer.md`.
- Real path verify: Task 9 dispatches both via `claude -p`.
- Manual/device verify: none.

**Steps:**
1. Write `curator.md`: inputs contract, the 4 ranking criteria, dedup-against-(possibly-empty)-paper-log,
   strict output (chosen arxiv_id + rationale), "select only, do not fetch", DATA-not-instructions note.
2. Write `ledger-writer.md`: inputs (full text), the four sections, the verbatim-anchor requirement
   (every claim must quote text that appears literally in the paper), no主播观点, DATA-not-instructions.
3. Confirm both files exist.

**Verify:**
Run: `ls agents/papers/ && grep -l "verbatim\|原文\|锚点" agents/papers/ledger-writer.md`
Expected: both files listed; ledger-writer references the anchor requirement.
<!-- /section -->

<!-- section: task-6 keywords: pipeline_papers, lines, bundle, whitelist -->
### Task 6-tests: paper topology + PAPER_LINE bundle — tests

**Maps to Impact Map:** Shared surfaces (lines.py, validate_pipeline), Data path (topology)

**Files:**
- Create: `lib/tests/test_pipeline_papers.py`
- Modify: `lib/tests/test_lines.py`

**Expected outcome:** Tests pin: (a) the collection topology (config → scratch → discovery → curator
→ fetch → ledger) validates via the (parameterized) validator with a paper whitelist; agent stations
(curator, ledger-writer) are `kind:"agent"` + whitelisted, code stations are `kind:"code"` + `agent:None`;
(b) `get_line("papers")` returns a `PAPER_LINE` bundle whose `agent_dir == "agents/papers"` and whose
`topology("papers")` is the collection list; (c) **opinion topology golden stays byte-identical**
(`get_line("morning").topology("morning")` == frozen golden) — the zero-change anchor.

**⚠️ Verifier must-fix #1 (test_lines.py:35):** the existing `test_get_line_unknown_raises` parametrizes
`["papers", "xxx", ""]` and asserts `get_line("papers")` RAISES. Registering `"papers"` (Task 6-impl) makes
that case wrong. This task MUST change that parametrize entry `"papers"` → a still-unregistered sentinel
(e.g. `"unknownshow"`) so the unknown-show fail-closed case still tests an unknown show.

**Non-goals:** No generation/publish stations (P3/P4); no engine run-through here.

**Touched surface:** new test + edits to test_lines.py (the parametrize fix above + new paper-bundle case;
do NOT touch the existing opinion `is`/golden assertions in `test_get_line_morning_evening_same_opinion`
and `test_bundle_topology_matches_frozen_golden`).

**Task Contract:**
- Expected behavior: The paper line is registered and structurally valid; morning/evening topology is
  provably unchanged.
- Automated verify: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_lines.py -q`
  — new cases FAIL (`lib.pipeline_papers` / `PAPER_LINE` missing); existing test_lines opinion cases PASS.
- Real path verify: Task 9.
- Manual/device verify: none.

**Steps:**
1. `test_pipeline_papers.py`: import topology builder; assert station names/kinds/order + validates clean.
2. In `test_lines.py` change `test_get_line_unknown_raises` parametrize `"papers"` → `"unknownshow"`
   (must-fix #1); ADD `test_get_line_papers_bundle()` (resolves, agent_dir == "agents/papers", topology) —
   do NOT touch the existing opinion `is`/golden cases.
3. Add `test_opinion_topology_unchanged_after_paper_registration()` asserting morning golden byte-identical.
4. Run; confirm new FAIL + opinion PASS (note: the parametrize edit alone passes once "papers" is the only
   change; the new paper-bundle case is the FAIL-first signal until Task 6-impl lands).

**Verify:**
Run: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_lines.py -q 2>&1 | tail -6`
Expected: paper cases FAIL on missing symbols; opinion/golden cases pass.
<!-- /section -->

<!-- section: task-6-impl keywords: pipeline_papers, PAPER_LINE, registry -->
### Task 6-impl: paper topology + PAPER_LINE bundle — implementation

**Depends on:** Task 6-tests, Task 1-impl, Task 2-impl, Task 3-impl, Task 4-impl, Task 5
**Crystal ref:** D-004 (per-line bundle shape), D-003 (same engine, per-line topology)

**Maps to Impact Map:** Shared surfaces (lines.py, pipeline.py::validate_pipeline), Must remain unchanged

**Files:**
- Create: `lib/pipeline_papers.py`
- Modify: `lib/lines.py`
- Modify: `lib/pipeline.py` (parameterize `validate_pipeline(steps, whitelist=AGENT_WHITELIST)` — default-preserving)

**Expected outcome:** `lib/pipeline_papers.py` defines `PAPER_AGENT_WHITELIST = {"curator","ledger-writer"}`,
`_build_paper_steps()` (collection station dicts) and `load_papers_pipeline(show)` returning a deep copy,
self-validating via `validate_pipeline(steps, whitelist=PAPER_AGENT_WHITELIST)`. `lib/lines.py` adds a
`PAPER_LINE = LineBundle(line_id="paper", topology=_paper_topology, gate_map=..., executor_map=...,
editorial_loader=<collection stub "">, agent_dir="agents/papers", floor_fn=<n/a for collection>)` and
registers `"papers"` → `PAPER_LINE`. `validate_pipeline` gains an optional `whitelist` param defaulting to
the opinion `AGENT_WHITELIST` so morning/evening validation is byte-identical.

**Non-goals:** No generation/publish stations; no full `run_pipeline` integration of `--show papers`
(P4). The collection `executor_map` wires ONLY the collection code stations (discovery/fetch/ledger-verify)
+ the two agent stations; generation executors land in P3.

**Touched surface:** `lib/pipeline_papers.py` (new), `lib/lines.py`, `lib/pipeline.py` (signature only).

**Regression shield:** `validate_pipeline` default arg keeps opinion call sites unchanged. Adding `"papers"`
to `_LINE_REGISTRY` does NOT alter the `morning`/`evening` entries → opinion topology golden byte-identical.
`pipeline_papers.py` imports ONLY `lib.paperline.*`, `lib.pipeline.validate_pipeline`, `lib.config` — never
stance/coveredground/magnitude/bible. Do not modify Task 6-tests files. NOTE (intended bridge): `lib/lines.py`
WILL gain a lazy `from lib.pipeline_papers import …` inside the `PAPER_LINE` callables — this is the one
legitimate cross-line reference (the registry), enabled by must-fix #2's removal of `"lines"` from the
opinion firewall list; it is NOT a firewall violation.

**Task Contract:**
- Expected behavior: same as Task 6-tests.
- Automated verify: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_lines.py -q` exits 0.
- Real path verify: Task 9.
- Manual/device verify: none.

**Steps:**
1. Parameterize `validate_pipeline(steps, whitelist=AGENT_WHITELIST)`; update its internal whitelist check
   to use the param. (Opinion call `validate_pipeline(_build_steps())` unchanged.)
2. Create `lib/pipeline_papers.py` per Expected outcome.
3. Extend `lib/lines.py` with `PAPER_LINE` + registry entry (lazy imports inside bundle callables, mirroring
   the opinion bundle pattern).
4. Run; confirm green.

**Verify:**
Run: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_lines.py -q 2>&1 | tail -3`
Expected: all pass.
<!-- /section -->

<!-- section: task-7 keywords: isolation, firewall, line-isolation -->
### Task 7: activate the two-lines firewall test (paper side)

**Maps to Impact Map:** Shared surfaces (test_line_isolation), Regression checks
**Crystal ref:** D-015 (structural isolation test)

**Files:**
- Modify: `lib/tests/test_line_isolation.py`

**Expected outcome:** `test_paper_line_does_not_import_opinion_line` is un-skipped and implemented: it AST-scans
`lib/pipeline_papers.py` + every `lib/paperline/*.py` and asserts NONE imports the **four** opinion-only
modules (`stance`, `coveredground`, `magnitude`, `bible`). Passes because Tasks 2/3/4/6 kept paperline clean.

**⚠️ Verifier must-fix #2 (test_line_isolation.py:18):** `_OPINION_MODULES` currently lists `"lines"`.
But `lib/lines.py` is the line REGISTRY — the one legitimate cross-line bridge: its `PAPER_LINE` callables
lazily `from lib.pipeline_papers import …` (mirroring how `OPINION_LINE` lazily imports `lib.pipeline`).
`_imported_modules` AST-walks the whole file (lazy imports included), so leaving `"lines"` in the opinion
list makes `test_opinion_line_does_not_import_paper_line` FALSELY flag the registry. This task MUST drop
`"lines"` from `_OPINION_MODULES`. The firewall's intent (D-015) is that line-SPECIFIC logic modules don't
cross-import; the registry is shared infrastructure, not opinion-specific logic. The other 12 opinion
modules (runner/pipeline/episode/stance/… ) stay in the list.

**Non-goals:** Do NOT widen the forbidden list beyond the four (factcheck/config/dispatch/episode/pipeline
are deliberately shareable — widening walls off P3's factcheck reuse).

**⚠️ No test split:** this IS the test; it asserts an invariant that should PASS once paperline exists.

**Touched surface:** `lib/tests/test_line_isolation.py` (remove `@pytest.mark.skip`, implement the paper-side
body, AND drop `"lines"` from `_OPINION_MODULES` per must-fix #2).

**Regression shield:** Leave `test_opinion_line_does_not_import_paper_line`'s remaining 12 modules + its
assertion logic intact; only `"lines"` is removed. The test must still catch a real opinion→paper import
(verify by a temporary local edit during dev, then revert).

**Task Contract:**
- Expected behavior: Optimizing the paper line later can never reach into opinion-line continuity modules
  (and vice versa) — proven structurally, not by discipline.
- Automated verify: `python3 -m pytest lib/tests/test_line_isolation.py -q` exits 0 (both directions).
- Real path verify: structural test IS the verification.
- Manual/device verify: none.

**Steps:**
1. Remove the `@pytest.mark.skip`; implement the body reusing `_imported_modules`, scanning
   `pipeline_papers` + glob `paperline/*.py`, forbidden set = `{"lib.stance","lib.coveredground",
   "lib.magnitude","lib.bible","stance","coveredground","magnitude","bible"}`.
2. Run; confirm both pass.

**Verify:**
Run: `python3 -m pytest lib/tests/test_line_isolation.py -q 2>&1 | tail -3`
Expected: 2 passed (no skips).
<!-- /section -->

<!-- section: task-8 keywords: docs, pdftotext, dependency -->
### Task 8: declare the `pdftotext` system dependency

**Maps to Impact Map:** Shared surfaces (docs)

**Files:**
- Modify: `CLAUDE.md` (Commands § "System: ffmpeg + curl must be on PATH" → add pdftotext for paper-line PDF fallback)
- Modify: `requirements.txt` (comment noting pdftotext/poppler as a system dep; or a README note if requirements is pip-only)

**Expected outcome:** The PDF-fallback system dependency (`pdftotext`, poppler) is documented alongside
ffmpeg/curl, so a fresh environment knows to install poppler. No code change.

**Non-goals:** No pure-Python PDF fallback in v1 (pdftotext present on this machine; a pypdf fallback is a
future option if poppler-less environments appear).

**⚠️ No test:** docs-only.

**Touched surface:** `CLAUDE.md`, `requirements.txt`.

**Task Contract:**
- Expected behavior: A reader setting up the repo learns pdftotext is required for the paper line's PDF path.
- Automated verify: N/A — docs. `grep -q pdftotext CLAUDE.md`.
- Real path verify: none.
- Manual/device verify: none.

**Steps:**
1. Add pdftotext to the CLAUDE.md system-deps line with a one-clause reason (paper-line PDF fallback).
2. Add a note in requirements.txt (or README) that poppler/pdftotext is a system dep.

**Verify:**
Run: `grep -n pdftotext CLAUDE.md requirements.txt 2>/dev/null`
Expected: at least the CLAUDE.md line matches.
<!-- /section -->

<!-- section: task-9 keywords: e2e, real-arxiv, ledger-validation -->
### Task 9: real arXiv collection e2e — select → fetch → ledger (acceptance #1/#2)

**Maps to Impact Map:** Data path (full real path), Regression checks
**Crystal ref:** D-005, D-007, D-008, D-017

**Files:**
- Create: `evals/paperline_collection_e2e.py` (a runnable validation harness, not a unit test)
- Create (output, gitignored): `.claude/p2-samples/paper-ledger.json` (the produced ledger artifact)

**Expected outcome:** A real run-through on LIVE arXiv: `fetch_candidates` pulls real recent candidates →
`agents/papers/curator.md` dispatched via `claude -p` picks 1 (empty paper-log dedup) → `fetch_fulltext`
gets the real full text (HTML or PDF fallback) → `agents/papers/ledger-writer.md` dispatched extracts the
事实账 → `verify_anchors` PASSES (every claim's anchor is a verbatim substring of the real full text) and
all four sections are present. This is the phase's "真实论文跑通" acceptance.

**Non-goals:** No episode/draft/TTS/publish (P3/P4). No engine `--show papers` full run (P4).

**Touched surface:** new eval harness + a produced ledger artifact.

**Task Contract:**
- Expected behavior: On a real arXiv paper, the system selects it, fetches its true full text (not the
  abstract), and produces a fact-ledger whose every claim is traceable to the paper.
- Automated verify: `python3 evals/paperline_collection_e2e.py` exits 0, prints the chosen arxiv_id, the
  fetch method (html/pdf), and `anchors_ok=True`; writes `paper-ledger.json` with 4 non-empty sections.
- Real path verify: THIS IS the real path (live arXiv + real `claude -p` dispatch).
- Manual/device verify: confirm the printed arxiv_id resolves to a real paper and the ledger's key_results
  numbers appear verbatim in the fetched text (the harness asserts this via `verify_anchors`).

**Steps:**
1. Write `evals/paperline_collection_e2e.py`: wire discovery → curator → fetch → ledger-writer → `verify_anchors`;
   print a summary; write the ledger. **Must-fix #3:** do NOT route the personas through
   `lib.dispatch.dispatch_persona` — it rejects non-opinion agents (dispatch.py:197 whitelist) and hardcodes
   `agents/<name>.md` (dispatch.py:211), so it cannot reach `agents/papers/*`. Build a small direct
   `claude -p` subprocess call in the harness: argv list `["claude","-p", prompt, "--append-system-prompt",
   <read agents/papers/<name>.md>, "--allowedTools", <tools>]`, `shell=False`, capture stdout (mirrors
   dispatch_persona's argv shape at dispatch.py:234-244 but reads the papers dir and skips the opinion whitelist).
2. Run it against live arXiv.
3. Assert: 1 paper chosen; fetch method reported; 4 sections non-empty; `verify_anchors().ok is True`.
4. **Deferral fallback (only if dispatch infra is unavailable at execution time):** the CODE path
   (discovery + fetch + verify_anchors on a hand-staged ledger) is fully validated offline + on live arXiv;
   if `claude -p` dispatch to the persona proxy is unreachable (mirrors P1's Option-B config gap), mark the
   AGENT half `⚠️ DEFERRED — needs persona proxy` with the exact resume command, and record the CODE-path
   real-arXiv proof as the partial acceptance. Do NOT fake a ledger verdict.

**Verify:**
Run: `python3 evals/paperline_collection_e2e.py 2>&1 | tail -15`
Expected: prints chosen arxiv_id + fetch method + `anchors_ok=True`; `paper-ledger.json` has 4 non-empty sections.
<!-- /section -->

---

## Verification

Verdict: Approved

plan-verifier (dev-workflow), 2 cycles — report `.claude/reviews/plan-verifier-2026-06-18-p2-collection.md`.
Cycle 1: Must-revise, 3 must-fix (test_lines.py:35 `get_line("papers")`-raises break; `_OPINION_MODULES`
`"lines"` false-flag on the registry bridge; `dispatch_persona` can't reach `agents/papers/*`). All three
applied (Task 6-tests/6-impl/7/9) + spot-checked against real code. Cycle 2: APPROVED — all resolved, no new
must-fix, no regressions (`"unknownshow"` confirmed unregistered; `validate_pipeline` whitelist param,
4-module firewall, golden pin, real-sample fixtures, and threat model all verified sound).

## Decisions

None. — All blocking decisions are locked by the crystal (D-001..D-017). The open
"Architecture decisions" the dev-guide left for /write-plan (arXiv API vs RSS/HTML; HTML vs PDF;
ledger schema; HF heat source) were **resolved from real arXiv samples** per D-017 and stated
inline in the header (`Resolved-from-samples mechanism choices`) — per the Decision Point
Necessity Gate, choices the validated data determines are not decision points.
