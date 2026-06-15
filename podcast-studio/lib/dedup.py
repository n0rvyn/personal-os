"""podcast-studio dedup — 站内 + 跨期 重复检查.

Three checks compose into a single per-episode dedup gate:

- `check_intra_dup(body, *, similarity_fn=None)`: 站内 段落/句子级重复
  - 主信号 (逐字) = shared contiguous run ≥ `VERBATIM_NGRAM_LEN=13` chars
    (a shared 13-gram ⟺ LCS ≥ 13) on paragraph / sentence pairs. Catches
    the 06-14 逐字复制 root cause (「17.2万」/「占GDP」repeated verbatim)
    EVEN when embedded in otherwise-different paragraphs — whole-text
    Jaccard dilutes there (0.452) and was REJECTED as 主信号. Does NOT
    depend on the embedded similarity helper — this is the 06-14 防-regression pin.
  - 次信号 (近识) = whole-text 2-gram Jaccard ≥ `HIGH_JACCARD_THRESHOLD=0.85`
    (reordered / near-identical without one long run).
  - 确认信号 (近义换词) = 若 `similarity_fn` 可用, 段对 cosine ≥
    `INTRA_EMBED_CONFIRM=0.93` 也判重 (reskin). High bar aligned with
    Phase-2; the distinct 苏伊士 pair (LCS=11, Jaccard=0.583) clears all
    three signals → unflagged unless embedding confirms.
  - 三者并集: any pair flagged by any path → hit.

- `check_cross_dup(script, store, today)`: 跨期 过热锚 在场检查
  - Reads the covered-ground store (schema `{"anchors": {name: entry}}`),
    applies `is_stale(entry, today)` per anchor, and reports any hot
    anchor whose name appears as a substring of the script.
  - Only a 读-only 在场 check — never extracts new anchors (the
    post-publish covered-ground distiller at step 19 is the sole
    authoritative anchor extractor; CLAUDE.md 不变量).

- `check_dedup(...)`: composes both.

Temperature shield: dedup operates on 字面/近义 字面 overlap (段/句
n-gram Jaccard, 高 bar cosine confirm) and on the covered-ground
热锚 set. It NEVER makes a judgment about whether two statements
express the same 主观 opinion / 下注. A body that restates the same
bet in different wording shares no long verbatim substring and
misses every hot-anchor key — it returns ok=True (see
`test_temperature_shield_repeated_opinion_not_dup`).

阈值 (`INTRA_JACCARD_THRESHOLD`, `INTRA_EMBED_CONFIRM`) 是命名常量,
fixture 标定后冻结; live run 不过门一律修生成侧 (提示词/davinci),
不擅自放宽阈值凑绿.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Optional

from lib.coveredground import is_stale


# ---------------------------------------------------------------------------
# 阈值常量 (named, frozen — Task 7 fixture 标定后不再改)
# ---------------------------------------------------------------------------

# Intra-episode 逐字重复 主信号 = 最长公共连续子串 (LCS) ≥ this many chars,
# computed as "two texts share an n-gram of length VERBATIM_NGRAM_LEN"
# (a shared k-gram ⟺ a contiguous identical run ≥ k). This is the 06-14
# 防-regression pin and does NOT need the embedded similarity helper.
#
# Calibrated on the Task-1 fixture pairs (fixture-frozen — do NOT widen
# to make a live run pass; fix the generation side instead):
#   MUST flag    : 「全行业…17.2万」repeat  LCS=15 ; 「占GDP…」identical LCS=22
#   must NOT flag: 苏伊士运河关闭/断航 pair  LCS=11 (similar-but-distinct —
#                  shared boilerplate clause, different core claim; this is
#                  the embedding path's job, not the verbatim path's)
# 13 sits with +2 margin on both sides (11 < 13 ≤ 15). A whole-text Jaccard
# 主信号 was REJECTED: it diluted to 0.452 on the embedded 17.2万 repeat yet
# scored 0.583 on the distinct 苏伊士 pair — magnitude can't separate them,
# only contiguous-run length can.
VERBATIM_NGRAM_LEN = 13

# Secondary 逐字 signal: very-high whole-text 2-gram Jaccard (reordered /
# near-identical bodies that may lack one long contiguous run). 0.85 sits
# safely above the distinct 苏伊士 pair (0.583), below identical (1.0).
HIGH_JACCARD_THRESHOLD = 0.85

# Intra-episode 嵌入确认信号 (近义换词 reskin). High bar — only fires when the
# semantic similarity is genuinely high; cross-anchor phrase pairs (e.g.
# 苏伊士 vs 石油, 0.891 per Phase-2 实测) stay BELOW it.
INTRA_EMBED_CONFIRM = 0.93

# Sentence / paragraph segmentation. ATX 段标题 (`## …`) is the
# canonical segmentation boundary — a body is a sequence of segments,
# each treated as a dedup unit. Sentences inside a segment are an
# additional finer-grained unit (catches the 「17.2万」repeat inside
# distinct paragraphs that happen to share a sentence).
_SECTION_HEADING_RE = re.compile(r"^##\s+", re.MULTILINE)
# After `_SECTION_HEADING_RE.split()`, each part starts with the
# heading TITLE TEXT (e.g. "① 开场\n...") — the `## ` prefix has
# already been consumed by the split. Strip this first line (the
# heading title line) so it doesn't pollute the Jaccard denominator
# (① vs ② would otherwise make segments look more different than
# they really are).
_HEADING_TITLE_LINE_RE = re.compile(r"^[^\n]*\n?")
# Sentence-final punctuation (CJK + ASCII). CJK marks (。！？) are
# boundary marks themselves — no trailing whitespace required. ASCII
# marks (.!?) are also followed by the split boundary; the lookbehind
# keeps the mark attached to the preceding sentence.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?])")

# 2-gram tokenization helper. A 2-gram here is a pair of consecutive
# non-whitespace characters (ASCII letters/digits/CJK). Cheap and
# deterministic; language-agnostic; does NOT require a tokenizer.
_NGRAM_LEN = 2


# ---------------------------------------------------------------------------
# segmentation helpers
# ---------------------------------------------------------------------------

def _segments(body: str) -> list[str]:
    """Split `body` into segments on ATX 段标题 (`## …`).

    Empty segments are dropped (a body of only headings returns []).
    Each returned segment is the post-heading content with the
    heading line itself stripped — the heading is what we matched
    on, not content to compare (including the heading in the unit
    would inflate the Jaccard denominator and dilute genuine
    repeats; e.g. 「## ① 开场」 vs 「## ② 展开」 would look
    dissimilar on their own).
    """
    if not isinstance(body, str) or not body:
        return []
    # Split on ATX 段标题 boundaries; each part starts with the
    # heading line (or is empty if body starts at column 0).
    parts = _SECTION_HEADING_RE.split(body)
    out: list[str] = []
    for p in parts:
        # Strip the leading heading line if present. After splitting
        # on `^##\s+`, the remainder may begin with the rest of the
        # heading (the title text after the `## `), then a newline.
        # Strip the leading heading-title line (already-split-off
        # "① 开场\n" or similar). See `_HEADING_TITLE_LINE_RE` docstring.
        seg = _HEADING_TITLE_LINE_RE.sub("", p, count=1).strip()
        if seg:
            out.append(seg)
    return out


def _sentences(text: str) -> list[str]:
    """Split a text into sentence-level units.

    Splits on CJK / ASCII sentence-final punctuation. CJK marks
    (。！？) are boundary marks themselves — no trailing whitespace
    required. ASCII marks (.!?) may optionally be followed by
    whitespace; we DO NOT split on plain `A. B` abbreviations
    because the regex only fires when the mark is preceded by a
    non-space char (the lookbehind anchors on the mark, not the
    surrounding text). Empty fragments dropped.
    """
    if not isinstance(text, str) or not text:
        return []
    parts = _SENTENCE_SPLIT_RE.split(text.strip())
    return [p.strip() for p in parts if p and p.strip()]


# ---------------------------------------------------------------------------
# 2-gram Jaccard
# ---------------------------------------------------------------------------

def _ngrams(text: str, n: int = _NGRAM_LEN) -> set[str]:
    """Return the set of n-gram substrings of `text`.

    n-grams are taken on a whitespace-collapsed view of the text (raw
    char slice — punctuation is NOT stripped because the 06-14
    verbatim repeats include punctuation patterns like
    "17.2万" / "占GDP的比重约百分之三"). The set (not multiset) form
    matches the Jaccard formula below.
    """
    if not isinstance(text, str) or len(text) < n:
        return set()
    return {text[i : i + n] for i in range(len(text) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    """Set Jaccard = |a ∩ b| / |a ∪ b|. Returns 0.0 when both empty."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    inter = a & b
    return len(inter) / len(union)


def _shared_long_ngram(a: str, b: str, n: int = VERBATIM_NGRAM_LEN) -> bool:
    """True iff `a` and `b` share a contiguous identical run of ≥ `n` chars.

    Equivalent to `longest_common_substring(a, b) >= n` but computed via
    n-gram set intersection (O(len) per text) instead of the O(len_a·len_b)
    LCS DP. A shared k-gram exists ⟺ a common contiguous substring of length
    ≥ k exists. This is the 逐字重复 主信号 (catches the 06-14 「17.2万」/
    「占GDP」verbatim repeats embedded in otherwise-different paragraphs,
    which whole-text Jaccard dilutes below threshold).

    Texts shorter than `n` can't share an `n`-gram → False.
    """
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    if len(a) < n or len(b) < n:
        return False
    a_grams = {a[i : i + n] for i in range(len(a) - n + 1)}
    for j in range(len(b) - n + 1):
        if b[j : j + n] in a_grams:
            return True
    return False


def _text_overlap_chars(text: str) -> int:
    """Approximate 'overlap weight' for a hit — length of text in chars.

    Used to compute the per-call `score` (the cumulative weight of all
    flagged pairs, normalised by total text). Empty input → 0.
    """
    return len(text) if isinstance(text, str) else 0


# ---------------------------------------------------------------------------
# public: 站内
# ---------------------------------------------------------------------------

def check_intra_dup(
    body: str,
    *,
    similarity_fn: Optional[Callable[[str, str], float]] = None,
) -> dict[str, Any]:
    """Detect 段落/句子 重复 within a single body.

    Args:
      body: the reader `.md` body text (whole, with ATX headings).
      similarity_fn: optional `(a, b) -> float in [0, 1]` confirming
        similarity for near-dup pairs (only consulted if non-None).
        Injected by tests / a thin `lib.embed.similarity` wrapper.

    Returns:
      `{ok: bool, reason: str, score: float, hits: list[str]}` — same
      shape as `check_artifact`. `ok` is the OPPOSITE of "any hit
      found" (no hits → ok=True, i.e. body is dedup-clean). `score`
      is the ratio of flagged-pair weight to total text weight; 0.0
      when no hits. `hits` is the literal substrings flagged (caller
      shows these in the scorecard / debug).
    """
    if not isinstance(body, str) or not body:
        return {"ok": True, "reason": "empty body", "score": 0.0, "hits": []}

    segs = _segments(body)
    # Build sentence pool: sentences inside each segment. We dedup at
    # BOTH levels (segment↔segment AND sentence↔sentence) because the
    # 06-14 root cause was a sentence-level repeat (「17.2万」appears
    # verbatim in two different segments). Segment-only dedup would
    # miss it when the surrounding paragraphs differ.
    #
    # Each pool entry carries its **origin segment index** so the
    # comparison loop can skip trivial containment matches (a sentence
    # that is a strict substring of its parent segment will share an
    # n-gram with the segment by construction — that's not a real
    # repeat, just sentence-in-segment structure). Cross-segment
    # repeats are unaffected: e.g. ②_sent in ② vs ③_seg in ③ are
    # different origin segments and are still compared.
    sents: list[tuple[str, str, int]] = []  # (text, kind, origin_seg_idx)
    for seg_idx, s in enumerate(segs):
        sents.append((s, "seg", seg_idx))
        for sent in _sentences(s):
            if sent and sent != s:  # avoid dup-adding the segment itself
                sents.append((sent, "sent", seg_idx))

    total_weight = sum(_text_overlap_chars(t) for t, _, _ in sents)
    if total_weight == 0:
        return {"ok": True, "reason": "no content to compare", "score": 0.0, "hits": []}

    hits: list[str] = []
    flagged_weight = 0

    # Quadratic over (segs + sents). With <50 segments + <200 sentences
    # in a typical 7k-char body, this is <10k pair-wise comparisons —
    # trivially fast at pipeline-time, and the structural-purity beats
    # any heuristic on a single broadcast body.
    n = len(sents)
    for i in range(n):
        ti, ki, oi = sents[i]
        ngi = _ngrams(ti)
        if not ngi:
            continue
        for j in range(i + 1, n):
            tj, kj, oj = sents[j]
            ngj = _ngrams(tj)
            if not ngj:
                continue

            # Skip trivial containment: a sentence that is a strict
            # substring of its parent segment will share an n-gram
            # with the segment by construction (the segment already
            # contains the sentence verbatim). This is sentence-in-
            # segment structure, not a real repeat. Cross-segment
            # pairs (different origin) are NEVER skipped — that's
            # where 06-14-style 逐字复制 is caught.
            if oi == oj and (ti in tj or tj in ti):
                continue

            # 三信号并集 (calibrated, frozen):
            #   1. verbatim: shared contiguous run ≥ VERBATIM_NGRAM_LEN (主信号,
            #      no embedding needed) — catches 06-14 逐字复制 embedded in
            #      otherwise-different paragraphs (whole-text Jaccard misses it).
            #   2. high Jaccard ≥ HIGH_JACCARD_THRESHOLD — reordered/near-identical.
            #   3. embed ≥ INTRA_EMBED_CONFIRM (only if similarity_fn given) —
            #      近义换词 reskin. The distinct 苏伊士 pair (LCS=11, Jaccard=0.583)
            #      clears all three, so it stays unflagged unless embedding says so.
            flagged = False
            flagged_by = ""
            if _shared_long_ngram(ti, tj):
                flagged = True
                flagged_by = f"verbatim-run>={VERBATIM_NGRAM_LEN}"
            else:
                j_score = _jaccard(ngi, ngj)
                if j_score >= HIGH_JACCARD_THRESHOLD:
                    flagged = True
                    flagged_by = f"ngram-jaccard={j_score:.2f}"
                elif similarity_fn is not None:
                    try:
                        sim = float(similarity_fn(ti, tj))
                    except Exception:
                        sim = 0.0
                    if sim >= INTRA_EMBED_CONFIRM:
                        flagged = True
                        flagged_by = f"embed-sim={sim:.2f}"

            if flagged:
                # Use the SHORTER fragment as the hit — it tends to be
                # the duplicated unit (e.g. a single sentence), and the
                # scorecard reader benefits from a compact label.
                hit = ti if len(ti) <= len(tj) else tj
                if hit and hit not in hits:
                    hits.append(f"[{flagged_by}] {hit}")
                flagged_weight += min(len(ti), len(tj))

    score = flagged_weight / total_weight if total_weight else 0.0
    ok = not hits
    reason = (
        "intra-dup clean"
        if ok
        else f"intra-dup: {len(hits)} repeated fragment(s) flagged"
    )
    return {"ok": ok, "reason": reason, "score": round(score, 4), "hits": hits}


# ---------------------------------------------------------------------------
# public: 跨期
# ---------------------------------------------------------------------------

def check_cross_dup(
    script: str,
    store: dict[str, Any],
    today: str,
) -> dict[str, Any]:
    """Detect 跨期 过热锚 在念稿中的在场.

    Reads the covered-ground store (the post-publish distiller's
    authoritative output). For each anchor whose `is_stale(entry,
    today)` is true, checks whether the anchor name appears as a
    substring of the script. Any match → hit.

    Iterates `store["anchors"].items()` — the schema is
    `name → entry dict`; `is_stale` takes the entry, not the name.
    A hand-rolled shortcut like `{name: bool}` would let dict-iter
    / entry-shape bugs slip through (Phase-2 GAP-2 lesson).

    Args:
      script: the broadcast `.txt` script (already finalized, what
        will be read aloud). Whole text.
      store: the covered-ground store dict (load_store output).
        `{"anchors": {name: entry}}`. Missing / wrong shape →
        empty-anchor safe-path.
      today: ISO date string `YYYY-MM-DD` (used by `is_stale`).

    Returns:
      `{ok, reason, score, hits}` — same shape as check_intra_dup.
      `ok=True` when no hot anchor is present (or store is empty).
      `score` is the count of flagged hot anchors / total hot
      anchors (0.0 when no hot anchors at all).
    """
    if not isinstance(script, str) or not script:
        return {"ok": True, "reason": "empty script", "score": 0.0, "hits": []}

    # Defensive shape check — load_store is fail-soft and returns
    # `{"anchors": {}}` on missing/garbage files; we re-check here so
    # a hand-rolled caller can't trip a KeyError.
    bucket: dict[str, Any] = {}
    if isinstance(store, dict):
        b = store.get("anchors")
        if isinstance(b, dict):
            bucket = b

    if not bucket:
        return {"ok": True, "reason": "no covered-ground anchors", "score": 0.0, "hits": []}

    # Iterate `.items()` — `is_stale` is an entry predicate, not a
    # name predicate (Phase-2 GAP-2 trap). See coveredground.render_memo
    # for the authoritative per-anchor iteration pattern.
    hot: list[str] = []
    for name, entry in bucket.items():
        if not isinstance(name, str) or not isinstance(entry, dict):
            continue
        try:
            if is_stale(entry, today):
                hot.append(name)
        except Exception:
            # Defensive — a malformed entry must not crash the gate.
            continue

    if not hot:
        return {"ok": True, "reason": "no hot anchors", "score": 0.0, "hits": []}

    # Substring in-script check. Use a single combined scan rather
    # than per-anchor `name in script` — the latter is O(n*anchors)
    # string scans; the former is one linear pass with a small set
    # of needles.
    hits = [name for name in hot if name in script]

    score = len(hits) / len(hot) if hot else 0.0
    ok = not hits
    reason = (
        "cross-dup clean"
        if ok
        else f"cross-dup: {len(hits)} hot anchor(s) present in script"
    )
    return {"ok": ok, "reason": reason, "score": round(score, 4), "hits": hits}


# ---------------------------------------------------------------------------
# public: 合并
# ---------------------------------------------------------------------------

def check_dedup(
    body: str,
    script: str,
    store: dict[str, Any],
    today: str,
    *,
    similarity_fn: Optional[Callable[[str, str], float]] = None,
) -> dict[str, Any]:
    """Compose intra + cross dedup checks.

    Args:
      body: reader `.md` body (segmented + sentence-pooled).
      script: broadcast `.txt` (cross-anchor substring scan).
      store: covered-ground store.
      today: ISO date string.
      similarity_fn: optional embed-confirm hook (intra only).

    Returns:
      `{ok, reason, score, hits}` — `ok = intra.ok AND cross.ok`.
      `hits` is the concatenation of intra.hits + cross.hits.
      `score` is the max of intra.score and cross.score (either
      dimension failing is the headline signal; per-dim scores
      are recoverable via the underlying functions).
    """
    intra = check_intra_dup(body, similarity_fn=similarity_fn)
    cross = check_cross_dup(script, store, today)
    ok = bool(intra["ok"]) and bool(cross["ok"])
    hits = list(intra.get("hits", [])) + list(cross.get("hits", []))
    score = max(float(intra.get("score", 0.0)), float(cross.get("score", 0.0)))
    if ok:
        reason = "dedup clean"
    else:
        parts: list[str] = []
        if not intra["ok"]:
            parts.append(intra["reason"])
        if not cross["ok"]:
            parts.append(cross["reason"])
        reason = "; ".join(parts) if parts else "dedup failed"
    return {"ok": ok, "reason": reason, "score": round(score, 4), "hits": hits}