"""Shared domain bucketing for PKOS vault notes.

Single source of truth for the keyword tables and classification logic used by:
  - the vault-retag pass (writes a domain tag onto untagged legacy notes)
  - podcast-prep cross_domain recall (buckets a note by domain at read time)

Two entry points:
  - classify_by_tags(tags)               — classify from frontmatter tags
  - classify_by_text(title, excerpt, parent_dir) — classify from note content +
                                            parent directory name, for untagged notes

Both return one of: philosophy / management / cognition / history / literature /
natural-science / tech / general.
"""
import re

# Domain keyword maps. Order matters: the FIRST matching domain wins. Non-tech
# domains come first so a note tagged both philosophy + ai buckets as philosophy.
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
        "心理学", "self-improvement", "自我提升", "学习方法",
    ],
    "history": [
        "历史", "history", "古代", "war", "civilization", "古典",
        "近代史", "帝国",
    ],
    "literature": [
        "文学", "literature", "小说", "fiction", "诗", "戏剧",
        "novel", "poetry", "drama", "俄罗斯文学", "陀思妥耶夫斯基", "卡拉马佐夫兄弟",
    ],
    "natural-science": [
        "科学", "science", "物理", "physics", "biology", "chemistry",
        "生物", "化学", "天文", "fitness", "health", "运动",
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

# Non-tech domain buckets, in cross-domain recall priority order.
CROSS_DOMAIN_PRIORITY = [
    "philosophy", "management", "cognition",
    "history", "literature", "natural-science",
]

# A parent directory name (slug) → domain. Migrated notes nest under a category
# directory; the directory name is a strong secondary signal for untagged notes.
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


def _kw_matches(kw, text):
    """True if keyword kw occurs in text (both lowercased).

    ASCII keywords require word boundaries — CJK chars and separators count as
    boundaries, so 'ai' matches 'ai编程' / 'ai-coding' but 'war' does NOT match
    'software'. CJK keywords of ≥2 chars match as plain substrings; single-char CJK
    keywords require the text to be exactly that char (avoids '道' matching '知道').
    """
    if not kw or not text:
        return False
    if kw.isascii():
        for m in re.finditer(re.escape(kw), text):
            before = text[m.start() - 1] if m.start() > 0 else ""
            after = text[m.end()] if m.end() < len(text) else ""
            before_ok = not (before and before.isascii() and before.isalnum())
            after_ok = not (after and after.isascii() and after.isalnum())
            if before_ok and after_ok:
                return True
        return False
    if len(kw) == 1:
        return kw == text
    return kw in text


def classify_by_tags(tags):
    """Return the domain bucket for a note from its frontmatter tags (list of str)."""
    if not tags:
        return "general"
    norm = [t.lower().strip() for t in tags if t and t.strip()]
    if not norm:
        return "general"
    for domain, kws in DOMAIN_KEYWORDS.items():
        for kw in kws:
            for tag in norm:
                if _kw_matches(kw.lower(), tag):
                    return domain
    for kw in TECH_KEYWORDS:
        for tag in norm:
            if _kw_matches(kw.lower(), tag):
                return "tech"
    return "general"


def classify_by_text(title, excerpt="", parent_dir=""):
    """Classify an untagged note from its title + leading excerpt + parent directory.

    Keyword scan runs over title + excerpt only (not full body) to limit false
    positives. If that yields nothing, the parent directory name is the fallback
    signal. Returns 'general' when neither resolves.
    """
    blob = f"{title or ''} {excerpt or ''}".lower()
    if blob.strip():
        for domain, kws in DOMAIN_KEYWORDS.items():
            for kw in kws:
                if _kw_matches(kw.lower(), blob):
                    return domain
        for kw in TECH_KEYWORDS:
            if _kw_matches(kw.lower(), blob):
                return "tech"
    if parent_dir:
        d = DIR_DOMAIN.get(parent_dir.strip().lower())
        if d:
            return d
    return "general"
