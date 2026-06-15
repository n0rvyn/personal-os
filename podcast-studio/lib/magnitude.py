"""Magnitude-judge pure helpers (design 2026-06-13-podcast-anti-repetition).

The magnitude judge (`agents/liangchen.md`) decides, for each candidate
topic the collector surfaced, whether it is the SAME ongoing story as a
recent episode and — if so — how much genuinely-new development it carries
(none / light / medium / heavy). That magnitude sets how much airtime the
topic gets this episode (brief / segment / lead).

This module holds only the deterministic glue around that LLM call:

- `build_judge_input(cards, candidates, today, window_days, recent_bodies)`
  assembles the judge's read material. It window-filters stance cards by
  episode date and carries (a) each recent card's bets + open_questions
  (the concrete reference points: "did today move one?") and (b) recent
  episode BODY excerpts — the anchor source. Historical anchors
  (1956苏伊士 / 1973石油) live in episode bodies, NOT in stance-card fields;
  per DP-001=A the magnitude judge no longer surfaces `recent_anchors` —
  anchor extraction moved to the covered-ground post-publish distiller
  (Phase 2). The body excerpts are what feed the distiller. Pure: no
  filesystem IO (the caller loads cards via `lib.stance.load_cards` and
  reads the bodies).

- `parse_verdict(raw)` validates the judge's JSON into a per-candidate list,
  fail-CLOSED (raises, names the field) — the same discipline as
  `episode.select_draft` ignoring a mislabeled `selected` flag.

- `safe_parse_verdict(raw, candidates)` wraps `parse_verdict` fail-SOFT: any
  error / None degrades EVERY candidate to `light` (+ `degraded=True`) so a
  judge hiccup never deadlocks the daily run. "light" is the safe default —
  it costs the topic a one-liner, never a wrong full-episode takeover.

- `magnitude_to_airtime(magnitude)` maps magnitude → airtime tier.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path
from typing import Any

_VALID_MAGNITUDES = ("none", "light", "medium", "heavy")
_EPISODE_MD = re.compile(r"^(\d{4}-\d{2}-\d{2})-.+\.md$")
_AIRTIME = {"none": "brief", "light": "brief", "medium": "segment", "heavy": "lead"}


# --------------------------------------------------------------------------
# input assembly
# --------------------------------------------------------------------------

def _card_date(card: dict[str, Any]) -> str | None:
    ep = card.get("episode")
    if isinstance(ep, dict):
        d = ep.get("date")
        return d if isinstance(d, str) else None
    return None


def _within_window(card_date: str, today: str, window_days: int) -> bool:
    try:
        cd = _dt.date.fromisoformat(card_date)
        td = _dt.date.fromisoformat(today)
    except (ValueError, TypeError):
        return False
    return 0 <= (td - cd).days <= window_days


def gather_recent_bodies(
    output_dir: str | os.PathLike,
    today: str,
    window_days: int = 14,
    max_chars_per_body: int = 8000,
) -> list[dict[str, Any]]:
    """Read recent published episode `.md` bodies — the anchor source.

    Historical anchors (1956苏伊士 / 1973石油) live in episode BODIES, not in
    stance-card fields. Per DP-001=A the magnitude judge no longer surfaces
    `recent_anchors` — anchor extraction moved to the covered-ground
    post-publish distiller (Phase 2), which reads these bodies to catch new
    apparatus. Deterministic (in lib, not Claude self-discipline) so the
    excerpt never silently truncates before a later anchor — bodies can ship
    with literal `\\n` (kuaidao double-escapes), which is normalized here.

    Returns `[{date, excerpt}]` for episode files dated strictly BEFORE `today`
    and within `window_days`, most-recent first. Non-episode files
    (`*.stance.yaml`, `character-bible.md`, scratch dirs) are skipped.
    """
    root = Path(os.path.realpath(str(output_dir)))
    if not root.is_dir():
        return []
    try:
        td = _dt.date.fromisoformat(today)
    except (ValueError, TypeError):
        return []

    rows: list[tuple[str, str]] = []
    for entry in root.iterdir():
        if not entry.is_file():
            continue
        m = _EPISODE_MD.match(entry.name)
        if not m:
            continue
        date = m.group(1)
        try:
            cd = _dt.date.fromisoformat(date)
        except ValueError:
            continue
        if not (0 < (td - cd).days <= window_days):  # strictly before today, within window
            continue
        try:
            text = entry.read_text(encoding="utf-8")
        except OSError:
            continue
        text = text.replace("\\n", "\n").strip()
        rows.append((date, text[:max_chars_per_body]))

    rows.sort(key=lambda t: t[0], reverse=True)
    return [{"date": d, "excerpt": ex} for d, ex in rows]


def build_judge_input(
    cards: list[dict[str, Any]],
    candidates: list[Any],
    today: str,
    window_days: int = 14,
    recent_bodies: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble the magnitude judge's read material (pure; no IO).

    `cards` — prior stance cards (from `lib.stance.load_cards`).
    `candidates` — today's candidate topics (strings or dicts; passed through).
    `recent_bodies` — list of `{date, show, excerpt}` from recent published
        episode `.md` bodies. Per DP-001=A the magnitude judge no longer
        surfaces `recent_anchors`; the body excerpts are the input the
        covered-ground post-publish distiller reads to catch new apparatus.
    """
    recent_cards: list[dict[str, Any]] = []
    for card in cards or []:
        cd = _card_date(card)
        if not cd or not _within_window(cd, today, window_days):
            continue
        ep = card.get("episode", {}) if isinstance(card.get("episode"), dict) else {}
        bets = [
            {
                "id": b.get("id"),
                "claim": b.get("claim"),
                "settle_by": b.get("settle_by"),
                "status": b.get("status"),
            }
            for b in (card.get("bets") or [])
            if isinstance(b, dict)
        ]
        recent_cards.append({
            "date": cd,
            "show": ep.get("show"),
            "bets": bets,
            "open_questions": list(card.get("open_questions") or []),
            "topics": list(card.get("topics") or []),
            "named_concept": list(card.get("named_concept") or []),
        })

    return {
        "today": today,
        "candidates": list(candidates or []),
        "recent_cards": recent_cards,
        "recent_bodies": list(recent_bodies or []),
    }


# --------------------------------------------------------------------------
# verdict parsing
# --------------------------------------------------------------------------

def _coerce_raw(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError(
            f"verdict must be an object (or its JSON string), got "
            f"{type(raw).__name__}"
        )
    return raw


def parse_verdict(raw: Any) -> list[dict[str, Any]]:
    """Validate the judge's verdict into a per-candidate list. Fail-closed.

    Accepts `{"verdicts": [ ... ]}` (dict) or its JSON string. Each entry:
        {candidate (str, required),
         magnitude (one of none/light/medium/heavy, required),
         matches_prior (str|None), what_moved (str), recap_hook (str|None)}
    Per DP-001=A the magnitude judge no longer surfaces `recent_anchors` —
    anchor extraction moved to the covered-ground post-publish distiller.
    Raises ValueError naming the offending field on any violation.
    """
    obj = _coerce_raw(raw)
    verdicts = obj.get("verdicts")
    if not isinstance(verdicts, list):
        raise ValueError("verdict.verdicts must be a list")

    out: list[dict[str, Any]] = []
    for i, item in enumerate(verdicts):
        if not isinstance(item, dict):
            raise ValueError(f"verdicts[{i}] must be an object")
        candidate = item.get("candidate")
        if not isinstance(candidate, str) or not candidate.strip():
            raise ValueError(f"verdicts[{i}].candidate is required (non-empty str)")
        magnitude = item.get("magnitude")
        if magnitude not in _VALID_MAGNITUDES:
            raise ValueError(
                f"verdicts[{i}].magnitude must be one of {_VALID_MAGNITUDES}, "
                f"got {magnitude!r}"
            )
        matches_prior = item.get("matches_prior")
        if matches_prior is not None and not isinstance(matches_prior, str):
            raise ValueError(f"verdicts[{i}].matches_prior must be str or null")
        recap = item.get("recap_hook")
        if recap is not None and not isinstance(recap, str):
            raise ValueError(f"verdicts[{i}].recap_hook must be str or null")
        out.append({
            "candidate": candidate,
            "matches_prior": matches_prior,
            "magnitude": magnitude,
            "what_moved": item.get("what_moved") or "",
            "recap_hook": recap,
        })
    return out


def safe_parse_verdict(raw: Any, candidates: list[Any]) -> list[dict[str, Any]]:
    """Fail-soft wrapper: any parse failure / None degrades every candidate to
    `light` (+ degraded=True). Never raises — a judge hiccup must not deadlock
    the daily run, and `light` is the safe default (one-liner, never a wrong
    full-episode takeover)."""
    try:
        if raw is None:
            raise ValueError("no verdict")
        parsed = parse_verdict(raw)
        for p in parsed:
            p.setdefault("degraded", False)
        return parsed
    except Exception:
        return [
            {
                "candidate": _candidate_label(c),
                "matches_prior": None,
                "magnitude": "light",
                "what_moved": "",
                "recap_hook": None,
                "degraded": True,
            }
            for c in (candidates or [])
        ]


def _candidate_label(c: Any) -> str:
    if isinstance(c, dict):
        return c.get("topic_tag") or c.get("candidate") or c.get("id") or str(c)
    return str(c)


# --------------------------------------------------------------------------
# airtime mapping
# --------------------------------------------------------------------------

def magnitude_to_airtime(magnitude: str) -> str:
    """none/light → 'brief' (一句带过); medium → 'segment' (一段);
    heavy → 'lead' (整期推进)."""
    try:
        return _AIRTIME[magnitude]
    except KeyError:
        raise ValueError(
            f"unknown magnitude {magnitude!r}; expected one of {_VALID_MAGNITUDES}"
        ) from None
