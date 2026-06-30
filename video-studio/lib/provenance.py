"""Provenance builder — map each beat's objective assertion → source doc (UX-004).

Consumes the per-page/per-doc map from `lib.extract` (NOT the joined fulltext
— page/doc boundaries must survive) and reuses `lib.ledger`'s number-token
extraction so "objective assertion" means exactly what the 忠实门 zero-tolerance
checks: an anchor carrying a number token (配速/VDOT/数字). The grounding key is
the first (ascending) key whose normalized text contains the anchor's number
tokens.

PDF mode: keys are int page numbers → `page` field is set.
Doc mode (Phase 6): keys are str doc-refs (e.g. "01-intensity-zones") → `page`
is None, `doc_ref` carries the str key.

A pure-prose beat (no number token) is the digest-writer's permitted paraphrase
— it is recorded with `page: None` and `doc_ref: None` (not an
objective-data assertion, so no grounding is required). An objective beat whose
number cannot be located anywhere also gets both as None (the 忠实门 is the gate
that would have halted it; provenance just reports what it could ground).

Per-assertion fields (Task 6 / UX-004 extension):
  - `source`: from beat (e.g. "Daniels" / "UESCA" / "Minetti" / ""); defaults ""
  - `verify_status`: from beat (e.g. "✅" / "⚠️" / "🔧"); defaults ""
  - `doc_ref`: grounding key as-is (int or str), or None if not found
  - `page`: int page number (PDF mode) or None (doc mode / not found)
"""
from __future__ import annotations

from lib.ledger import _content_tokens, _is_number_token, _norm_match, _norm_number


def _number_tokens(anchor: str) -> list[str]:
    # Same normalization as the 忠实門 gate: strip trailing unit/punctuation so a
    # gate-grounded number (e.g. 400m ≡ 400-meter) also locates its page. Stays
    # consistent with verify_anchors — a number the gate accepts maps to a page.
    return [_norm_number(t) for t in _content_tokens(_norm_match(anchor))
            if _is_number_token(t)]


def build_provenance(
    beats: list[dict],
    page_text_map: dict,  # int (PDF) or str (doc) keys → text
) -> list[dict]:
    """Return `[{beat_id, assertion, page, doc_ref, source, verify_status}]`, one per beat.

    `assertion` = the beat's anchor (the grounded claim).
    `page` = int page (PDF mode) when grounding key is int, else None.
    `doc_ref` = raw grounding key (int or str), or None when not found.
    `source` = from beat (e.g. "Daniels"); defaults to "".
    `verify_status` = from beat (e.g. "✅" / "⚠️"); defaults to "".
    """
    # pre-normalize each page/doc once; keep original keys for doc_ref
    norm_pages = {k: _norm_match(text) for k, text in page_text_map.items()}
    # sort: ints naturally before strs; sort within each type to stay deterministic
    int_keys = sorted(k for k in norm_pages if isinstance(k, int))
    str_keys = sorted(k for k in norm_pages if isinstance(k, str))
    ordered_keys = int_keys + str_keys

    out: list[dict] = []
    for beat in beats:
        anchor = beat.get("anchor", "") or ""
        nums = _number_tokens(anchor)
        grounding_key: int | str | None = None
        if nums:
            for k in ordered_keys:
                if all(n in norm_pages[k] for n in nums):
                    grounding_key = k
                    break
        # page is int when grounding key is int (PDF mode), else None
        page: int | None = grounding_key if isinstance(grounding_key, int) else None
        out.append({
            "beat_id": beat.get("beat_id", ""),
            "assertion": anchor,
            "page": page,
            "doc_ref": grounding_key,
            "source": str(beat.get("source", "") or ""),
            "verify_status": str(beat.get("verify_status", "") or ""),
        })
    return out


def stamp_episode_provenance(records, source: str = "", verify_status: str = ""):
    """MF-1: digest-writer beats carry no source/verify_status (the syllabus does,
    per episode). Stamp the episode-level source/verify_status onto any record that
    lacks them, so provenance.json ships the real attribution instead of empty
    strings (DP-602/603 — 溯源状态穿到成片)."""
    for r in records:
        if not r.get("source"):
            r["source"] = str(source or "")
        if not r.get("verify_status"):
            r["verify_status"] = str(verify_status or "")
    return records
