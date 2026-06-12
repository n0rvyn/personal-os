"""podcast-studio stance-card continuity mechanics.

Owns the per-episode stance card schema and the append-only ledger that
makes a fabricated track record structurally impossible:

  - write_card refuses to overwrite (append-only)
  - validate_settlement rejects any `settles.ref` that is not in some
    PRIOR card's bet ids AND that is not a bet defined in the SAME card
    being written (closes the self-reference bypass)
  - future-dated writes are rejected (basic backdating guard)
  - empty `{}` placeholder cards are SKIPPED on load (a leftover Phase-2
    placeholder must not block continuity); genuinely malformed non-empty
    cards RAISE naming the file (fail-closed)
  - no confidence-style numeric FIELDS (numbers inside free-text `claim`
    are allowed — the rule targets confidence scores, not all digits)

Cards are YAML (PyYAML); see requirements.txt.

Shared `stance_path` lives in `lib/episode.py` to keep the literal
single-sourced (MR-2).
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import date as _date
from pathlib import Path
from typing import Any

import yaml

from lib.episode import stance_path as _stance_path

# Re-export so callers (and tests) can import everything from lib.stance.
stance_path = _stance_path


# Schema constants -----------------------------------------------------------

# Keys that, if present at any dict level with a numeric value, are rejected.
# The rule targets confidence-style scores; numbers inside `claim` text are
# free text and are not subject to this rule.
_CONFIDENCE_NUMERIC_FIELDS = frozenset({
    "confidence",
    "confidence_score",
    "score",
    "probability",
})


# ---------- internal helpers -------------------------------------------------

def _is_empty_card_placeholder(parsed: Any) -> bool:
    """True if a parsed card is effectively empty (`{}`, or a dict whose
    only key is `episode` with no body, or None)."""
    if parsed is None:
        return True
    if not isinstance(parsed, dict):
        return False
    if parsed == {}:
        return True
    # A dict with only `episode` set is also contentless (the Phase-2
    # `{}` placeholder, possibly slightly expanded).
    non_episode = {k: v for k, v in parsed.items() if k != "episode"}
    if not non_episode:
        return True
    return False


def _walk_for_confidence_fields(obj: Any, path: str = "") -> list[str]:
    """Walk a parsed card and return list of JSON-pointer-ish paths where
    a confidence-named key holds a numeric value. Walks dicts + lists."""
    findings: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}" if path else k
            if k in _CONFIDENCE_NUMERIC_FIELDS and isinstance(v, (int, float)) and not isinstance(v, bool):
                findings.append(sub)
            findings.extend(_walk_for_confidence_fields(v, sub))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            findings.extend(_walk_for_confidence_fields(item, f"{path}[{i}]"))
    return findings


def _validate_bet(bet: dict[str, Any]) -> None:
    """Per-bet field validation. Raises ValueError naming the bet id."""
    if not isinstance(bet, dict):
        raise ValueError(f"bet must be a dict, got {type(bet).__name__}")
    bid = bet.get("id")
    if not isinstance(bid, str) or not bid:
        raise ValueError("bet missing required string field: id")
    for field in ("claim", "horizon", "settle_by", "status"):
        if field not in bet:
            raise ValueError(
                f"bet {bid!r} missing required field: {field}"
            )
    # settle_by must parse as ISO date
    sby = bet["settle_by"]
    if not isinstance(sby, str):
        raise ValueError(
            f"bet {bid!r} settle_by must be a string, got {type(sby).__name__}"
        )
    try:
        _date.fromisoformat(sby)
    except ValueError as e:
        raise ValueError(
            f"bet {bid!r} settle_by not a valid ISO date: {sby!r} ({e})"
        ) from e


def _validate_card_shape(card: dict[str, Any]) -> None:
    """Top-level card validation (independent of anti-fabrication)."""
    if not isinstance(card, dict):
        raise ValueError(f"card must be a dict, got {type(card).__name__}")

    # Required top-level keys
    for key in ("episode", "bets", "open_questions"):
        if key not in card:
            raise ValueError(f"card missing required key: {key}")
    # `episode` must carry date + show
    ep = card["episode"]
    if not isinstance(ep, dict) or "date" not in ep or "show" not in ep:
        raise ValueError("card.episode must be a dict with date + show")

    bets = card["bets"]
    if not isinstance(bets, list):
        raise ValueError("card.bets must be a list")
    for bet in bets:
        _validate_bet(bet)

    oq = card["open_questions"]
    if not isinstance(oq, list):
        raise ValueError("card.open_questions must be a list")

    # Optional but-typed: settles, named_concept, topics, resonance
    if "settles" in card and not isinstance(card["settles"], list):
        raise ValueError("card.settles must be a list if present")
    if "named_concept" in card and not isinstance(card["named_concept"], list):
        raise ValueError("card.named_concept must be a list if present")
    if "topics" in card and not isinstance(card["topics"], list):
        raise ValueError("card.topics must be a list if present")
    # `resonance` (Phase 5, optional): the "would a listener forward /
    # re-listen?" self-critique answer. Free-text only — str or list
    # of str. No numeric confidence numbers (temperature principle:
    # numbers in free-text are fine, but the FIELD itself must be
    # textual, not a score). NOTE: the LLM might surface numeric
    # expressions inside the strings (e.g. "ten-times more shareable");
    # those are within free-text and are allowed.
    if "resonance" in card:
        r = card["resonance"]
        if isinstance(r, bool) or not isinstance(r, (str, list)):
            raise ValueError(
                "card.resonance must be a string or list of strings, "
                f"got {type(r).__name__}"
            )
        if isinstance(r, list):
            for i, item in enumerate(r):
                if isinstance(item, bool) or not isinstance(item, str):
                    raise ValueError(
                        f"card.resonance[{i}] must be a string, got "
                        f"{type(item).__name__}"
                    )

    # No confidence-style numeric FIELDS
    bad = _walk_for_confidence_fields(card)
    if bad:
        raise ValueError(
            "card contains numeric confidence-style field(s): "
            + ", ".join(bad)
            + " (temperature principle: no confidence numbers)"
        )


def _all_prior_bet_ids(prior_cards: list[dict[str, Any]]) -> set[str]:
    """Collect the set of all bet ids across prior cards."""
    ids: set[str] = set()
    for card in prior_cards:
        for bet in card.get("bets", []):
            if isinstance(bet, dict) and isinstance(bet.get("id"), str):
                ids.add(bet["id"])
    return ids


def _this_card_bet_ids(card: dict[str, Any]) -> set[str]:
    return {
        b["id"] for b in card.get("bets", [])
        if isinstance(b, dict) and isinstance(b.get("id"), str)
    }


def validate_settlement(
    settles: list[dict[str, Any]],
    prior_cards: list[dict[str, Any]],
    this_card_bet_ids: set[str] | None = None,
) -> None:
    """Anti-fabrication invariant: every `settles[].ref` must point at a
    bet id that exists in some PRIOR card AND must NOT point at a bet
    defined in the same card being written (closes self-reference bypass).

    Raises ValueError on the first violation, naming the offending ref.
    """
    if not isinstance(settles, list):
        raise ValueError(f"settles must be a list, got {type(settles).__name__}")
    prior_ids = _all_prior_bet_ids(prior_cards)
    same_card_ids = this_card_bet_ids if this_card_bet_ids is not None else set()
    for entry in settles:
        if not isinstance(entry, dict):
            raise ValueError(
                f"settles entry must be a dict, got {type(entry).__name__}"
            )
        ref = entry.get("ref")
        if not isinstance(ref, str) or not ref:
            raise ValueError("settles entry missing required string field: ref")
        if ref in same_card_ids:
            raise ValueError(
                f"settlement ref {ref!r} is defined in the SAME card being "
                f"written (self-reference bypass); must reference a prior card"
            )
        if ref not in prior_ids:
            raise ValueError(
                f"settlement ref {ref!r} not found in any prior card's bets "
                f"(anti-fabrication)"
            )


# ---------- public API -------------------------------------------------------

def load_cards(output_dir: str | os.PathLike) -> list[dict[str, Any]]:
    """Load all stance cards in `output_dir`, sorted by episode date+show.

    - An empty `{}` / contentless card file is SKIPPED (treated as no card,
      so a Phase-2 placeholder never blocks continuity).
    - A genuinely malformed non-empty card RAISES naming the file
      (fail-closed: silently treating as 'no bets' would hide a
      settlement and enable a fabricated fresh start).
    """
    out = Path(os.path.realpath(str(output_dir)))
    if not out.exists():
        return []
    if not out.is_dir():
        return []

    cards: list[dict[str, Any]] = []
    # Pattern: YYYY-MM-DD-{show}.stance.yaml
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}-.+\.stance\.yaml$")
    for entry in sorted(out.iterdir()):
        if not entry.is_file():
            continue
        if not pattern.match(entry.name):
            continue
        try:
            raw = entry.read_text(encoding="utf-8")
        except OSError as e:
            raise RuntimeError(
                f"failed to read stance card {entry}: {e}"
            ) from e

        # Empty file → skip
        if not raw.strip():
            continue

        try:
            parsed = yaml.safe_load(raw)
        except yaml.YAMLError as e:
            raise ValueError(
                f"malformed stance card {entry.name}: YAML parse error: {e}"
            ) from e

        # Empty `{}` or contentless placeholder → skip
        if _is_empty_card_placeholder(parsed):
            continue

        if not isinstance(parsed, dict):
            raise ValueError(
                f"malformed stance card {entry.name}: top-level must be a "
                f"mapping, got {type(parsed).__name__}"
            )

        # Full shape validation (raises on missing required fields, bad
        # settle_by, confidence-numeric-field, etc.)
        try:
            _validate_card_shape(parsed)
        except ValueError as e:
            raise ValueError(
                f"malformed stance card {entry.name}: {e}"
            ) from e

        cards.append(parsed)

    # Sort by episode.date then episode.show for deterministic order
    cards.sort(
        key=lambda c: (
            c.get("episode", {}).get("date", ""),
            c.get("episode", {}).get("show", ""),
        )
    )
    return cards


def write_card(
    output_dir: str | os.PathLike,
    date: str,
    show: str,
    card: dict[str, Any],
) -> Path:
    """Append-only write of a stance card.

    - Refuses if `{date}-{show}.stance.yaml` already exists (append-only).
    - Rejects a future `date` (backdating guard).
    - Validates card shape + no-confidence-numeric-field rule.
    - Validates `settles[]` against prior cards' bet ids (anti-fabrication)
      AND rejects any same-card self-reference.
    - Atomic write: temp in `output_dir` + `os.replace`; on any error the
      temp is removed (no orphan).

    Returns the path written.
    """
    out = Path(os.path.realpath(str(output_dir)))
    if not out.exists():
        raise FileNotFoundError(f"output_dir does not exist: {out}")
    if not out.is_dir():
        raise NotADirectoryError(f"output_dir is not a directory: {out}")

    # Future-date guard
    try:
        episode_date = _date.fromisoformat(date)
    except ValueError as e:
        raise ValueError(
            f"date {date!r} is not a valid ISO date: {e}"
        ) from e
    today = _date.today()
    if episode_date > today:
        raise ValueError(
            f"refusing to write a future-dated stance card: {date} > {today.isoformat()}"
        )

    target = stance_path(out, date, show)
    if target.exists():
        raise FileExistsError(
            f"stance card already exists (append-only): {target}"
        )

    # Top-level shape + no-confidence-numeric-field validation
    _validate_card_shape(card)

    # Anti-fabrication: validate settles against prior cards (excluding
    # any card at the same {date,show} — which can't exist yet anyway since
    # we already checked `target.exists()`, but be explicit).
    prior_cards = [
        c for c in load_cards(out)
        if not (
            c.get("episode", {}).get("date") == date
            and c.get("episode", {}).get("show") == show
        )
    ]
    settles = card.get("settles", [])
    if settles:
        validate_settlement(
            settles,
            prior_cards,
            this_card_bet_ids=_this_card_bet_ids(card),
        )

    # Atomic write: temp in output_dir, then os.replace.
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(out),
    )
    tmp_p = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(card, f, allow_unicode=True, sort_keys=False)
        os.replace(str(tmp_p), str(target))
    except Exception:
        # On any error, remove the temp file (no orphan)
        try:
            tmp_p.unlink()
        except OSError:
            pass
        raise

    return target


def due_bets(
    cards: list[dict[str, Any]],
    today: str,
) -> list[dict[str, Any]]:
    """Return bets with `status: open` and `settle_by <= today`.

    `today` is an ISO date string. Each bet dict is annotated with its
    source `episode` (date + show) so callers can surface it.
    """
    try:
        t = _date.fromisoformat(today)
    except ValueError as e:
        raise ValueError(f"today {today!r} not a valid ISO date: {e}") from e

    out: list[dict[str, Any]] = []
    for card in cards:
        ep = card.get("episode", {})
        for bet in card.get("bets", []):
            if bet.get("status") != "open":
                continue
            sby = bet.get("settle_by")
            if not isinstance(sby, str):
                continue
            try:
                d = _date.fromisoformat(sby)
            except ValueError:
                continue
            if d <= t:
                out.append({**bet, "_source": ep})
    return out


def carried_open_questions(
    cards: list[dict[str, Any]],
    today: str,
    show: str,
) -> list[str]:
    """Open questions from prior cards that should surface in today's `show`.

    Same-day morning→evening carry: a morning card's open_questions surface
    for the same-day evening (and persist until they're settled in a later
    card). Other days' questions do NOT carry (each day is its own arc).
    """
    try:
        t = _date.fromisoformat(today)
    except ValueError as e:
        raise ValueError(f"today {today!r} not a valid ISO date: {e}") from e

    carried: list[str] = []
    for card in cards:
        ep = card.get("episode", {})
        ep_date = ep.get("date")
        ep_show = ep.get("show")
        if not isinstance(ep_date, str):
            continue
        try:
            d = _date.fromisoformat(ep_date)
        except ValueError:
            continue
        # Only same-day morning → same-day evening
        if d != t:
            continue
        if ep_show == show:
            # Same show on the same day — don't surface (a card's own
            # open_questions are its own).
            continue
        if show != "evening":
            # Currently only evening receives morning's carry
            continue
        for q in card.get("open_questions", []):
            if isinstance(q, str) and q:
                carried.append(q)
    return carried


def new_bet_id(date: str, show: str, n: int) -> str:
    """Globally-unique bet id: `bet-{YYYYMMDD}{show}-{n}`.

    `n` is a per-episode counter the writer manages (1-based).
    """
    yyyymmdd = date.replace("-", "")
    return f"bet-{yyyymmdd}{show}-{n}"
