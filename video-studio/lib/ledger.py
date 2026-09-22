"""Faithfulness gate (忠实门) for video-studio — anchor recompute.

Ported from podcast-studio `lib/paperline/ledger.py` (`verify_anchors`). The
recompute discipline is identical: every objective claim a beat makes must
trace to a *grounded* location in the source text — number-bearing tokens are
zero-tolerance (every one must appear in the source), pure-word tokens must hit
`_GROUND_THRESHOLD` coverage. Never trust the producing persona's self-label;
recompute.

Running-domain adaptation (the ONE substantive change from the paper port):
the tokenizer keeps `:` glued to numbers so a pace like `5:20` is ONE number
token. The paper port's number class (no `:`) splits `5:20`→`5`+`20`, which
makes pace zero-tolerance vacuous (`5` is a substring of almost any text). With
`:` in the class, `5:20` stays whole and substring-matches the `pdftotext
-layout` source form `5:20.0/5:45.7` (verified against Daniels p217). No
table-tolerance machinery is needed — `-layout` preserves the digits and the
substring match absorbs the trailing `.0` / `/alt` suffixes.

Chinese prose is intentionally NOT tokenized (`[a-z0-9:]` matches no CJK): the
gate targets the OBJECTIVE data (配速/VDOT/数字 + ASCII terms), which is the
dev-guide's "数字零容忍" core. The Chinese explanation is the digest-writer's
permitted paraphrase. CJK tokenization is a future enhancement, not a Phase-3
requirement.

Known residual (same as the paper port): "right numbers, wrong attribution"
(every number/term individually real but recombined wrong) and substring
collisions (`5:45` ⊂ `15:45`) are left to a downstream LLM-judge layer.

Schema: a video fact-ledger is `{"beats": [{beat_id, text, anchor, page?}]}`.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any


# ---------- errors ----------

class LedgerError(Exception):
    """Schema violation in a video fact-ledger (missing/empty beats, or an
    entry with empty `text`/`anchor`). The message names the offending field."""
    pass


# ---------- normalization (anchor recompute) ----------

_WS_RE = re.compile(r"\s+")

# Equivalent-notation normalization (digits stay zero-tolerance; only the
# unit/punctuation surface differs — see _norm_number / _norm_match).
_COMMA_BETWEEN_DIGITS_RE = re.compile(r"(?<=\d),(?=\d)")
_TRAILING_NONDIGIT_RE = re.compile(r"\D+$")

# Token = a run starting [a-z0-9] then [a-z0-9 . : - % × /]. The `:` (vs the
# paper port) keeps a pace `5:20` / `2:36.2` as a SINGLE number token — without
# it the colon splits the pace and `5` matches any text, defeating zero-tolerance.
# Linear character class — no backtracking (ReDoS-safe on a 100k-char fulltext).
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.:\-%×/]*")

# Connector / function words dropped before the word-coverage ratio (lowercase).
_STOP: frozenset[str] = frozenset(
    {
        "a", "an", "the",
        "of", "to", "in", "on", "for", "and", "or", "with", "by",
        "as", "at", "from", "into", "than", "that", "this", "these",
        "it", "its", "our", "we", "is", "are", "be", "was", "were",
        "has", "have", "had", "which", "where", "when", "while",
        "their", "them", "they", "his", "her",
    }
)

# Min fraction of pure-word (non-number, non-stop) tokens that must appear in
# the source. Reused verbatim from the paper port's calibration.
_GROUND_THRESHOLD = 0.8

# Decorative typography → nearest ASCII so normalized forms line up.
_QUOTE_DASH_MAP: dict[str, str] = {
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
}


def _norm_match(s: str) -> str:
    """NFKC + lower + ASCII quote/dash + whitespace-collapse. The form we
    tokenize and substring-search against (case/typography-insensitive)."""
    if not isinstance(s, str):
        return ""
    # Full-width Chinese punctuation (：U+FF1A, ，U+FF0C) are prose separators,
    # never part of a number, but NFKC folds them to half-width ':' / ',' which
    # then fuse adjacent tokens: ':' is kept inside a token (to hold paces like
    # "5:20"), so "verbatim：59" → "verbatim:59"; ',' is stripped when between
    # digits (the "1,500"→"1500" thousands rule), so "v1.2，2026-06" →
    # "v1.22026-06". Both fused tokens then fail zero-tolerance grounding. Split
    # these full-width marks to spaces BEFORE NFKC so the pieces tokenize apart
    # (the real digits still ground). Half-width ':' in paces and half-width ','
    # in genuine thousands separators are untouched.
    s = s.replace("：", " ").replace("，", " ")
    out = unicodedata.normalize("NFKC", s).lower()
    out = "".join(_QUOTE_DASH_MAP.get(ch, ch) for ch in out)
    out = _WS_RE.sub(" ", out).strip()
    # Thousands separator: "1,500" ≡ "1500" — drop a comma between two digits so
    # the writer's "1500" grounds against the source's "1,500" (notation, not a
    # different number). Only digit,digit — never touches "a, b".
    out = _COMMA_BETWEEN_DIGITS_RE.sub("", out)
    return out


def _norm_number(tok: str) -> str:
    """Strip a trailing run of non-digit chars from a number token, so faithful
    but non-literal notation grounds: `60.0:` → `60.0` (trailing prose colon),
    `400m` / `400-meter` → `400` (unit suffix), `1500m/mile` → `1500`. The digit
    core stays zero-tolerance (a fabricated `5:25` still has no match); only the
    equivalent unit/punctuation tail is dropped. A pace ending in a digit
    (`5:20`, `2:36.2`, `4:48.5/5:11.7`) is unchanged."""
    return _TRAILING_NONDIGIT_RE.sub("", tok)


def _content_tokens(s: str) -> list[str]:
    """Linear-regex tokenize a normalized string, drop stop-words. Order-
    preserving. (CJK chars are not matched by `_TOKEN_RE` and fall away.)"""
    if not s:
        return []
    return [t for t in _TOKEN_RE.findall(s) if t not in _STOP]


def _is_number_token(tok: str) -> bool:
    """True iff `tok` contains a digit anywhere — zero-tolerance set. Catches
    `5:20`, `2:36.2`, `71`, `50.5%`, `vdot72`. Pure words go to the ratio check."""
    return any(ch.isdigit() for ch in tok)


def _anchor_grounded(anchor: str, ft_norm: str) -> bool:
    """True iff `anchor` is grounded in the normalized fulltext `ft_norm`.

    ALL number tokens must be substrings of the fulltext (zero-tolerance);
    pure-word coverage must reach `_GROUND_THRESHOLD`. An anchor with no
    content tokens is vacuously grounded (empty-anchor is a schema concern)."""
    toks = _content_tokens(_norm_match(anchor))
    if not toks:
        return True

    numbers = [t for t in toks if _is_number_token(t)]
    words = [t for t in toks if not _is_number_token(t)]

    for n in numbers:
        # Match the digit core (trailing unit/punctuation stripped) so faithful
        # notation grounds; the digits themselves stay zero-tolerance.
        if _norm_number(n) not in ft_norm:
            return False

    if not words:
        return True

    present = sum(1 for w in words if w in ft_norm)
    return (present / len(words)) >= _GROUND_THRESHOLD


# ---------- schema gate ----------

def validate_ledger(d: Any) -> None:
    """Validate a video fact-ledger: `{"beats": [{beat_id, text, anchor}]}`.

    Raises LedgerError on: not-a-dict, missing/empty `beats`, an entry that is
    not a dict, or an entry with missing/empty `text` or `anchor`. `page` and
    other fields are not enforced. Returns None on success."""
    if not isinstance(d, dict):
        raise LedgerError(f"ledger must be a mapping, got {type(d).__name__}")

    beats = d.get("beats")
    if beats is None:
        raise LedgerError("missing required key: 'beats'")
    if not isinstance(beats, list):
        raise LedgerError(f"'beats' must be a list, got {type(beats).__name__}")
    if not beats:
        raise LedgerError("'beats' must contain at least one entry")

    for idx, entry in enumerate(beats):
        if not isinstance(entry, dict):
            raise LedgerError(
                f"beat {idx} must be a mapping, got {type(entry).__name__}")
        text = entry.get("text")
        anchor = entry.get("anchor")
        if not isinstance(text, str) or not text.strip():
            raise LedgerError(f"beat {idx} missing or empty 'text'")
        if not isinstance(anchor, str) or not anchor.strip():
            raise LedgerError(f"beat {idx} missing or empty 'anchor'")


# ---------- anchor recompute gate ----------

# ---------- style gate (cold open + standalone episode) ----------
#
# Same discipline as the anchor gate, applied to the two structural rules the
# channel's own numbers justify: viewers average 55.5 seconds, so the opening
# cannot be spent on a greeting; and every video is pushed to strangers who never
# saw the last one, so cross-episode references only tell a new viewer they missed
# something.
#
# These are gates rather than instructions because instructions did not hold. The
# persona forbade the greeting, the user prompt forbade it in bold, and the writer
# opened with 「大家好，我是X，欢迎来到Y」 anyway — on two consecutive live runs.
# Recompute, never trust.

# Banned in the FIRST beat only: the cold open. A greeting later is fine — the
# self-intro is supposed to land right after the hook.
_COLD_OPEN_BANNED: tuple[str, ...] = (
    "大家好", "欢迎来到", "欢迎收看", "今天我们讲", "今天我们聊",
    "本期我们", "这期我们讲", "这期我们聊",
)

# Banned ANYWHERE: an episode must stand alone.
_STANDALONE_BANNED: tuple[str, ...] = (
    "上期", "上一期", "下期", "下一期", "前几期", "前面几期",
    "我们下次见", "我们下期见", "咱们下期", "下集",
)

# Citation framing ("教材说 X" instead of just saying X) is not zero-tolerance like
# the other two — the persona allows citing authority once or twice per episode
# (e.g. "UESCA 直接说"). Only flag when the whole draft leans on this framing past
# the budget the persona itself sets ("控制在两三句以内").
#
# This is a correctness/voice gate, not an engagement-tuned threshold — it does not
# need the completion-rate CSV justification § 4.1 requires for pacing/length gates
# (see running-class/CLAUDE.md § 4.1 "适用范围"). Evidence instead: the same framing
# instruction lived in a prior digest-writer.md revision and was dropped in a later
# rewrite; the writer produced 10+ instances per episode once unconstrained (EP011).
# Upgraded from a fixed-phrase tuple (7 exact strings) to a pattern: the fixed
# list caught only 2 of 15 known real-episode variants (EP012 pre-polish audit,
# 2026-07-28) — "教材里写出的" / "这本教材从头到尾" / "教材同一章里讲了" etc.
# all slipped past exact matching. The subject (教材/原文/书里) followed within
# a few characters by a citation-shaped verb is the actual signal, not any one
# fixed phrase. "原话" is kept in the verb alternation — it was one of the 7
# exact phrases in the old table and dropping it silently breaks 2 existing
# tests (verified 2026-07-28 via plan-verifier: budget-exceeding fixture drops
# from 4 hits to 3 == budget, gate stops firing).
_CITATION_PATTERN = re.compile(
    r"(教材|原文|书里)[^。！？，、]{0,6}"
    r"(说|写|给|讲|提到|引用|强调|指出|列|把|原话|停了|另一[章段篇节]|同一[章段篇节])"
)
_CITATION_BUDGET = 3


def verify_style(ledger: dict) -> dict:
    """Recompute the three structural/voice rules on a finished draft.

    Returns ``{"ok": bool, "violations": [{"beat_id", "rule", "found", "excerpt"}]}``.
    Does not raise; the caller (the script dispatch closure) decides to halt/retry.
    """
    violations: list[dict] = []
    citation_hits: list[dict] = []
    beats = [b for b in (ledger.get("beats") or []) if isinstance(b, dict)]

    for idx, beat in enumerate(beats):
        text = beat.get("text")
        if not isinstance(text, str) or not text:
            continue
        bid = beat.get("beat_id") or f"b{idx:02d}"

        if idx == 0:
            for phrase in _COLD_OPEN_BANNED:
                if phrase in text:
                    violations.append({
                        "beat_id": bid,
                        "rule": "冷开场",
                        "found": phrase,
                        "excerpt": text[:60],
                    })

        for phrase in _STANDALONE_BANNED:
            if phrase in text:
                at = text.find(phrase)
                violations.append({
                    "beat_id": bid,
                    "rule": "独立成篇",
                    "found": phrase,
                    "excerpt": text[max(0, at - 20): at + 20],
                })

        for m in _CITATION_PATTERN.finditer(text):
            at = m.start()
            citation_hits.append({
                "beat_id": bid,
                "rule": "引用腔",
                "found": m.group(0),
                "excerpt": text[max(0, at - 20): at + 20],
            })

    if len(citation_hits) > _CITATION_BUDGET:
        violations.extend(citation_hits)

    return {"ok": not violations, "violations": violations}


def verify_anchors(ledger: dict, fulltext: str) -> dict:
    """Recompute every beat's anchor as a grounded location in `fulltext`.

    Number tokens are zero-tolerance (each must substring-match the normalized
    fulltext); pure-word tokens must reach `_GROUND_THRESHOLD` coverage. Does
    NOT raise on a flagged beat — returns the verdict for the caller (the
    runner's 忠实门 step) to halt/retry on.

    Returns: {"ok": bool, "flagged": [{"beat_id", "text", "anchor"}, ...]}.
    An empty fulltext flags every numbered/worded anchor (nothing is present)."""
    flagged: list[dict] = []
    norm_fulltext = _norm_match(fulltext)

    for entry in ledger.get("beats") or []:
        if not isinstance(entry, dict):
            continue
        anchor = entry.get("anchor")
        text = entry.get("text")
        if not isinstance(anchor, str) or not anchor:
            continue
        if not _anchor_grounded(anchor, norm_fulltext):
            flagged.append({
                "beat_id": entry.get("beat_id", ""),
                "text": text if isinstance(text, str) else "",
                "anchor": anchor,
            })

    return {"ok": not flagged, "flagged": flagged}


def ground_text(text: str, fulltext: str) -> bool:
    """Public wrapper around the anchor-grounding check — NOT a faithfulness
    gate, despite reusing the same machinery as `verify_anchors`.

    Known limitation (load-bearing, do not remove this warning): pure-CJK
    `text` with no ASCII/digit tokens returns True unconditionally (`_anchor_
    grounded`'s empty-token-list short circuit, ledger.py:143-144) — it
    cannot catch a fabricated Chinese claim with no numbers in it. Even with
    digit tokens present, this only checks "does this digit string appear
    ANYWHERE in fulltext" — on a multi-thousand-character source document, a
    short 2-digit number can coincidentally match unrelated text elsewhere
    (verified 2026-07-28: on EP012's real ~95k-char source, both the correct
    "48" and the fabricated "99" test as grounded).

    Use this as an informational "does this number's string form appear in
    the source somewhere" spot-check for human review — never as an
    automated pass/fail gate. The actual anti-fabrication check for the
    running-class citation-voice rewrite tooling is the digit-subset
    invariant in `citation_voice_scan.py` (candidate digits must be a
    subset of the original beat's digits), which this function does not
    replace.
    """
    return _anchor_grounded(text, _norm_match(fulltext))
