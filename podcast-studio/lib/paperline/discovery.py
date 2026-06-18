"""arXiv API discovery + Atom parse for the paper line.

Parses arXiv Atom feed responses into normalized candidate dicts and fetches
recent candidates by arXiv category. This module is part of the **paper line**
and must NOT import any opinion-line modules (stance / coveredground /
magnitude / bible) — see test_line_isolation.py for the firewall.

Central contracts (D-005, D-007):
  - `parse_atom(xml_bytes) -> list[dict]` — each candidate dict carries
    `arxiv_id`, `title`, `summary`, `published`, `primary_category`,
    `categories` (list[str]), `pdf_url`. arxiv_id is parsed from the
    `<id>.../abs/{id}</id>` URL and validated against the strict regex
    ``^\\d{4}\\.\\d{4,5}(v\\d+)?$``. Entries missing required fields are
    SKIPPED, never defaulted (fail-closed on the input boundary, D-005 +
    Threat Model §1).
  - `fetch_candidates(categories, *, max_results=60, fetcher=_https_get) ->
    list[dict]` — builds the query URL
    `https://export.arxiv.org/api/query?search_query=cat:...&sortBy=submittedDate&sortOrder=descending&max_results=...`,
    calls the injected (or default `_https_get`) fetcher, parses via
    `parse_atom`. Default fetcher uses `urllib.request` over HTTPS with a
    timeout, closed in `finally`.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Callable, Iterable

# ---------- constants ----------

# arXiv Atom feed base (HTTPS only — HTTP returns empty reply per D-017).
_API_BASE = "https://export.arxiv.org/api/query"

# Strict arxiv_id regex per Threat Model §1 + D-005. Validated BEFORE any URL
# build or subprocess invocation.
_ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")

# Default network timeout for the default `_https_get` fetcher.
_DEFAULT_TIMEOUT = 30  # seconds

# Atom / arXiv XML namespaces.
_NS_ATOM = "http://www.w3.org/2005/Atom"
_NS_ARXIV = "http://arxiv.org/schemas/atom"


# ---------- default fetcher ----------

def _https_get(url: str, *, timeout: int = _DEFAULT_TIMEOUT) -> bytes:
    """Default HTTPS fetcher: request-scoped urllib GET, closed in `finally`.

    Uses HTTPS by construction (the URL is built with `https://export.arxiv.org`
    — D-017: HTTP/80 returns empty reply). The returned bytes are the raw
    response body; callers parse them via `parse_atom`.
    """
    resp = urllib.request.urlopen(url, timeout=timeout)
    try:
        return resp.read()
    finally:
        resp.close()


# ---------- arxiv_id extraction ----------

def _extract_arxiv_id(id_url: str | None) -> str | None:
    """Extract the bare arxiv_id (e.g. `2606.19341v1`) from an `<id>` URL
    like `http://arxiv.org/abs/2606.19341v1`. Returns None when missing or
    when the suffix fails the strict regex.
    """
    if not id_url:
        return None
    # The id URL ends with /abs/{arxiv_id}; split on '/abs/' and take the tail.
    tail = id_url.rsplit("/abs/", 1)[-1].strip()
    if not tail:
        return None
    if not _ARXIV_ID_RE.match(tail):
        return None
    return tail


# ---------- parse_atom ----------

def parse_atom(xml_bytes: bytes) -> list[dict]:
    """Parse an arXiv Atom feed into a list of normalized candidate dicts.

    Each candidate dict has 7 keys: `arxiv_id`, `title`, `summary`,
    `published`, `primary_category`, `categories` (list[str]), `pdf_url`.

    Entries missing any required field (id, title, summary, primary_category,
    pdf_url, published) are SKIPPED, never defaulted. This is the fail-closed
    discipline on the discovery→candidate boundary (D-005).
    """
    if not xml_bytes:
        return []

    root = ET.fromstring(xml_bytes)
    candidates: list[dict] = []

    for entry in root.findall(f"{{{_NS_ATOM}}}entry"):
        # --- arxiv_id (parsed from <id>.../abs/{id}</id>; strict regex) ---
        id_el = entry.find(f"{{{_NS_ATOM}}}id")
        arxiv_id = _extract_arxiv_id(id_el.text if id_el is not None else None)
        if arxiv_id is None:
            # Threat Model §1: missing/invalid id → skip the entry.
            continue

        # --- title ---
        title_el = entry.find(f"{{{_NS_ATOM}}}title")
        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            continue

        # --- summary (abstract) ---
        summary_el = entry.find(f"{{{_NS_ATOM}}}summary")
        summary = (summary_el.text or "").strip() if summary_el is not None else ""
        if not summary:
            continue

        # --- published ---
        published_el = entry.find(f"{{{_NS_ATOM}}}published")
        published = (published_el.text or "").strip() if published_el is not None else ""
        if not published:
            continue

        # --- primary_category ---
        primary_el = entry.find(f"{{{_NS_ARXIV}}}primary_category")
        primary_category = (
            primary_el.get("term", "").strip()
            if primary_el is not None and primary_el.get("term")
            else ""
        )
        if not primary_category:
            continue

        # --- categories (list of <category term="..."/>) ---
        categories: list[str] = []
        for cat_el in entry.findall(f"{{{_NS_ATOM}}}category"):
            term = (cat_el.get("term") or "").strip()
            if term:
                categories.append(term)
        if not categories:
            continue

        # --- pdf_url: <link rel="related" type="application/pdf"/> ---
        pdf_url = ""
        for link_el in entry.findall(f"{{{_NS_ATOM}}}link"):
            rel = link_el.get("rel") or ""
            link_type = link_el.get("type") or ""
            if rel == "related" and link_type == "application/pdf":
                href = (link_el.get("href") or "").strip()
                if href:
                    pdf_url = href
                    break
        if not pdf_url:
            continue

        candidates.append(
            {
                "arxiv_id": arxiv_id,
                "title": title,
                "summary": summary,
                "published": published,
                "primary_category": primary_category,
                "categories": categories,
                "pdf_url": pdf_url,
            }
        )

    return candidates


# ---------- fetch_candidates ----------

def _build_query_url(categories: Iterable[str], max_results: int) -> str:
    """Build the arXiv API query URL for the given categories.

    Query string: `search_query=cat:{c}` (arXiv API takes a single primary
    category per call; multiple categories require multiple calls). For v1
    we take the first category; curating across categories is the curator
    persona's job (Task 5) once we have candidates in hand.
    """
    cats = [c for c in categories if c]
    if not cats:
        raise ValueError("at least one arXiv category is required")
    if max_results <= 0:
        raise ValueError("max_results must be a positive integer")

    # Use the first category; the curator persona decides whether the
    # resulting candidate is worth picking (D-007: selection is the
    # curator's, not here).
    search_query = f"cat:{cats[0]}"

    params = {
        "search_query": search_query,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
        "max_results": str(max_results),
    }
    # Preserve the literal `:` in `cat:cs.CL` — arXiv API treats `cat:` as a
    # query-syntax delimiter, not a URL-fragment separator. Using `safe=":,"`
    # also keeps any future list joins (e.g. multi-cat) readable.
    return f"{_API_BASE}?{urllib.parse.urlencode(params, safe=':,')}"


def fetch_candidates(
    categories: Iterable[str],
    *,
    max_results: int = 60,
    fetcher: Callable[[str], bytes] | None = None,
) -> list[dict]:
    """Fetch recent arXiv candidates for the given categories and parse them.

    Args:
        categories: Iterable of arXiv category strings (e.g. `["cs.CL"]`).
        max_results: Maximum candidates to retrieve (default 60).
        fetcher: Optional HTTP fetcher `(url) -> bytes`. Injected for offline
            tests; defaults to `_https_get` (urllib over HTTPS, timeout 30s,
            closed in `finally`).

    Returns:
        A list of candidate dicts (see `parse_atom` for the field shape).
        Fail-closed on empty input: raises `ValueError` if no category is
        provided or `max_results <= 0`.
    """
    url = _build_query_url(categories, max_results)
    active_fetcher = fetcher if fetcher is not None else _https_get
    xml_bytes = active_fetcher(url)
    return parse_atom(xml_bytes)