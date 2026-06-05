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
    "linux", "linux-sre", "macos", "windows", "docker", "k8s", "kubernetes",
    "git", "ansible", "database", "sql", "observability", "wordpress",
    "ocr", "certification", "cka", "computer-geometry", "obsidian",
]

# A note's parent directory name (slug) → domain. For an untagged migrated note the
# directory it nests under is a strong secondary signal — used by the content-scan
# fallback in classify_note_domain.
DIR_DOMAIN = {
    "linux-sre": "tech", "python": "tech", "macos": "tech", "windows-etc": "tech",
    "git": "tech", "ansible": "tech", "docker-k8s": "tech", "database": "tech",
    "observability": "tech", "wordpress-blog-caddy": "tech", "ocr": "tech",
    "certification": "tech", "computer-geometry": "tech", "cloud-vmware": "tech",
    "ai-llm": "tech", "obsidian-markdown": "tech", "draft-by-ai": "tech",
    "认知科学": "cognition", "self-improvement": "cognition",
    "卡拉马佐夫兄弟": "literature",
    "fitness-health-food": "natural-science",
}

PKOS_DEFAULT_DIRS = ["10-Knowledge", "20-Ideas", "50-References", "30-Projects",
                     "90-Productions/Podcasts"]

# Recall directories, per the vault directory contract (KL-4):
# - self_past reads the user's viewpoints (20-Ideas/观点心得) + past on-record stances
#   (90-Productions/Podcasts episode archives).
# - cross_domain reads the user's knowledge + ideas, bucketed by domain.
SELF_PAST_DIRS = ("20-Ideas/观点心得/", "90-Productions/Podcasts/")
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


def _classify_by_text(title, excerpt="", parent_dir=""):
    """Domain from a note's title + leading excerpt, with the parent directory name
    as a last-resort signal. The keyword scan runs over title + excerpt only (not the
    full body) to limit false positives. Returns 'general' when nothing resolves."""
    blob = f"{title or ''} {excerpt or ''}".lower()
    if blob.strip():
        for domain, kws in DOMAIN_KEYWORDS.items():
            for kw in kws:
                if _kw_matches_tag(kw.lower(), blob):
                    return domain
        for kw in TECH_KEYWORDS:
            if _kw_matches_tag(kw.lower(), blob):
                return "tech"
    if parent_dir:
        d = DIR_DOMAIN.get(parent_dir.strip().lower())
        if d:
            return d
    return "general"


def classify_note_domain(tags, title="", excerpt="", parent_dir=""):
    """Return the domain bucket for a note.

    Returns one of: philosophy / management / cognition / history / literature /
    natural-science / tech / general.

    Tags first: non-tech domains, then tech. If the tags do not classify (or there
    are none) and content/parent-dir signals are supplied, fall back to a title +
    excerpt keyword scan, then the parent directory name. The fallback is what keeps
    a note with no domain tag (≈1/3 of the legacy vault) from silently falling to
    'general' and dropping out of cross-domain recall.
    """
    tags_norm = [t.lower().strip() for t in (tags or []) if t and t.strip()]
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
    if title or excerpt or parent_dir:
        return _classify_by_text(title, excerpt, parent_dir)
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
            excerpt = _extract_excerpt(text)
            notes.append({
                "path": str(md.relative_to(root)),
                "title": title,
                "tags": tags,
                "created": created[:10],
                "domain": classify_note_domain(tags, title=title, excerpt=excerpt,
                                               parent_dir=md.parent.name),
                "excerpt": excerpt,
            })
    return notes


def _tag_overlap(a, b):
    """Count of lowercase tag intersection."""
    sa = {t.lower().strip() for t in a if t}
    sb = {t.lower().strip() for t in b if t}
    return len(sa & sb)


# Non-tech domain buckets, in cross-domain recall priority order.
CROSS_DOMAIN_PRIORITY = [
    "philosophy", "management", "cognition",
    "history", "literature", "natural-science",
]


def cross_domain_candidates(today_tags, vault_root, n=5, notes=None,
                            recent_pool=8, seed=None, force_domain=None):
    """Return up to n notes from NON-tech domains.

    Default: one note per domain bucket, in domain priority order — a spread across
    domains. For each non-tech domain, prefer notes matching ≥1 today_tag; from that
    set (or the whole domain if no overlap), take the `recent_pool` most-recent notes and
    pick one at random. Random (not always-newest) so consecutive episodes on similar
    topics do not keep surfacing the identical note — a cooldown against mechanical repeat.
    Pass `seed` for reproducibility; seed=None → fresh pick each run.

    `force_domain`: when set, return up to n notes from ONLY that one bucket — used by
    parallel-N brief perturbation to pin each path to a distinct domain so the candidates
    genuinely diverge. Raises ValueError on an unknown domain.

    Returns notes ordered by domain priority (philosophy → management → cognition →
    history → literature → natural-science). Pass `notes` to skip filesystem walk.
    """
    if notes is None:
        notes = load_pkos_notes(vault_root)
    # KL-4: cross_domain reads only the contract's cross-domain directories.
    notes = [nt for nt in notes if nt["path"].startswith(CROSS_DOMAIN_DIRS)]
    domains_priority = CROSS_DOMAIN_PRIORITY
    if force_domain is not None:
        if force_domain not in CROSS_DOMAIN_PRIORITY:
            raise ValueError(
                f"unknown cross-domain bucket {force_domain!r}; "
                f"valid: {CROSS_DOMAIN_PRIORITY}")
        domains_priority = [force_domain]
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
        head = pool[:recent_pool]
        if force_domain is not None:
            # Forced single bucket → return up to n distinct notes from it.
            picked.extend(rng.sample(head, min(n, len(head))))
        else:
            picked.append(rng.choice(head))
        if len(picked) >= n:
            break
    return picked[:n]


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
    # KL-4: self_past reads only 20-Ideas/观点心得 (viewpoints) + 90-Productions/Podcasts (past
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


# Markers in a note's title + excerpt that signal an OPEN question vs. a closed
# stance. Used by rank_open_questions to elevate the brief's open-question spine.
# CJK question mark is a strong signal; explicit 开放/未定/未决 flag intent.
_OPEN_QUESTION_MARKERS = (
    "？", "?", "开放问题", "开放", "未定", "未决", "未想清楚",
    "open question", "unresolved", "tbd", "open", "undecided",
)


def _openness_score(note: dict) -> int:
    """Count open-question markers in title + excerpt. 0 = looks like a closed
    stance, higher = more open. Empty fields contribute 0."""
    blob = f"{note.get('title', '')} {note.get('excerpt', '')}".lower()
    return sum(1 for m in _OPEN_QUESTION_MARKERS if m.lower() in blob)


def rank_open_questions(notes: list, today_tags: list, n: int = 5) -> list:
    """Rank vault notes as the evening brief's open-questions spine.

    Sort key (in order):
      1. openness_score DESC — open questions beat closed stances
      2. tag overlap with today_tags DESC — relevant beats tangential
      3. created DESC — newer first when scores tie

    Returns the top-n. Pure function on caller-passed data (no IO, no DB).
    """
    today_tag_set = {t.lower().strip() for t in today_tags if t}
    def _overlap(note_tags):
        return sum(1 for t in (note_tags or []) if t and t.lower().strip() in today_tag_set)
    ranked = sorted(
        notes,
        key=lambda nt: (
            _openness_score(nt),
            _overlap(nt.get("tags", [])),
            nt.get("created", ""),
        ),
        reverse=True,
    )
    return ranked[:n]


def fresh_today_notes(notes: list, today: str, n: int = 5) -> list:
    """Notes that newly entered the vault TODAY — evening brief's "今日新进 vault 的笔记".

    D-025 single-funnel: pure SELECTION over already-loaded vault notes (no new source,
    no Adam events, no direct API). Filters `notes` to those whose created date (first 10
    chars, YYYY-MM-DD) equals `today`, ranks by recency then tag count, returns up to n.

    Pure function on caller-passed data (no IO, no DB).
    """
    fresh = [nt for nt in notes if str(nt.get("created", ""))[:10] == today]
    fresh.sort(
        key=lambda nt: (str(nt.get("created", "")), len(nt.get("tags", []))),
        reverse=True,
    )
    return fresh[:n]
