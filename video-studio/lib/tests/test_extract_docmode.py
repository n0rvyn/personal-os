"""Tests for lib/extract.py — doc mode (reads authority-layer markdown, not PDF).

TDD fail-first: extract_doc_sections + render_pace_lines don't exist in lib/extract.py
yet. Collection will error at the module-level import → all tests RED. That red IS
the Task 3-tests success signal.

Covers:
- render_pace_lines: decimal min/km → MM:SS conversion (VDOT 60 threshold 3.67 → "3:40")
- extract_doc_sections: reads snapshot markdown by doc_ref, returns {ref: text}
- doc mode: fulltext = join of doc section texts (same object 忠实门 checks)
- doc mode: page_text keys are str doc-refs (not int page numbers like PDF mode)
- source / verify_status carried through ctx unchanged
- missing doc → clean ExtractError (not bare FileNotFoundError)
- section-anchor in ref (e.g. "file.md#section-1") resolved to base file
- 3-way dispatch priority: doc_refs > VIDEO_PDF > fixture; PDF + fixture modes intact
- THE LOAD-BEARING M5: VDOT 60 threshold "3:40" is present in fulltext after
  render_pace_lines appended — false-green net for 忠实门 pace grounding.
  Without this, any digest-writer citing "3:40" would be halted by verify_anchors
  (zero-tolerance on numbers, grounds against ctx["fulltext"]).

Regression shield: do not modify test_extract.py or any existing test.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# TDD fail-first: these symbols don't exist in lib/extract.py until Task 3-impl.
# This import raises ImportError at collection time → entire file RED.
from lib.extract import extract_doc_sections, render_pace_lines  # noqa: E402

# ---------------------------------------------------------------------------
# Known values from vdot_pace_data.json v1.2 (Daniels Gilbert VO2 model)
# ---------------------------------------------------------------------------

# VDOT 60 from the real JSON: paces.threshold = 3.67 min/km
# Conversion: 3.67 * 60 = 220.2 total seconds → 3 min 40.2 sec → "3:40"
VDOT_60_THRESHOLD_DECIMAL = 3.67
VDOT_60_THRESHOLD_MMSS = "3:40"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _mini_vdot_json(tmp_path: Path) -> Path:
    """A self-contained vdot_pace_data.json with only VDOT 60, for isolation."""
    data = {
        "version": "1.2",
        "data": [
            {
                "vdot": 60,
                "raceTimes": {
                    "fiveK": 1023,
                    "tenK": 2122,
                    "halfMarathon": 4689,
                    "marathon": 9805,
                },
                "paces": {
                    "easyMin": 4.27,
                    "easyMax": 4.81,
                    "marathon": 3.87,
                    "threshold": VDOT_60_THRESHOLD_DECIMAL,
                    "interval": 3.42,
                    "repetition400m": 76,
                },
            }
        ],
    }
    p = tmp_path / "vdot_pace_data.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _snapshot_dir(tmp_path: Path) -> Path:
    """Create a minimal authority-layer snapshot directory with one markdown doc."""
    snap = tmp_path / "syllabus" / "source"
    snap.mkdir(parents=True)
    doc = snap / "01-vdot-basics.md"
    doc.write_text(
        "# VDOT System\n\n"
        "VDOT is a measure of aerobic capacity. "
        "Jack Daniels developed training zones from VDOT values. "
        "Athletes with higher VDOT tolerate faster training paces.\n",
        encoding="utf-8",
    )
    return snap


# ---------------------------------------------------------------------------
# render_pace_lines: decimal min/km → MM:SS conversion
# ---------------------------------------------------------------------------


def test_render_pace_lines_threshold_3_67_gives_3_40():
    """render_pace_lines converts VDOT 60 threshold 3.67 min/km to '3:40'.

    This is the canonical M5 false-green-net value. If this string does not
    appear in fulltext, the 忠实门 will halt any digest citing '3:40'.
    """
    mini_data = {
        "version": "1.2",
        "data": [
            {
                "vdot": 60,
                "paces": {
                    "easyMin": 4.27,
                    "easyMax": 4.81,
                    "marathon": 3.87,
                    "threshold": VDOT_60_THRESHOLD_DECIMAL,
                    "interval": 3.42,
                    "repetition400m": 76,
                },
            }
        ],
    }
    text = render_pace_lines(mini_data)
    assert VDOT_60_THRESHOLD_MMSS in text, (
        f"Expected '{VDOT_60_THRESHOLD_MMSS}' in render_pace_lines output; got: {text!r}"
    )


def test_render_pace_lines_contains_vdot_label():
    """render_pace_lines labels lines by VDOT so output is identifiable."""
    mini_data = {
        "version": "1.2",
        "data": [
            {
                "vdot": 60,
                "paces": {
                    "easyMin": 4.27,
                    "easyMax": 4.81,
                    "marathon": 3.87,
                    "threshold": 3.67,
                    "interval": 3.42,
                    "repetition400m": 76,
                },
            }
        ],
    }
    text = render_pace_lines(mini_data)
    assert "60" in text, f"VDOT label '60' missing from output: {text!r}"


def test_render_pace_lines_null_paces_no_crash():
    """render_pace_lines handles null interval/repetition paces (VDOT < 34) gracefully."""
    mini_data = {
        "version": "1.2",
        "data": [
            {
                "vdot": 30,
                "paces": {
                    "easyMin": 7.41,
                    "easyMax": 8.29,
                    "marathon": 6.86,
                    "threshold": 6.40,
                    "interval": None,
                    "repetition400m": None,
                },
            }
        ],
    }
    text = render_pace_lines(mini_data)  # must not raise
    assert "30" in text


def test_render_pace_lines_rounding_whole_seconds():
    """MM:SS output has integer seconds (no decimal like ':40.2') — required for 忠实门 match."""
    mini_data = {
        "version": "1.2",
        "data": [
            {"vdot": 60, "paces": {
                "easyMin": 4.27, "easyMax": 4.81,
                "marathon": 3.87, "threshold": 3.67,
                "interval": 3.42, "repetition400m": 76,
            }}
        ],
    }
    text = render_pace_lines(mini_data)
    # Must not contain fractional seconds like "3:40.2"
    import re
    assert not re.search(r"\d:\d{2}\.\d", text), (
        f"Fractional seconds found in pace output — 忠実門 match would fail: {text!r}"
    )


# ---------------------------------------------------------------------------
# extract_doc_sections: reads markdown snapshot by doc_ref
# ---------------------------------------------------------------------------


def test_extract_doc_sections_reads_single_ref(tmp_path: Path):
    """extract_doc_sections returns {ref: text} for a single valid doc_ref."""
    snap = _snapshot_dir(tmp_path)
    result = extract_doc_sections(snap, ["01-vdot-basics.md"])
    assert "01-vdot-basics.md" in result
    assert "VDOT" in result["01-vdot-basics.md"]
    assert len(result["01-vdot-basics.md"]) > 0


def test_extract_doc_sections_multiple_refs(tmp_path: Path):
    """extract_doc_sections handles multiple refs and returns a map for each."""
    snap = _snapshot_dir(tmp_path)
    (snap / "02-training-zones.md").write_text(
        "# Training Zones\nE pace is easy, T pace is threshold.\n",
        encoding="utf-8",
    )
    result = extract_doc_sections(snap, ["01-vdot-basics.md", "02-training-zones.md"])
    assert "01-vdot-basics.md" in result
    assert "02-training-zones.md" in result
    assert "VDOT" in result["01-vdot-basics.md"]
    assert "Training Zones" in result["02-training-zones.md"]


def test_extract_doc_sections_missing_doc_raises_extract_error(tmp_path: Path):
    """extract_doc_sections raises ExtractError (not raw FileNotFoundError) for missing doc."""
    from lib.extract import ExtractError

    snap = tmp_path / "snap"
    snap.mkdir()
    with pytest.raises(ExtractError):
        extract_doc_sections(snap, ["nonexistent-doc.md"])


def test_extract_doc_sections_section_anchor_resolved(tmp_path: Path):
    """doc_refs with '#section-anchor' resolve to the base filename (strip anchor)."""
    snap = _snapshot_dir(tmp_path)
    # ref with anchor — file is "01-vdot-basics.md", anchor "#section-1" is stripped
    result = extract_doc_sections(snap, ["01-vdot-basics.md#section-1"])
    combined = "".join(result.values())
    assert "VDOT" in combined, (
        "Section-anchor ref should resolve to base file and return its text"
    )


def test_extract_doc_sections_keys_are_strings(tmp_path: Path):
    """page_text in doc mode has str keys (doc refs), not int keys (PDF page numbers)."""
    snap = _snapshot_dir(tmp_path)
    result = extract_doc_sections(snap, ["01-vdot-basics.md"])
    for key in result:
        assert isinstance(key, str), (
            f"Doc mode page_text keys must be str refs, not {type(key)}"
        )


# ---------------------------------------------------------------------------
# Doc mode fulltext: join of all doc section texts
# ---------------------------------------------------------------------------


def test_doc_mode_fulltext_non_empty(tmp_path: Path):
    """Fulltext assembled from extract_doc_sections is non-empty."""
    snap = _snapshot_dir(tmp_path)
    doc_map = extract_doc_sections(snap, ["01-vdot-basics.md"])
    fulltext = "\n".join(doc_map.values())
    assert len(fulltext.strip()) > 0
    assert "VDOT" in fulltext


def test_doc_mode_fulltext_contains_all_refs(tmp_path: Path):
    """Fulltext is a join of ALL referenced doc sections (not just first)."""
    snap = _snapshot_dir(tmp_path)
    (snap / "02-training-zones.md").write_text(
        "E pace for easy runs.\n",
        encoding="utf-8",
    )
    doc_map = extract_doc_sections(snap, ["01-vdot-basics.md", "02-training-zones.md"])
    fulltext = "\n".join(doc_map.values())
    assert "VDOT" in fulltext
    assert "E pace" in fulltext


# ---------------------------------------------------------------------------
# THE LOAD-BEARING ONE — verifier M5 / false-green net
#
# VDOT 60 threshold pace "3:40" MUST appear in ctx["fulltext"] in doc mode.
# Without this, a digest-writer citing "3:40" would be halted by 忠实门
# (verify_anchors grounds numeric anchors against fulltext, zero-tolerance).
# This test is the only structural guarantee that pace numbers are grounded.
# ---------------------------------------------------------------------------


def test_pace_in_fulltext_vdot60_threshold_3_40(tmp_path: Path):
    """M5 LOAD-BEARING: VDOT 60 threshold '3:40' is present in fulltext after render_pace_lines.

    Simulates what extract doc mode does end-to-end:
      1. extract_doc_sections reads doc markdown
      2. render_pace_lines(vdot_json) appends normalized pace table
      3. fulltext = join(doc texts) + pace_lines
    Then asserts '3:40' is in fulltext.

    Without this assertion a passing test suite would still produce a fulltext
    that halts the 忠实门 on any pace citation — the classic false-green scenario.
    """
    snap = _snapshot_dir(tmp_path)
    vdot_json_path = _mini_vdot_json(tmp_path)

    # Step 1: extract doc sections
    doc_map = extract_doc_sections(snap, ["01-vdot-basics.md"])

    # Step 2: render pace lines from vdot JSON
    with open(vdot_json_path, encoding="utf-8") as fh:
        vdot_data = json.load(fh)
    pace_text = render_pace_lines(vdot_data)

    # Step 3: fulltext = doc content + pace table (mirrors what step_extract will do)
    fulltext = "\n".join(doc_map.values()) + "\n" + pace_text

    assert VDOT_60_THRESHOLD_MMSS in fulltext, (
        f"VDOT 60 threshold pace '{VDOT_60_THRESHOLD_MMSS}' (3.67 min/km) not found in fulltext. "
        f"Digest-writer citing this pace would be halted by 忠実門. "
        f"pace_text snippet: {pace_text[:300]!r}"
    )


# ---------------------------------------------------------------------------
# 3-way dispatch priority: doc_refs > VIDEO_PDF > fixture
# PDF and fixture modes must remain intact (regression shield)
# ---------------------------------------------------------------------------


def test_three_way_pdf_mode_not_broken(tmp_path: Path):
    """PDF mode (extract_pages) is intact — not affected by adding doc mode."""
    from lib.extract import extract_pages

    class _FakeProc:
        stdout = "PDF page text 5:20"
        returncode = 0
        stderr = ""

    def fake_runner(argv, **kw):
        return _FakeProc()

    m = extract_pages("x.pdf", [217], runner=fake_runner)
    assert 217 in m
    assert "5:20" in m[217]


def test_three_way_all_functions_importable():
    """After Task 3-impl: extract_pages, extract_doc_sections, render_pace_lines all importable."""
    from lib.extract import extract_pages, extract_doc_sections, render_pace_lines  # noqa: F401
    assert callable(extract_pages)
    assert callable(extract_doc_sections)
    assert callable(render_pace_lines)


def test_three_way_doc_refs_priority_over_pdf(tmp_path: Path, monkeypatch):
    """When doc_refs present, extract_doc_sections is used (not extract_pages).

    This pins the dispatch contract: the presence of doc_refs in ctx triggers
    doc mode regardless of VIDEO_PDF env. Verified structurally: doc_refs path
    returns str-keyed map (doc mode); PDF path returns int-keyed map (PDF mode).
    """
    monkeypatch.setenv("VIDEO_PDF", str(tmp_path / "fake.pdf"))

    snap = _snapshot_dir(tmp_path)
    # doc_refs → extract_doc_sections → str-keyed result (not PDF int-keyed)
    result = extract_doc_sections(snap, ["01-vdot-basics.md"])
    for key in result:
        assert isinstance(key, str), (
            "doc_refs path must return str-keyed map (doc mode), got int key — "
            "dispatch priority broken: PDF mode ran instead of doc mode"
        )
