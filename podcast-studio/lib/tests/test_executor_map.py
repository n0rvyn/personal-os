"""Task 4 (P1 engine extraction): executor_map dispatch correctness.

Guards the cycle-1 gate-bypass defect: routing code stations through the line
bundle's executor_map must NOT skip the gate check. Gated code stations
(stance-card-exists / stance-card-gate) must still HALT on a gate miss, and
the ctx side-effects that live in the dispatch block (chosen_id/chosen_path)
must still be applied.
"""
from __future__ import annotations

import json

from lib.pipeline import load_pipeline
from lib.runner import (
    _default_gate_map,
    _execute_step,
    _opinion_executor_map,
    _run_code_step,
)


def _step(name: str) -> dict:
    for s in load_pipeline("morning"):
        if s["name"] == name:
            return dict(s)
    raise KeyError(name)


def _noop_dispatch(*a, **k):
    return {}


def test_executor_map_covers_all_code_stations():
    """The opinion executor_map must cover every code station in the topology —
    a missing station would silently fall through to the no-op return."""
    m = _opinion_executor_map()
    code_stations = {
        s["name"] for s in load_pipeline("morning") if s["kind"] == "code"
    }
    missing = code_stations - set(m)
    assert not missing, f"executor_map missing code stations: {sorted(missing)}"


def test_select_draft_executor_sets_ctx_chosen(tmp_path):
    """select-draft's ctx side-effect (chosen_id/chosen_path) lives in the
    executor, not in _select_draft_step — assert it survives the refactor.
    稿-B has the max total (17) so it must be chosen."""
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    verdict = {
        "candidates": [
            {"candidate_id": "稿-A", "scores": {"洞察": 3, "命名": 3, "跨域": 3, "思考问句": 3, "total": 12, "self_past_dialectic_present": False}, "selected": True, "editor_notes": ""},
            {"candidate_id": "稿-B", "scores": {"洞察": 5, "命名": 4, "跨域": 4, "思考问句": 4, "total": 17, "self_past_dialectic_present": False}, "selected": False, "editor_notes": ""},
            {"candidate_id": "稿-C", "scores": {"洞察": 3, "命名": 3, "跨域": 3, "思考问句": 3, "total": 12, "self_past_dialectic_present": False}, "selected": False, "editor_notes": ""},
        ]
    }
    (scratch / "score-verdict.json").write_text(json.dumps(verdict), encoding="utf-8")
    ctx = {"show": "morning", "scratch_dir": scratch}

    res = _run_code_step(_step("select-draft"), ctx)

    assert res is None
    # never trust the verdict's `selected` flag — max-total wins (稿-B)
    assert ctx["chosen_id"] == "稿-B"
    assert ctx["chosen_path"] == "polish-B.md"


def test_stance_card_exists_tripwire_halts_when_card_present(tmp_path):
    """GATE-BYPASS GUARD: stance-card-exists (a no-op-body, GATED station) must
    HALT when the card already exists. If the executor_map refactor early-returned
    this station at _execute_step (the cycle-1 defect), the gate would be skipped
    and this would NOT halt."""
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    (episodes / "2026-06-18-morning.stance.yaml").write_text(
        "date: 2026-06-18\nbets: []\n", encoding="utf-8"
    )
    ctx = {
        "show": "morning",
        "date": "2026-06-18",
        "scratch_dir": tmp_path,
        "plugin_root": str(tmp_path),
        "episodes_dir": str(episodes),
    }
    res = _execute_step(_step("stance-card-exists"), ctx, _default_gate_map(), _noop_dispatch)
    assert isinstance(res, dict) and res.get("status") == "halted", (
        f"stance-card-exists must halt when card present, got {res!r}"
    )
    assert res.get("failed_step") == "stance-card-exists"


def test_stance_card_gate_tripwire_halts_when_card_missing(tmp_path):
    """GATE-BYPASS GUARD: stance-card-gate must HALT when the card is missing."""
    episodes = tmp_path / "episodes"
    episodes.mkdir()
    ctx = {
        "show": "morning",
        "date": "2026-06-18",
        "scratch_dir": tmp_path,
        "plugin_root": str(tmp_path),
        "episodes_dir": str(episodes),
    }
    res = _execute_step(_step("stance-card-gate"), ctx, _default_gate_map(), _noop_dispatch)
    assert isinstance(res, dict) and res.get("status") == "halted", (
        f"stance-card-gate must halt when card missing, got {res!r}"
    )
    assert res.get("failed_step") == "stance-card-gate"
