"""PKOS vault scanner + tag-based cross-domain bucketing.

Phase-1 implementation uses Jaccard on frontmatter tags (matches pkos:serendipity convention)
rather than embeddings. Upgrade path: replace classify_note_domain + jaccard with embedding
similarity once concept-validation passes 5 episodes.

Public helpers:
- classify_note_domain(tags) -> domain bucket str
- load_pkos_notes(vault_root, dirs) -> list of note dicts
- cross_domain_candidates(today_tags, vault_root, n) -> list of notes from NON-tech domains
- same_topic_past_notes(today_tags, vault_root, days_min, days_max, n) -> past notes on similar topic
"""
import os
import random
import re
from datetime import date, timedelta
from pathlib import Path

# Domain keyword maps. Order matters: classify_note_domain returns the FIRST matching domain.
# Non-tech domains come first so a note with both philosophy + ai tags is bucketed as philosophy.
DOMAIN_KEYWORDS = {
    "philosophy": [
        "哲学", "philosophy", "思想", "ethics", "伦理", "存在", "意义",
        "认识论", "本体论", "形而上学", "禅", "道", "君子", "卡拉马佐夫",
    ],
    "management": [
        "管理", "management", "战略", "strategy", "business", "经济",
        "economics", "极简管理", "组织", "leadership", "领导力", "decision-theory",
    ],
    "cognition": [
        "认知", "cognition", "心理", "psychology", "思维", "thinking",
        "卡尼曼", "kahneman", "刻意练习", "decision", "judgment", "metacognition",
        "心理学",
    ],
    "history": [
        "历史", "history", "古代", "war", "civilization", "古典",
        "近代史", "帝国",
    ],
    "literature": [
        "文学", "literature", "小说", "fiction", "诗", "戏剧",
        "novel", "poetry", "drama", "俄罗斯文学", "陀思妥耶夫斯基",
    ],
    "natural-science": [
        "科学", "science", "物理", "physics", "biology", "chemistry",
        "生物", "化学", "天文",
    ],
}

TECH_KEYWORDS = [
    "ai", "ml", "llm", "agent", "agents", "swift", "code", "coding",
    "api", "engineering", "ios", "model", "neural", "tts", "rag",
    "python", "typescript", "rust", "swift6", "swiftui", "actor",
    "claude", "openai", "anthropic", "github", "vibe-coding", "mcp",
    "ai-agents", "ai-coding", "on-device-ai", "device-side-ai",
    "compile-time-safety", "llm-api-cost", "swift-actor-model",
    "swift-agent-frameworks", "ai-cost-economics", "local-ai-inference",
]

PKOS_DEFAULT_DIRS = ["10-Knowledge", "20-Ideas", "50-References", "30-Projects",
                     "90-Podcasts"]

# Recall directories, per the vault directory contract (KL-4):
# - self_past reads the user's viewpoints (20-Ideas/观点心得) + past on-record stances
#   (90-Podcasts episode archives).
# - cross_domain reads the user's knowledge + ideas, bucketed by domain.
SELF_PAST_DIRS = ("20-Ideas/观点心得/", "90-Podcasts/")
CROSS_DOMAIN_DIRS = ("10-Knowledge/", "20-Ideas/")

# Placeholder titles carrying no information. getnote/dedao captures use these, often
# with a numeric suffix ("无标题笔记-1054"), so this is a prefix-pattern match, not a set.
_UNUSABLE_TITLE_RE = re.compile(
    r"^(无标题|未命名|untitled)[\s\-_0-9笔记]*$", re.IGNORECASE
)


def _is_unusable_title(title):
    """True if the title is a content-free placeholder (incl. numbered variants)."""
    return bool(title) and bool(_UNUSABLE_TITLE_RE.match(title.strip()))

# CJK function words stripped when building a title signature for dedup, so that
# "X的研究发现" and "X研究发现" collapse to the same near-duplicate.
_DEDUP_FILLER = str.maketrans("", "", "的了之与和及在")


def _kw_matches_tag(kw, tag):
    """True if keyword kw matches tag (both already lowercased).

    ASCII keywords require boundaries: CJK chars and separators count as boundaries,
    so 'ai' matches 'ai编程' but 'war' does NOT match 'software'. CJK keywords of ≥2
    chars match as plain substrings; single-char CJK keywords require exact tag equality
    (avoids '道' matching '知道').
    """
    if not kw or not tag:
        return False
    is_ascii = kw.isascii()
    if is_ascii:
        for m in re.finditer(re.escape(kw), tag):
            before = tag[m.start() - 1] if m.start() > 0 else ""
            after = tag[m.end()] if m.end() < len(tag) else ""
            before_ok = not (before and before.isascii() and before.isalnum())
            after_ok = not (after and after.isascii() and after.isalnum())
            if before_ok and after_ok:
                return True
        return False
    if len(kw) == 1:
        return kw == tag
    return kw in tag


def classify_note_domain(tags):
    """Return the domain bucket for a note given its tags (list of str).

    Returns one of: philosophy / management / cognition / history / literature /
    natural-science / tech / general.

    Logic: check non-tech domains first; if any non-tech domain has a keyword that
    matches any tag → return that domain. If only tech keywords match → tech. Else → general.
    """
    if not tags:
        return "general"
    tags_norm = [t.lower().strip() for t in tags if t and t.strip()]
    if not tags_norm:
        return "general"
    for domain, kws in DOMAIN_KEYWORDS.items():
        for kw in kws:
            kw_l = kw.lower()
            for tag in tags_norm:
                if _kw_matches_tag(kw_l, tag):
                    return domain
    for kw in TECH_KEYWORDS:
        kw_l = kw.lower()
        for tag in tags_norm:
            if _kw_matches_tag(kw_l, tag):
                return "tech"
    return "general"


def _parse_frontmatter(text):
    """Extract YAML frontmatter as dict. Minimal parser, handles tags + created + topics + title.

    Returns dict with keys present in frontmatter, or empty dict if none.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fm_text = text[3:end].strip()
    result = {}
    current_list_key = None
    for raw in fm_text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if line.startswith("  - ") and current_list_key:
            result.setdefault(current_list_key, []).append(
                line[4:].strip().strip("'\"")
            )
            continue
        current_list_key = None
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            if not v:
                result[k] = []
                current_list_key = k
            elif v.startswith("[") and v.endswith("]"):
                # inline list [a, b, c]
                items = [
                    item.strip().strip("'\"")
                    for item in v[1:-1].split(",")
                    if item.strip()
                ]
                result[k] = items
            else:
                result[k] = v.strip("'\"")
    return result


def _extract_title(text, fallback_path):
    """Get title from the first USABLE of: aliases[0] / frontmatter title / first H1 /
    filename stem. Roughly 23% of the vault are getnote/dedao captures whose title is a
    placeholder ("无标题") — it can land in any of those slots, so each candidate is
    checked. When all are unusable, fall back to the first body line so the note still
    carries a referenceable label."""
    fm = _parse_frontmatter(text)
    candidates = []
    if isinstance(fm.get("aliases"), list) and fm["aliases"]:
        candidates.append(fm["aliases"][0])
    if fm.get("title"):
        candidates.append(str(fm["title"]))
    for line in text.splitlines():
        if line.startswith("# "):
            candidates.append(line[2:].strip())
            break
    candidates.append(Path(fallback_path).stem)
    for c in candidates:
        if c and c.strip() and not _is_unusable_title(c):
            return c
    # Every candidate is a placeholder → use the first body line as a de-facto title.
    return _extract_excerpt(text, max_len=40) or (candidates[0] if candidates else "")


def _extract_excerpt(text, max_len=200):
    """First non-empty body line (post-frontmatter), trimmed."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith(">") or line.startswith("```"):
            continue
        return line[:max_len]
    return ""


def load_pkos_notes(vault_root, dirs=None):
    """Walk vault and return [{path, title, tags, created, domain, excerpt}, ...].

    `dirs` defaults to PKOS_DEFAULT_DIRS. Notes without a parseable created date are dropped.
    """
    root = Path(os.path.expanduser(vault_root))
    if not root.exists():
        return []
    dirs = dirs or PKOS_DEFAULT_DIRS
    notes = []
    for sub in dirs:
        for md in (root / sub).rglob("*.md") if (root / sub).exists() else []:
            try:
                text = md.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            fm = _parse_frontmatter(text)
            tags = []
            if isinstance(fm.get("tags"), list):
                tags.extend(fm["tags"])
            if isinstance(fm.get("topics"), list):
                tags.extend(fm["topics"])
            created = fm.get("created", "")
            if not created or not re.match(r"^\d{4}-\d{2}-\d{2}", str(created)):
                continue
            title = _extract_title(text, md)
            # Drop only notes that are still unusable after the excerpt fallback —
            # i.e. truly empty notes (no title, no body). Placeholder-titled notes
            # with real content keep their first body line as the label.
            if not title or not title.strip() or _is_unusable_title(title):
                continue
            notes.append({
                "path": str(md.relative_to(root)),
                "title": title,
                "tags": tags,
                "created": created[:10],
                "domain": classify_note_domain(tags),
                "excerpt": _extract_excerpt(text),
            })
    return notes


def _tag_overlap(a, b):
    """Count of lowercase tag intersection."""
    sa = {t.lower().strip() for t in a if t}
    sb = {t.lower().strip() for t in b if t}
    return len(sa & sb)


def cross_domain_candidates(today_tags, vault_root, n=5, notes=None,
                            recent_pool=8, seed=None):
    """Return up to n notes from NON-tech domains, one per domain bucket.

    Selection: for each non-tech domain, prefer notes matching ≥1 today_tag; from that
    set (or the whole domain if no overlap), take the `recent_pool` most-recent notes and
    pick one at random. Random (not always-newest) so consecutive episodes on similar
    topics do not keep surfacing the identical note — a cooldown against mechanical repeat.
    Pass `seed` for reproducibility; seed=None → fresh pick each run.

    Returns notes ordered by domain priority (philosophy → management → cognition →
    history → literature → natural-science). Pass `notes` to skip filesystem walk.
    """
    if notes is None:
        notes = load_pkos_notes(vault_root)
    # KL-4: cross_domain reads only the contract's cross-domain directories.
    notes = [nt for nt in notes if nt["path"].startswith(CROSS_DOMAIN_DIRS)]
    domains_priority = [
        "philosophy", "management", "cognition",
        "history", "literature", "natural-science",
    ]
    rng = random.Random(seed)
    picked = []
    for dom in domains_priority:
        domain_notes = [nt for nt in notes if nt["domain"] == dom]
        if not domain_notes:
            continue
        with_overlap = [
            nt for nt in domain_notes
            if _tag_overlap(nt["tags"], today_tags) > 0
        ]
        pool = with_overlap if with_overlap else domain_notes
        pool.sort(key=lambda nt: nt["created"], reverse=True)
        picked.append(rng.choice(pool[:recent_pool]))
        if len(picked) >= n:
            break
    return picked


def _title_signature(title):
    """Normalized title key for near-duplicate detection: drop whitespace, lowercase,
    strip CJK function words so "X的研究发现" and "X研究发现" collapse together."""
    return re.sub(r"\s+", "", title).lower().translate(_DEDUP_FILLER)


def same_topic_past_notes(today_tags, vault_root, days_min=7, days_max=90, n=5,
                          today=None, notes=None):
    """Past notes for the self-past-contrarian feature: surface notes the user may have
    taken a STANCE on, so 达芬奇 can find one that contradicts today's argument and write
    a "我X天前是这么想的→为什么变了" passage.

    Selection: ≥1 tag overlap with today_tags, created in [today-days_max, today-days_min].
    Ranking prefers notes from 20-Ideas/ (the user's own opinions — likely to carry a
    stance) over 10-Knowledge/ excerpts, then higher tag overlap, then recency. The window
    is 90 days because a past stance worth debating is often older than a month.

    Pass `today` (ISO date str) and `notes` for testing.
    """
    if notes is None:
        notes = load_pkos_notes(vault_root)
    # KL-4: self_past reads only 20-Ideas/观点心得 (viewpoints) + 90-Podcasts (past
    # on-record stances), per the vault directory contract.
    notes = [nt for nt in notes if nt["path"].startswith(SELF_PAST_DIRS)]
    today_d = date.fromisoformat(today) if today else date.today()
    upper = today_d - timedelta(days=days_min)
    lower = today_d - timedelta(days=days_max)
    pool = []
    for nt in notes:
        if _tag_overlap(nt["tags"], today_tags) == 0:
            continue
        try:
            created_d = date.fromisoformat(nt["created"])
        except ValueError:
            continue
        if lower <= created_d <= upper:
            pool.append(nt)
    pool.sort(
        key=lambda nt: (
            _tag_overlap(nt["tags"], today_tags),
            nt["created"],
        ),
        reverse=True,
    )
    # Dedup by title signature — the vault holds near-identical re-syncs of the same note.
    seen = set()
    deduped = []
    for nt in pool:
        sig = _title_signature(nt["title"])
        if sig in seen:
            continue
        seen.add(sig)
        deduped.append(nt)
    return deduped[:n]
