"""Paper fact-ledger schema + anchor verification (the recompute gate).

The fact-ledger is the faithfulness baseline for the paper line: every claim
the ledger-writer persona extracts from a paper's full text must trace back
to a verbatim substring of that text. This module owns the SCHEMA check
(structure of the ledger dict) and the ANCHOR RECOMPUTE gate (never trust
the agent's self-label — re-verify by substring matching).

Central contracts (D-008 + Threat Model §2 — recompute, never trust agent
self-label):

  - `validate_ledger(d) -> None` raises `LedgerError` on:
      * a missing section among problem / method / key_results / limitations
      * a present-but-empty section (e.g. `problem: []`)
      * an entry missing or empty `text`
      * an entry missing or empty `anchor`
    Returns `None` on success. The four-section requirement is the schema
    gate — without it the anchor verifier has no complete surface to check.

  - `verify_anchors(ledger, fulltext) -> dict {ok: bool, flagged: list}`:
    For each entry across the four sections, normalize whitespace on both
    the anchor and the fulltext (collapse runs of whitespace to a single
    space) and check whether the normalized anchor appears as a substring
    of the normalized fulltext. On a miss, append a flagged entry with
    `section`, `text`, `anchor` (the locator a human can chase down). The
    function does NOT raise on a flagged entry — it returns the verdict and
    lets the caller decide (the ledger gate downstream recomputes).

Self-contained: does NOT import `factcheck` (P3's faithfulness gate may, P2
does not) — keeps the line-isolation firewall clean (see
test_line_isolation.py for the structural proof).

This module imports NOTHING from the opinion line (stance / coveredground /
magnitude / bible); it is part of the paper line and lives under
`lib/paperline/`.
"""
from __future__ import annotations

import re
from typing import Any

# ---------- constants ----------

# The four required sections of a paper fact-ledger. Each section holds a
# list of `{text, anchor, ...}` entries. Order is the contract: the schema
# gate checks sections as a SET (presence), but `verify_anchors` walks them
# in this order so a flagged entry's `section` field is stable.
REQUIRED_SECTIONS: tuple[str, ...] = (
    "problem",
    "method",
    "key_results",
    "limitations",
)


# ---------- errors ----------

class LedgerError(Exception):
    """Schema violation in a paper fact-ledger.

    Raised by `validate_ledger` when the ledger is missing a required
    section, has an empty section, or carries an entry with empty `text` or
    `anchor`. The message names the offending section / field so a caller
    can correct the producing persona prompt without guessing.
    """
    pass


# ---------- whitespace normalization ----------

# A single regex that collapses any run of whitespace (spaces, tabs,
# newlines, CR) to a single space — for anchor recompute. We normalize
# BOTH sides the same way; content tokens (numbers, punctuation) are not
# touched (the rule per the plan: normalize only whitespace, not content).
_WS_RE = re.compile(r"\s+")


def _norm_ws(s: str) -> str:
    """Collapse runs of whitespace to a single space; strip outer whitespace.

    Used for substring matching only — never for storage. This is the
    permissive side of "verbatim": an anchor split across a linebreak in
    the source paper still matches.
    """
    return _WS_RE.sub(" ", s).strip()


# ---------- schema gate ----------

def validate_ledger(d: Any) -> None:
    """Validate a fact-ledger dict against the four-section schema.

    Args:
        d: A candidate fact-ledger. Expected shape:

            {
                "problem":       [{"text": ..., "anchor": ...}, ...],
                "method":        [{"text": ..., "anchor": ...}, ...],
                "key_results":   [{"text": ..., "anchor": ...}, ...],
                "limitations":   [{"text": ..., "anchor": ...}, ...],
            }

            Each entry MAY carry additional fields (e.g. `metric`, `value`
            on `key_results`) — schema checks do not enforce them.

    Raises:
        LedgerError: on any schema violation (missing/empty section,
            missing/empty `text` or `anchor`).
    """
    if not isinstance(d, dict):
        raise LedgerError(
            f"ledger must be a mapping, got {type(d).__name__}"
        )

    # Section presence + non-emptiness.
    for section in REQUIRED_SECTIONS:
        if section not in d:
            raise LedgerError(
                f"missing required section: {section!r} "
                f"(must include all of: {', '.join(REQUIRED_SECTIONS)})"
            )
        section_value = d[section]
        if not isinstance(section_value, list):
            raise LedgerError(
                f"section {section!r} must be a list, "
                f"got {type(section_value).__name__}"
            )
        if not section_value:
            raise LedgerError(
                f"section {section!r} must contain at least one entry"
            )

    # Per-entry shape: each entry must be a mapping with non-empty `text`
    # and `anchor`. Other fields (e.g. `metric`, `value`) are not enforced.
    for section in REQUIRED_SECTIONS:
        for idx, entry in enumerate(d[section]):
            if not isinstance(entry, dict):
                raise LedgerError(
                    f"entry {idx} in section {section!r} must be a mapping, "
                    f"got {type(entry).__name__}"
                )
            text = entry.get("text")
            anchor = entry.get("anchor")
            if not isinstance(text, str) or not text.strip():
                raise LedgerError(
                    f"entry {idx} in section {section!r} missing or empty 'text'"
                )
            if not isinstance(anchor, str) or not anchor.strip():
                raise LedgerError(
                    f"entry {idx} in section {section!r} missing or empty 'anchor'"
                )


# ---------- anchor recompute gate ----------

def verify_anchors(ledger: dict, fulltext: str) -> dict:
    """Recompute every anchor as a verbatim substring of `fulltext`.

    For each entry across the four required sections, normalize whitespace
    on both the anchor and the fulltext (collapse runs of whitespace to a
    single space), then check substring containment. This is the recompute
    discipline that catches a fabricating agent — never trust the ledger's
    self-label, only the substring match against the original full text.

    Args:
        ledger: A fact-ledger (must already pass `validate_ledger`, but
            this function does not re-validate to keep the recompute gate
            independent — pass a known-good ledger for clean output).
        fulltext: The paper's full text (whitespace-normalizable). May be
            empty; in that case every anchor flags.

    Returns:
        A dict:

            {
                "ok": bool,           # True iff no entry flagged
                "flagged": [          # empty list when ok=True
                    {"section": str, "text": str, "anchor": str}, ...
                ],
            }

        Each flagged entry carries the original (non-normalized) anchor
        and the entry's `text` so a human reader can locate the failing
        claim without re-deriving which entry it came from.
    """
    flagged: list[dict] = []
    norm_fulltext = _norm_ws(fulltext) if isinstance(fulltext, str) else ""

    for section in REQUIRED_SECTIONS:
        entries = ledger.get(section) or []
        for entry in entries:
            # Be defensive even if `validate_ledger` was bypassed: skip
            # entries that don't even have the required keys. We do NOT
            # raise here — the recompute gate's job is to verdict, not
            # to redo the schema check.
            if not isinstance(entry, dict):
                continue
            anchor = entry.get("anchor")
            text = entry.get("text")
            if not isinstance(anchor, str) or not anchor:
                continue
            norm_anchor = _norm_ws(anchor)
            if not norm_anchor:
                continue
            if norm_anchor not in norm_fulltext:
                flagged.append(
                    {
                        "section": section,
                        "text": text if isinstance(text, str) else "",
                        "anchor": anchor,
                    }
                )

    return {"ok": not flagged, "flagged": flagged}
