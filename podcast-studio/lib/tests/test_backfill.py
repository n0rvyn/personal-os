"""Tests for lib/backfill.py — covered-ground backfill over published episodes.

Pins (LLM dispatch is injected as `distill_fn`, so folding is verified
without an LLM):
- a gap date's anchors get folded into the store with the right (date, show)
- show is derived from the stance card; a date with no card is skipped
- a per-episode distill failure is recorded, the rest proceed
"""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

_VALID_CARD = (
    "episode:\n  date: '{date}'\n  show: {show}\nbets: []\nopen_questions: []\n"
)


def _seed(tmp_path):
    state = tmp_path / "state"
    episodes = tmp_path / "episodes"
    state.mkdir()
    episodes.mkdir()
    return state, episodes


def test_backfill_folds_gap_episode_anchors(tmp_path):
    from lib.backfill import backfill_covered_ground
    from lib.coveredground import load_store
    state, episodes = _seed(tmp_path)
    # A published episode + its stance card (attributes the show).
    (episodes / "2026-06-23-信道脱手的清晨.md").write_text("正文", encoding="utf-8")
    (episodes / "2026-06-23-evening.stance.yaml").write_text(
        _VALID_CARD.format(date="2026-06-23", show="evening"), encoding="utf-8"
    )

    def fake_distill(body, date, show):
        assert body == "正文" and date == "2026-06-23" and show == "evening"
        return ["信道脱手", "复述确认"]

    summary = backfill_covered_ground(
        str(tmp_path), start_date="2026-06-23", end_date="2026-06-23",
        today="2026-07-02", distill_fn=fake_distill,
        similarity_fn=lambda a, b: 0.0,
    )
    assert len(summary["folded"]) == 1
    assert summary["folded"][0]["show"] == "evening"
    store = load_store(str(state))
    assert "信道脱手" in store["anchors"]
    assert "复述确认" in store["anchors"]
    ep = store["anchors"]["信道脱手"]["episodes"][0]
    assert ep == {"date": "2026-06-23", "show": "evening"}


def test_backfill_skips_date_without_stance_card(tmp_path):
    from lib.backfill import backfill_covered_ground
    state, episodes = _seed(tmp_path)
    (episodes / "2026-06-25-无卡.md").write_text("正文", encoding="utf-8")
    # No stance card for 2026-06-25 → can't attribute a show → skipped.

    summary = backfill_covered_ground(
        str(tmp_path), start_date="2026-06-25", end_date="2026-06-25",
        today="2026-07-02", distill_fn=lambda b, d, s: ["x"],
        similarity_fn=lambda a, b: 0.0,
    )
    assert summary["folded"] == []
    assert len(summary["skipped"]) == 1
    assert summary["skipped"][0]["date"] == "2026-06-25"


def test_backfill_records_distill_failure_and_continues(tmp_path):
    from lib.backfill import backfill_covered_ground
    from lib.coveredground import load_store
    state, episodes = _seed(tmp_path)
    for d, title in [("2026-06-23", "a"), ("2026-06-24", "b")]:
        (episodes / f"{d}-{title}.md").write_text("正文", encoding="utf-8")
        (episodes / f"{d}-evening.stance.yaml").write_text(
            _VALID_CARD.format(date=d, show="evening"), encoding="utf-8"
        )

    def flaky(body, date, show):
        if date == "2026-06-23":
            raise RuntimeError("boom")
        return ["ok锚"]

    summary = backfill_covered_ground(
        str(tmp_path), start_date="2026-06-23", end_date="2026-06-24",
        today="2026-07-02", distill_fn=flaky,
        similarity_fn=lambda a, b: 0.0,
    )
    assert len(summary["failed"]) == 1
    assert summary["failed"][0]["date"] == "2026-06-23"
    assert len(summary["folded"]) == 1
    assert summary["folded"][0]["date"] == "2026-06-24"
    store = load_store(str(state))
    assert "ok锚" in store["anchors"]
