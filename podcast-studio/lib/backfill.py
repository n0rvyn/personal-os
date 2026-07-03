"""podcast-studio covered-ground backfill (Phase 2).

Re-runs the covered-ground distillation over a range of ALREADY-published
episodes, folding their apparatus anchors into `state/covered-ground.yaml`.
This closes the historical store gaps (the real 6/23 + 6/25–27 holes) that the
fail-soft distiller left behind when it silently skipped — now that Phase 2's
fail-LOUD run-log makes those gaps visible, this tool fills them.

Design (testable orchestration; the LLM dispatch is a seam):

- `distill_fn(body, date, show) -> list[str]` is the seam. The REAL caller
  wires it to the coveredground-distiller dispatch (same station the runner
  uses at publish time); tests inject a fake that returns fixed anchors — so
  the fold/merge orchestration is verified WITHOUT an LLM.
- Show is NOT in the episode `.md` filename (`{date}-{title}.md`), so it is
  derived from the stance cards present for that date (`{date}-{show}.stance.yaml`).
  A date with no stance card is SKIPPED and reported (can't attribute a show).
- The store is loaded once, folded in memory across all episodes, written once
  (atomic) at the end — mirrors the runner's update station discipline.
- Returns a structured summary so the caller (and tests) can assert what was
  backfilled vs skipped; never raises on a single-episode distill failure
  (that episode is recorded as failed and the rest proceed).
"""
from __future__ import annotations

import datetime as _dt
import re
from pathlib import Path
from typing import Any, Callable

_EPISODE_MD = re.compile(r"^(\d{4}-\d{2}-\d{2})-.+\.md$")


def _episodes_in_range(
    episodes_dir: Path,
    start_date: str,
    end_date: str,
) -> list[tuple[str, Path]]:
    """Return `[(date, md_path)]` for published episode `.md` files whose date
    is within [start_date, end_date] inclusive, sorted by date ascending."""
    try:
        sd = _dt.date.fromisoformat(start_date)
        ed = _dt.date.fromisoformat(end_date)
    except (ValueError, TypeError):
        return []
    out: list[tuple[str, Path]] = []
    if not episodes_dir.is_dir():
        return out
    for entry in episodes_dir.iterdir():
        if not entry.is_file():
            continue
        m = _EPISODE_MD.match(entry.name)
        if not m:
            continue
        date = m.group(1)
        try:
            d = _dt.date.fromisoformat(date)
        except ValueError:
            continue
        if sd <= d <= ed:
            out.append((date, entry))
    out.sort(key=lambda t: t[0])
    return out


def _shows_for_date(cards: list[dict[str, Any]], date: str) -> list[str]:
    """Shows that have a stance card on `date` (the show attribution source —
    the `.md` filename doesn't carry show)."""
    shows: list[str] = []
    for card in cards:
        ep = card.get("episode", {})
        if isinstance(ep, dict) and ep.get("date") == date:
            s = ep.get("show")
            if isinstance(s, str) and s and s not in shows:
                shows.append(s)
    return shows


def backfill_covered_ground(
    output_dir: str,
    *,
    start_date: str,
    end_date: str,
    today: str,
    distill_fn: Callable[[str, str, str], list[str]],
    state_dir: str | None = None,
    episodes_dir: str | None = None,
    similarity_fn: Callable[[str, str], float] | None = None,
) -> dict[str, Any]:
    """Fold apparatus from published episodes in [start_date, end_date] into
    the covered-ground store.

    Args:
      output_dir: vault output_dir (fallback root for state/episodes).
      start_date/end_date: inclusive ISO date range.
      today: ISO date (unused for folding, carried for symmetry/logging).
      distill_fn: `(body, date, show) -> list[str]` anchors. The seam the real
        caller wires to the coveredground-distiller dispatch.
      state_dir/episodes_dir: Phase-4 dirs; default to output_dir/{state,episodes}.
      similarity_fn: passed through to update_store (reskin fold); default None
        → update_store uses its embed-backed default.

    Returns a summary:
      {"folded": [{date, show, anchors}], "skipped": [{date, reason}],
       "failed": [{date, show, reason}], "store_path": <str>}
    """
    from lib.coveredground import load_store, update_store, write_store
    from lib.stance import load_cards

    root = Path(str(output_dir))
    state = Path(state_dir) if state_dir else root / "state"
    episodes = Path(episodes_dir) if episodes_dir else root / "episodes"

    summary: dict[str, Any] = {
        "folded": [], "skipped": [], "failed": [], "store_path": None,
    }

    eps = _episodes_in_range(episodes, start_date, end_date)
    if not eps:
        return summary

    cards = load_cards(str(episodes))
    store = load_store(str(state))

    for date, md_path in eps:
        shows = _shows_for_date(cards, date)
        if not shows:
            summary["skipped"].append(
                {"date": date, "reason": "no stance card → can't attribute show"}
            )
            continue
        try:
            body = md_path.read_text(encoding="utf-8")
        except OSError as e:
            summary["failed"].append(
                {"date": date, "show": None, "reason": f"read failed: {e}"}
            )
            continue
        for show in shows:
            try:
                anchors = distill_fn(body, date, show)
            except Exception as e:  # noqa: BLE001 — one episode's distill failure must not abort the batch
                summary["failed"].append(
                    {"date": date, "show": show, "reason": f"distill failed: {e}"}
                )
                continue
            anchors = [a for a in (anchors or []) if isinstance(a, str) and a.strip()]
            update_store(
                store,
                anchors=anchors,
                date=date,
                episode={"date": date, "show": show},
                similarity_fn=similarity_fn,
            )
            summary["folded"].append(
                {"date": date, "show": show, "anchors": anchors}
            )

    if summary["folded"]:
        path = write_store(str(state), store)
        summary["store_path"] = str(path)
    return summary
