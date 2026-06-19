"""Paper fact-ledger schema + anchor verification (the recompute gate).

The fact-ledger is the faithfulness baseline for the paper line: every claim
the ledger-writer persona extracts from a paper's full text must trace back
to a *grounded* location in that text. The grounding criterion (DP-001) is
"all number-bearing tokens MUST appear in the original text, and at least
`_GROUND_THRESHOLD` of the remaining content tokens MUST be present" — the
text is no longer required to appear as a verbatim substring. This is the
refined recompute discipline: a fabricating agent that invents a number or
fabricates a sentence still flags (numbers are zero-tolerance), but a
faithful writer who paraphrases prose — moving the context phrase up front,
replacing a conjunction, changing case — is no longer penalized for being
a re-writer instead of a photocopier.

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
    For each entry across the four sections, normalize (NFKC + lower +
    ASCII-only + whitespace-collapsed) the anchor and the fulltext,
    extract content tokens (linear char-class regex, stop-words removed),
    split into number-bearing tokens and pure-word tokens. A fabricated
    number (any number token not present in the normalized fulltext)
    flags the entry. A high paraphrase rate (pure-word tokens present
    below `_GROUND_THRESHOLD`) also flags. The known residual — a
    "right numbers, wrong attribution" construction (e.g. numbers and
    names are all real but recombined incorrectly) — is left for the
    faithfulness gate's LLM-judge layer downstream.
    Each flagged entry carries `{section, text, anchor}` (the locator a
    human can chase down). The function does NOT raise on a flagged
    entry — it returns the verdict and lets the caller decide.

Self-contained: does NOT import `factcheck` (P3's faithfulness gate may, P2
does not) — keeps the line-isolation firewall clean (see
test_line_isolation.py for the structural proof).

This module imports NOTHING from the opinion line (stance / coveredground /
magnitude / bible); it is part of the paper line and lives under
`lib/paperline/`.
"""
from __future__ import annotations

import re
import unicodedata
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


# ---------- normalization (anchor recompute) ----------

# Collapse any run of whitespace (spaces, tabs, newlines, CR) to a single
# space — for both the substring and the token forms. Content tokens
# (numbers, punctuation) are not touched here; `_norm_match` handles the
# unicode/quote/dash normalization BEFORE whitespace collapse.
_WS_RE = re.compile(r"\s+")

# A token is a run of [a-z0-9] followed by an optional run of the
# punctuation/format characters we want to KEEP attached to a number
# (percent, percent-with-dp, multiplication-sign, slash, dash, dot,
# plus). This is intentionally a linear character class — no nested
# quantifiers, no backtracking. Per the threat model (ReDoS surface),
# a 100k-character fulltext must match in linear time; this regex
# guarantees it.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.\-%×/]*")

# Stop-words: connector + function words we drop before computing the
# word-coverage ratio. They carry grammar, not meaning, so requiring
# their presence in the fulltext would be noise (e.g. an anchor that
# drops "the" or moves "on" to the front would flag for nothing).
# Keep this list small + obvious; every entry must be in lowercase.
_STOP: frozenset[str] = frozenset(
    {
        "a", "an", "the",
        "of", "to", "in", "on", "for", "and", "or", "with", "by",
        "as", "at", "from", "into", "than", "that", "this", "these",
        "it", "its", "our", "we", "is", "are", "be", "was", "were",
        "has", "have", "had", "which", "where", "when", "while",
        "their", "them", "they", "his", "her", "its",
    }
)

# Minimum fraction of pure-word (non-number, non-stop-word) content tokens
# that must appear in the normalized fulltext for an anchor to pass.
# 0.8 is the implementation-time calibration: it lets the live paraphrased
# anchor ("On LVBench, our 7B agent …") clear against the pdftotext
# rearrangement, while still flagging anchors that fabricate a sentence
# (most of whose words are absent from the paper). Tunable later if a
# new paper type demands a different tight/loose dial.
_GROUND_THRESHOLD = 0.8

# Characters we consider "decorative typography" — curly quotes, en/em
# dashes, etc. — that pdftotext / HTML extraction will sometimes produce
# but a rewriter may collapse to ASCII. Map to the nearest ASCII so the
# normalized forms line up.
_QUOTE_DASH_MAP: dict[str, str] = {
    "‘": "'",  # left single quote
    "’": "'",  # right single quote
    "“": '"',  # left double quote
    "”": '"',  # right double quote
    "–": "-",  # en dash
    "—": "-",  # em dash
    "−": "-",  # minus sign
    " ": " ",  # non-breaking space → regular space (folded further by _WS_RE)
}


def _norm_match(s: str) -> str:
    """Normalize for anchor match: NFKC + lower + ASCII quote/dash + ws.

    Used as the FIRST step of anchor recompute and fulltext normalization.
    Keeps us case- AND typography-insensitive (pdftotext sometimes flips
    case mid-token; writers sometimes re-cap a name). The output is what
    we tokenize + what we substring-search against.
    """
    if not isinstance(s, str):
        return ""
    out = unicodedata.normalize("NFKC", s).lower()
    if _QUOTE_DASH_MAP:
        out = "".join(_QUOTE_DASH_MAP.get(ch, ch) for ch in out)
    out = _WS_RE.sub(" ", out).strip()
    return out


def _content_tokens(s: str) -> list[str]:
    """Extract content tokens from a normalized string.

    Linear-regex tokenize (no backtracking), then drop stop-words. The
    returned list preserves order and is what `_anchor_grounded` then
    splits into number-bearing vs pure-word.
    """
    if not s:
        return []
    return [t for t in _TOKEN_RE.findall(s) if t not in _STOP]


def _is_number_token(tok: str) -> bool:
    """True iff `tok` contains at least one digit (zero-tolerance check).

    A token is treated as a number token if it has a digit ANYWHERE — this
    catches `50.5%`, `10×`, `72b`, `+33.4%`, `2025`, `qwen2.5-vl-72b`. Each
    such token must appear in the normalized fulltext, otherwise the
    anchor is flagged as fabricated. Pure words (`lvb`, `agent`, `larger`)
    are NOT in this set and are subject to the threshold check instead.
    """
    return any(ch.isdigit() for ch in tok)


def _anchor_grounded(anchor: str, ft_norm: str) -> bool:
    """Decide whether `anchor` is grounded in the normalized fulltext.

    Split content tokens (after stop-word removal) into number-bearing and
    pure-word. ALL number tokens must be present in the fulltext (substring
    match against the normalized form) — this is the zero-tolerance
    fabrication check. The pure-word ratio (present_in_fulltext / total)
    must reach `_GROUND_THRESHOLD` (currently 0.8) — this is the
    "paraphrase tolerance" half. An anchor with no content tokens at all
    is vacuously grounded (we don't flag empty anchors; that contract
    lives in `validate_ledger`).

    Returns True iff the anchor is grounded.
    """
    toks = _content_tokens(anchor)
    if not toks:
        return True

    numbers = [t for t in toks if _is_number_token(t)]
    words = [t for t in toks if not _is_number_token(t)]

    # Numbers: zero-tolerance — every one must appear in the fulltext.
    for n in numbers:
        if n not in ft_norm:
            return False

    # Pure words: ratio >= threshold. If there are no pure words, we're
    # done — the anchor's only content was numbers, all grounded.
    if not words:
        return True

    present = sum(1 for w in words if w in ft_norm)
    return (present / len(words)) >= _GROUND_THRESHOLD


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
    """Recompute every anchor as a grounded location in `fulltext`.

    For each entry across the four required sections, normalize both
    the anchor and the fulltext (NFKC + lower + ASCII quote/dash + ws-
    collapse → `_norm_match`) and then run `_anchor_grounded`: a number-
    token zero-tolerance check (every digit-bearing token in the anchor
    must appear in the fulltext) plus a pure-word coverage check (≥
    `_GROUND_THRESHOLD` of non-number, non-stop-word tokens must appear).

    This is the recompute discipline that catches a fabricating agent —
    never trust the ledger's self-label. The check is more permissive
    than a verbatim-substring check (a faithful paraphrase that
    rearranges word order or drops a conjunction now passes) but still
    zero-tolerance on numbers (a fabricated metric or a wrong year
    flags). Known residual: "right numbers, wrong attribution" (numbers
    + names are individually real but the construction is wrong) is
    left to the LLM-judge layer in the downstream faithfulness gate.

    Args:
        ledger: A fact-ledger (must already pass `validate_ledger`, but
            this function does not re-validate to keep the recompute gate
            independent — pass a known-good ledger for clean output).
        fulltext: The paper's full text (whitespace-normalizable). May be
            empty; in that case every anchor flags (no number/word can
            be present in an empty fulltext).

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
    norm_fulltext = _norm_match(fulltext)

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
            if not _anchor_grounded(anchor, norm_fulltext):
                flagged.append(
                    {
                        "section": section,
                        "text": text if isinstance(text, str) else "",
                        "anchor": anchor,
                    }
                )

    return {"ok": not flagged, "flagged": flagged}
