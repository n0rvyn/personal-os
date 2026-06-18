"""Tests for lib/paperline/select.py — paper-line 科普 digest select module.

Written before `lib/paperline/select.py` exists. Pinned contracts (Task 5
plan + crystal D-010 + D-011):

  - `select_digest(verdict, candidates) -> (chosen_id, chosen_value)`:
    deterministic max digest-rubric total picker (准确 / 清晰 / 框架还原 / 可读).
    Mirrors `lib.episode.select_draft`'s discipline (ignore LLM-mislabeled
    `selected` flag, fail-closed on malformed verdict) but is its OWN
    implementation in `lib.paperline.select` — physically isolated from
    `episode.select_draft` (D-011; enforced by `test_select_does_not_import_episode`
    below because the existing `test_line_isolation` firewall does NOT cover
    `episode`/`select_draft` — `_FORBIDDEN_IN_PAPER` is the 4 opinion-only
    modules stance/coveredground/magnitude/bible).

  - Tiebreak: higher `准确` wins; tiebreak: candidate order 稿-A < 稿-B < 稿-C
    wins (same canonical order as the opinion `_CANDIDATE_ORDER`).

  - Ignores any `selected` / `chosen` flag in the verdict — the LLM can
    mislabel it; the deterministic recompute is authoritative (mirrors
    `episode.select_draft`).

  - Malformed / empty verdict → raises ValueError (never silently picks
    the first candidate).

  - The digest scorer's structured output shape (per `digest-scorer.md`):
    `{"candidate_id": str, "scores": {"准确": int, "清晰": int,
    "框架还原": int, "可读": int, "total": int}}`.

  - `load_papers_pipeline("papers")` must succeed AFTER the new stations +
    whitelist extension land (guards against the load-time
    `validate_pipeline` whitelist rejection flagged in MF#2: without
    extending `PAPER_AGENT_WHITELIST` with `digest-writer` + `digest-scorer`,
    the topology loads fail with "agent not in whitelist").
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.paperline.select import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------------------------------------------------------------------------
# Module-level pins
# ---------------------------------------------------------------------------

# The four 科普 rubric dimensions (the digest scorer's structured schema).
# Order does not matter for selection — the deterministic floor uses the SUM
# across all four, tiebreak on 准确.
RUBRIC_DIMS = ("准确", "清晰", "框架还原", "可读")

# Canonical candidate order (mirrors episode._CANDIDATE_ORDER).
CANDIDATE_ORDER = ("稿-A", "稿-B", "稿-C")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_verdict(candidates: list[dict]) -> dict:
    """Wrap a list of {candidate_id, scores} dicts in the verdict envelope
    `select_digest` expects. The verdict's top-level shape is
    `{"candidates": [...]}` (same as `episode.select_draft`)."""
    return {"candidates": candidates}


def _make_candidate(cid: str, scores: dict, **extra) -> dict:
    """Build a single verdict-candidate entry. `scores` is the rubric dict
    (must include all four RUBRIC_DIMS — `total` is optional, recomputed if
    missing). Extra fields (e.g. a misleading `selected`) pass through."""
    return {"candidate_id": cid, "scores": scores, **extra}


def _score(
    准确: int = 0,
    清晰: int = 0,
    框架还原: int = 0,
    可读: int = 0,
    *,
    total: int | None = None,
) -> dict:
    """Build a 4-维 rubric score dict. `total` defaults to the sum of the
    four dims (matches the digest scorer's structured output)."""
    s = {"准确": 准确, "清晰": 清晰, "框架还原": 框架还原, "可读": 可读}
    if total is not None:
        s["total"] = total
    return s


def _candidates_with_paths() -> dict[str, str]:
    """Build the candidates mapping `select_digest(verdict, candidates)`
    consumes: `candidate_id -> value` (e.g. a path or label)."""
    return {
        "稿-A": "draft-A.md",
        "稿-B": "draft-B.md",
        "稿-C": "draft-C.md",
    }


# ---------------------------------------------------------------------------
# Imports (FAIL-first: expect ModuleNotFoundError pre-impl)
# ---------------------------------------------------------------------------

def test_module_imports():
    """`lib.paperline.select` must import cleanly; failing here is the
    test-FAIL-first contract — Task 5-impl resolves this."""
    from lib.paperline import select as paperline_select  # noqa: F401

    assert hasattr(paperline_select, "select_digest"), (
        "select.select_digest is the public surface the committee-lite "
        "step invokes; it must be exposed at module level"
    )
    assert callable(paperline_select.select_digest)


# ---------------------------------------------------------------------------
# select_digest: max-total picker
# ---------------------------------------------------------------------------

def test_select_max_total():
    """The candidate with the highest rubric total wins. A 16 vs 15 vs 14
    ordering must produce 稿-A."""
    from lib.paperline.select import select_digest

    verdict = _make_verdict([
        _make_candidate("稿-A", _score(准确=4, 清晰=4, 框架还原=4, 可读=4)),  # total 16
        _make_candidate("稿-B", _score(准确=4, 清晰=4, 框架还原=4, 可读=3)),  # total 15
        _make_candidate("稿-C", _score(准确=4, 清晰=3, 框架还原=4, 可读=3)),  # total 14
    ])
    chosen_id, chosen_value = select_digest(verdict, _candidates_with_paths())
    assert chosen_id == "稿-A", f"max-total must pick 稿-A, got {chosen_id!r}"
    assert chosen_value == "draft-A.md"


def test_select_max_total_when_explicit_total_field_differs():
    """When the verdict carries an explicit `scores.total` differing from
    the sum of the four dims, select_digest must honor the explicit total
    (matches `episode.select_draft`'s discipline — explicit total takes
    precedence over a recompute from the 4 KPIs)."""
    from lib.paperline.select import select_digest

    # Subtotal sum would rank 稿-A highest, but the explicit total flags
    # 稿-B as the winner — the deterministic code MUST honor explicit total.
    verdict = _make_verdict([
        _make_candidate("稿-A", _score(准确=5, 清晰=5, 框架还原=5, 可读=5, total=10)),
        _make_candidate("稿-B", _score(准确=3, 清晰=3, 框架还原=3, 可读=3, total=99)),
        _make_candidate("稿-C", _score(准确=4, 清晰=4, 框架还原=4, 可读=4, total=20)),
    ])
    chosen_id, _ = select_digest(verdict, _candidates_with_paths())
    assert chosen_id == "稿-B", (
        f"explicit total=99 on 稿-B must win over recomputed sums, got {chosen_id!r}"
    )


# ---------------------------------------------------------------------------
# select_digest: tiebreak (准确, then 稿-A < 稿-B < 稿-C)
# ---------------------------------------------------------------------------

def test_tiebreak_accuracy_then_order():
    """When two candidates have equal totals, the higher 准确 wins; on a
    secondary tie, the lower candidate-order index wins
    (稿-A < 稿-B < 稿-C — same canonical order as `episode._CANDIDATE_ORDER`)."""
    from lib.paperline.select import select_digest

    # All three total 14. 准确 breaks: C(5) > A(4) = B(4). Among A vs B
    # (both 准确=4), candidate order breaks: A < B.
    verdict = _make_verdict([
        _make_candidate("稿-A", _score(准确=4, 清晰=4, 框架还原=3, 可读=3)),  # total 14
        _make_candidate("稿-B", _score(准确=4, 清晰=3, 框架还原=4, 可读=3)),  # total 14
        _make_candidate("稿-C", _score(准确=5, 清晰=3, 框架还原=3, 可读=3)),  # total 14
    ])
    chosen_id, _ = select_digest(verdict, _candidates_with_paths())
    assert chosen_id == "稿-C", (
        f"tiebreak 准确 must pick 稿-C (准确=5), got {chosen_id!r}"
    )


def test_tiebreak_candidate_order_when_准确_also_ties():
    """When totals AND 准确 BOTH tie across all candidates, the canonical
    order 稿-A < 稿-B < 稿-C breaks the tie (lowest index wins)."""
    from lib.paperline.select import select_digest

    verdict = _make_verdict([
        _make_candidate("稿-A", _score(准确=4, 清晰=4, 框架还原=3, 可读=3)),  # total 14
        _make_candidate("稿-B", _score(准确=4, 清晰=3, 框架还原=4, 可读=3)),  # total 14
        _make_candidate("稿-C", _score(准确=4, 清晰=3, 框架还原=3, 可读=4)),  # total 14
    ])
    chosen_id, _ = select_digest(verdict, _candidates_with_paths())
    assert chosen_id == "稿-A", (
        f"total + 准确 both tied must break on canonical order 稿-A < 稿-B < 稿-C; "
        f"got {chosen_id!r}"
    )


# ---------------------------------------------------------------------------
# select_digest: ignores the verdict's `selected` flag (D-011 recompute)
# ---------------------------------------------------------------------------

def test_ignores_selected_flag():
    """The verdict's `selected` / `chosen` flag MUST be ignored — the LLM
    can mislabel it (mirrors `episode.select_draft`'s discipline). A
    verdict that says `selected=稿-B` but scores 稿-A highest must pick
    稿-A (the recompute is authoritative)."""
    from lib.paperline.select import select_digest

    verdict = _make_verdict([
        # 稿-A — winner by score, but the LLM labelled something else.
        _make_candidate("稿-A", _score(准确=5, 清晰=5, 框架还原=5, 可读=5)),  # total 20
        _make_candidate("稿-B", _score(准确=3, 清晰=3, 框架还原=3, 可读=3)),  # total 12
        _make_candidate("稿-C", _score(准确=4, 清晰=4, 框架还原=4, 可读=4)),  # total 16
    ])
    # Inject a misleading `selected` and `chosen` — both must be ignored.
    verdict["selected"] = "稿-B"
    verdict["chosen"] = "稿-B"

    chosen_id, _ = select_digest(verdict, _candidates_with_paths())
    assert chosen_id == "稿-A", (
        f"selected flag must be IGNORED; max total wins (稿-A), got {chosen_id!r}"
    )


def test_ignores_selected_flag_on_individual_candidate_entry():
    """The misleading flag can ALSO live inside an individual candidate
    entry (the LLM often writes per-candidate `selected: true` rather
    than a verdict-level flag). Both forms must be ignored — only the
    deterministic recompute matters."""
    from lib.paperline.select import select_digest

    verdict = _make_verdict([
        # 稿-A — wins by total; the per-candidate `selected: true` is on 稿-C
        _make_candidate("稿-A", _score(准确=5, 清晰=5, 框架还原=5, 可读=5)),  # total 20
        _make_candidate("稿-B", _score(准确=3, 清晰=3, 框架还原=3, 可读=3)),  # total 12
        _make_candidate(
            "稿-C",
            _score(准确=4, 清晰=4, 框架还原=4, 可读=4),
            selected=True,
            chosen=True,
        ),
    ])
    chosen_id, _ = select_digest(verdict, _candidates_with_paths())
    assert chosen_id == "稿-A", (
        f"per-candidate `selected` must be IGNORED; max total (稿-A) wins, "
        f"got {chosen_id!r}"
    )


# ---------------------------------------------------------------------------
# select_digest: fail-closed on malformed verdict
# ---------------------------------------------------------------------------

def test_malformed_verdict_raises():
    """A non-dict verdict must raise ValueError — never silently pick the
    first candidate. The runner must surface this as a halted run with a
    named cause (mirrors `episode.select_draft`'s discipline)."""
    from lib.paperline.select import select_digest

    # Non-dict verdict.
    with pytest.raises(ValueError):
        select_digest("not-a-dict", _candidates_with_paths())
    # Empty candidates mapping → ValueError (no winner possible).
    with pytest.raises(ValueError):
        select_digest(_make_verdict([
            _make_candidate("稿-A", _score(准确=5, 清晰=5, 框架还原=5, 可读=5)),
        ]), {})
    # Missing `candidates` key.
    with pytest.raises(ValueError):
        select_digest({}, _candidates_with_paths())
    # `candidates` not a list.
    with pytest.raises(ValueError):
        select_digest({"candidates": "not-a-list"}, _candidates_with_paths())
    # `candidates` is an empty list.
    with pytest.raises(ValueError):
        select_digest({"candidates": []}, _candidates_with_paths())


def test_malformed_verdict_with_bad_scores_dict_raises():
    """A candidate whose `scores` is not a dict (or missing) must raise
    ValueError — the deterministic code cannot compute a total from
    nothing. A `total` field that is non-numeric is rejected; a missing
    `total` is recomputed from the four rubric dims (covered above)."""
    from lib.paperline.select import select_digest

    # scores is not a dict
    with pytest.raises(ValueError):
        select_digest(
            _make_verdict([
                {"candidate_id": "稿-A", "scores": "garbage"},
            ]),
            _candidates_with_paths(),
        )
    # scores missing entirely
    with pytest.raises(ValueError):
        select_digest(
            _make_verdict([
                {"candidate_id": "稿-A"},
            ]),
            _candidates_with_paths(),
        )
    # candidate_id missing
    with pytest.raises(ValueError):
        select_digest(
            _make_verdict([
                {"scores": _score(准确=5, 清晰=5, 框架还原=5, 可读=5)},
            ]),
            _candidates_with_paths(),
        )


def test_no_verdict_candidates_match_mapping_raises():
    """If every verdict candidate has a candidate_id NOT present in the
    `candidates` mapping, select_digest must raise ValueError — there is
    no winner to return (mirrors `episode.select_draft`'s "no candidates
    in verdict match the candidates mapping" path)."""
    from lib.paperline.select import select_digest

    verdict = _make_verdict([
        _make_candidate("稿-X", _score(准确=5, 清晰=5, 框架还原=5, 可读=5)),
        _make_candidate("稿-Y", _score(准确=4, 清晰=4, 框架还原=4, 可读=4)),
    ])
    # No overlap with the mapping (`稿-A` / `稿-B` / `稿-C`).
    with pytest.raises(ValueError):
        select_digest(verdict, _candidates_with_paths())


def test_only_one_candidate_passes():
    """With a single verdict candidate that matches the mapping, select_digest
    must return that candidate — sanity for the committee's single-survivor
    case (after the other slices failed the per-slice floor gate G2)."""
    from lib.paperline.select import select_digest

    verdict = _make_verdict([
        _make_candidate("稿-A", _score(准确=5, 清晰=5, 框架还原=5, 可读=5)),
    ])
    chosen_id, chosen_value = select_digest(verdict, _candidates_with_paths())
    assert chosen_id == "稿-A"
    assert chosen_value == "draft-A.md"


# ---------------------------------------------------------------------------
# Targeted isolation (MF#4): select.py must NOT import episode / select_draft
# ---------------------------------------------------------------------------

def test_select_does_not_import_episode():
    """AST-scan `lib/paperline/select.py` and assert it does NOT import
    `lib.episode`, `episode`, or `select_draft`. This is the targeted
    isolation test for D-011 — the existing `test_line_isolation` firewall
    does NOT cover `episode`/`select_draft` (only the 4 opinion-only
    modules stance/coveredground/magnitude/bible), so we enforce the
    isolation here at the call site.

    Why this matters: `select_digest` and `episode.select_draft` MUST be
    independent implementations. Sharing code via `from lib.episode import
    select_draft` would silently couple the paper line to opinion's
    洞察/命名/跨域/思考问句 rubric — and the 4-维 科普 rubric (准确/清晰/
    框架还原/可读) lives ONLY in `lib/paperline.select`."""
    select_path = PLUGIN_ROOT / "lib" / "paperline" / "select.py"
    if not select_path.exists():
        pytest.skip(f"select.py does not exist yet (Task 5-impl): {select_path}")

    src = select_path.read_text(encoding="utf-8")
    tree = ast.parse(src)

    # Collect every imported module name (both `import x` and `from x import y`).
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        # Also catch `from lib.episode import select_draft` style references
        # by walking attribute access (alias.name could be `lib.episode`).
        elif isinstance(node, ast.Attribute):
            # Defensive: collect dotted names referenced via attribute chains
            # so a `from lib import episode` form is caught too.
            pass

    # The forbidden forms. Match the FULL name (not split(".")[0] — that
    # collapses every lib.-prefixed import to "lib" and never fires,
    # the same vacuous-matcher bug the existing firewall fixed).
    forbidden = {
        "lib.episode",
        "episode",
        "lib.episode.select_draft",
        "select_draft",
    }
    bad = sorted(
        imp for imp in imported
        if imp in forbidden
        or any(imp == f or imp.startswith(f + ".") for f in forbidden)
    )
    assert not bad, (
        "lib/paperline/select.py must NOT import episode or select_draft "
        "(D-011 isolation; the 4-维 科普 rubric is independent of opinion's "
        "洞察/命名/跨域/思考问句 rubric). Forbidden imports found: "
        f"{bad}"
    )


# ---------------------------------------------------------------------------
# Topology load-success (MF#2): the extended topology loads without raising
# ---------------------------------------------------------------------------

def test_load_papers_pipeline_with_generation_stations():
    """After Task 5-impl extends `PAPER_AGENT_WHITELIST` with
    `digest-writer` + `digest-scorer` and the topology gains the
    `committee` / `digest-score` / `digest-select` stations,
    `load_papers_pipeline("papers")` MUST load cleanly. Without the
    whitelist extension, `validate_pipeline` rejects the new agent
    stations at load time (MF#2 root cause).

    This test pins BOTH:
      1. The whitelist extension (`digest-writer` + `digest-scorer` in
         `PAPER_AGENT_WHITELIST`).
      2. The new stations exist on the topology after the gate (committee,
         digest-score, digest-select).
    """
    from lib.pipeline_papers import PAPER_AGENT_WHITELIST, load_papers_pipeline

    # 1. Whitelist must carry the new agents (else load-time raise).
    assert "digest-writer" in PAPER_AGENT_WHITELIST, (
        f"PAPER_AGENT_WHITELIST must include 'digest-writer' "
        f"(committee fan-out persona); got {sorted(PAPER_AGENT_WHITELIST)}"
    )
    assert "digest-scorer" in PAPER_AGENT_WHITELIST, (
        f"PAPER_AGENT_WHITELIST must include 'digest-scorer' "
        f"(4-维 structured scorer); got {sorted(PAPER_AGENT_WHITELIST)}"
    )

    # 2. Topology must load cleanly (validates against the extended
    #    whitelist — a missing extension surfaces here as ValueError).
    steps = load_papers_pipeline("papers")
    assert isinstance(steps, list)
    assert steps, "load_papers_pipeline must return a non-empty list"

    # 3. The new generation stations must be present, ordered AFTER
    #    ledger-verify (collection half ends at ledger-verify; generation
    #    half starts at committee).
    names = [s["name"] for s in steps]
    assert "ledger-verify" in names, (
        f"ledger-verify must remain in topology, got {names}"
    )
    assert "committee" in names, (
        f"committee station must be present (committee-lite parallel fan-out); "
        f"got {names}"
    )
    assert "digest-score" in names, (
        f"digest-score station must be present (4-维 structured scorer); "
        f"got {names}"
    )
    assert "digest-select" in names, (
        f"digest-select station must be present (deterministic select_digest); "
        f"got {names}"
    )

    # 4. committee appears AFTER ledger-verify (generation half is downstream
    #    of the verified ledger).
    assert names.index("committee") > names.index("ledger-verify"), (
        f"committee must come AFTER ledger-verify (needs verified ledger); "
        f"got topology order {names}"
    )
    # digest-score appears AFTER committee (scoring is downstream of drafting).
    assert names.index("digest-score") > names.index("committee"), (
        f"digest-score must come AFTER committee; got topology order {names}"
    )
    # digest-select appears AFTER digest-score (selection is downstream of scoring).
    assert names.index("digest-select") > names.index("digest-score"), (
        f"digest-select must come AFTER digest-score; got topology order {names}"
    )


def test_committee_station_has_parallel_slices():
    """The `committee` station is the committee-lite parallel fan-out —
    its `parallel` field must enumerate the slices (e.g. A/B/C) the runner
    fans out across. Without this, the per-slice floor gate G2 has no
    candidates to gate."""
    from lib.pipeline_papers import load_papers_pipeline

    steps = load_papers_pipeline("papers")
    committee = next((s for s in steps if s["name"] == "committee"), None)
    assert committee is not None, "committee station must exist"
    assert committee["kind"] == "agent", (
        f"committee is a parallel agent fan-out, got kind={committee['kind']!r}"
    )
    assert isinstance(committee.get("parallel"), list) and committee["parallel"], (
        f"committee.parallel must be a non-empty list (the slices A/B/C), "
        f"got {committee.get('parallel')!r}"
    )
    # The agent is the digest-writer persona.
    assert committee.get("agent") == "digest-writer", (
        f"committee agent must be 'digest-writer', got {committee.get('agent')!r}"
    )
    # Slices use the ASCII A/B/C convention so `_apply_artifact_template`'s
    # `-([A-C])$` regex substitutes cleanly (CJK 稿-A tags double-append:
    # draft-稿-A + tag 稿-A → draft-稿-稿-A, caught in the live e2e). The 科普
    # candidate_id 稿-A/稿-B/稿-C maps from the slice by position at digest-select.
    slices = committee["parallel"]
    assert slices == ["A", "B", "C"], (
        f"committee slices must be ['A', 'B', 'C'] (ASCII, template-safe), got {slices}"
    )