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
    "curator",
    "ledger-writer",
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
        # --- step 4: curator (agent — 选题判官) ----------------------------
        # Reads candidates.json + a (possibly empty) paper-log dedup input;
        # writes chosen-arxiv-id.json carrying the pick + one-line rationale.
        {
            "name": "curator",
            "kind": "agent",
            "agent": "curator",
            "inputs": ["candidates.json", "paper-log"],
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
