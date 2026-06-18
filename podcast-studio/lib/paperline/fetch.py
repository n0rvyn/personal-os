"""Full-text fetch (HTML primary → PDF fallback) for the paper line.

Given an arxiv_id, returns the paper's full text (HTML preferred, PDF via
pdftotext as fallback). This module is part of the **paper line** and must
NOT import any opinion-line modules (stance / coveredground / magnitude /
bible) — see test_line_isolation.py for the firewall.

Central contracts (D-005, Threat Model §1 + §2):
  - `fetch_fulltext(arxiv_id, *, fetcher=_https_get, pdftotext=_run_pdftotext)`
    returns `{method: "html"|"pdf", text: str, source_url: str}`.
  - arxiv_id is validated against the strict regex
    ``^\\d{4}\\.\\d{4,5}(v\\d+)?$`` BEFORE any URL or subprocess invocation
    (URL/path + argument injection guard, Threat Model §1). Invalid id
    raises `ValueError` and neither fetcher nor pdftotext is invoked.
  - HTML-available detection = http 200 + the `ltx_abstract` /
    `ltx_title_document` markers present in the body. Else → PDF fallback.
  - PDF path writes the bytes to a tempfile, runs `pdftotext` (argv list,
    `try/finally` unlink), returns its text.
  - Both unavailable (HTML 404 / no markers AND PDF 404 / no body) →
    fail-closed raise (never proceed on the abstract, D-005).
  - All fetchers and the pdftotext runner are injected; offline-friendly.
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Callable


# ---------- constants ----------

# Strict arxiv_id regex (Threat Model §1): require both a 4-digit year.month
# stem AND an explicit `v\d+` version suffix. The version suffix is mandatory:
# bare `2606.19341` is ambiguous (could be v1, v2, …) and we never want to
# guess at fetch URLs or argv. The D-017 regex `^\d{4}\.\d{4,5}(v\d+)?$` is
# permissive on paper; the FETCHER is stricter — invalid ids raise
# `ValueError` BEFORE any URL build or subprocess invocation.
_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")

# HTML-availability markers — LaTeXML-generated arXiv HTML pages set these
# class names on the abstract block (`ltx_abstract`) and the document title
# (`ltx_title_document`). A "HTML is not available" 404 page (e.g. for
# TeX-only papers) does NOT contain them, so we use presence of either as
# the success signal.
_HTML_MARKERS = ("ltx_abstract", "ltx_title_document")

# Default network timeout for the default `_https_get` fetcher.
_DEFAULT_TIMEOUT = 30  # seconds

# Block-level tags that should inject a paragraph break when we strip tags.
# Other tags are stripped silently; we accumulate text content.
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "header", "footer", "main", "aside",
    "nav", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "br", "hr", "table", "tr", "figure", "figcaption",
})


# ---------- response shape ----------

class _Response:
    """Minimal response protocol: `.status` (int) + `.read() -> bytes`.
    The default `_https_get` returns one of these; tests inject a fake.
    """


# ---------- default fetcher (urllib, HTTPS) ----------

def _https_get(url: str, *, timeout: int = _DEFAULT_TIMEOUT) -> _Response:
    """Default HTTPS fetcher: request-scoped urllib GET.

    Returns an `http.client.HTTPResponse` (or compatible) exposing `.status`
    (int) and `.read()` (bytes). HTTPS by construction — D-017: HTTP/80
    returns empty reply, so the URLs we build are always `https://...`.

    Connection lifecycle is owned by the caller via `_fetch_bytes`, which
    closes the response in `finally` (request-scoped, Threat Model §3).
    """
    return urllib.request.urlopen(url, timeout=timeout)


def _fetch_bytes(fetcher: Callable[[str], _Response], url: str) -> tuple[int, bytes]:
    """Call the injected `fetcher(url)` and return `(status, body)`. Closes
    the response in `finally` so callers can't leak sockets on error paths.
    Any object exposing `.status` (int) + `.read()` (bytes) is accepted —
    matches `urllib`'s HTTPResponse and the test `_FakeResp`.
    """
    resp = fetcher(url)
    try:
        status = int(getattr(resp, "status", 200))
        body = resp.read()
        if not isinstance(body, (bytes, bytearray)):
            # Defensive: tests inject bytes; if a caller hands us str, encode.
            body = str(body).encode("utf-8")
        return status, bytes(body)
    finally:
        close = getattr(resp, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass


# ---------- arxiv_id validation ----------

def _validate_arxiv_id(arxiv_id: object) -> str:
    """Strictly validate an arxiv_id. Returns the id string on success;
    raises `ValueError` (no fetch, no subprocess) on any other input.

    Threat Model §1: this gate runs BEFORE any URL build or subprocess
    invocation. Path/argument injection at the id boundary is impossible
    because the regex matches ONLY digits, dots, and `v`.
    """
    if not isinstance(arxiv_id, str) or not _ARXIV_ID_RE.match(arxiv_id):
        raise ValueError(
            f"invalid arxiv_id {arxiv_id!r}: must match {_ARXIV_ID_RE.pattern}"
        )
    return arxiv_id


# ---------- HTML → plain text ----------

class _HTMLTextExtractor(HTMLParser):
    """Strip HTML to plain text, preserving paragraph breaks for block-level
    elements. We use stdlib `html.parser` (no new dep, D-017) and keep things
    minimal: accumulate text content; emit a newline before/after block tags
    so paragraph order survives the strip.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if data:
            self._chunks.append(data)

    def text(self) -> str:
        # Collapse runs of whitespace WITHIN a line (preserves paragraph
        # breaks). Trim leading/trailing whitespace on the whole doc.
        raw = "".join(self._chunks)
        lines = [ln.strip() for ln in raw.splitlines()]
        # Drop empty lines that are pure-whitespace; keep one between blocks.
        out: list[str] = []
        blank = False
        for ln in lines:
            if ln:
                out.append(ln)
                blank = False
            else:
                if not blank and out:
                    out.append("")
                blank = True
        return "\n".join(out).strip()


def _html_to_text(html_bytes: bytes) -> str:
    """Strip tags from an HTML byte payload; return plain text with paragraph
    breaks preserved. UTF-8 with replacement on decode failure (LaTeXML
    output is always UTF-8 in our samples; defensive elsewhere).
    """
    try:
        html_str = html_bytes.decode("utf-8")
    except UnicodeDecodeError:
        html_str = html_bytes.decode("utf-8", errors="replace")
    parser = _HTMLTextExtractor()
    parser.feed(html_str)
    parser.close()
    return parser.text()


# ---------- default pdftotext runner ----------

def _run_pdftotext(pdf_path: str) -> str:
    """Default pdftotext runner: argv-list subprocess, capture stdout.
    `try/finally` is the caller's responsibility (the tempfile lifetime
    is owned by `fetch_fulltext`); this runner just runs pdftotext.
    """
    proc = subprocess.run(
        ["pdftotext", pdf_path, "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"pdftotext failed (rc={proc.returncode}): "
            f"{proc.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return proc.stdout.decode("utf-8", errors="replace")


# ---------- pdftotext-on-tempfile helper ----------

def _pdf_bytes_to_text(
    pdf_bytes: bytes,
    pdftotext_runner: Callable[[str], str],
) -> str:
    """Write pdf bytes to a tempfile, run the injected pdftotext runner on it,
    unlink in `finally`. The tempfile path is a `WE control` path, never the
    raw arxiv_id (Threat Model §1: pdftotext argv is a tempfile path).
    """
    fd, path = tempfile.mkstemp(suffix=".pdf", prefix="arxiv-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(pdf_bytes)
        return pdftotext_runner(path)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------- public API ----------

def fetch_fulltext(
    arxiv_id: str,
    *,
    fetcher: Callable[[str], _Response] | None = None,
    pdftotext: Callable[[str], str] | None = None,
) -> dict:
    """Fetch the full text of an arXiv paper (HTML primary → PDF fallback).

    Args:
        arxiv_id: A validated arXiv id matching `^\\d{4}\\.\\d{4,5}(v\\d+)?$`
            (strict — a `v` suffix is required to disambiguate versions).
        fetcher: Optional HTTP fetcher `(url) -> response` where the response
            exposes `.status` (int) and `.read() -> bytes`. Defaults to
            `_https_get` (urllib over HTTPS, timeout 30s). Injected in tests
            to stay offline.
        pdftotext: Optional runner `(pdf_path) -> str`. Defaults to
            `_run_pdftotext` (argv-list subprocess: `pdftotext <path> -`).
            Injected in tests.

    Returns:
        A dict with three keys:
          - `method`: `"html"` (preferred) or `"pdf"` (fallback).
          - `text`: the extracted plain text (non-empty).
          - `source_url`: the URL that succeeded.

    Raises:
        ValueError: `arxiv_id` does not match the strict regex (no fetch
            is attempted).
        RuntimeError: both the HTML and PDF endpoints failed (fetch-closed
            per D-005 — never proceed on the abstract alone).
    """
    # --- 1. Strict id validation (Threat Model §1) — runs FIRST. ---
    valid_id = _validate_arxiv_id(arxiv_id)

    active_fetcher = fetcher if fetcher is not None else _https_get
    active_pdftotext = pdftotext if pdftotext is not None else _run_pdftotext

    # --- 2. HTML primary. ---
    html_url = f"https://arxiv.org/html/{valid_id}"
    html_status, html_body = _fetch_bytes(active_fetcher, html_url)
    if html_status == 200 and any(m in html_body.decode("utf-8", errors="replace") for m in _HTML_MARKERS):
        text = _html_to_text(html_body)
        if text:
            return {"method": "html", "text": text, "source_url": html_url}

    # --- 3. PDF fallback. ---
    # Only invoke pdftotext when the PDF fetch actually succeeded (status 200):
    # feeding a 404 error page to pdftotext wastes a subprocess and masks the
    # real cause (status=404) behind a misleading "pdftotext failed". The
    # pdftotext argv is the tempfile path we control, never the raw arxiv_id
    # (Threat Model §1, §3).
    pdf_url = f"https://arxiv.org/pdf/{valid_id}"
    pdf_status, pdf_body = _fetch_bytes(active_fetcher, pdf_url)
    pdf_text = ""
    if pdf_status == 200:
        pdf_text = _pdf_bytes_to_text(pdf_body, active_pdftotext)
        if pdf_text:
            return {"method": "pdf", "text": pdf_text, "source_url": pdf_url}

    # --- 4. Both unavailable → fail-closed (D-005, Threat Model §2). ---
    raise RuntimeError(
        f"failed to fetch full text for {valid_id!r}: "
        f"html_status={html_status}, pdf_status={pdf_status}, "
        f"pdf_text_len={len(pdf_text) if pdf_text else 0}"
    )
