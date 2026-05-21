#!/usr/bin/env python3
"""Offline ingestion of a GetNote (Get笔记) HTML export archive into the PKOS vault.

The official getnote OpenAPI is rate-limited, so a full historical backfill is done
by exporting the whole archive from the getnote app and ingesting it offline.

Export layout:
  <export_dir>/<archive>/notes/<hash>.html      one file per note
  <export_dir>/<archive>/notes/files/           shared css/js + image blobs

Each note's visible HTML carries everything needed:
  - <title> and/or <h1>                title (often empty for quick captures)
  - <p>创建于：YYYY-MM-DD HH:MM:SS</p>  created timestamp
  - <span class="tag">...</span>       tags (one per span; may include book names)
  - <div class="attachment">原文：<a>  source link  (a "link" note)
  - <blockquote>...</blockquote>       摘抄 / 原文 excerpt
  - body <p> block                     summary / reflection / knowledge text
  - <div id="jsonData" data-json="..."> encrypted, never read by the export's own JS
                                        — ignored here.

Routing (HTML structure → vault dir), faithful to the PKOS directory contract
(99-System/10-Directory-Contract.md):
  - <blockquote> present, OR a 原文 link → 50-References/   (external-derived material;
                                            the export lost the API note_type, so a
                                            quoted/linked note is treated as reference,
                                            never a stance source)
  - pure-plain note WITH an <h1> title   → 10-Knowledge/    (deliberate knowledge entry)
  - pure-plain note, no title            → 20-Ideas/观点心得/ (quick reflection capture)
  - pure-image note with no text         → skipped           (text-only policy)

Dedup: every written note carries a `getnote_id` frontmatter field; a state file
(<vault>/.state/getnote-import-state.yaml) records imported ids. Re-runs skip notes
already imported (state) or already present in the vault (getnote_id scan).

Usage:
  import_getnote_export.py --export-dir DIR [--vault DIR] [--dry-run] [--limit N]
"""
import argparse
import html
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

# --------------------------------------------------------------------------
# HTML → Markdown
# --------------------------------------------------------------------------

_CODE_BLOCK_RE = re.compile(r"<pre>\s*<code[^>]*>(.*?)</code>\s*</pre>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _unescape_full(s):
    """Unescape HTML entities repeatedly — the getnote export double-escapes some
    content (e.g. '&amp;amp;' renders a literal '&')."""
    for _ in range(3):
        new = html.unescape(s)
        if new == s:
            return s
        s = new
    return s


def _strip_tags(fragment):
    """Remove any remaining HTML tags, then fully unescape entities."""
    return _unescape_full(_TAG_RE.sub("", fragment))


def html_to_md(fragment):
    """Convert a getnote body/blockquote HTML fragment back to readable Markdown.

    getnote renders the user's Markdown to HTML for display; this reverses the common
    cases. Perfect fidelity is not required — readable Markdown is.
    """
    if not fragment:
        return ""
    out = fragment

    # Fenced code blocks first, so their contents are not mangled by tag stripping.
    placeholders = []

    def _stash_code(m):
        code = _unescape_full(m.group(1))
        placeholders.append(code)
        return f"\x00CODE{len(placeholders) - 1}\x00"

    out = _CODE_BLOCK_RE.sub(_stash_code, out)

    # Headings.
    for level in range(1, 7):
        out = re.sub(rf"<h{level}[^>]*>(.*?)</h{level}>", rf"\n{'#' * level} \1\n",
                     out, flags=re.S | re.I)
    # Blockquotes (inner content; caller may re-quote when extracting an excerpt).
    out = re.sub(r"</?blockquote[^>]*>", "\n", out, flags=re.I)
    # Lists.
    out = re.sub(r"<li[^>]*>(.*?)</li>", r"\n- \1", out, flags=re.S | re.I)
    out = re.sub(r"</?(ul|ol)[^>]*>", "\n", out, flags=re.I)
    # Inline emphasis.
    out = re.sub(r"</?(strong|b)>", "**", out, flags=re.I)
    out = re.sub(r"</?(em|i)>", "*", out, flags=re.I)
    out = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", out, flags=re.S | re.I)
    # Links — keep text + url; images are dropped (text-only policy).
    out = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", out,
                 flags=re.S | re.I)
    out = re.sub(r"<img[^>]*>", "", out, flags=re.I)
    # Breaks and paragraphs.
    out = re.sub(r"<br\s*/?>", "\n", out, flags=re.I)
    out = re.sub(r"<hr\s*/?>", "\n---\n", out, flags=re.I)
    out = re.sub(r"</p>", "\n", out, flags=re.I)
    out = re.sub(r"<p[^>]*>", "", out, flags=re.I)

    out = _strip_tags(out)

    # Restore code blocks.
    for idx, code in enumerate(placeholders):
        out = out.replace(f"\x00CODE{idx}\x00", f"\n```\n{code.rstrip()}\n```\n")

    # Collapse runs of blank lines; trim trailing whitespace per line.
    out = "\n".join(line.rstrip() for line in out.splitlines())
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


# --------------------------------------------------------------------------
# Parsing one export HTML file
# --------------------------------------------------------------------------

_NOTE_DIV_RE = re.compile(r'<div class="note">(.*?)</div>\s*</div>', re.S)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.S | re.I)
_CREATED_RE = re.compile(r"创建于：\s*([0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9:]+)")
_TAG_SPAN_RE = re.compile(r'<span class="tag">(.*?)</span>', re.S)
_ATTACH_RE = re.compile(r'<div class="attachment">.*?</div>', re.S)
_SRC_LINK_RE = re.compile(r'原文：\s*<a[^>]*href="([^"]*)"', re.S)
_BLOCKQUOTE_RE = re.compile(r"<blockquote>(.*?)</blockquote>", re.S | re.I)
_IMG_RE = re.compile(r"<img\b", re.I)


def parse_note_html(path):
    """Parse one export HTML file into a note dict, or return None if it carries no text.

    Returned dict keys: getnote_id, title, created (YYYY-MM-DD), tags (list),
    source_url, summary, excerpt, has_image.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None

    note_div_m = _NOTE_DIV_RE.search(text)
    note_div = note_div_m.group(1) if note_div_m else text

    getnote_id = Path(path).stem

    title_m = _TITLE_RE.search(text)
    title = _strip_tags(title_m.group(1)).strip() if title_m else ""
    if not title:
        h1_m = _H1_RE.search(note_div)
        if h1_m:
            title = _strip_tags(h1_m.group(1)).strip()

    created_m = _CREATED_RE.search(note_div)
    created = ""
    if created_m:
        raw = created_m.group(1).replace("T", " ")
        created = raw[:10]

    tags = []
    for t in _TAG_SPAN_RE.findall(note_div):
        tag = _strip_tags(t).strip()
        # Keep real tags; drop punctuation-only noise (e.g. a stray "..").
        if tag and any(c.isalnum() for c in tag) and tag not in tags:
            tags.append(tag)

    src_m = _SRC_LINK_RE.search(note_div)
    source_url = src_m.group(1).strip() if src_m else ""

    has_image = bool(_IMG_RE.search(note_div))

    # Excerpt = blockquote(s); body summary = everything else in the note div.
    excerpts = _BLOCKQUOTE_RE.findall(note_div)
    excerpt = "\n\n".join(html_to_md(e) for e in excerpts).strip()

    body = _BLOCKQUOTE_RE.sub("", note_div)
    body = _ATTACH_RE.sub("", body)
    # Drop the metadata lines (创建于 / 标签 / hr) — they precede the first <hr>.
    hr_split = re.split(r"<hr\s*/?>", body, maxsplit=1, flags=re.I)
    body = hr_split[1] if len(hr_split) == 2 else body
    summary = html_to_md(body).strip()

    if not summary and not excerpt:
        return None  # pure-image or empty note — skipped by the text-only policy

    return {
        "getnote_id": getnote_id,
        "title": title,
        "created": created,
        "tags": tags,
        "source_url": source_url,
        "summary": summary,
        "excerpt": excerpt,
        "has_image": has_image,
    }


# --------------------------------------------------------------------------
# Routing + Markdown note construction
# --------------------------------------------------------------------------

def route(note):
    """Map a parsed note to (vault subdir, frontmatter type), per the directory contract."""
    if note["excerpt"] or note["source_url"]:
        return ("50-References", "reference")
    if note["title"]:
        return ("10-Knowledge", "knowledge")
    return ("20-Ideas/观点心得", "idea")


def _slugify(text, maxlen=48):
    """Filename-safe slug; CJK is kept (str.isalnum is true for CJK)."""
    s = re.sub(r"\s+", "-", (text or "").strip())
    s = "".join(c for c in s if c.isalnum() or c in "-_")
    return s[:maxlen].strip("-_") or "untitled"


_YAML_SPECIAL = set(",:[]{}&*#?|<>=!%@\"'`")


def _yaml_tag(t):
    """Quote a tag for a YAML inline list when it carries characters that would
    break flow syntax (book-name tags routinely contain '：', ',', brackets)."""
    if any(c in _YAML_SPECIAL for c in t) or t.strip() != t or t[:1] in "-?":
        return '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return t


def _first_line(text, maxlen=40):
    for line in (text or "").splitlines():
        line = line.strip().lstrip("#> -*`")
        if line:
            return line[:maxlen]
    return ""


def build_markdown(note, ntype):
    """Render a parsed note into a vault Markdown file body (frontmatter + content)."""
    heading = note["title"] or _first_line(note["summary"]) or _first_line(note["excerpt"]) \
        or "untitled"
    created = note["created"] or datetime.now().strftime("%Y-%m-%d")
    tags = note["tags"] or ["getnote"]
    tag_str = "[" + ", ".join(_yaml_tag(t) for t in tags) + "]"

    fm = (
        "---\n"
        f"type: {ntype}\n"
        "source: getnote\n"
        f"created: {created}\n"
        f"tags: {tag_str}\n"
        "quality: 0\n"
        "citations: 0\n"
        "related: []\n"
        "status: seed\n"
        "aliases: []\n"
        f"getnote_id: {note['getnote_id']}\n"
        "---\n"
    )

    lines = [fm, f"# {heading}", ""]
    src_line = f"\n> 原文: {note['source_url']}" if note["source_url"] else ""
    lines.append("> [!insight] Source")
    lines.append(f"> Captured from getnote on {created}.{src_line}")
    lines.append("")
    if note["summary"]:
        lines.append(note["summary"])
        lines.append("")
    if note["excerpt"]:
        lines.append("## 摘抄")
        lines.append("")
        for ln in note["excerpt"].splitlines():
            lines.append(f"> {ln}" if ln.strip() else ">")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------
# Dedup state
# --------------------------------------------------------------------------

_FM_GETNOTE_ID_RE = re.compile(r"^getnote_id:\s*(\S+)\s*$", re.M)


def scan_vault_getnote_ids(vault_root):
    """Return the set of getnote_id values already present in the vault frontmatter."""
    ids = set()
    root = Path(vault_root)
    for sub in ("10-Knowledge", "20-Ideas", "50-References"):
        d = root / sub
        if not d.exists():
            continue
        for md in d.rglob("*.md"):
            try:
                head = md.read_text(encoding="utf-8")[:600]
            except (OSError, UnicodeDecodeError):
                continue
            m = _FM_GETNOTE_ID_RE.search(head)
            if m:
                ids.add(m.group(1))
    return ids


def load_state(state_path):
    """Load the import state file (a flat YAML-ish map getnote_id: vault_path)."""
    imported = {}
    p = Path(state_path)
    if not p.exists():
        return imported
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        imported[k.strip()] = v.strip()
    return imported


def write_state(state_path, imported):
    p = Path(state_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# getnote-import state — getnote_id: vault_path", ""]
    for k in sorted(imported):
        lines.append(f"{k}: {imported[k]}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def find_notes_dir(export_dir):
    """Locate the notes/ directory inside an export archive (one level may be nested)."""
    export = Path(export_dir)
    if (export / "notes").is_dir():
        return export / "notes"
    for child in sorted(export.iterdir()):
        if child.is_dir() and (child / "notes").is_dir():
            return child / "notes"
    return None


def run(export_dir, vault_root, dry_run=False, limit=0):
    notes_dir = find_notes_dir(export_dir)
    if notes_dir is None:
        print(f"ERROR: no notes/ directory found under {export_dir}", file=sys.stderr)
        return 1

    vault_root = os.path.expanduser(vault_root)
    state_path = os.path.join(vault_root, ".state", "getnote-import-state.yaml")
    imported = load_state(state_path)
    vault_ids = scan_vault_getnote_ids(vault_root)
    already = set(imported) | vault_ids

    html_files = sorted(notes_dir.glob("*.html"))
    if limit:
        html_files = html_files[:limit]

    stats = Counter()
    route_counts = Counter()
    used_paths = set()
    written = 0

    for path in html_files:
        stats["scanned"] += 1
        note = parse_note_html(path)
        if note is None:
            stats["skipped_no_text"] += 1
            continue
        if note["getnote_id"] in already:
            stats["skipped_dup"] += 1
            continue

        subdir, ntype = route(note)
        route_counts[subdir] += 1

        base = _slugify(note["title"] or _first_line(note["summary"])
                        or _first_line(note["excerpt"]))
        rel = f"{subdir}/{base}.md"
        abs_path = os.path.join(vault_root, rel)
        if rel in used_paths or os.path.exists(abs_path):
            rel = f"{subdir}/{base}-{note['getnote_id'][:6]}.md"
            abs_path = os.path.join(vault_root, rel)
        used_paths.add(rel)

        if not dry_run:
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(build_markdown(note, ntype))
            imported[note["getnote_id"]] = rel
        already.add(note["getnote_id"])
        written += 1
        stats["written"] += 1

    if not dry_run:
        write_state(state_path, imported)

    mode = "DRY-RUN" if dry_run else "IMPORT"
    print(f"[getnote-import] {mode} — {notes_dir}")
    print(f"  scanned:          {stats['scanned']}")
    print(f"  written:          {stats['written']}")
    print(f"  skipped (dup):    {stats['skipped_dup']}")
    print(f"  skipped (no text):{stats['skipped_no_text']}")
    print("  routing:")
    for subdir in sorted(route_counts):
        print(f"    {subdir}: {route_counts[subdir]}")
    if not dry_run:
        print(f"  state file: {state_path}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Ingest a GetNote HTML export into the PKOS vault.")
    ap.add_argument("--export-dir", required=True,
                    help="Path to the unzipped getnote export archive directory.")
    ap.add_argument("--vault", default="~/Obsidian/PKOS",
                    help="PKOS vault root (default: ~/Obsidian/PKOS).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Parse and report routing without writing any files.")
    ap.add_argument("--limit", type=int, default=0,
                    help="Process at most N note files (0 = all).")
    args = ap.parse_args(argv)
    return run(args.export_dir, args.vault, dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
