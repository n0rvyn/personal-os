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

PKOS_DEFAULT_DIRS = ["10-Knowledge", "20-Ideas", "50-References", "30-Projects"]


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
    """Get title: frontmatter aliases[0] / title / first H1 / filename."""
    fm = _parse_frontmatter(text)
    if isinstance(fm.get("aliases"), list) and fm["aliases"]:
        return fm["aliases"][0]
    if fm.get("title"):
        return fm["title"]
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return Path(fallback_path).stem


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
            notes.append({
                "path": str(md.relative_to(root)),
                "title": _extract_title(text, md),
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


def same_topic_past_notes(today_tags, vault_root, days_min=7, days_max=30, n=5,
                          today=None, notes=None):
    """Notes with ≥1 tag overlap with today_tags, created in [today-days_max, today-days_min].

    Used for the self-past-contrarian feature: 达芬奇 picks the one whose stance contradicts
    today's main argument. Orchestrator surfaces candidates; stance comparison stays with 达芬奇.

    Pass `today` (ISO date str) and `notes` for testing.
    """
    if notes is None:
        notes = load_pkos_notes(vault_root)
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
        key=lambda nt: (_tag_overlap(nt["tags"], today_tags), nt["created"]),
        reverse=True,
    )
    # Dedup by normalized title — the vault holds near-identical re-syncs of the same
    # note (e.g. "X的研究发现" vs "X研究发现"); collapse them so the writer sees variety.
    seen = set()
    deduped = []
    for nt in pool:
        norm = re.sub(r"\s+", "", nt["title"]).lower()
        if norm in seen:
            continue
        seen.add(norm)
        deduped.append(nt)
    return deduped[:n]
