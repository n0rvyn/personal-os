"""Paper-line collection topology — the Phase-2 collection skeleton.

The paper line (P2 collection side) is a separate line on the same
line-agnostic engine P1 extracted. This module holds its **collection-only**
topology:

    config → scratch → discovery → curator → fetch → ledger-write → ledger-verify

Generation/publish stations (script writing, TTS, publish, etc.) land in
P3/P4. Declaring them here would create dead-code stations; the plan's
"generation executors land in P3" constraint governs this boundary.

Two agent stations: `curator` (选题判官 — picks 1 arxiv_id from candidates)
and `ledger-write` (事实账 writer — extracts problem/method/key_results/
limitations). Both are gated by `PAPER_AGENT_WHITELIST`, distinct from
the opinion `AGENT_WHITELIST`. Five code stations: config (load papers
config), scratch (make_scratch), discovery (fetch_candidates from arXiv),
fetch (full-text HTML→PDF fallback), ledger-verify (validate_ledger +
verify_anchors gate).

`validate_pipeline` is parameterized to accept a custom whitelist
(P2 plan, Task 6-impl), so the paper topology validates against its own
PAPER_AGENT_WHITELIST while the opinion topology's call site stays
byte-identical (default = AGENT_WHITELIST).

This module imports ONLY `lib.paperline.*`, `lib.pipeline.validate_pipeline`,
and `lib.config` — never stance/coveredground/magnitude/bible (Task 7
firewall).
"""
from __future__ import annotations

from typing import Any

from lib.pipeline import validate_pipeline


# ---------------------------------------------------------------------------
# Agent whitelist — must match the two personas the collection topology
# dispatches (Task 5 creates agents/papers/curator.md and ledger-writer.md).
# Distinct from the opinion-line AGENT_WHITELIST in lib.pipeline.py.
# ---------------------------------------------------------------------------
PAPER_AGENT_WHITELIST = frozenset({
    # collection (P2)
    "curator",
    "ledger-writer",
    # generation (P3): committee drafts, 科普 scorer, 讲解者 finalize, 忠实门 judge
    "digest-writer",
    "digest-scorer",
    "finalizer",
    "faithfulness-judge",
    # publish side (P4): 口播改写 + TTS. BOTH must be here or load_papers_pipeline
    # raises at validate_pipeline (pipeline.py:739) — `jay` lives in the OPINION
    # AGENT_WHITELIST, not here, and the paper line dispatches from agents/papers/
    # (no fallback to agents/), so the paper line needs its own jay persona + entry.
    "broadcaster",
    "jay",
})


# ---------------------------------------------------------------------------
# The collection topology — `load_papers_pipeline` returns a deep-ish copy of
# this list (fresh list of fresh dicts on every call, mirroring the opinion
# `load_pipeline` contract).
# ---------------------------------------------------------------------------
def _build_paper_steps() -> list[dict[str, Any]]:
    """Construct the canonical paper-collection topology.

    Returns a list of step dicts. Each dict is a fresh copy (callers
    receive a new list of new dicts on every `load_papers_pipeline` call).

    Station roles:
      - config        (code) — load papers.* section via require_papers(cfg).
      - scratch       (code) — make scratch dir; collection output lives here.
      - discovery     (code) — fetch_candidates from arXiv API (lib.paperline.discovery).
      - curator       (agent) — 选题判官 picks 1 arxiv_id (agents/papers/curator.md).
      - fetch         (code) — fetch_fulltext HTML primary → PDF fallback
                                (lib.paperline.fetch).
      - ledger-write  (agent) — 事实账 writer extracts anchored claim ledger
                                (agents/papers/ledger-writer.md).
      - ledger-verify (code) — validate_ledger + verify_anchors gate
                                (lib.paperline.ledger). Fail-closed on
                                missing-section or fabricated anchor.
    """
    return [
        # --- step 1: load papers config (code) -----------------------------
        {
            "name": "config",
            "kind": "code",
            "agent": None,
            "inputs": ["papers.categories", "papers.max_candidates"],
            "artifact": None,
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 2: make scratch (code) ----------------------------------
        {
            "name": "scratch",
            "kind": "code",
            "agent": None,
            "inputs": ["vault.output_dir", "{date}-papers"],
            "artifact": None,
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 2a: same-day guard (code — P4, DP-404=A) ----------------
        # One episode per line per day: if the paper episodes dir already has
        # a {date}-*.md, fail-fast (mirrors opinion `stance-card-exists`).
        # No artifact; the executor returns a halt dict on a hit. Keyed on
        # EPISODE presence (not paper-log) so a log-then-publish-fail leaves
        # no episode → guard passes → curator dedup skips the logged paper.
        {
            "name": "same-day-guard",
            "kind": "code",
            "agent": None,
            "inputs": ["{date}"],
            "artifact": None,
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 3: discovery (code) -------------------------------------
        # fetch_candidates returns a list[dict]; the persona (curator) reads
        # the list from scratch + selects 1.
        {
            "name": "discovery",
            "kind": "code",
            "agent": None,
            "inputs": ["papers.categories", "papers.max_candidates"],
            "artifact": "candidates.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 3a: paper-log-read (code — P4, DP-403=A) ----------------
        # Stages the paper-log for the curator (writes paper-log.json — the
        # curator's REAL dedup input, replacing the literal-string stub) AND
        # drops covered arXiv ids from candidates.json IN PLACE (hard dedup).
        # Fail-closed read (corrupt paper-log → halt). Must run AFTER discovery
        # (needs candidates.json) and BEFORE curator (feeds it).
        {
            "name": "paper-log-read",
            "kind": "code",
            "agent": None,
            "inputs": ["candidates.json"],
            "artifact": "paper-log.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 4: curator (agent — 选题判官) ----------------------------
        # Reads the deduped candidates.json + the real paper-log.json (for
        # concept-level soft-avoidance); writes chosen-arxiv-id.json carrying
        # the pick + one-line rationale. (arXiv-id hard dedup already applied
        # by paper-log-read; the curator does the concept near-dedup judgment.)
        {
            "name": "curator",
            "kind": "agent",
            "agent": "curator",
            "inputs": ["candidates.json", "paper-log.json"],
            "artifact": "chosen-arxiv-id.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 5: fetch (code — full-text HTML→PDF fallback) -----------
        {
            "name": "fetch",
            "kind": "code",
            "agent": None,
            "inputs": ["chosen-arxiv-id.json"],
            "artifact": "fulltext.txt",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 6: ledger-write (agent — 事实账 writer) ------------------
        # Reads fulltext.txt, writes paper-ledger.json (problem/method/
        # key_results/limitations, each entry with a verbatim anchor).
        {
            "name": "ledger-write",
            "kind": "agent",
            "agent": "ledger-writer",
            "inputs": ["fulltext.txt"],
            "artifact": "paper-ledger.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 7: ledger-verify (code — anchor traceability gate) -------
        # validate_ledger (schema: 4 sections, each entry with non-empty
        # text + anchor) + verify_anchors (every anchor is a verbatim
        # substring of fulltext). Fail-closed: a missing section or a
        # fabricated anchor flags the gate.
        {
            "name": "ledger-verify",
            "kind": "code",
            "agent": None,
            "inputs": ["paper-ledger.json", "fulltext.txt"],
            "artifact": "ledger-verify-report.json",
            "gate": [{"fn": "check_ledger_verify"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # ===================================================================
        # GENERATION HALF (P3) — committee → score → select → finalize → 忠实门
        # (downstream of the VERIFIED ledger; produces the 科普 解读稿.md).
        # ===================================================================
        # --- step 8: committee-lite (agent parallel fan-out) --------------
        # Fan out digest-writer across 稿-A/稿-B/稿-C from the verified ledger
        # (变讲法不变观点, no host opinion). Per-slice EXISTENCE gate ONLY — the
        # length floor (过长度门) does NOT live here. committee writes 3 drafts
        # but digest-select keeps 1, so flooring all 3 lets a draft that gets
        # discarded anyway halt an otherwise-fine episode (the live e2e's
        # 2950<4500 B-draft false-halt while A=5385/C=4692 were fine). Gate the
        # deliverable, not the throwaways — the floor moved to the finalize
        # body (step 11), mirroring the opinion line (which floors its finalize
        # body, never every committee draft).
        {
            "name": "committee",
            "kind": "agent",
            "agent": "digest-writer",
            "inputs": ["paper-ledger.json", "papers.md"],
            # Slices use the ASCII A/B/C convention so `_apply_artifact_template`
            # (regex `-([A-C])$`) substitutes cleanly → draft-A.md/draft-B.md/
            # draft-C.md. (CJK 稿-A tags double-append: draft-稿-A + tag 稿-A →
            # draft-稿-稿-A — caught in the live e2e.) The 科普 candidate_id
            # 稿-A/稿-B/稿-C maps by position (A→稿-A) at digest-select.
            "artifact": "draft-A.md",
            # Per-slice existence only (each A/B/C draft landed + non-empty).
            # Length is enforced once, on the selected+finalized body (step 11).
            "gate": [{"fn": "check_artifact"}],
            "parallel": ["A", "B", "C"],
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 9: digest-score (agent — 科普 4-维 structured scorer) ----
        {
            "name": "digest-score",
            "kind": "agent",
            "agent": "digest-scorer",
            "inputs": ["draft-A.md", "draft-B.md", "draft-C.md"],
            "artifact": "digest-score-verdict.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 10: digest-select (code — deterministic select_digest) --
        {
            "name": "digest-select",
            "kind": "code",
            "agent": None,
            "inputs": ["digest-score-verdict.json"],
            "artifact": "digest-selected.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 11: finalize (agent — 讲解者 voice unify) ----------------
        # The _RETRY_PARENT target for the 忠实门 retry (re-derives body content).
        # Carries the paper line's ONE length floor (过长度门): check_min_chars
        # over the finalize body (json_field="body"). The body IS the published
        # deliverable, and the finalizer EXPANDS (translates terms into 大白话 +
        # adds analogies, never trims — live: 6539-char body > any committee
        # draft), so a borderline-short selected draft usually clears here; the
        # floor gates what actually airs. retry=3: a too-short body re-derives
        # the finalizer up to 3× (fresh LLM variance per dispatch — NOT a
        # length-prompt, which the live run proved backfires), then HALTS
        # (D-009, never a half-product). Same gate shape as the opinion line's
        # finalize body floor; the retry policy differs (opinion re-derives via
        # the factcheck/12a station, the paper line via this station's retry=3).
        {
            "name": "finalize",
            "kind": "agent",
            "agent": "finalizer",
            "inputs": ["digest-selected.json", "papers-voice.md"],
            "artifact": "finalize-result.json",
            "gate": [
                {"fn": "check_artifact"},
                {"fn": "check_min_chars", "args": {"min_chars": "floor", "json_field": "body"}},
            ],
            "parallel": None,
            "retry": 3,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 12: 忠实门 (agent extract + code gate, blocking retry=1) --
        # faithfulness-judge extracts per-claim signals; the check_faithfulness
        # gate RECOMPUTES the deterministic floor (traceability + 夸大 + 局限)
        # and merges agent ADD-only flags. A miss re-dispatches `finalize`
        # (_RETRY_PARENT) to re-derive the body; a second miss HALTS — no
        # 解读稿.md is published (D-009: never a half-product).
        {
            "name": "faithfulness",
            "kind": "agent",
            "agent": "faithfulness-judge",
            "inputs": ["finalize-result.json", "paper-ledger.json", "fulltext.txt"],
            "artifact": "faithfulness-verdict.json",
            "gate": [{"fn": "check_faithfulness"}],
            "parallel": None,
            "retry": 1,
            "skip_when": None,
            "fail_soft": None,
        },
        # ===================================================================
        # PUBLISH HALF (P4) — broadcast-script → tts → paper-log-write →
        # publish → cleanup. (paper-log-write + publish + cleanup land in
        # Task 5/6; this Task adds the口播稿 + TTS pair.)
        # ===================================================================
        # --- step 13: broadcast-script (agent — 口播改写) ------------------
        # 讲解者口吻把过了忠实门的 body 改成念稿。论文线自己的 broadcaster
        # persona（agents/papers/broadcaster.md），不引主播声音/观点。
        {
            "name": "broadcast-script",
            "kind": "agent",
            "agent": "broadcaster",
            "inputs": ["finalize-result.json", "papers-voice.md"],
            "artifact": "broadcast-script-{date}.txt",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 14: tts (agent=jay, skip_when=no_tts) -------------------
        # 复用 opinion 的 TTS 机制（synth-auto 经 tts skill）+ skip_when="no_tts"
        # 跳过语义（runner.py:1175 既有，不改）。论文线自己的 jay persona
        # （agents/papers/jay.md — agent_dir 隔离，不回退 agents/）。
        {
            "name": "tts",
            "kind": "agent",
            "agent": "jay",
            "inputs": ["broadcast-script-{date}.txt"],
            "artifact": "audio-files.mp3",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": "no_tts",
            "fail_soft": None,
        },
        # --- step 15: paper-log-write (code — record dedup BEFORE airing) -
        # DP-601=B: append {arxiv_id,title,date,concepts} to papers/state/
        # paper-log.yaml BEFORE publish, so a write failure halts before any
        # .md/.mp3 is aired (no aired-but-unlogged duplicate window; D-009/D-013).
        # Blocking gate (check_artifact) — NOT fail-soft (dedup命脉).
        {
            "name": "paper-log-write",
            "kind": "code",
            "agent": None,
            "inputs": ["chosen-arxiv-id.json", "finalize-result.json", "{date}"],
            "artifact": None,
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 16: publish (code — write .md + move mp3) ---------------
        # Writes the finalize body → {papers_episodes}/{date}-{slug}.md and moves
        # audio-files.mp3 (unless no_tts). Resolves the PAPER episodes dir, not
        # opinion's. Runs AFTER paper-log-write (DP-601=B: log before airing).
        {
            "name": "publish",
            "kind": "code",
            "agent": None,
            "inputs": ["finalize-result.json", "{date}", "{show}"],
            "artifact": None,
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 17: cleanup (code — no-op; runner finally does teardown) -
        {
            "name": "cleanup",
            "kind": "code",
            "agent": None,
            "inputs": [],
            "artifact": None,
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_papers_pipeline(show: str) -> list[dict[str, Any]]:
    """Return the ordered collection topology for the given show.

    `show` is the paper-line show id (currently the only one is "papers").
    The validator's contract fail-closes on a typo'd show name so a typo
    surfaces here, not later at the gate.

    Returns a fresh list of fresh step dicts on every call — callers can
    safely mutate the returned list without affecting subsequent loads.
    Self-validates against `PAPER_AGENT_WHITELIST` so a malformed topology
    surfaces at load time, not at runner dispatch.
    """
    if show != "papers":
        raise ValueError(
            f"unknown paper-line show {show!r}; expected 'papers'"
        )
    # Fresh copies so test isolation / runtime mutations are safe.
    steps = [dict(step) for step in _build_paper_steps()]
    # Self-validate against the paper whitelist (Task 6-impl: parameterized
    # validator accepts a per-line whitelist; default = AGENT_WHITELIST for
    # the opinion path which keeps its call site byte-identical).
    validate_pipeline(steps, whitelist=PAPER_AGENT_WHITELIST)
    return steps
