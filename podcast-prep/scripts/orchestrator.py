"""Podcast-prep orchestrator. Composes helpers (topic_log + angle_slots + minhash_check
+ contrarian_pull + cross_domain) into check + finalize subcommands. PKOS note is
caller-provided per DP-001 A.

The `check` brief carries three insight-density fields beyond the base topic gate:
- cross_domain_candidates: PKOS notes from NON-tech domains, to seed cross-domain synthesis
- self_past_candidates: past notes (7-30d) on a similar topic, for self-contrarian dialogue
- named_concept_prompt: a directive nudging the writer to name an emergent pattern
"""
import argparse, json, os, re, sys
from datetime import date
from pathlib import Path

# Helper imports (relative to scripts/)
# DP-001 A: NO pkos_pull import — PKOS metadata is supplied by 达芬奇 invoking
# pkos:serendipity SKILL and passing the resulting note as `--pkos-note` argument.
sys.path.insert(0, str(Path(__file__).parent))
from topic_log import load_topic_log, recent_topic_tags, append_episode
from angle_slots import DEFAULT_ANGLES, pick_unused_angle
from minhash_check import max_jaccard_against
from contrarian_pull import pick_contrarian_source
from cross_domain import load_pkos_notes, cross_domain_candidates, same_topic_past_notes

NAMED_CONCEPT_PROMPT = (
    "命名任务：通读今天的素材，找出其中一个还没有名字的新现象、新模式或新张力，"
    "给它起一个 3-5 字、有画面感、可复用的名字（参考范例「信息平原」）。"
    "在脚本里用一整段做命名仪式：先描述现象 → 再亮出名字 → 再说明为什么这个名字贴切。"
    "如果今天的素材确实没有值得命名的新现象，明确说明原因，不要硬凑。"
)

# Indirection point for test mocking (contrarian-source random pick)
def _contrarian_pull(seed=None, exclude_categories=None):
    return pick_contrarian_source(seed=seed, exclude_categories=exclude_categories)


def _resolve_vault_root(explicit=None):
    """Resolve the PKOS vault root. Priority: explicit arg > PKOS_VAULT_ROOT env >
    personal-os.yaml (pkos_root key, else exchange_dir parent) > ~/Obsidian/PKOS default."""
    if explicit:
        return explicit
    env = os.environ.get("PKOS_VAULT_ROOT")
    if env:
        return env
    cfg = Path(os.path.expanduser("~/.claude/personal-os.yaml"))
    if cfg.exists():
        try:
            text = cfg.read_text(encoding="utf-8")
        except OSError:
            text = ""
        m = re.search(r"^pkos_root:\s*(.+)$", text, re.MULTILINE)
        if m:
            return m.group(1).strip().strip("\"'")
        m = re.search(r"^exchange_dir:\s*(.+)$", text, re.MULTILINE)
        if m:
            ex = Path(os.path.expanduser(m.group(1).strip().strip("\"'")))
            return str(ex.parent) if ex.name == ".exchange" else str(ex)
    return "~/Obsidian/PKOS"


def _expand_topic_tags(approved_topics):
    """Flatten approved topic_tags plus their hyphen-split tokens for looser tag overlap.
    e.g. 'ai-agents' → {'ai-agents', 'ai', 'agents'}."""
    out = set()
    for t in approved_topics:
        tag = t.get("topic_tag", "")
        if not tag:
            continue
        out.add(tag)
        for part in tag.split("-"):
            if len(part) >= 2:
                out.add(part)
    return list(out)

def novelty_score(candidate_tag: str, topic_log_path: str, today: str, window_days: int = 7) -> float:
    """1 - matching_days/7 where matching_days = count of past N-day episodes whose topic_tag
    exactly equals candidate_tag. Per design D-002 Option A (chosen by user)."""
    recent_tags = recent_topic_tags(topic_log_path, today=today, window_days=window_days)
    matching = sum(1 for t in recent_tags if t == candidate_tag)
    return 1 - (matching / window_days)

def used_angles_for_topic(topic_tag: str, topic_log_path: str, today: str, window_days: int = 14) -> list:
    """Return the list of angles previously used for this topic_tag within window_days."""
    data = load_topic_log(topic_log_path)
    angles = []
    cutoff_d = date.fromisoformat(today)
    from datetime import timedelta
    cutoff = cutoff_d - timedelta(days=window_days)
    for ep in data["episodes"]:
        try:
            ep_d = date.fromisoformat(ep.get("date", ""))
        except ValueError:
            continue
        if not (cutoff <= ep_d <= cutoff_d):
            continue
        for t in ep.get("topics", []):
            if t.get("tag") == topic_tag and "angle" in t:
                angles.append(t["angle"])
    return angles

def run_check(candidates: list, topic_log_path: str, today: str,
              pkos_note: dict = None, seed: int = None,
              vault_root: str = None) -> dict:
    """Build the structured brief consumed by the writer agent.

    DP-001 A: pkos_note is supplied by the caller (达芬奇 via pkos:serendipity SKILL).
    The orchestrator validates it's present but does NOT pull PKOS itself. If missing,
    returns an error brief that 达芬奇 must remediate (re-invoke pkos:serendipity + retry).

    The brief also carries cross_domain_candidates + self_past_candidates (pulled from the
    PKOS vault) and a named_concept_prompt directive. vault_root is resolved from
    personal-os.yaml when not given explicitly.
    """
    if not pkos_note or not isinstance(pkos_note, dict) or not pkos_note.get("id"):
        return {
            "error": "pkos_note required — invoke pkos:serendipity SKILL and pass the resulting {id, title, excerpt} as --pkos-note input. See podcast-prep SKILL.md for caller protocol.",
            "approved_topics": [],
            "pkos_note": None,
            "contrarian_source": None,
            "cross_domain_candidates": [],
            "self_past_candidates": [],
            "named_concept_prompt": NAMED_CONCEPT_PROMPT,
            "generated_at": f"{today}T00:00:00Z",
        }
    approved = []
    for cand in candidates:
        score = novelty_score(cand, topic_log_path, today)
        if score < 0.3:
            continue  # drop
        used = used_angles_for_topic(cand, topic_log_path, today)
        if score > 0.7:
            required_angle = DEFAULT_ANGLES[0]  # free; default to first
        else:
            required_angle = pick_unused_angle(used)
        approved.append({
            "topic_tag": cand,
            "novelty_score": round(score, 3),
            "required_angle": required_angle,
        })
    # Single vault walk shared by both cross-domain and self-past recall.
    resolved_root = _resolve_vault_root(vault_root)
    today_tags = _expand_topic_tags(approved)
    notes = load_pkos_notes(resolved_root) if today_tags else []
    brief = {
        "approved_topics": approved,
        "pkos_note": pkos_note,  # caller-provided, propagated verbatim
        "contrarian_source": _contrarian_pull(seed=seed),
        "cross_domain_candidates": cross_domain_candidates(
            today_tags, resolved_root, n=5, notes=notes, seed=seed),
        "self_past_candidates": same_topic_past_notes(
            today_tags, resolved_root, today=today, n=5, notes=notes),
        "named_concept_prompt": NAMED_CONCEPT_PROMPT,
        "generated_at": f"{today}T00:00:00Z",
    }
    return brief

def _archive_episode(script_text: str, today: str, approved_topics: list,
                     archive_dir: str, named_concept: str = None) -> str:
    """Archive a finished episode to the vault as a `type: podcast` note (KL-3).

    One note per episode at {archive_dir}/{today}-{slug}.md. Recall (self_past) reads
    this directory — see the vault directory contract. Returns the written path.
    """
    archive_dir = os.path.expanduser(archive_dir)
    os.makedirs(archive_dir, exist_ok=True)
    # Slug from the script's first H1, else the first topic_tag.
    slug = ""
    for line in script_text.splitlines():
        if line.startswith("# "):
            slug = line[2:].strip()
            break
    if not slug and approved_topics:
        slug = approved_topics[0].get("topic_tag", "")
    slug = re.sub(r"\s+", "-", slug)[:48]
    slug = "".join(c for c in slug if c.isalnum() or c in "-_") or "episode"
    tags = [t.get("topic_tag", "") for t in approved_topics if t.get("topic_tag")]
    fm = [
        "---", "type: podcast", f"created: {today}",
        "tags: [" + ", ".join(tags) + "]",
    ]
    if named_concept:
        fm.append(f"named_concept: {named_concept}")
    fm += ["status: archived", "---", ""]
    path = os.path.join(archive_dir, f"{today}-{slug}.md")
    Path(path).write_text("\n".join(fm) + script_text, encoding="utf-8")
    return path


def run_finalize(script_path: str, topic_log_path: str, today: str,
                 approved_topics: list, script_archive_dir: str = None,
                 threshold: float = 0.15, archive_dir: str = None,
                 named_concept: str = None) -> dict:
    """MinHash-dedupe the script against the past-7-day script archive.
    On accept: append the episode to topic_log, and (if archive_dir is given) archive
    the episode to the vault 90-Podcasts directory. On retry: return without state change."""
    script = Path(script_path).read_text(encoding="utf-8")
    corpus = []
    if script_archive_dir and Path(script_archive_dir).exists():
        # Collect scripts from past 7 days; filenames assumed YYYY-MM-DD.md
        today_d = date.fromisoformat(today)
        from datetime import timedelta
        for d_offset in range(1, 8):
            d_str = (today_d - timedelta(days=d_offset)).isoformat()
            p = Path(script_archive_dir) / f"{d_str}.md"
            if p.exists():
                corpus.append(p.read_text(encoding="utf-8"))
    max_sim = max_jaccard_against(script, corpus)
    if max_sim >= threshold:
        return {"action": "retry", "jaccard": round(max_sim, 4),
                "reason": f"4-gram Jaccard similarity {max_sim:.4f} >= threshold {threshold}"}
    # Accept: write topic_log
    append_episode(topic_log_path, today, [
        {"tag": t["topic_tag"], "angle": t["required_angle"]}
        for t in approved_topics
    ])
    result = {"action": "accept", "jaccard": round(max_sim, 4),
              "topics_appended": len(approved_topics)}
    if archive_dir:
        result["archived"] = _archive_episode(
            script, today, approved_topics, archive_dir, named_concept)
    return result

def main():
    parser = argparse.ArgumentParser(description="Podcast-prep orchestrator")
    sub = parser.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check")
    chk.add_argument("--candidates", required=True, help="JSON array of candidate topic tags")
    chk.add_argument("--date", required=True)
    chk.add_argument("--topic-log", required=True)
    chk.add_argument("--pkos-note", default=None,
                     help="REQUIRED. JSON object {id, title, excerpt} from a prior pkos:serendipity invocation. DP-001 A: caller (达芬奇) supplies this.")
    chk.add_argument("--seed", type=int, default=None)
    chk.add_argument("--vault-root", default=None,
                     help="PKOS vault root. Defaults to personal-os.yaml (pkos_root or exchange_dir parent).")
    fin = sub.add_parser("finalize")
    fin.add_argument("--script", required=True)
    fin.add_argument("--topic-log", required=True)
    fin.add_argument("--date", required=True)
    fin.add_argument("--approved-topics", required=True, help="JSON array of {topic_tag, required_angle}")
    fin.add_argument("--script-archive-dir", default=None)
    fin.add_argument("--archive-dir", default=None,
                     help="Vault 90-Podcasts dir. When set, an accepted episode is archived there as a type:podcast note.")
    fin.add_argument("--named-concept", default=None,
                     help="Optional — the concept this episode named, written to the archive note frontmatter.")
    args = parser.parse_args()
    if args.cmd == "check":
        candidates = json.loads(args.candidates)
        pkos_note = json.loads(args.pkos_note) if args.pkos_note else None
        brief = run_check(candidates, args.topic_log, args.date,
                          pkos_note=pkos_note, seed=args.seed,
                          vault_root=args.vault_root)
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    elif args.cmd == "finalize":
        approved = json.loads(args.approved_topics)
        result = run_finalize(args.script, args.topic_log, args.date, approved,
                              script_archive_dir=args.script_archive_dir,
                              archive_dir=args.archive_dir,
                              named_concept=args.named_concept)
        print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
