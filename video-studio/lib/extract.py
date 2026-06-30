"""PDF text extraction + doc-mode extraction for video-studio.

`pdftotext -layout`, per page (PDF mode) OR markdown doc sections from a
snapshot authority-layer directory (doc mode, Phase 6).

Two design points that matter downstream:

- `-layout` (not default): the source data (配速/VDOT) lives in PDF tables;
  default pdftotext column-serializes them (rows destroyed). `-layout` keeps
  rows intact (`71.0  2:36.2  5:20.0/5:45.7`), which keeps a pace associated
  with its VDOT and keeps digits whole for the faithfulness gate.
- per-page map `{page_no: text}` (not one blob): provenance.json must map each
  objective assertion → the PDF page it grounds in (UX-004). The merged
  fulltext (the object the 忠实門 checks) is `join_fulltext(map)`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
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


# ---------------------------------------------------------------------------
# Doc mode — reads authority-layer markdown snapshots (Phase 6)
# ---------------------------------------------------------------------------


def extract_doc_sections(
    snapshot_dir: "Path | str",
    doc_refs: "list[str]",
) -> "dict[str, str]":
    """Read markdown doc sections from a snapshot directory.

    Returns ``{ref: text}`` for each ref in *doc_refs*.  Section anchors
    (``#section-name``) are stripped — only the base file is read.

    Raises :exc:`ExtractError` if any referenced doc is missing.
    """
    snap = Path(snapshot_dir)
    result: dict[str, str] = {}
    for ref in doc_refs:
        # Strip optional #anchor suffix.
        base_name = ref.split("#")[0]
        file_path = snap / base_name
        if not file_path.exists():
            raise ExtractError(
                f"Doc ref not found: {ref!r} "
                f"(looked for {file_path})"
            )
        result[ref] = file_path.read_text(encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# VDOT pace table renderer — decimal min/km → MM:SS (Phase 6, verifier M5)
# ---------------------------------------------------------------------------


def _secs_to_mmss(total_secs: float) -> str:
    """Convert a total-seconds value to ``M:SS`` (integer seconds, no decimal)."""
    s = int(total_secs)
    return f"{s // 60}:{s % 60:02d}"


def _min_to_mmss(decimal_min: float) -> str:
    """Convert a decimal-minutes value to ``M:SS``.

    Example: 3.67 → 220.2 total seconds → int(220.2)=220 → "3:40".
    """
    return _secs_to_mmss(decimal_min * 60)


def render_pace_lines(vdot_json: dict) -> str:
    """Render a ``vdot_pace_data.json`` dict as normalized pace-table text.

    Decimal min/km values are converted to ``M:SS`` (integer seconds, no
    fractional part) so the 忠实门 (verify_anchors) can find exact string
    matches when the digest-writer cites a pace like "3:40".

    Returns a multi-line string:
        VDOT Pace Table
        VDOT 60: E 4:16-4:51/km, M 3:52/km, T 3:40/km, I 3:25/km, R 1:16/400m
        …
    """
    lines = ["VDOT Pace Table"]
    for entry in vdot_json.get("data", []):
        vdot = entry.get("vdot")
        paces = entry.get("paces", {})
        pace_parts: list[str] = []

        easy_min = paces.get("easyMin")
        easy_max = paces.get("easyMax")
        if easy_min is not None and easy_max is not None:
            pace_parts.append(
                f"E {_min_to_mmss(easy_min)}-{_min_to_mmss(easy_max)}/km"
            )

        marathon = paces.get("marathon")
        if marathon is not None:
            pace_parts.append(f"M {_min_to_mmss(marathon)}/km")

        threshold = paces.get("threshold")
        if threshold is not None:
            pace_parts.append(f"T {_min_to_mmss(threshold)}/km")

        interval = paces.get("interval")
        if interval is not None:
            pace_parts.append(f"I {_min_to_mmss(interval)}/km")

        rep400 = paces.get("repetition400m")
        if rep400 is not None:
            pace_parts.append(f"R {_secs_to_mmss(rep400)}/400m")

        pace_str = ", ".join(pace_parts) if pace_parts else "(no paces)"
        lines.append(f"VDOT {vdot}: {pace_str}")

    return "\n".join(lines)
