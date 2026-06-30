"""PDF text extraction for video-studio — `pdftotext -layout`, per page.

The `extract` pipeline step's helper. Two design points that matter downstream:

- `-layout` (not default): the source data (配速/VDOT) lives in PDF tables;
  default pdftotext column-serializes them (rows destroyed). `-layout` keeps
  rows intact (`71.0  2:36.2  5:20.0/5:45.7`), which keeps a pace associated
  with its VDOT and keeps digits whole for the faithfulness gate.
- per-page map `{page_no: text}` (not one blob): provenance.json must map each
  objective assertion → the PDF page it grounds in (UX-004). The merged
  fulltext (the object the 忠实门 checks) is `join_fulltext(map)`.
"""
from __future__ import annotations

import subprocess
from typing import Any, Callable, Iterable


class ExtractError(Exception):
    """pdftotext failed for a page (non-zero exit / binary missing)."""


def extract_pages(
    pdf_path: str,
    pages: Iterable[int],
    *,
    runner: Callable[..., Any] = subprocess.run,
    pdftotext_bin: str = "pdftotext",
    timeout: int = 60,
) -> dict[int, str]:
    """Return `{page_no: text}` extracting each page with `pdftotext -layout`.

    One subprocess per page so the page boundary is preserved (UX-004). Each
    call is `pdftotext -layout -f P -l P <pdf> -`. Raises ExtractError on a
    non-zero exit or a missing binary.
    """
    out: dict[int, str] = {}
    for page in pages:
        argv = [
            pdftotext_bin, "-layout",
            "-f", str(page), "-l", str(page),
            pdf_path, "-",
        ]
        try:
            proc = runner(argv, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as exc:
            raise ExtractError(
                f"pdftotext not found ({pdftotext_bin!r}): {exc}. "
                f"Install poppler (brew install poppler / apt install poppler-utils)."
            ) from exc
        if getattr(proc, "returncode", 0) != 0:
            raise ExtractError(
                f"pdftotext -layout failed for page {page} "
                f"(rc={getattr(proc, 'returncode', '?')}): "
                f"{(getattr(proc, 'stderr', '') or '').strip()[:200]}"
            )
        out[page] = getattr(proc, "stdout", "") or ""
    return out


def join_fulltext(page_text_map: dict[int, str]) -> str:
    """Merge a per-page map into one fulltext in ascending page order.

    This is the SINGLE source text object the 忠实门 (verify_anchors) checks
    against and the same text the digest-writer is handed — so a faithful copy
    grounds by construction (no false halts from divergent text)."""
    return "\n".join(page_text_map[p] for p in sorted(page_text_map))
