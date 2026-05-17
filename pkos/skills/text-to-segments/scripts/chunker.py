#!/usr/bin/env python3
"""text-to-segments — TTS-ready chunking shared across vendors.

Reads markdown / plain text from --input (or stdin), produces a JSON file with
chunks ≤ --max-chars on natural boundaries that respect:

- Paragraph / heading breaks (highest priority)
- Sentence-final punctuation in both Chinese (。！？；) and English (.!?;)
- Comma / pause punctuation when no stronger boundary exists in window
- Whitespace as last resort for English-token-heavy text

Hard "do not cut" guarantees:
- Inside paired quotes / brackets (Chinese and English variants)
- Across number + unit ("4.2 倍", "32 kHz", "2026 年", "100%")
- Across explicit preserve-terms (CLI --preserve-terms a,b,c)
- Inside inline code spans `...`

Markdown clean (--clean-markdown true, default):
- Code fences  ```...```  removed entirely (text inside read as code, unsuitable for TTS)
- Heading markers # ## ###  stripped, keep text
- Bold **/__, italic */_ stripped, keep text
- Inline code `code` → bare text
- Horizontal rules --- *** ___ removed
- HTML comments <!-- ... --> removed
- List bullet prefixes ("- ", "* ", "1. ") stripped, keep text
- 3+ blank lines collapsed to 2

Output JSON shape (--vendor-format generic, default):
{
  "metadata": {
    "source": "<input path or '<stdin>'>",
    "total_chars": <int>,
    "segment_count": <int>,
    "avg_chars": <int>,
    "max_chars": <int>,
    "preserved_terms_count": <int>
  },
  "segments": [
    {"id": "seg_001", "text": "...", "char_count": N,
     "ends_with": "。", "boundary_priority": "sentence"},
    ...
  ]
}

Vendor adapters:
- minimax: [{"id": "seg_001", "text": "...", "voice_id": "", "emotion": ""}, ...]
- volcengine: {"segments": [{"id": "seg_001", "text": "..."}, ...]}
- generic: as above

Exit codes:
  0 success
  1 argument error
  2 input not found
  3 output write error
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from typing import Iterable

DEFAULT_MAX_CHARS = 280
COMMA_FLUSH_RATIO = 0.8  # only break on commas after ≥ 80% of budget is filled

# Sentence-final punctuation across CJK + Latin.
SENTENCE_PUNCT = "。！？；!?;"
# Pause / comma punctuation (lower priority).
PAUSE_PUNCT = "，、,:：—–"

# Directional pairs: open != close, depth-based tracking.
PAIR_OPEN = "“‘「『（《【([{<"
PAIR_CLOSE = "”’」』）》】)]}>"
PAIRS = dict(zip(PAIR_OPEN, PAIR_CLOSE))

# Ambiguous quotes where open == close — parity tracking instead.
PARITY_CHARS = "\"'"

# Regex: number+optional-decimal followed (no whitespace OR single space) by unit-like token.
# Captures Chinese-dominant unit phrases like "4.2 倍", "32 kHz", "2026 年", "100%".
NUM_UNIT_RE = re.compile(
    r"\d+(?:[.,]\d+)?\s?(?:%|‰|kHz|Hz|MHz|GHz|fps|kbps|ms|°C|°F|"
    r"年|月|日|时|分|秒|倍|个|次|步|页|章|节|节奏|遍|"
    r"kg|g|mg|km|cm|mm|m|°|GB|MB|KB|TB)"
)


@dataclass
class Segment:
    id: str
    text: str
    char_count: int
    ends_with: str
    boundary_priority: str


# ---------------------------------------------------------------------------
# Markdown clean
# ---------------------------------------------------------------------------


def clean_markdown(text: str) -> str:
    # Code fences first — their contents shouldn't be touched by other rules.
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"~~~[\s\S]*?~~~", "", text)
    # HTML comments.
    text = re.sub(r"<!--[\s\S]*?-->", "", text)
    # Inline code → bare text.
    text = re.sub(r"`([^`\n]+)`", r"\1", text)
    # Heading markers (keep text).
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Bold and italic. Order matters: double-marker forms first.
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"\1", text)
    # Horizontal rules.
    text = re.sub(r"^[ \t]*[-*_]{3,}[ \t]*$", "", text, flags=re.MULTILINE)
    # List bullets: "- ", "* ", "+ " or "1. " "2) "
    text = re.sub(r"^[ \t]*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[ \t]*\d+[.)]\s+", "", text, flags=re.MULTILINE)
    # Collapse runs of blank lines (>=3 newlines) to a paragraph break.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Cut-protection masks
# ---------------------------------------------------------------------------


def build_protected_mask(text: str, preserve_terms: list[str]) -> list[bool]:
    """Return a parallel list[bool] of length len(text); True means index i is
    inside a non-cuttable span. We refuse to split inside any True run."""
    n = len(text)
    mask = [False] * n

    # Directional pair depth + ambiguous-quote parity, combined into one
    # "inside any open span" predicate.
    depth = 0
    parity: dict[str, int] = {c: 0 for c in PARITY_CHARS}
    for i, ch in enumerate(text):
        if ch in PAIR_OPEN:
            depth += 1
            mask[i] = True
            continue
        if ch in PAIR_CLOSE:
            mask[i] = True
            depth = max(0, depth - 1)
            continue
        if ch in PARITY_CHARS:
            # The quote char itself is always part of the protected span.
            mask[i] = True
            parity[ch] += 1
            continue
        inside_parity = any(v % 2 == 1 for v in parity.values())
        if depth > 0 or inside_parity:
            mask[i] = True

    # Number + unit spans.
    for m in NUM_UNIT_RE.finditer(text):
        for i in range(m.start(), m.end()):
            mask[i] = True

    # Preserve terms (case-insensitive substring match).
    for term in preserve_terms:
        if not term:
            continue
        # Case-insensitive search; use re for overlapping iter via finditer.
        for m in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
            for i in range(m.start(), m.end()):
                mask[i] = True

    return mask


# ---------------------------------------------------------------------------
# Boundary finder
# ---------------------------------------------------------------------------


def find_cut(
    text: str,
    start: int,
    end: int,
    mask: list[bool],
    max_chars: int,
) -> tuple[int, str]:
    """Find the best cut index in [start, end). Returns (cut_index, priority).

    cut_index is exclusive — text[start:cut_index] becomes one chunk; the next
    chunk starts at cut_index. priority ∈ {"paragraph", "sentence", "pause",
    "whitespace", "hard"} from best (cleanest break) to worst (hard cut)."""

    # Search backwards from end so we get the latest possible cut that still
    # respects max_chars. start..end is the candidate window.

    # 1) Paragraph break — \n\n inside the window.
    for i in range(end - 1, start, -1):
        if i + 1 < len(text) and text[i] == "\n" and text[i + 1] == "\n":
            if not mask[i] and not mask[i + 1]:
                # Cut after the first \n; the second \n becomes the next chunk's
                # leading char and is stripped during emit.
                return i + 1, "paragraph"

    # 2) Sentence punctuation.
    for i in range(end - 1, start, -1):
        ch = text[i]
        if ch in SENTENCE_PUNCT and not mask[i]:
            return i + 1, "sentence"

    # 3) Pause/comma — but only if we've already filled ≥ COMMA_FLUSH_RATIO.
    threshold = start + int(max_chars * COMMA_FLUSH_RATIO)
    for i in range(end - 1, max(start, threshold), -1):
        ch = text[i]
        if ch in PAUSE_PUNCT and not mask[i]:
            return i + 1, "pause"

    # 4) Whitespace fallback — useful for English-heavy text.
    for i in range(end - 1, start, -1):
        if text[i] == " " and not mask[i]:
            return i + 1, "whitespace"

    # 5) Hard cut at end (we tried; nothing better is available).
    # Walk back into a non-protected position so we don't bisect a quote/number.
    cut = end
    while cut > start and mask[cut - 1]:
        cut -= 1
    if cut <= start:
        # The whole window is protected (e.g. one massive bracketed quote).
        # Fail soft: emit the protected run as-is by cutting at `end`.
        return end, "hard"
    return cut, "hard"


# ---------------------------------------------------------------------------
# Chunker
# ---------------------------------------------------------------------------


def chunk(text: str, max_chars: int, preserve_terms: list[str]) -> list[Segment]:
    text = text.strip()
    if not text:
        return []
    mask = build_protected_mask(text, preserve_terms)
    n = len(text)

    segs: list[Segment] = []
    i = 0
    while i < n:
        end = min(i + max_chars, n)
        # If `end` falls inside a protected span (i.e., the span crosses the
        # max-chars boundary), extend `end` to the end of the run. This may
        # produce a chunk slightly larger than max_chars when the protected
        # span itself exceeds max_chars — TTS engines tolerate small overshoots
        # and the alternative is producing an unbalanced quote / split number.
        while end < n and end > i and mask[end - 1] and mask[end]:
            end += 1
        if end >= n:
            piece = text[i:end].strip()
            if piece:
                segs.append(
                    Segment(
                        id=f"seg_{len(segs) + 1:03d}",
                        text=piece,
                        char_count=len(piece),
                        ends_with=piece[-1] if piece else "",
                        boundary_priority="eof",
                    )
                )
            break

        cut, priority = find_cut(text, i, end, mask, max_chars)
        # find_cut guarantees cut > i (unless the window is fully protected;
        # in that case cut == end and we let it through as a hard cut).
        piece = text[i:cut].strip()
        if piece:
            segs.append(
                Segment(
                    id=f"seg_{len(segs) + 1:03d}",
                    text=piece,
                    char_count=len(piece),
                    ends_with=piece[-1] if piece else "",
                    boundary_priority=priority,
                )
            )
        i = cut
        # Skip any leading whitespace / blank line at the new boundary.
        while i < n and text[i] in " \n\t":
            i += 1
    return segs


# ---------------------------------------------------------------------------
# Vendor adapters
# ---------------------------------------------------------------------------


def to_vendor(segs: list[Segment], metadata: dict, vendor: str) -> dict | list:
    if vendor == "generic":
        return {"metadata": metadata, "segments": [asdict(s) for s in segs]}
    if vendor == "minimax":
        return [
            {"id": s.id, "text": s.text, "voice_id": "", "emotion": ""}
            for s in segs
        ]
    if vendor == "volcengine":
        return {
            "metadata": metadata,
            "segments": [{"id": s.id, "text": s.text} for s in segs],
        }
    raise ValueError(f"unknown vendor-format: {vendor}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="text-to-segments",
        description="Chunk markdown / plain text into TTS-ready segments.",
    )
    p.add_argument("--input", help="Input file (markdown or plain text). - or omit for stdin.")
    p.add_argument("--output", help="Output JSON file. Omit for stdout.")
    p.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                   help=f"Maximum chars per segment (default {DEFAULT_MAX_CHARS}).")
    p.add_argument("--clean-markdown", default="true",
                   choices=["true", "false"],
                   help="Strip markdown syntax before chunking (default true).")
    p.add_argument("--preserve-terms", default="",
                   help="Comma-separated terms that must not be split (case-insensitive).")
    p.add_argument("--vendor-format", default="generic",
                   choices=["generic", "minimax", "volcengine"],
                   help="Output schema. Default 'generic'.")
    args = p.parse_args(argv)

    # Read input.
    if args.input and args.input != "-":
        try:
            with open(args.input, "r", encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            print(f"text-to-segments: input not found: {args.input}", file=sys.stderr)
            return 2
        source = args.input
    else:
        raw = sys.stdin.read()
        source = "<stdin>"

    if args.max_chars < 10:
        print(f"text-to-segments: --max-chars must be ≥ 10 (got {args.max_chars})", file=sys.stderr)
        return 1

    if args.clean_markdown == "true":
        raw = clean_markdown(raw)

    preserve_terms = [t.strip() for t in args.preserve_terms.split(",") if t.strip()]
    segs = chunk(raw, args.max_chars, preserve_terms)

    total = sum(s.char_count for s in segs)
    metadata = {
        "source": source,
        "total_chars": total,
        "segment_count": len(segs),
        "avg_chars": (total // len(segs)) if segs else 0,
        "max_chars": args.max_chars,
        "preserved_terms_count": len(preserve_terms),
    }

    payload = to_vendor(segs, metadata, args.vendor_format)
    data = json.dumps(payload, ensure_ascii=False, indent=2)

    if args.output:
        try:
            os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(data)
        except OSError as e:
            print(f"text-to-segments: write failed: {e}", file=sys.stderr)
            return 3
    else:
        sys.stdout.write(data)
        sys.stdout.write("\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
