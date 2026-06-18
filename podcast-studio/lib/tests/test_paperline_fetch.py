"""Tests for lib/paperline/fetch.py — full-text fetch (HTML primary → PDF fallback).

Written before lib/paperline/fetch.py exists; collection must fail at this
point (`No module named 'lib.paperline.fetch'`). The fetcher's central contract
(D-005 + Threat Model §1, §2):

  - `fetch_fulltext(arxiv_id, *, fetcher=_https_get, pdftotext=_run_pdftotext)`
    returns `{method: "html"|"pdf", text: str, source_url: str}`.
  - arxiv_id is validated against the strict regex
    ``^\\d{4}\\.\\d{4,5}(v\\d+)?$`` BEFORE any URL or subprocess invocation —
    invalid id raises `ValueError` (URL/path injection guard, Threat Model §1).
  - HTML-available detection = http 200 + `ltx_abstract` / `ltx_title_document`
    marker present in body. Else → PDF fallback.
  - PDF path runs `pdftotext` (injected runner) and returns its text.
  - Both unavailable → fail-closed raise.
  - All fetchers are injected; no live network in tests (offline).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.paperline.fetch import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# ---------- fixtures (real arXiv samples, trimmed) ----------

_FX_DIR = Path(__file__).resolve().parent / "fixtures"

_HTML_AVAILABLE = (_FX_DIR / "arxiv-html-available.html").read_bytes()
_HTML_UNAVAILABLE = (_FX_DIR / "arxiv-html-unavailable.html").read_bytes()
_PDFTOTEXT_TXT = (_FX_DIR / "arxiv-pdftotext.txt").read_text(encoding="utf-8")

# arxiv_id regex per Threat Model §1 + D-005 — strict, the same regex the
# fetcher must apply at its entry boundary.
_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")

# A real, valid arxiv_id used across the success-path tests.
_VALID_ID = "2606.19341v1"


# ---------- imports (FAIL-first: expect ModuleNotFoundError pre-impl) ----------

def test_module_imports():
    """The module must import cleanly; failing here is the test-FAIL-first
    contract — Task 3-impl will resolve this."""
    from lib.paperline import fetch  # noqa: F401

    assert hasattr(fetch, "fetch_fulltext")


# ---------- helpers ----------

class _FakeResp:
    """Minimal stand-in for an HTTP response: holds (status, body) and
    reports the URL it was fetched for via the closure below."""

    def __init__(self, status: int, body: bytes):
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body


def _make_status_fetcher(table: dict[str, tuple[int, bytes]]):
    """Build a fetcher that returns the configured (status, body) for a given
    URL, otherwise raises. Used by the HTML/PDF path tests."""

    def fetcher(url: str) -> _FakeResp:
        if url not in table:
            raise AssertionError(f"unexpected URL fetched: {url}")
        status, body = table[url]
        return _FakeResp(status, body)

    return fetcher


# ---------- HTML available → method=="html" ----------

def test_html_available_extracts():
    """When the HTML endpoint returns http 200 with `ltx_abstract` /
    `ltx_title_document` markers, the fetcher returns method=='html' with a
    non-empty extracted text (tag-stripped)."""
    from lib.paperline.fetch import fetch_fulltext

    html_url = f"https://arxiv.org/html/{_VALID_ID}"
    fetcher = _make_status_fetcher({html_url: (200, _HTML_AVAILABLE)})

    # PDF runner MUST NOT be called on the HTML-available path — but we still
    # inject a sentinel that would error loudly if invoked.
    pdf_called = {"n": 0}

    def fake_pdftotext(pdf_path):
        pdf_called["n"] += 1
        raise AssertionError("pdftotext must not be called when HTML is available")

    result = fetch_fulltext(_VALID_ID, fetcher=fetcher, pdftotext=fake_pdftotext)

    assert isinstance(result, dict)
    assert result["method"] == "html"
    assert isinstance(result["text"], str) and result["text"], (
        "text should be non-empty after tag-stripping"
    )
    # source_url points to the HTML endpoint that succeeded.
    assert result["source_url"] == html_url
    # pdftotext was NOT invoked.
    assert pdf_called["n"] == 0
    # The tag-stripped text preserves a recognizable fragment of the abstract
    # so we know we actually extracted content (not just returned the raw HTML).
    assert "OmniAgent" in result["text"] or "Omni-Modal" in result["text"]


# ---------- HTML unavailable → PDF fallback ----------

def test_html_unavailable_falls_back_to_pdf():
    """When the HTML endpoint returns http 404 (or its body lacks the ltx
    markers), the fetcher falls back to the PDF endpoint and runs the
    injected `pdftotext` runner; result method is 'pdf'."""
    from lib.paperline.fetch import fetch_fulltext

    html_url = f"https://arxiv.org/html/{_VALID_ID}"
    pdf_url = f"https://arxiv.org/pdf/{_VALID_ID}"
    pdf_bytes = b"%PDF-1.4\n%fake-pdf-bytes-for-test\n"

    fetcher = _make_status_fetcher({
        html_url: (404, _HTML_UNAVAILABLE),
        pdf_url: (200, pdf_bytes),
    })

    def fake_pdftotext(pdf_path) -> str:
        # Pretend `pdftotext` ran on the bytes; return the staged text.
        # The runner contract is (path) -> str; we don't read the path here.
        return _PDFTOTEXT_TXT

    result = fetch_fulltext(_VALID_ID, fetcher=fetcher, pdftotext=fake_pdftotext)

    assert result["method"] == "pdf"
    assert result["source_url"] == pdf_url
    # Text returned from the injected pdftotext runner, verbatim.
    assert result["text"] == _PDFTOTEXT_TXT
    assert "OmniAgent" in result["text"]


# ---------- invalid arxiv_id → ValueError, no fetch ----------

@pytest.mark.parametrize(
    "bad_id",
    [
        "../etc/passwd",            # path traversal
        "2606.19341v1; rm -rf /",   # argument injection
        "not-an-id",                # non-numeric
        # NOTE: a version-LESS id ("2606.19341") is VALID — fetch's regex is
        # `^\d{4}\.\d{4,5}(v\d+)?$` (version optional), aligned with discovery
        # and D-017. It is asserted valid in test_arxiv_id_regex_pins, not here.
        "<script>",                 # HTML/script injection
        "",                         # empty
    ],
)
def test_invalid_id_rejected(bad_id):
    """An invalid arxiv_id is rejected with `ValueError` BEFORE any URL is
    built or subprocess is invoked (Threat Model §1: id validation first)."""
    from lib.paperline.fetch import fetch_fulltext

    fetcher_calls: list[str] = []

    def must_not_be_called(url: str):
        fetcher_calls.append(url)
        raise AssertionError(
            f"fetcher must not be called for invalid id {bad_id!r} (got url={url!r})"
        )

    def must_not_run_pdftotext(pdf_path):
        raise AssertionError(
            f"pdftotext must not be called for invalid id {bad_id!r}"
        )

    with pytest.raises(ValueError):
        fetch_fulltext(
            bad_id,
            fetcher=must_not_be_called,
            pdftotext=must_not_run_pdftotext,
        )

    assert fetcher_calls == [], "no URL must be built for invalid id"


# ---------- both unavailable → fail-closed raise ----------

def test_both_unavailable_raises():
    """When both HTML (404) and PDF (404) endpoints fail, the fetcher raises
    (fail-closed). It must NOT proceed on the abstract or silently fall
    through to an empty string (Threat Model §2, D-005)."""
    from lib.paperline.fetch import fetch_fulltext

    html_url = f"https://arxiv.org/html/{_VALID_ID}"
    pdf_url = f"https://arxiv.org/pdf/{_VALID_ID}"
    fetcher = _make_status_fetcher({
        html_url: (404, _HTML_UNAVAILABLE),
        pdf_url: (404, b"Not Found"),
    })

    pdf_called = {"n": 0}

    def fake_pdftotext(pdf_path) -> str:
        pdf_called["n"] += 1
        return ""

    with pytest.raises(Exception):
        # The contract is "raise" — accept any non-OK exception class so the
        # impl can choose between RuntimeError, FetchError, etc. The key
        # invariant is fail-closed (no return value, no silent default).
        fetch_fulltext(_VALID_ID, fetcher=fetcher, pdftotext=fake_pdftotext)

    # pdftotext is NOT invoked when the PDF endpoint 404s: status is checked
    # before extraction, so a 404 error page is never fed to pdftotext (the
    # raised error names status=404, not a misleading "pdftotext failed").
    assert pdf_called["n"] == 0


# ---------- strict arxiv_id regex sanity ----------

def test_arxiv_id_regex_pins():
    """Pin the regex the fetcher must enforce (mirrors discovery's id
    boundary discipline, Threat Model §1)."""
    # Valid ids
    for good in ("2606.19341v1", "2606.19341v12", "1234.5678v9", "2606.00001"):
        assert _ARXIV_ID_RE.match(good), f"expected valid: {good!r}"

    # Invalid ids — anything outside the strict pattern must NOT match.
    for bad in (
        "../etc/passwd",
        "2606.19341v1; rm -rf /",
        "not-an-id",
        "<script>",
        "",
        "v2606.19341",
        "26.6.19341v1",
        "2606.19341v",
    ):
        assert not _ARXIV_ID_RE.match(bad), f"expected invalid: {bad!r}"