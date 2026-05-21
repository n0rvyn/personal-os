#!/usr/bin/env python3
"""Migrate an external Obsidian vault into PKOS, faithful to the vault directory contract.

This rebuilds the `migrate` capability after a prior broken run left damage in the
vault: source category names slug-flattened directly under 10-Knowledge/, titles
captured as the frontmatter delimiter ('---'), no domain tags, and a whole source
directory (Linux SRE/, 383 notes) that never landed at all.

What this run fixes:
  - Title is taken from the FILE NAME (cleaned), never from a '---' line or a
    bold-wrapped first body line.
  - A source category routes to a NESTED directory: `Linux SRE/x.md` →
    `10-Knowledge/linux-sre/x.md`, never a flat top-level `linux-sre/`.
  - Every migrated note gets a category-slug tag so it is classifiable.
  - WeChat content (the user's own published articles/courses) routes to
    `90-Productions/WeChat/<series>/` as `type: production` — not reference/knowledge.
  - Low-value notes (empty, mojibake-garbled, tiny fragments) are auto-discarded to
    `.trash/migrate-discarded/` rather than imported.
  - `--force` first relocates the prior broken run's output (everything recorded in
    migrate-state.yaml) to `.trash/migrate-prior-run/`, then re-migrates cleanly.

Usage:
  migrate.py --source-name 99-Obsidian [--scan-only] [--force] [--resume]
  migrate.py --source-vault PATH [...]
"""
import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    print("ERROR: pyyaml is required (pip install pyyaml).", file=sys.stderr)
    raise

DEFAULT_VAULT = os.path.expanduser("~/Obsidian/PKOS")
DEFAULT_SOURCES = os.path.expanduser("~/Obsidian/PKOS/.state/migrate-sources.yaml")
DEFAULT_STATE = os.path.expanduser("~/Obsidian/PKOS/.state/migrate-state.yaml")

# Source directories that carry no migratable knowledge.
SKIP_DIRS = {".obsidian", ".trash", ".cursor", ".AI_ChatHistory",
             "Z-Images", "Z-Template", "images", "Images"}

# A note with fewer real characters than this AND no code/structure is flagged for
# review (still migrated — never auto-discarded on length; a one-line command note
# is valid knowledge). Only truly-empty and mojibake notes are discarded.
REVIEW_CHARS = 200

# WeChat series the user refers to by name; the source has a typo (路→跑).
WECHAT_SERIES_FIX = {"丹尼尔斯路步方程式": "丹尼尔斯跑步方程式"}


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def slugify(name, maxlen=64):
    """Filename/dir-safe slug. CJK is kept; spaces and '&' collapse to '-'."""
    s = (name or "").strip().lower()
    s = re.sub(r"[\s&/]+", "-", s)
    s = "".join(c for c in s if c.isalnum() or c in "-_")
    s = re.sub(r"-{2,}", "-", s).strip("-_")
    return s[:maxlen] or "untitled"


def clean_title(stem):
    """Derive a clean title from a filename stem — strip markdown noise the source
    sometimes embeds (a bold-wrapped '**# title**' became part of the name)."""
    t = stem.strip()
    t = re.sub(r"^[*#\s]+", "", t)
    t = re.sub(r"[*#\s]+$", "", t)
    return t.strip() or stem.strip() or "untitled"


def is_mojibake(text):
    """True if the text looks like CJK decoded through the wrong codec — a high
    density of Latin-1 supplement characters (è¿™ä¸ª…) with little real CJK."""
    sample = text[:2000]
    if not sample:
        return False
    latin_supp = sum(1 for c in sample if "À" <= c <= "ÿ")
    cjk = sum(1 for c in sample if "一" <= c <= "鿿")
    return latin_supp > 60 and latin_supp > cjk * 2


def measurable_content(text):
    """Real-content length proxy. Frontmatter is dropped and markdown punctuation is
    dropped, but code-block CONTENT is kept — for a technical note a one-line command
    or config snippet IS the content, so it must count toward "is this note empty?"."""
    body = text
    if body.startswith("---"):
        end = body.find("\n---", 3)
        if end != -1:
            body = body[end + 4:]
    body = body.replace("```", "")                 # drop fence markers, keep the code
    body = re.sub(r"[#*>`\-\|=~\s]+", "", body)     # drop markup punctuation + whitespace
    return body


def value_verdict(text):
    """Classify a source note: 'discard', 'review', or 'keep'.

    Only truly-empty notes and mojibake are discarded. A short note is NOT discarded
    — a single command or config snippet is valid knowledge — it is merely flagged
    'review' (and still migrated) so a human/LLM pass can judge it later.
    """
    if is_mojibake(text):
        return "discard"
    content = measurable_content(text)
    if len(content) == 0:
        return "discard"
    if len(content) < REVIEW_CHARS and not re.search(r"```|^\s*[#|]", text, re.M):
        return "review"
    return "keep"


def content_hash(text):
    return hashlib.md5(text.encode("utf-8", "replace")).hexdigest()


def parse_frontmatter_tags(text):
    """Return existing frontmatter tags (list) if the source note already has any."""
    if not text.startswith("---"):
        return []
    end = text.find("\n---", 3)
    if end == -1:
        return []
    try:
        fm = yaml.safe_load(text[3:end]) or {}
    except yaml.YAMLError:
        return []
    tags = fm.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    return [str(t) for t in tags if t]


def strip_frontmatter(text):
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:].lstrip("\n")
    return text


def strip_leading_h1(body, title):
    """Drop a leading level-1 heading line when it duplicates the title — the source
    note's own title-as-heading, redundant with the '# {title}' the migrator writes.
    Kept if the leading H1 differs from the title (then it carries distinct info)."""
    lines = body.lstrip("\n").splitlines()
    if not lines:
        return body
    m = re.match(r"^\*{0,2}#\s+(.+?)\*{0,2}\s*$", lines[0].strip())
    if not m:
        return body
    norm = lambda s: re.sub(r"[`*#\s]", "", s)
    h1, t = norm(m.group(1)), norm(title)
    if h1 and t and (h1 == t or h1 in t or t in h1):
        return "\n".join(lines[1:]).lstrip("\n")
    return body


# --------------------------------------------------------------------------
# Classification + routing
# --------------------------------------------------------------------------

def load_sources(path):
    with open(path, encoding="utf-8") as fh:
        return (yaml.safe_load(fh) or {}).get("sources", {})


def classify(rel_path, rules):
    """Apply migrate-sources classification_rules; return (type, source, quality)."""
    for rule in rules or []:
        if fnmatch.fnmatch(rel_path, rule.get("pattern", "")):
            return (rule.get("type", "knowledge"),
                    rule.get("source", "external-vault"),
                    rule.get("quality", 1))
    return ("knowledge", "external-vault", 1)


def wechat_target(parts):
    """For a source path under WeChat/, return (series, '90-Productions/WeChat/<series>')."""
    if not parts or parts[0] != "WeChat":
        return None
    rest = parts[1:]
    if rest and rest[0] == "Channel" and len(rest) > 1:
        series = WECHAT_SERIES_FIX.get(rest[1], rest[1])
    elif rest and rest[0] == "Official Account":
        series = "公众号随笔"
    else:
        series = "WeChat"
    return (series, f"90-Productions/WeChat/{series}")


def route(rel_path, ntype):
    """Map a source-relative path + classified type to a vault destination directory.

    A source category becomes a NESTED slug directory under the type's home dir.
    """
    parts = Path(rel_path).parts
    wt = wechat_target(parts)
    if wt is not None:
        return ("production", wt[1])
    category = parts[0] if len(parts) > 1 else ""
    cat_slug = slugify(category) if category else ""
    if category in ("Project", "WorkSpace"):
        home = "30-Projects"
        ntype = "project"
    elif ntype == "reference":
        home = "50-References"
    else:
        home = "10-Knowledge"
        ntype = "knowledge"
    dest = f"{home}/{cat_slug}" if cat_slug else home
    return (ntype, dest)


def build_note(title, ntype, source, quality, tags, body):
    tag_list = sorted(set(tags))
    tag_str = "[" + ", ".join(_yaml_tag(t) for t in tag_list) + "]" if tag_list else "[]"
    fm = (
        "---\n"
        f"type: {ntype}\n"
        f"source: {source}\n"
        f"created: {_today()}\n"
        f"tags: {tag_str}\n"
        f"quality: {quality}\n"
        "citations: 0\n"
        "related: []\n"
        "status: seed\n"
        "aliases: []\n"
        "migrated_from: 99-Obsidian\n"
        "---\n"
    )
    return f"{fm}\n# {title}\n\n{body.strip()}\n"


_YAML_SPECIAL = set(",:[]{}&*#?|<>=!%@\"'`")


def _yaml_tag(t):
    if any(c in _YAML_SPECIAL for c in t) or t.strip() != t or t[:1] in "-?":
        return '"' + t.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return t


def _today():
    from datetime import date
    return date.today().isoformat()


# --------------------------------------------------------------------------
# Prior-run cleanup
# --------------------------------------------------------------------------

def load_state(path):
    if not os.path.exists(path):
        return {"migrated": [], "errors": []}
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {"migrated": [], "errors": []}


_MIGRATED_FROM_RE = re.compile(r"^migrated_from:\s*\S", re.M)
_MIGRATED_HOMES = ("10-Knowledge", "20-Ideas", "30-Projects", "50-References",
                   "90-Productions")


def clean_prior_run(vault, state):
    """Relocate prior-migration output to .trash/migrate-prior-run/.

    Sweeps the union of (a) every path recorded in migrate-state.yaml and (b) every
    vault .md that carries a `migrated_from:` frontmatter field. (b) is essential:
    an earlier migration that did not record state (e.g. a Notion-synced run) leaves
    files this skill would otherwise collide with, producing `-<hash>` duplicates.
    """
    trash = os.path.join(vault, ".trash", "migrate-prior-run")
    targets = set()
    for entry in state.get("migrated", []):
        vp = entry.get("vault_path", "")
        if vp:
            targets.add(vp)
    for top in _MIGRATED_HOMES:
        base = Path(vault) / top
        if not base.is_dir():
            continue
        for md in base.rglob("*.md"):
            try:
                head = md.read_text(encoding="utf-8")[:600]
            except (OSError, UnicodeDecodeError):
                continue
            if _MIGRATED_FROM_RE.search(head):
                targets.add(str(md.relative_to(vault)))

    moved = 0
    for rel in targets:
        src = os.path.join(vault, rel)
        if os.path.isfile(src):
            dst = os.path.join(trash, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)
            moved += 1
    # Drop now-empty directories left behind under the migration home dirs.
    for top in _MIGRATED_HOMES:
        base = os.path.join(vault, top)
        if not os.path.isdir(base):
            continue
        for dp, dns, fns in os.walk(base, topdown=False):
            if dp != base and not os.listdir(dp):
                os.rmdir(dp)
    return moved


# --------------------------------------------------------------------------
# LLM value-judgment queue
# --------------------------------------------------------------------------

def emit_judgment_queue(vault, entries, queue_path):
    """Write a JSONL queue of {vault_path, title, excerpt} for every migrated note.

    The migration itself is mechanical; this queue is what the migrate skill's LLM
    pass reads to judge each note's content value ('整篇价值不大' → discard). The
    excerpt is the note body's leading text — enough to judge value without loading
    every full note into the skill's context.
    """
    written = 0
    with open(queue_path, "w", encoding="utf-8") as fh:
        for e in entries:
            ap = os.path.join(vault, e["vault_path"])
            try:
                text = Path(ap).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            tm = re.search(r"^#\s+(.+)$", text, re.M)
            title = tm.group(1).strip() if tm else ""
            body = strip_frontmatter(text)
            body = re.sub(r"^#\s+.+\n+", "", body, count=1)   # drop the title heading
            excerpt = re.sub(r"\s+", " ", body).strip()[:320]
            fh.write(json.dumps({"vault_path": e["vault_path"], "title": title,
                                 "excerpt": excerpt}, ensure_ascii=False) + "\n")
            written += 1
    return written


def apply_discards(vault, discard_file, state_path):
    """Move LLM-judged low-value notes to .trash/migrate-discarded/ and drop them
    from migrate-state. `discard_file` is a plain list of vault-relative paths."""
    vault = os.path.expanduser(vault)
    discard = set()
    for line in Path(discard_file).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            discard.add(line)
    state = load_state(state_path)
    trash = os.path.join(vault, ".trash", "migrate-discarded")
    moved = 0
    kept = []
    for e in state.get("migrated", []):
        vp = e.get("vault_path", "")
        if vp in discard:
            src = os.path.join(vault, vp)
            if os.path.isfile(src):
                dst = os.path.join(trash, vp)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)
                moved += 1
        else:
            kept.append(e)
    state["migrated"] = kept
    with open(state_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(state, fh, allow_unicode=True, sort_keys=False)
    print(f"[migrate] apply-discards: moved {moved} low-value notes to "
          f".trash/migrate-discarded/  ({len(kept)} notes remain migrated)")
    return 0


# --------------------------------------------------------------------------
# Main migration
# --------------------------------------------------------------------------

def iter_source_notes(source_root):
    """Yield (abs_path, rel_path) for every migratable .md under the source vault."""
    root = Path(source_root)
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".")]
        for f in fns:
            if f.endswith(".md"):
                ap = Path(dp) / f
                yield (ap, str(ap.relative_to(root)))


def run(source_root, vault=DEFAULT_VAULT, rules=None, state_path=DEFAULT_STATE,
        scan_only=False, force=False, resume=False):
    vault = os.path.expanduser(vault)
    source_root = os.path.expanduser(source_root)
    if not os.path.isdir(source_root):
        print(f"ERROR: source vault not found: {source_root}", file=sys.stderr)
        return 1

    state = load_state(state_path)
    if force and not scan_only:
        moved = clean_prior_run(vault, state)
        print(f"[migrate] --force: relocated {moved} prior-run files to .trash/migrate-prior-run/")
        state = {"migrated": [], "errors": []}

    done_hashes = set()
    if resume and not force:
        done_hashes = {e.get("dedup_hash") for e in state.get("migrated", [])}

    stats = Counter()
    dest_counts = Counter()
    review_list = []
    discard_list = []
    new_entries = []
    seen_targets = set()

    for ap, rel in iter_source_notes(source_root):
        stats["scanned"] += 1
        try:
            text = ap.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            try:
                text = ap.read_text(encoding="utf-8", errors="replace")
            except OSError:
                stats["unreadable"] += 1
                continue

        h = content_hash(text)
        if h in done_hashes:
            stats["skipped_resume"] += 1
            continue

        verdict = value_verdict(text)
        if verdict == "discard":
            stats["discarded"] += 1
            discard_list.append(rel)
            if not scan_only:
                dst = os.path.join(vault, ".trash", "migrate-discarded", rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(ap, dst)
            continue
        if verdict == "review":
            stats["review"] += 1
            review_list.append(rel)

        ntype, _src, quality = classify(rel, rules)
        ntype, dest_dir = route(rel, ntype)
        dest_counts[dest_dir] += 1

        title = clean_title(Path(rel).stem)
        category = Path(rel).parts[0] if len(Path(rel).parts) > 1 else ""
        tags = parse_frontmatter_tags(text)
        if category:
            tags.append(slugify(category))
        body = strip_leading_h1(strip_frontmatter(text), title)

        slug = slugify(Path(rel).stem)
        target_rel = f"{dest_dir}/{slug}.md"
        if target_rel in seen_targets or os.path.exists(os.path.join(vault, target_rel)):
            target_rel = f"{dest_dir}/{slug}-{h[:6]}.md"
        seen_targets.add(target_rel)

        if not scan_only:
            abs_target = os.path.join(vault, target_rel)
            os.makedirs(os.path.dirname(abs_target), exist_ok=True)
            note_source = "wechat" if ntype == "production" else _src
            with open(abs_target, "w", encoding="utf-8") as fh:
                fh.write(build_note(title, ntype, note_source, quality, tags, body))
        new_entries.append({"dedup_hash": h, "source_path": rel,
                            "vault_path": target_rel, "type": ntype, "status": "migrated"})
        stats["migrated"] += 1

    queue_path = ""
    if not scan_only:
        state.setdefault("migrated", []).extend(new_entries)
        state["last_migration_path"] = "batch-complete"
        with open(state_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(state, fh, allow_unicode=True, sort_keys=False)
        queue_path = os.path.join(os.path.dirname(state_path) or ".",
                                  "migrate-judgment-queue.jsonl")
        emit_judgment_queue(vault, new_entries, queue_path)

    mode = "SCAN-ONLY" if scan_only else "MIGRATE"
    print(f"[migrate] {mode} — source: {source_root}")
    print(f"  scanned:        {stats['scanned']}")
    print(f"  migrated:       {stats['migrated']}")
    print(f"  discarded:      {stats['discarded']}  (→ .trash/migrate-discarded/)")
    print(f"  flagged review: {stats['review']}")
    print(f"  skipped resume: {stats['skipped_resume']}")
    print(f"  unreadable:     {stats['unreadable']}")
    print("  destinations:")
    for d in sorted(dest_counts):
        print(f"    {d}: {dest_counts[d]}")
    if discard_list:
        print(f"  DISCARDED ({len(discard_list)} — empty or mojibake; review this list):")
        for r in discard_list:
            print(f"    {r}")
    if review_list:
        print(f"  review candidates ({len(review_list)} — short, no structure):")
        for r in review_list[:30]:
            print(f"    {r}")
        if len(review_list) > 30:
            print(f"    ... +{len(review_list) - 30} more")
    if queue_path:
        print(f"  judgment queue: {queue_path}")
        print(f"    → next: LLM value-judgment pass over {len(new_entries)} migrated notes")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Migrate an external vault into PKOS.")
    g = ap.add_mutually_exclusive_group(required=False)
    g.add_argument("--source-name", help="A named source from migrate-sources.yaml.")
    g.add_argument("--source-vault", help="A source vault path directly.")
    ap.add_argument("--vault", default=DEFAULT_VAULT)
    ap.add_argument("--sources-file", default=DEFAULT_SOURCES)
    ap.add_argument("--state-file", default=DEFAULT_STATE)
    ap.add_argument("--scan-only", action="store_true", help="Report without writing.")
    ap.add_argument("--dry-run", action="store_true", help="Alias for --scan-only.")
    ap.add_argument("--force", action="store_true",
                    help="Relocate the prior run's output to .trash/ and re-migrate all.")
    ap.add_argument("--resume", action="store_true", help="Skip already-migrated notes.")
    ap.add_argument("--apply-discards", metavar="FILE",
                    help="Apply an LLM value-judgment pass: move the vault-relative "
                         "paths listed in FILE to .trash/migrate-discarded/.")
    args = ap.parse_args(argv)

    if args.apply_discards:
        return apply_discards(args.vault, args.apply_discards, args.state_file)

    if not (args.source_name or args.source_vault):
        ap.error("one of --source-name / --source-vault / --apply-discards is required")

    rules = None
    source_root = args.source_vault
    if args.source_name:
        sources = load_sources(args.sources_file)
        if args.source_name not in sources:
            print(f"ERROR: source '{args.source_name}' not in {args.sources_file}",
                  file=sys.stderr)
            return 1
        cfg = sources[args.source_name]
        source_root = cfg.get("path")
        rules = cfg.get("classification_rules")

    return run(source_root, vault=args.vault, rules=rules, state_path=args.state_file,
               scan_only=args.scan_only or args.dry_run, force=args.force,
               resume=args.resume)


if __name__ == "__main__":
    sys.exit(main())
