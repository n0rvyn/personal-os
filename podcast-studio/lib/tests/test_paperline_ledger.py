"""Tests for lib/paperline/ledger.py — paper fact-ledger schema + anchor verification.

Written before lib/paperline/ledger.py exists; collection must fail at this
point (`No module named 'lib.paperline.ledger'`). The ledger module's central
contracts (D-008 + Threat Model §2 — recompute, never trust agent self-label):

  - `validate_ledger(d) -> None` raises on a missing or empty section among
    problem / method / key_results / limitations. Each entry needs non-empty
    `text` + `anchor`; key_results entries additionally MAY carry
    `{metric, value}` (optional, not enforced here).
  - `verify_anchors(ledger, fulltext) -> dict` with keys `ok` (bool) and
    `flagged` (list). Each anchor is recomputed as a verbatim substring of
    `fulltext` (normalize ONLY whitespace, not content). A missing anchor
    flags the entry with its locator (section + text + the bad anchor).
  - Self-contained: does NOT import `factcheck` (P3's faithfulness gate may,
    P2's anchor verifier does not — keeps the line-isolation firewall clean,
    see test_line_isolation.py).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.paperline.ledger import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- shared fixtures ----------

# A minimal-but-real fulltext used by the verify_anchors tests. Anchors are
# exact substrings of this body; whitespace-only normalization is allowed.
_SAMPLE_FULLTEXT = (
    "Title: Native Active Perception as Reasoning for Omni-Modal Understanding\n"
    "\n"
    "We propose OmniAgent, a unified agent that achieves 87.3% accuracy on the\n"
    "MMMU benchmark. Our method jointly reasons over vision, language, and audio.\n"
    "Experiments on three benchmarks show consistent gains over prior work.\n"
    "\n"
    "The authors acknowledge limited evaluation on out-of-distribution samples.\n"
)


def _minimal_ledger():
    """A complete, schema-valid ledger. Used by the OK-path tests.

    Each entry has non-empty `text` + `anchor`; key_results entry also carries
    `metric` + `value` (optional, illustrative). Anchors are exact substrings
    of `_SAMPLE_FULLTEXT`.
    """
    return {
        "problem": [
            {"text": "Multi-modal reasoning over vision, language, and audio.",
             "anchor": "jointly reasons over vision, language, and audio"},
        ],
        "method": [
            {"text": "A unified agent that interleaves perception and reasoning.",
             "anchor": "OmniAgent, a unified agent"},
        ],
        "key_results": [
            {"text": "87.3% accuracy on the MMMU benchmark.",
             "anchor": "87.3% accuracy on the",
             "metric": "MMMU accuracy",
             "value": "87.3%"},
        ],
        "limitations": [
            {"text": "Limited out-of-distribution evaluation.",
             "anchor": "limited evaluation on out-of-distribution samples"},
        ],
    }


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 4-impl will resolve this."""
    from lib.paperline import ledger  # noqa: F401

    assert hasattr(ledger, "validate_ledger")
    assert hasattr(ledger, "verify_anchors")


# ---------- validate_ledger: schema OK ----------

def test_valid_ledger_schema_ok():
    """A complete ledger (problem / method / key_results / limitations, each
    with at least one entry having non-empty `text` + `anchor`) validates
    without raising."""
    from lib.paperline.ledger import validate_ledger

    # Must NOT raise on a complete, well-formed ledger.
    validate_ledger(_minimal_ledger())


# ---------- validate_ledger: missing section rejected ----------

def test_missing_section_rejected():
    """Dropping any one of the four required sections causes validation to
    raise (fail-closed on the schema gate)."""
    from lib.paperline.ledger import validate_ledger

    ledger = _minimal_ledger()
    del ledger["limitations"]

    with pytest.raises(Exception):
        validate_ledger(ledger)


def test_empty_section_rejected():
    """A present-but-empty section (e.g. `problem: []`) is rejected the same
    way a missing section is — the schema requires each section to carry at
    least one entry."""
    from lib.paperline.ledger import validate_ledger

    ledger = _minimal_ledger()
    ledger["problem"] = []

    with pytest.raises(Exception):
        validate_ledger(ledger)


def test_entry_missing_anchor_rejected():
    """Each entry must carry a non-empty `anchor`. An entry with empty/missing
    `anchor` is rejected — anchor traceability is the whole point of the
    ledger."""
    from lib.paperline.ledger import validate_ledger

    ledger = _minimal_ledger()
    ledger["method"][0]["anchor"] = ""

    with pytest.raises(Exception):
        validate_ledger(ledger)


def test_entry_missing_text_rejected():
    """Each entry must carry non-empty `text`."""
    from lib.paperline.ledger import validate_ledger

    ledger = _minimal_ledger()
    ledger["problem"][0]["text"] = ""

    with pytest.raises(Exception):
        validate_ledger(ledger)


# ---------- verify_anchors: PASS on verbatim substrings ----------

def test_verify_anchors_pass():
    """When every entry's `anchor` is a verbatim substring of `fulltext`,
    `verify_anchors` reports `ok=True` and an empty `flagged` list. This is
    the recompute gate — never trust the agent's self-label."""
    from lib.paperline.ledger import verify_anchors

    ledger = _minimal_ledger()
    result = verify_anchors(ledger, _SAMPLE_FULLTEXT)

    assert isinstance(result, dict)
    assert result["ok"] is True, (
        f"expected ok=True, got flagged={result.get('flagged')!r}"
    )
    assert result["flagged"] == []


def test_verify_anchors_whitespace_normalized_pass():
    """An anchor that differs from the fulltext by whitespace alone (extra
    spaces, newlines collapsed) STILL matches — normalize whitespace, not
    content (per Task 4-impl contract)."""
    from lib.paperline.ledger import verify_anchors

    ledger = {
        "problem": [
            {"text": "x", "anchor": "jointly reasons over   vision,\nlanguage,  and audio"},
        ],
        "method": [{"text": "y", "anchor": "OmniAgent, a unified agent"}],
        "key_results": [{"text": "z", "anchor": "87.3% accuracy on the"}],
        "limitations": [{"text": "w", "anchor": "limited evaluation on out-of-distribution samples"}],
    }

    result = verify_anchors(ledger, _SAMPLE_FULLTEXT)
    assert result["ok"] is True
    assert result["flagged"] == []


# ---------- verify_anchors: FLAGS fabricated / paraphrased anchors ----------

def test_verify_anchors_flags_fabricated():
    """An anchor that is NOT a substring of `fulltext` (fabricated number or
    paraphrase) is flagged with its locator (section + text + the bad
    anchor). `ok` is False. This is the recompute discipline that catches a
    lying agent — never trust the ledger's self-label, only the substring
    match."""
    from lib.paperline.ledger import verify_anchors

    ledger = _minimal_ledger()
    # Replace the key_results anchor with a number that does NOT appear in
    # the fulltext — a classic fabrication (the agent invents a metric).
    ledger["key_results"][0]["anchor"] = "99.9% accuracy on the"

    result = verify_anchors(ledger, _SAMPLE_FULLTEXT)

    assert result["ok"] is False
    flagged = result["flagged"]
    assert isinstance(flagged, list) and len(flagged) == 1, (
        f"expected exactly one flagged entry, got {flagged!r}"
    )
    entry = flagged[0]

    # The locator includes the section, the offending text, and the anchor
    # that failed to match. Tests pin at least the section + anchor so a
    # silent regression (e.g. returning ok=False but no locator) is caught.
    assert entry.get("section") == "key_results"
    assert "99.9% accuracy on the" in (entry.get("anchor") or "")


def test_verify_anchors_flags_multiple():
    """Multiple fabricated anchors across multiple sections all get flagged;
    `ok` is False; each flag carries its section locator."""
    from lib.paperline.ledger import verify_anchors

    ledger = _minimal_ledger()
    ledger["problem"][0]["anchor"] = "fabricated problem anchor"
    ledger["limitations"][0]["anchor"] = "fabricated limitations anchor"

    result = verify_anchors(ledger, _SAMPLE_FULLTEXT)

    assert result["ok"] is False
    flagged = result["flagged"]
    assert len(flagged) == 2
    sections = {e.get("section") for e in flagged}
    assert sections == {"problem", "limitations"}
