"""Phase 3: thesis-ledger-update code executor (lib/runner.py).

Pins:
- reads scratch/thesis-apparatus.json, folds theses into state/thesis-ledger.yaml
  with per-episode advanced flag + bet_ids
- missing / empty apparatus → no-op (fail-soft)
- a malformed apparatus json is swallowed (post-publish must never crash)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _ctx(tmp_path):
    scratch = tmp_path / "scratch"
    state = tmp_path / "state"
    scratch.mkdir()
    state.mkdir()
    return {
        "scratch_dir": scratch,
        "state_dir": str(state),
        "date": "2026-06-30",
        "show": "morning",
    }


def test_thesis_update_folds_apparatus_into_ledger(tmp_path):
    from lib.runner import _thesis_ledger_update_executor
    from lib.thesis_ledger import load_store
    ctx = _ctx(tmp_path)
    (ctx["scratch_dir"] / "thesis-apparatus.json").write_text(
        json.dumps({"theses": [
            {"statement": "判断力是最后护城河", "advanced": False, "bet_ids": ["bet-a"]},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    _thesis_ledger_update_executor(ctx)
    store = load_store(ctx["state_dir"])
    assert "判断力是最后护城河" in store["theses"]
    ep = store["theses"]["判断力是最后护城河"]["episodes"][0]
    assert ep == {"date": "2026-06-30", "show": "morning",
                  "advanced": False, "bet_ids": ["bet-a"]}


def test_thesis_update_noop_when_apparatus_missing(tmp_path):
    from lib.runner import _thesis_ledger_update_executor
    from lib.thesis_ledger import load_store
    ctx = _ctx(tmp_path)  # no apparatus file
    _thesis_ledger_update_executor(ctx)
    assert load_store(ctx["state_dir"]) == {"theses": {}}


def test_thesis_update_failsoft_on_malformed_json(tmp_path):
    """A malformed apparatus json must NOT crash the post-publish station."""
    from lib.runner import _thesis_ledger_update_executor
    from lib.thesis_ledger import load_store
    ctx = _ctx(tmp_path)
    (ctx["scratch_dir"] / "thesis-apparatus.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    _thesis_ledger_update_executor(ctx)  # must not raise
    assert load_store(ctx["state_dir"]) == {"theses": {}}
