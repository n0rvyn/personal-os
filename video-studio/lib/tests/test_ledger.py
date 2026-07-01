"""Tests for lib/ledger.py — the video-studio faithfulness gate (忠实门).

Ported from podcast-studio paperline `verify_anchors` with a running-domain
adaptation: the anchor tokenizer keeps `:` glued to numbers so a pace like
`5:20` is ONE zero-tolerance token (not `5`+`20`, which would make pace
matching vacuous since `5` is a substring of almost any text).

Contract pinned here:
- pace token completeness: `5:20` / `4:50` / `2:36.2` tokenize as single
  number tokens.
- number zero-tolerance: a fabricated pace (not in the source) flags; a real
  pace present in the `-layout` fulltext passes (`5:20` ⊂ `5:20.0/5:45.7`).
- pure-word ratio: < 0.8 coverage of non-number tokens flags.
- dirty-table robustness: even the column-serialized DEFAULT pdftotext form
  (rows destroyed, digits intact) grounds a real pace by substring.
- schema: missing/empty `anchor` or `text` is a LedgerError.
- verify_anchors returns {ok, flagged:[{beat_id,text,anchor}]} and never raises.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# Real `pdftotext -layout` rows from Daniels' Running Formula p217 (VDOT table).
# Rows preserved → a pace sits with its VDOT.
LAYOUT_FULLTEXT = (
    "65.0   2:23.0   4:53.0/5:16.6\n"
    "66.0   2:25.2   4:57.5/5:21.5\n"
    "67.0   2:27.4   5:02.0/5:26.3\n"
    "70.0   2:34.0   5:15.5/5:40.9\n"
    "71.0   2:36.2   5:20.0/5:45.7\n"
    "72.0   2:38.4   5:24.5/5:50.6\n"
    "乳酸阈 T 配速 与 VDOT 对应 训练 强度\n"
)

# DEFAULT pdftotext (column-serialized): rows destroyed, digits intact.
DIRTY_FULLTEXT = (
    "65.0\n66.0\n67.0\n70.0\n71.0\n72.0\n"
    "2:23.0\n2:25.2\n2:27.4\n2:34.0\n2:36.2\n2:38.4\n"
    "4:53.0/5:16.6\n4:57.5/5:21.5\n5:15.5/5:40.9\n5:20.0/5:45.7\n5:24.5/5:50.6\n"
)


def _beat(beat_id, text, anchor, page=None):
    e = {"beat_id": beat_id, "text": text, "anchor": anchor}
    if page is not None:
        e["page"] = page
    return e


# ---------------- tokenizer: pace stays one token ----------------

def test_pace_is_single_number_token():
    from lib.ledger import _content_tokens, _is_number_token
    toks = _content_tokens("乳酸阈配速 5:20 / 公里")
    nums = [t for t in toks if _is_number_token(t)]
    assert "5:20" in nums, f"pace must stay one token, got {nums}"
    assert "5" not in nums and "20" not in nums, f"colon must not split pace: {nums}"


def test_fullwidth_colon_does_not_fuse_word_and_number():
    # A full-width '：' (Chinese prose separator) must NOT fuse a word to a
    # following number: "verbatim：59" → "verbatim" + "59", NOT "verbatim:59"
    # (which _is_number_token would treat as a number and zero-tolerance-flag).
    from lib.ledger import _content_tokens, _norm_match
    toks = _content_tokens(_norm_match("verbatim：59 to 74 percent"))
    assert "verbatim:59" not in toks, f"word:number must not fuse, got {toks}"
    assert "59" in toks and "74" in toks, f"digits must tokenize apart, got {toks}"
    # half-width pace colon is unaffected (regression guard)
    assert _content_tokens(_norm_match("配速 5:20/km")) == ["5:20/km"]


def test_fullwidth_comma_does_not_fuse_across_digits():
    # A full-width '，' between digits ("v1.2，2026-06") must NOT be swallowed by
    # the thousands-separator rule and fuse to "v1.22026-06"; a half-width ','
    # thousands separator ("1,500") still collapses to "1500".
    from lib.ledger import _norm_match
    assert _norm_match("v1.2，2026-06") == "v1.2 2026-06"
    assert _norm_match("报名 1,500 人") == "报名 1500 人"


def test_compound_anchor_with_verbatim_annotation_grounds():
    # The exact false-flag class that halted a live render: a compound anchor
    # (table row ；verbatim：english quote) whose numbers are all in the source.
    from lib.ledger import verify_anchors
    fulltext = (
        "| E | Easy | 59–74% | 65–79% | 有氧基础 | Daniels p.83 |\n"
        'verbatim 锚点：E = "59 to 74 percent of O2max or about 65 to 79 percent"\n'
    )
    ledger = {"beats": [_beat(
        "b02",
        "E 区是有氧基础区。",
        "E | Easy | 59–74% | 65–79% | 有氧基础 | Daniels p.83；"
        "verbatim：59 to 74 percent of O2max or about 65 to 79 percent",
    )]}
    res = verify_anchors(ledger, fulltext)
    assert res["ok"], f"faithful compound anchor must ground, flagged={res['flagged']}"


def test_pace_with_decimal_and_split_stays_grouped():
    from lib.ledger import _content_tokens, _is_number_token
    toks = [t for t in _content_tokens("2:36.2 区间") if _is_number_token(t)]
    assert "2:36.2" in toks


# ---------------- number zero-tolerance ----------------

def test_real_pace_grounds_against_layout():
    from lib.ledger import verify_anchors
    ledger = {"beats": [_beat("b1", "乳酸阈配速约每公里五分二十", "乳酸阈 T 配速 5:20")]}
    res = verify_anchors(ledger, LAYOUT_FULLTEXT)
    assert res["ok"] is True, res["flagged"]


def test_fabricated_pace_flags():
    from lib.ledger import verify_anchors
    # 5:25 is NOT in the source paces
    ledger = {"beats": [_beat("b1", "乳酸阈配速五分二十五", "乳酸阈 T 配速 5:25")]}
    res = verify_anchors(ledger, LAYOUT_FULLTEXT)
    assert res["ok"] is False
    assert res["flagged"][0]["beat_id"] == "b1"


def test_fabricated_vdot_flags():
    from lib.ledger import verify_anchors
    ledger = {"beats": [_beat("b1", "VDOT 九十九", "VDOT 99 阈值")]}
    res = verify_anchors(ledger, LAYOUT_FULLTEXT)
    assert res["ok"] is False


# ---------------- pure-word ratio ----------------

def test_high_paraphrase_below_threshold_flags():
    from lib.ledger import verify_anchors
    # No numbers; pure-word tokens almost none present in fulltext → < 0.8
    ledger = {"beats": [_beat("b1", "完全编造的句子", "fabricated invented nonsense claim words")]}
    res = verify_anchors(ledger, LAYOUT_FULLTEXT)
    assert res["ok"] is False


def test_faithful_paraphrase_passes():
    from lib.ledger import verify_anchors
    # ASCII content words all present in fulltext; reordered prose OK.
    ledger = {"beats": [_beat("b1", "阈值配速", "VDOT 71 乳酸阈 T 配速 5:20")]}
    res = verify_anchors(ledger, LAYOUT_FULLTEXT)
    assert res["ok"] is True, res["flagged"]


# ---------------- dirty-table robustness ----------------

def test_real_pace_grounds_against_dirty_serialized_text():
    """Even when pdftotext default-mode destroys rows, the pace digits survive
    so substring zero-tolerance still grounds a real pace."""
    from lib.ledger import verify_anchors
    ledger = {"beats": [_beat("b1", "乳酸阈配速", "配速 5:20")]}
    res = verify_anchors(ledger, DIRTY_FULLTEXT)
    assert res["ok"] is True, res["flagged"]


# ---------------- equivalent-notation tolerance (digits stay zero-tolerance) ----------------

NOTATION_FT = (
    "vdot 60.0   64.0   68.0\n"
    "the 400-meter and 800-meter columns, plus 1,500 and mile times\n"
    "71.0  2:36.2  5:20.0/5:45.7\n"
)


def test_trailing_colon_grounds():
    """`60.0:` (trailing prose colon) ≡ source `60.0`."""
    from lib.ledger import verify_anchors
    r = verify_anchors({"beats": [_beat("b1", "t", "VDOT 60.0: 阈值")]}, NOTATION_FT)
    assert r["ok"] is True, r["flagged"]


def test_unit_compression_grounds():
    """`400m` ≡ source `400-meter`; `800m` ≡ `800-meter`."""
    from lib.ledger import verify_anchors
    r = verify_anchors({"beats": [_beat("b1", "t", "400m 与 800m 列")]}, NOTATION_FT)
    assert r["ok"] is True, r["flagged"]


def test_thousands_comma_grounds():
    """`1500` ≡ source `1,500`."""
    from lib.ledger import verify_anchors
    r = verify_anchors({"beats": [_beat("b1", "t", "1500 米")]}, NOTATION_FT)
    assert r["ok"] is True, r["flagged"]


def test_fabricated_still_flags_after_notation_normalization():
    """Digit zero-tolerance must survive: a number truly absent still flags."""
    from lib.ledger import verify_anchors
    r = verify_anchors({"beats": [_beat("b1", "t", "VDOT 93.0 阈值")]}, NOTATION_FT)
    assert r["ok"] is False  # 93.0 not in source


def test_fabricated_pace_still_flags_after_normalization():
    from lib.ledger import verify_anchors
    r = verify_anchors({"beats": [_beat("b1", "t", "配速 5:25")]}, NOTATION_FT)
    assert r["ok"] is False  # 5:25 not in source


# ---------------- schema ----------------

def test_validate_ledger_ok():
    from lib.ledger import validate_ledger
    validate_ledger({"beats": [_beat("b1", "t", "a")]})  # no raise


def test_validate_ledger_missing_beats():
    from lib.ledger import validate_ledger, LedgerError
    with pytest.raises(LedgerError):
        validate_ledger({})


def test_validate_ledger_empty_beats():
    from lib.ledger import validate_ledger, LedgerError
    with pytest.raises(LedgerError):
        validate_ledger({"beats": []})


def test_validate_ledger_empty_anchor():
    from lib.ledger import validate_ledger, LedgerError
    with pytest.raises(LedgerError):
        validate_ledger({"beats": [_beat("b1", "t", "")]})


def test_validate_ledger_empty_text():
    from lib.ledger import validate_ledger, LedgerError
    with pytest.raises(LedgerError):
        validate_ledger({"beats": [_beat("b1", "", "a")]})


# ---------------- verify_anchors shape ----------------

def test_verify_anchors_shape_and_no_raise():
    from lib.ledger import verify_anchors
    ledger = {"beats": [
        _beat("b1", "乳酸阈配速", "VDOT 71 配速 5:20"),
        _beat("b2", "编造", "配速 5:25"),
    ]}
    res = verify_anchors(ledger, LAYOUT_FULLTEXT)
    assert set(res.keys()) == {"ok", "flagged"}
    assert res["ok"] is False
    assert len(res["flagged"]) == 1
    f = res["flagged"][0]
    assert set(f.keys()) >= {"beat_id", "text", "anchor"}
    assert f["beat_id"] == "b2"


def test_empty_fulltext_flags_numbered_anchor():
    from lib.ledger import verify_anchors
    res = verify_anchors({"beats": [_beat("b1", "t", "配速 5:20")]}, "")
    assert res["ok"] is False
