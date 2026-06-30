"""Tests for lib/provenance.py — objective assertion → PDF page (UX-004)."""
from __future__ import annotations

import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

PAGE_MAP = {
    216: "65.0  2:23.0  4:53.0/5:16.6\n66.0  2:25.2  4:57.5/5:21.5",
    217: "71.0  2:36.2  5:20.0/5:45.7\n72.0  2:38.4  5:24.5/5:50.6",
}


def _beat(bid, text, anchor):
    return {"beat_id": bid, "text": text, "anchor": anchor}


def test_objective_beat_maps_to_grounding_page():
    from lib.provenance import build_provenance
    beats = [_beat("b1", "乳酸阈配速", "乳酸阈 T 配速 5:20")]
    prov = build_provenance(beats, PAGE_MAP)
    assert len(prov) == 1
    assert prov[0]["beat_id"] == "b1"
    assert prov[0]["page"] == 217  # 5:20 is on page 217


def test_multi_page_hit_takes_first_page():
    from lib.provenance import build_provenance
    # '5' appears on both pages; first ascending page wins
    beats = [_beat("b1", "x", "配速 4:57")]  # 4:57 only on page 216
    prov = build_provenance(beats, PAGE_MAP)
    assert prov[0]["page"] == 216


def test_pure_prose_beat_has_no_page_required():
    from lib.provenance import build_provenance
    beats = [_beat("b1", "轻松跑打底很重要", "轻松跑 是 训练 的 基础")]  # no number
    prov = build_provenance(beats, PAGE_MAP)
    # included but page is None (not an objective-data assertion)
    assert prov[0]["page"] is None


def test_every_objective_assertion_has_a_page_ux004():
    from lib.provenance import build_provenance
    beats = [
        _beat("b1", "x", "配速 5:20"),
        _beat("b2", "x", "配速 4:57"),
        _beat("b3", "x", "VDOT 72"),
    ]
    prov = build_provenance(beats, PAGE_MAP)
    objective = [p for p in prov if _has_number(p["assertion"])]
    assert all(p["page"] is not None for p in objective), prov


def test_unfindable_number_page_is_none():
    from lib.provenance import build_provenance
    beats = [_beat("b1", "x", "配速 9:99")]  # not in any page
    prov = build_provenance(beats, PAGE_MAP)
    assert prov[0]["page"] is None


def test_equivalent_notation_maps_to_page():
    """A gate-grounded number in equivalent notation still locates its page
    (consistent with verify_anchors): 400m → page with '400-meter'."""
    from lib.provenance import build_provenance
    pm = {214: "the 400-meter and 800-meter columns, plus 1,500 times\n"}
    beats = [
        {"beat_id": "b1", "text": "x", "anchor": "400m 与 800m"},
        {"beat_id": "b2", "text": "x", "anchor": "1500 米"},   # ≡ 1,500
    ]
    prov = build_provenance(beats, pm)
    assert prov[0]["page"] == 214
    assert prov[1]["page"] == 214


def _has_number(s):
    return any(c.isdigit() for c in s)


# ---------------------------------------------------------------------------
# New tests: source + verify_status per assertion (Task 6 / UX-004 extension)
# ---------------------------------------------------------------------------


def test_provenance_carries_source_and_verify_status():
    """Each provenance record must carry source + verify_status from the beat."""
    from lib.provenance import build_provenance
    beats = [
        {
            "beat_id": "b1",
            "text": "乳酸阈配速",
            "anchor": "乳酸阈 T 配速 5:20",
            "source": "Daniels",
            "verify_status": "✅",
        },
    ]
    prov = build_provenance(beats, PAGE_MAP)
    assert prov[0]["source"] == "Daniels"
    assert prov[0]["verify_status"] == "✅"


def test_provenance_engineering_estimate_flagged():
    """⚠️ engineering estimate beats carry verify_status through provenance."""
    from lib.provenance import build_provenance
    beats = [
        {
            "beat_id": "b2",
            "text": "冷启动 VDOT 估计",
            "anchor": "VDOT 先验 48",
            "source": "UESCA",
            "verify_status": "⚠️",
        },
    ]
    prov = build_provenance(beats, PAGE_MAP)
    assert prov[0]["verify_status"] == "⚠️"
    assert prov[0]["source"] == "UESCA"


def test_provenance_missing_source_defaults_empty():
    """Beats without source/verify_status get empty-string defaults (not KeyError)."""
    from lib.provenance import build_provenance
    beats = [{"beat_id": "b1", "text": "x", "anchor": "配速 5:20"}]
    prov = build_provenance(beats, PAGE_MAP)
    assert "source" in prov[0]
    assert "verify_status" in prov[0]


def test_provenance_doc_ref_str_key():
    """Doc mode uses str keys; grounding must work with str keys (doc_ref field)."""
    from lib.provenance import build_provenance
    doc_map = {
        "01-intensity-zones": "训练区间 T 配速 5:20/km",
        "02-vdot-system": "VDOT 72 对应马拉松 2:38",
    }
    beats = [
        {
            "beat_id": "b1",
            "text": "阈值配速",
            "anchor": "T 配速 5:20",
            "source": "Daniels",
            "verify_status": "✅",
        },
    ]
    prov = build_provenance(beats, doc_map)
    assert prov[0]["source"] == "Daniels"
    assert prov[0]["verify_status"] == "✅"
    # doc_ref should be the str key that grounded it
    assert prov[0].get("doc_ref") == "01-intensity-zones"
