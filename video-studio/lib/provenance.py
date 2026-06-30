"""Provenance builder — map each beat's objective assertion → PDF page (UX-004).

Consumes the per-page map from `lib.extract` (NOT the joined fulltext — page
boundaries must survive) and reuses `lib.ledger`'s number-token extraction so
"objective assertion" means exactly what the 忠实门 zero-tolerance checks: an
anchor carrying a number token (配速/VDOT/数字). The grounding page is the first
(ascending) page whose normalized text contains the anchor's number tokens.

A pure-prose beat (no number token) is the digest-writer's permitted paraphrase
— it is recorded with `page: None` (not an objective-data assertion, so no page
is required). An objective beat whose number cannot be located anywhere also
gets `page: None` (the 忠实门 is the gate that would have halted it; provenance
just reports what it could ground)."""
from __future__ import annotations

from lib.ledger import _content_tokens, _is_number_token, _norm_match, _norm_number


def _number_tokens(anchor: str) -> list[str]:
    # Same normalization as the 忠实门 gate: strip trailing unit/punctuation so a
    # gate-grounded number (e.g. 400m ≡ 400-meter) also locates its page. Stays
    # consistent with verify_anchors — a number the gate accepts maps to a page.
    return [_norm_number(t) for t in _content_tokens(_norm_match(anchor))
            if _is_number_token(t)]


def build_provenance(
    beats: list[dict],
    page_text_map: dict[int, str],
) -> list[dict]:
    """Return `[{beat_id, assertion, page}]`, one per beat.

    `assertion` = the beat's anchor (the grounded claim). `page` = the first
    ascending page whose normalized text contains ALL the anchor's number
    tokens, or None when the beat has no number token (pure prose) or the
    number is not found in any page.
    """
    # pre-normalize each page once
    norm_pages = {p: _norm_match(text) for p, text in page_text_map.items()}
    ordered_pages = sorted(norm_pages)

    out: list[dict] = []
    for beat in beats:
        anchor = beat.get("anchor", "") or ""
        nums = _number_tokens(anchor)
        page: int | None = None
        if nums:
            for p in ordered_pages:
                if all(n in norm_pages[p] for n in nums):
                    page = p
                    break
        out.append({
            "beat_id": beat.get("beat_id", ""),
            "assertion": anchor,
            "page": page,
        })
    return out
