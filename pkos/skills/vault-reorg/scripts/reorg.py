#!/usr/bin/env python3
"""PKOS vault reorganization — retag untagged notes, dedup, and clear stale dirs.

After the getnote backfill and the 99-Obsidian migration, the vault still carries
legacy debris this pass cleans up:

  retag    — legacy notes with no domain tag fall to 'general' in cross-domain
             recall. Classify each by content + parent directory and write a
             domain tag, so podcast recall can bucket them.
  dedup    — exact content-duplicate notes (same body) — the getnote export and
             the migration each captured some of the same material. Keep one
             copy, move the rest to .trash/dedup-removed/.
  cleanup  — remove empty directories; relocate the stale 00-Inbox/getnote/ tree
             (a defunct getnote integration, superseded by the getnote-import
             skill) to .trash/stale-getnote-inbox/.

Nothing is deleted — everything removed goes to .trash/. Run with --dry-run first.

Usage:
  reorg.py [--vault DIR] [--dry-run] [--only retag|dedup|cleanup]
"""
import argparse
import hashlib
import importlib.util
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

VAULT_DEFAULT = os.path.expanduser("~/Obsidian/PKOS")
RETAG_DIRS = ("10-Knowledge", "20-Ideas")
DEDUP_DIRS = ("10-Knowledge", "20-Ideas", "50-References", "30-Projects")


# --------------------------------------------------------------------------
# domain_classify — bundled sibling module (same scripts/ directory)
# --------------------------------------------------------------------------

def _load_domain_classify():
    """Load the bundled domain_classify module. It ships inside this skill, so the
    retag pass has no cross-plugin dependency."""
    try:
        path = Path(__file__).resolve().parent / "domain_classify.py"
        spec = importlib.util.spec_from_file_location("domain_classify", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except (OSError, ImportError):
        return None


_DC = _load_domain_classify()


# --------------------------------------------------------------------------
# Frontmatter helpers
# --------------------------------------------------------------------------

def split_frontmatter(text):
    """Return (frontmatter_str_without_fences, body, had_frontmatter)."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return (text[3:end].strip("\n"), text[end + 4:].lstrip("\n"), True)
    return ("", text, False)


def parse_fm_terms(fm):
    """Extract the combined `tags` + `topics` terms from frontmatter.

    Handles inline `[a, b]` and block-style (`- item`) lists. cross_domain recall
    reads BOTH fields, so a note carrying usable `topics:` is already classifiable
    and must not be counted as untagged.
    """
    terms = []
    for field in ("tags", "topics"):
        inline = re.search(rf"^{field}:\s*\[(.*?)\]\s*$", fm, re.M)
        if inline:
            terms += [x.strip().strip("\"'") for x in inline.group(1).split(",")
                      if x.strip()]
            continue
        block = re.search(rf"^{field}:\s*$", fm, re.M)
        if block:
            for line in fm[block.end():].splitlines():
                lm = re.match(r"\s*-\s+(.+)$", line)
                if lm:
                    terms.append(lm.group(1).strip().strip("\"'"))
                elif line.strip():
                    break
            continue
        single = re.search(rf"^{field}:\s*(\S.*?)\s*$", fm, re.M)
        if single and not single.group(1).startswith("["):
            terms.append(single.group(1).strip().strip("\"'"))
    return terms


def note_excerpt(body, limit=200):
    """First chunk of real body text, markup-stripped, for classification."""
    b = re.sub(r"```.*?```", " ", body, flags=re.S)
    b = re.sub(r"[#*>`\-\|=~]+", " ", b)
    b = re.sub(r"\s+", " ", b)
    return b.strip()[:limit]


def write_domain_tag(text, domain):
    """Return `text` with `domain` APPENDED to its frontmatter tags.

    Existing tags are preserved — the domain is added, never substituted. Handles an
    inline `[a, b]` list, a block-style (`- item`) list, a single inline value, a
    missing `tags:` field, and a missing frontmatter block.
    """
    fm, body, had = split_frontmatter(text)
    if not had:
        return f"---\ntags: [{domain}]\n---\n\n{text}"

    inline = re.search(r"^tags:\s*\[(.*?)\]\s*$", fm, re.M)
    if inline:
        inner = inline.group(1).strip()
        new = f"tags: [{inner}, {domain}]" if inner else f"tags: [{domain}]"
        fm = fm[:inline.start()] + new + fm[inline.end():]
        return f"---\n{fm}\n---\n\n{body}"

    block = re.search(r"^tags:[ \t]*$", fm, re.M)
    if block:
        rest = fm[block.end():]
        # Match the existing list items' indentation — mixing 0-space and 2-space
        # list items under one key is invalid YAML.
        im = re.match(r"\n([ \t]*)-[ \t]", rest)
        indent = im.group(1) if im else "  "
        fm = fm[:block.end()] + f"\n{indent}- {domain}" + rest
        return f"---\n{fm}\n---\n\n{body}"

    single = re.search(r"^tags:\s*(\S.*?)\s*$", fm, re.M)
    if single:
        fm = (fm[:single.start()] + f"tags: [{single.group(1).strip()}, {domain}]"
              + fm[single.end():])
    else:
        fm = fm + f"\ntags: [{domain}]"
    return f"---\n{fm}\n---\n\n{body}"


# --------------------------------------------------------------------------
# retag
# --------------------------------------------------------------------------

def retag(vault, dry_run):
    if _DC is None:
        print("  [retag] SKIPPED — domain_classify.py not found "
              "(podcast-prep plugin missing).")
        return Counter()
    stats = Counter()
    for top in RETAG_DIRS:
        base = Path(vault) / top
        if not base.is_dir():
            continue
        for md in base.rglob("*.md"):
            stats["scanned"] += 1
            try:
                text = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            fm, body, _ = split_frontmatter(text)
            # A note already classifiable by its tags OR topics needs no retag.
            if _DC.classify_by_tags(parse_fm_terms(fm)) != "general":
                continue
            stats["unclassified"] += 1
            tm = re.search(r"^#\s+(.+)$", body, re.M)
            title = tm.group(1).strip() if tm else md.stem
            domain = _DC.classify_by_text(title, note_excerpt(body), md.parent.name)
            if domain == "general":
                stats["stay_general"] += 1
                continue
            stats[f"tagged_{domain}"] += 1
            stats["tagged"] += 1
            if not dry_run:
                md.write_text(write_domain_tag(text, domain), encoding="utf-8")
    return stats


# --------------------------------------------------------------------------
# dedup
# --------------------------------------------------------------------------

def _body_hash(text):
    """Whitespace-normalized body hash. Bodies under 80 real chars are not hashed —
    too short to dedup safely (distinct trivial notes would collide)."""
    _, body, _ = split_frontmatter(text)
    norm = re.sub(r"\s+", "", body)
    if len(norm) < 80:
        return ""
    return hashlib.md5(norm.encode("utf-8", "replace")).hexdigest()


def _keep_rank(path):
    """Sort key for choosing which duplicate to keep: prefer a clean filename
    (no -<hash> collision suffix), then the shortest name, then the path."""
    name = Path(path).name
    suffixed = 1 if re.search(r"-[0-9a-f]{6}\.md$", name) else 0
    return (suffixed, len(name), path)


def dedup(vault, dry_run):
    groups = defaultdict(list)
    for top in DEDUP_DIRS:
        base = Path(vault) / top
        if not base.is_dir():
            continue
        for md in base.rglob("*.md"):
            try:
                text = md.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            h = _body_hash(text)
            if h:
                groups[h].append(str(md))
    stats = Counter()
    trash = Path(vault) / ".trash" / "dedup-removed"
    for h, paths in groups.items():
        if len(paths) < 2:
            continue
        stats["dup_groups"] += 1
        ordered = sorted(paths, key=_keep_rank)
        for victim in ordered[1:]:
            stats["removed"] += 1
            if not dry_run:
                rel = os.path.relpath(victim, vault)
                dst = trash / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.rename(victim, dst)
    return stats


# --------------------------------------------------------------------------
# cleanup
# --------------------------------------------------------------------------

def cleanup(vault, dry_run):
    stats = Counter()
    # Relocate the stale 00-Inbox/getnote/ tree (defunct integration).
    stale = Path(vault) / "00-Inbox" / "getnote"
    if stale.is_dir():
        n = sum(1 for _ in stale.rglob("*.md"))
        stats["stale_getnote"] = n
        if not dry_run:
            dst = Path(vault) / ".trash" / "stale-getnote-inbox"
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                dst = Path(f"{dst}-{os.getpid()}")
            os.rename(stale, dst)
    # Remove empty directories under the content homes.
    for top in ("10-Knowledge", "20-Ideas", "30-Projects", "50-References",
                "90-Productions"):
        base = Path(vault) / top
        if not base.is_dir():
            continue
        for dp, dns, fns in os.walk(base, topdown=False):
            entries = os.listdir(dp)
            if dp != str(base) and (not entries or entries == [".DS_Store"]):
                stats["empty_dirs"] += 1
                if not dry_run:
                    ds = os.path.join(dp, ".DS_Store")
                    if os.path.exists(ds):
                        os.remove(ds)
                    os.rmdir(dp)
    return stats


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def run(vault, dry_run=False, only=None):
    vault = os.path.expanduser(vault)
    if not os.path.isdir(vault):
        print(f"ERROR: vault not found: {vault}", file=sys.stderr)
        return 1
    mode = "DRY-RUN" if dry_run else "REORG"
    print(f"[vault-reorg] {mode} — {vault}\n")

    if only in (None, "retag"):
        st = retag(vault, dry_run)
        print("retag:")
        print(f"  scanned: {st['scanned']}  unclassified: {st['unclassified']}  "
              f"tagged: {st['tagged']}  stayed general: {st['stay_general']}")
        for k in sorted(k for k in st if k.startswith("tagged_")):
            print(f"    {k[7:]}: {st[k]}")

    if only in (None, "dedup"):
        st = dedup(vault, dry_run)
        print("dedup:")
        print(f"  duplicate groups: {st['dup_groups']}  "
              f"removed copies: {st['removed']}  (→ .trash/dedup-removed/)")

    if only in (None, "cleanup"):
        st = cleanup(vault, dry_run)
        print("cleanup:")
        print(f"  stale 00-Inbox/getnote notes relocated: {st['stale_getnote']}  "
              f"(→ .trash/stale-getnote-inbox/)")
        print(f"  empty directories removed: {st['empty_dirs']}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Reorganize the PKOS vault.")
    ap.add_argument("--vault", default=VAULT_DEFAULT)
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change without writing.")
    ap.add_argument("--only", choices=["retag", "dedup", "cleanup"],
                    help="Run only one step (default: all three).")
    args = ap.parse_args(argv)
    return run(args.vault, dry_run=args.dry_run, only=args.only)


if __name__ == "__main__":
    sys.exit(main())
