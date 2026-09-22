#!/usr/bin/env python3
"""Build the running-class source corpus: 4 PDFs → per-chapter markdown.

The pipeline's doc mode (lib.extract.extract_doc_sections) hands the
digest-writer whatever markdown its episode's `doc_refs` point at, and the
忠实门 (verify_anchors) grounds every objective number against that same text.
So the corpus IS the ceiling on what a script can say: the previous corpus was
a 6.5 KB engineering snapshot (zone table + code line numbers), which is why
scripts came out as table recitals — the books' stories, people and verbatim
quotes were never in the writer's hands.

This materialises the real books as doc-mode markdown, one file per chapter /
module, with `[p.N]` markers so provenance can still cite a page.

Extraction mode is per-PDF and deliberate:
  - Daniels  → -layout : the VDOT / training tables are the data. Default mode
                         column-serialises them and a pace loses its VDOT row.
  - UESCA    → -raw    : this PDF's kerning makes -layout emit broken words
                         ("certif ication", "runni ng"), which would poison the
                         faithfulness gate's word containment. -raw is clean.
  - the two small PDFs → -layout : headings survive, no kerning damage.

Output lands in running-class/source/ — which is gitignored, so the books'
copyrighted text never enters this published marketplace repo.

Usage:
    python3 scripts/build_source_corpus.py            # build
    python3 scripts/build_source_corpus.py --check    # verify an existing build
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = PLUGIN_ROOT.parent
OUT_ROOT = REPO_ROOT / "running-class" / "source"
PDF_DIR = Path.home() / "Downloads" / "UESCA"

# The old snapshot still owns the authoritative pace/zone JSON the pipeline
# appends to every doc-mode fulltext (lib/wiring step_extract). Carry it over.
OLD_SNAPSHOT = PLUGIN_ROOT / "syllabus" / "source" / "daniels-uesca"
CARRY_JSON = ("vdot_pace_data.json", "zone_boundary_data.json")

DANIELS = "Daniels' Running Formula Human Kinetics (2021) reduced.pdf"
UESCA = "UESCA-Running-Coach-Certification-Full-Course-PDF-V2_3-16-26.pdf"
PROGRAM = "Running-Program-Design-Slide-Deck-PDF.pdf"
OVERTRAIN = "How-to-Avoid-Overtraining.pdf"

# Titles come from each book's own table of contents. Deriving them from the
# chapter's opening page instead picks up the epigraph that Daniels prints under
# every chapter head ("Chapter 1 / Essentials of Running Success / Everyone
# should take advantage of…"), and UESCA's module heads line-wrap. doc_refs cite
# these filenames, so they have to read cleanly.
DANIELS_TITLES = {
    1: "Essentials of Running Success",
    2: "Training Principles and Tips",
    3: "Physiological and Personal Training Profiles",
    4: "Types of Training and Intensities",
    5: "VDOT System of Training",
    6: "Environment- and Altitude-Specific Training",
    7: "Treadmill Training",
    8: "Fitness Training",
    9: "Training Breaks and Supplemental Training",
    10: "Season-Tailored Training",
    11: "800 Meters",
    12: "1500 Meters to 2 Miles",
    13: "5K and 10K",
    14: "Cross Country",
    15: "15K to 30K",
    16: "Marathon",
    17: "Ultradistance",
    18: "Triathlon",
}
UESCA_TITLES = {
    1: "Run Coaching 101",
    2: "Skeletal System",
    3: "Muscular System",
    4: "Energy and Cardiopulmonary Systems",
    5: "Environmental Physiology",
    6: "Performance Assessments and Metrics",
    7: "Running Mechanics and Drills",
    8: "Injury and Illness",
    9: "Resistance Training",
    11: "Running Recovery",
    12: "Athlete Intake",
    13: "Goal Setting",
    14: "Female Physiology and Considerations",
    15: "Periodization and Program Design",
    16: "Pacing",
    17: "Mental Training",
    18: "Sports Nutrition",
    19: "Safety",
    20: "Running Shoes and Apparel",
    21: "Race Preparation and Racing",
    22: "Keeping it Legal",
}


def pdf_pages(pdf: Path, mode: str) -> list[str]:
    """Extract the whole PDF once and split on the form feed → page list."""
    argv = ["pdftotext", f"-{mode}", str(pdf), "-"]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise SystemExit(f"pdftotext failed on {pdf.name}: {proc.stderr[:200]}")
    return proc.stdout.split("\f")


def slugify(title: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", title.strip().lower())
    return s.strip("-")[:48]


def write_section(
    out: Path, pages: list[str], first: int, last: int, header: str
) -> int:
    """Write pages[first..last] (1-based, inclusive) as one markdown file.

    Each page's text is prefixed with a `[p.N]` marker so a downstream
    provenance step can still map an assertion back to its printed page.
    Returns the character count written.
    """
    body = [f"# {header}\n"]
    for n in range(first, last + 1):
        text = pages[n - 1].strip()
        if not text:
            continue
        body.append(f"\n[p.{n}]\n{text}")
    blob = "\n".join(body)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(blob, encoding="utf-8")
    return len(blob)


def find_starts(pages: list[str], pattern: str, skip_first_page: bool) -> dict[int, tuple[int, str]]:
    """Map ordinal → (start_page, title) by matching `pattern` in a page's head.

    Only the FIRST page matching a given ordinal counts (later cross-references
    in body text must not re-open a chapter). `skip_first_page` drops the PDF's
    own table of contents, which lists every ordinal on one page.
    """
    pat = re.compile(pattern, re.M)
    found: dict[int, tuple[int, str]] = {}
    for i, page in enumerate(pages, start=1):
        if skip_first_page and i == 1:
            continue
        nonempty = [ln for ln in page.splitlines() if ln.strip()]
        head = "\n".join(nonempty[:6])
        m = pat.search(head)
        if not m:
            continue
        ordinal = int(m.group(1))
        if ordinal in found:
            continue
        # Title: the ordinal's own captured tail, else the following heading lines.
        tail = (m.group(2) or "").strip() if m.lastindex and m.lastindex >= 2 else ""
        if not tail:
            after = [ln.strip() for ln in nonempty if ln.strip()]
            idx = next((j for j, ln in enumerate(after) if pat.search(ln)), 0)
            tail = " ".join(after[idx + 1: idx + 3])
        found[ordinal] = (i, tail)
    return found


def build_book(
    pdf_name: str, mode: str, pattern: str, subdir: str, kind: str,
    skip_toc: bool, titles: dict[int, str],
) -> list[tuple[str, int]]:
    pdf = PDF_DIR / pdf_name
    if not pdf.exists():
        raise SystemExit(f"missing PDF: {pdf}")
    pages = pdf_pages(pdf, mode)
    starts = find_starts(pages, pattern, skip_toc)
    if not starts:
        raise SystemExit(f"no {kind} boundaries found in {pdf_name}")

    missing = set(starts) - set(titles)
    if missing:
        raise SystemExit(
            f"{pdf_name}: no title for {kind} {sorted(missing)} — the book's "
            f"structure changed; update the title map."
        )

    ordinals = sorted(starts)
    written: list[tuple[str, int]] = []
    for pos, ordinal in enumerate(ordinals):
        start_page, _detected = starts[ordinal]
        # A chapter runs until the next chapter starts, or the last page.
        end_page = (
            starts[ordinals[pos + 1]][0] - 1
            if pos + 1 < len(ordinals)
            else len(pages)
        )
        title_clean = titles[ordinal]
        fname = f"{kind[:3].lower()}{ordinal:02d}-{slugify(title_clean)}.md"
        path = OUT_ROOT / subdir / fname
        size = write_section(
            path, pages, start_page, end_page,
            f"{kind} {ordinal} · {title_clean}  (pp.{start_page}-{end_page})",
        )
        written.append((f"{subdir}/{fname}", size))
    return written


def build_flat(pdf_name: str, mode: str, subdir: str, fname: str, header: str) -> tuple[str, int]:
    pdf = PDF_DIR / pdf_name
    if not pdf.exists():
        raise SystemExit(f"missing PDF: {pdf}")
    pages = pdf_pages(pdf, mode)
    path = OUT_ROOT / subdir / fname
    size = write_section(path, pages, 1, len(pages), f"{header}  (pp.1-{len(pages)})")
    return f"{subdir}/{fname}", size


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="verify an existing build")
    args = ap.parse_args()

    if args.check:
        return check()

    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)
    OUT_ROOT.mkdir(parents=True)

    written: list[tuple[str, int]] = []
    print("building corpus →", OUT_ROOT)

    written += build_book(
        DANIELS, "layout", r"^\s*Chapter\s+(\d{1,2})\s*$", "daniels", "Chapter",
        True, DANIELS_TITLES,
    )
    written += build_book(
        UESCA, "raw", r"^\s*Module\s+(\d{1,2})\s*[:：]\s*(.+?)\s*$", "uesca", "Module",
        True, UESCA_TITLES,
    )
    written.append(build_flat(
        PROGRAM, "layout", "coaching", "program-design.md",
        "UESCA Running Coach · Program Design (slide deck)",
    ))
    written.append(build_flat(
        OVERTRAIN, "layout", "coaching", "overtraining.md",
        "UESCA · How to Avoid Overtraining",
    ))

    # Carry the authoritative pace/zone JSON the pipeline appends to fulltext.
    for name in CARRY_JSON:
        src = OLD_SNAPSHOT / name
        if src.exists():
            shutil.copy2(src, OUT_ROOT / name)
            print(f"  carried {name}")
        else:
            print(f"  WARNING: {name} not found in {OLD_SNAPSHOT}", file=sys.stderr)

    total = sum(s for _, s in written)
    for ref, size in written:
        print(f"  {ref:56} {size:>8,} chars")
    print(f"\n{len(written)} docs, {total:,} chars total")
    return 0


def check() -> int:
    """Verify the built corpus: every doc non-empty, page markers present, and
    the kerning damage that would poison the faithfulness gate is absent."""
    if not OUT_ROOT.exists():
        print(f"FAIL: corpus not built ({OUT_ROOT})", file=sys.stderr)
        return 1
    docs = sorted(OUT_ROOT.rglob("*.md"))
    if not docs:
        print("FAIL: no markdown docs in corpus", file=sys.stderr)
        return 1

    # A word split by a stray space ("certif ication") is the signature of the
    # kerning damage; probe with words that must appear intact in a clean build.
    broken = re.compile(r"\b(certif ication|runni ng|trai ning|thres hold)\b", re.I)
    bad: list[str] = []
    for d in docs:
        text = d.read_text(encoding="utf-8")
        if len(text) < 200:
            bad.append(f"{d.name}: suspiciously small ({len(text)} chars)")
        if "[p." not in text:
            bad.append(f"{d.name}: no [p.N] page markers")
        if broken.search(text):
            bad.append(f"{d.name}: kerning-broken words present")

    for name in CARRY_JSON:
        p = OUT_ROOT / name
        if not p.exists():
            bad.append(f"{name}: missing (pace grounding would break)")
        else:
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                bad.append(f"{name}: invalid JSON ({exc})")

    total = sum(len(d.read_text(encoding="utf-8")) for d in docs)
    print(f"corpus: {len(docs)} docs, {total:,} chars")
    if bad:
        print("\nFAIL:", file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
        return 1
    print("OK: all docs non-empty, page-marked, no kerning damage; pace JSON valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
