"""Phase 2 fail-LOUD + absence-detection tests (lib/runner.py helpers).

Pins:
- _log_fail_soft_skip: a fail_soft station skip appends a WARNING to
  state/run-log.jsonl (the fix for the silent covered-ground store gaps).
- _check_continuity_landed: a published episode with NO stance card appends
  a continuity-check WARNING; a present stance card → no warning.
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def _ctx(tmp_path):
    state = tmp_path / "state"
    episodes = tmp_path / "episodes"
    state.mkdir()
    episodes.mkdir()
    return {
        "date": "2026-06-23",
        "show": "evening",
        "state_dir": str(state),
        "episodes_dir": str(episodes),
    }


def test_log_fail_soft_skip_writes_warning(tmp_path):
    from lib.runner import _log_fail_soft_skip
    from lib.runlog import read_run_log
    ctx = _ctx(tmp_path)
    _log_fail_soft_skip(ctx, "coveredground-distill", "dispatch returned ok=False")
    events = read_run_log(ctx["state_dir"])
    assert len(events) == 1
    e = events[0]
    assert e["level"] == "WARNING"
    assert e["step"] == "coveredground-distill"
    assert e["date"] == "2026-06-23"
    assert e["show"] == "evening"
    assert "fail_soft skip" in e["reason"]
    assert "dispatch returned ok=False" in e["reason"]


def test_log_fail_soft_skip_no_state_dir_is_noop(tmp_path):
    """No state_dir in ctx → no crash, no log (fail-soft)."""
    from lib.runner import _log_fail_soft_skip
    _log_fail_soft_skip({"date": "2026-06-23", "show": "evening"}, "s", "r")
    # nothing to assert beyond "did not raise"


def test_continuity_check_warns_when_no_stance_card(tmp_path):
    from lib.runner import _check_continuity_landed
    from lib.runlog import read_run_log
    ctx = _ctx(tmp_path)  # episodes/ is empty → no stance card
    _check_continuity_landed(ctx)
    events = read_run_log(ctx["state_dir"])
    assert len(events) == 1
    e = events[0]
    assert e["level"] == "WARNING"
    assert e["step"] == "continuity-check"
    assert "orphan" in e["reason"]


def test_continuity_check_silent_when_stance_card_present(tmp_path):
    from lib.runner import _check_continuity_landed
    from lib.runlog import read_run_log
    ctx = _ctx(tmp_path)
    # Drop the stance card where stance_card_exists looks for it.
    (Path(ctx["episodes_dir"]) / "2026-06-23-evening.stance.yaml").write_text(
        "episode:\n  date: '2026-06-23'\n  show: evening\nbets: []\nopen_questions: []\n",
        encoding="utf-8",
    )
    _check_continuity_landed(ctx)
    assert read_run_log(ctx["state_dir"]) == []
