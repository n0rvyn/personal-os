"""Podcast-prep orchestrator. Composes 4 helpers (topic_log + angle_slots + minhash_check + contrarian_pull) into check + finalize subcommands. PKOS note is caller-provided per DP-001 A."""
import argparse, json, os, sys
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

# Indirection point for test mocking (contrarian-source random pick)
def _contrarian_pull(seed=None, exclude_categories=None):
    return pick_contrarian_source(seed=seed, exclude_categories=exclude_categories)

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
              pkos_note: dict = None, seed: int = None) -> dict:
    """Build the structured brief consumed by the writer agent.

    DP-001 A: pkos_note is supplied by the caller (达芬奇 via pkos:serendipity SKILL).
    The orchestrator validates it's present but does NOT pull PKOS itself. If missing,
    returns an error brief that 达芬奇 must remediate (re-invoke pkos:serendipity + retry).
    """
    if not pkos_note or not isinstance(pkos_note, dict) or not pkos_note.get("id"):
        return {
            "error": "pkos_note required — invoke pkos:serendipity SKILL and pass the resulting {id, title, excerpt} as --pkos-note input. See podcast-prep SKILL.md for caller protocol.",
            "approved_topics": [],
            "pkos_note": None,
            "contrarian_source": None,
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
    brief = {
        "approved_topics": approved,
        "pkos_note": pkos_note,  # caller-provided, propagated verbatim
        "contrarian_source": _contrarian_pull(seed=seed),
        "generated_at": f"{today}T00:00:00Z",
    }
    return brief

def run_finalize(script_path: str, topic_log_path: str, today: str,
                 approved_topics: list, script_archive_dir: str = None,
                 threshold: float = 0.15) -> dict:
    """MinHash-dedupe the script against the past-7-day script archive.
    On accept: append the episode to topic_log. On retry: return without state change."""
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
    return {"action": "accept", "jaccard": round(max_sim, 4),
            "topics_appended": len(approved_topics)}

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
    fin = sub.add_parser("finalize")
    fin.add_argument("--script", required=True)
    fin.add_argument("--topic-log", required=True)
    fin.add_argument("--date", required=True)
    fin.add_argument("--approved-topics", required=True, help="JSON array of {topic_tag, required_angle}")
    fin.add_argument("--script-archive-dir", default=None)
    args = parser.parse_args()
    if args.cmd == "check":
        candidates = json.loads(args.candidates)
        pkos_note = json.loads(args.pkos_note) if args.pkos_note else None
        brief = run_check(candidates, args.topic_log, args.date,
                          pkos_note=pkos_note, seed=args.seed)
        print(json.dumps(brief, ensure_ascii=False, indent=2))
    elif args.cmd == "finalize":
        approved = json.loads(args.approved_topics)
        result = run_finalize(args.script, args.topic_log, args.date, approved,
                              script_archive_dir=args.script_archive_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
