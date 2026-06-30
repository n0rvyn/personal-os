"""Tests for lib/extract.py — pdftotext -layout per-page extraction.

Pins:
- uses `pdftotext -layout` (table rows survive — required for pace grounding).
- one `pdftotext` call per page → a {page_no: text} map (per-page so provenance
  can map an assertion → page; UX-004).
- `-f P -l P` page bounds passed per page.
- empty-text page surfaces (caller can gate).
- fulltext = join of the per-page texts in page order (the SAME object the
  faithfulness gate checks against).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

DANIELS = Path("/Users/norvyn/Downloads/UESCA/"
               "Daniels' Running Formula Human Kinetics (2021) reduced.pdf")


class _FakeProc:
    def __init__(self, stdout):
        self.stdout = stdout
        self.returncode = 0
        self.stderr = ""


def test_extract_uses_layout_and_page_bounds():
    from lib.extract import extract_pages
    calls = []

    def fake_runner(argv, **kw):
        calls.append(argv)
        return _FakeProc("page text")

    extract_pages("x.pdf", [217, 218], runner=fake_runner)
    assert len(calls) == 2, "one pdftotext call per page"
    for argv in calls:
        assert "-layout" in argv, f"-layout required, got {argv}"
        assert "-f" in argv and "-l" in argv
    # page 217 bounded -f 217 -l 217
    a0 = calls[0]
    assert a0[a0.index("-f") + 1] == "217"
    assert a0[a0.index("-l") + 1] == "217"


def test_extract_returns_per_page_map():
    from lib.extract import extract_pages
    seq = {"217": "VDOT table 5:20", "218": "more paces 1:40"}

    def fake_runner(argv, **kw):
        pg = argv[argv.index("-f") + 1]
        return _FakeProc(seq[pg])

    m = extract_pages("x.pdf", [217, 218], runner=fake_runner)
    assert m == {217: "VDOT table 5:20", 218: "more paces 1:40"}


def test_fulltext_join_in_page_order():
    from lib.extract import extract_pages, join_fulltext

    def fake_runner(argv, **kw):
        pg = argv[argv.index("-f") + 1]
        return _FakeProc(f"P{pg}")

    m = extract_pages("x.pdf", [218, 217], runner=fake_runner)
    ft = join_fulltext(m)
    # page order ascending regardless of request order
    assert ft.index("P217") < ft.index("P218")


def test_empty_page_text_kept_as_empty():
    from lib.extract import extract_pages

    def fake_runner(argv, **kw):
        return _FakeProc("   \n  ")

    m = extract_pages("x.pdf", [5], runner=fake_runner)
    assert m[5].strip() == ""


@pytest.mark.skipif(not DANIELS.exists(), reason="Daniels PDF not present")
def test_real_pdftotext_layout_preserves_pace_rows():
    """Real-path: -layout extraction of a VDOT page keeps pace rows intact."""
    from lib.extract import extract_pages
    m = extract_pages(str(DANIELS), [217])
    txt = m[217]
    # a real pace from the layout table should appear glued
    assert "5:20" in txt
    # row form (VDOT  pace) — at least one line has both a VDOT and a pace
    import re
    assert re.search(r"7[01]\.0.*[0-9]:[0-9]{2}", txt), "rows not preserved"
