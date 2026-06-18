"""Tests for lib/paperline/discovery.py — arXiv API discovery + Atom parse.

Written before lib/paperline/discovery.py exists; collection must fail at
this point (`No module named 'lib.paperline.discovery'`). The parser's
central contract (D-005, D-007):

  - `parse_atom(xml_bytes) -> list[dict]` — each candidate dict carries
    `arxiv_id`, `title`, `summary`, `published`, `primary_category`,
    `categories` (list[str]), `pdf_url`. arxiv_id is parsed from the
    `<id>.../abs/{id}</id>` URL and validated against the strict regex
    ``^\\d{4}\\.\\d{4,5}(v\\d+)?$``. Entries missing required fields are
    SKIPPED, never defaulted (fail-closed on the input boundary).
  - `fetch_candidates(categories, *, max_results=60, fetcher=...)` —
    injects the HTTP fetcher (offline test), builds the query URL
    `https://export.arxiv.org/api/query?search_query=cat:...&sortBy=submittedDate&sortOrder=descending&max_results=...`,
    returns the parsed candidate list.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.paperline.discovery import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- fixtures ----------

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "arxiv-api-sample.xml"
_ATOM_XML = _FIXTURE_PATH.read_bytes()

# arxiv_id regex per Threat Model §1 + D-005 (id validation at the
# discovery→candidate boundary, BEFORE any URL or argv build).
_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 2-impl will resolve this."""
    from lib.paperline import discovery  # noqa: F401

    assert hasattr(discovery, "parse_atom")
    assert hasattr(discovery, "fetch_candidates")


# ---------- parse_atom ----------

def test_parse_atom_fields():
    """Each parsed candidate dict has the 7 required fields, and arxiv_id
    matches the strict regex parsed from the <id>.../abs/{id}</id> URL."""
    from lib.paperline.discovery import parse_atom

    candidates = parse_atom(_ATOM_XML)

    assert isinstance(candidates, list)
    assert len(candidates) == 2  # fixture has 2 entries

    required = {
        "arxiv_id",
        "title",
        "summary",
        "published",
        "primary_category",
        "categories",
        "pdf_url",
    }
    for cand in candidates:
        assert required.issubset(cand.keys()), (
            f"candidate missing fields: {required - cand.keys()}"
        )
        # arxiv_id is parsed from the abs URL and matches the strict regex.
        assert _ARXIV_ID_RE.match(cand["arxiv_id"]), (
            f"arxiv_id {cand['arxiv_id']!r} does not match strict regex"
        )
        # string fields are non-empty
        for key in ("arxiv_id", "title", "summary", "published", "primary_category", "pdf_url"):
            assert isinstance(cand[key], str) and cand[key], (
                f"{key} should be non-empty str, got {cand[key]!r}"
            )
        # categories is a non-empty list[str]
        assert isinstance(cand["categories"], list)
        assert cand["categories"], "categories should be non-empty"
        for cat in cand["categories"]:
            assert isinstance(cat, str) and cat

    # First fixture entry: arxiv id 2606.19341v1
    first = candidates[0]
    assert first["arxiv_id"] == "2606.19341v1"
    # Real fixture title is "Native Active Perception as Reasoning for Omni-Modal
    # Understanding" (the model name "OmniAgent" appears only in the abstract/summary).
    assert "Native Active Perception" in first["title"]
    assert "cs.CV" == first["primary_category"]
    assert "cs.CV" in first["categories"]
    assert "cs.CL" in first["categories"]
    assert first["pdf_url"].endswith("/pdf/2606.19341v1")

    # Second fixture entry: arxiv id 2606.19336v1
    second = candidates[1]
    assert second["arxiv_id"] == "2606.19336v1"
    assert "Turing" in second["title"]
    assert second["primary_category"] == "cs.CL"


def test_parse_skips_entry_missing_id():
    """An entry without an <id> element is SKIPPED, not defaulted. This is
    the fail-closed discipline on the discovery→candidate boundary (D-005)."""
    from lib.paperline.discovery import parse_atom

    # Build a 2-entry Atom feed; the first entry is missing <id>.
    malformed = b"""<?xml version='1.0' encoding='UTF-8'?>
<feed xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>No-Id Entry That Must Be Skipped</title>
    <summary>No id, so it must drop out at parse time.</summary>
    <link href="https://arxiv.org/pdf/2606.00001v1" rel="related" type="application/pdf"/>
    <published>2026-06-17T00:00:00Z</published>
    <arxiv:primary_category term="cs.CL"/>
    <category term="cs.CL"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2606.00002v1</id>
    <title>Valid Entry</title>
    <summary>A real entry with an id.</summary>
    <link href="https://arxiv.org/pdf/2606.00002v1" rel="related" type="application/pdf"/>
    <published>2026-06-17T01:00:00Z</published>
    <arxiv:primary_category term="cs.CL"/>
    <category term="cs.CL"/>
  </entry>
</feed>"""

    candidates = parse_atom(malformed)
    assert len(candidates) == 1, (
        f"expected 1 valid candidate (id-less entry dropped), got {len(candidates)}"
    )
    assert candidates[0]["arxiv_id"] == "2606.00002v1"


# ---------- fetch_candidates ----------

def test_fetch_candidates_uses_injected_fetcher():
    """`fetch_candidates` builds a query URL containing `cat:` +
    `sortBy=submittedDate` and parses the injected fetcher's bytes into
    the expected number of candidates. (No live network in tests.)"""
    from lib.paperline.discovery import fetch_candidates

    captured_urls: list[str] = []

    def fake_fetcher(url: str) -> bytes:
        captured_urls.append(url)
        return _ATOM_XML

    candidates = fetch_candidates(
        ["cs.CL"],
        max_results=5,
        fetcher=fake_fetcher,
    )

    # Fetcher called exactly once; URL contains the expected query params.
    assert len(captured_urls) == 1
    url = captured_urls[0]
    assert "cat:cs.CL" in url
    assert "sortBy=submittedDate" in url
    assert "sortOrder=descending" in url
    assert "max_results=5" in url

    # Returns the same parsed candidates as parse_atom.
    assert len(candidates) == 2
    assert candidates[0]["arxiv_id"] == "2606.19341v1"
