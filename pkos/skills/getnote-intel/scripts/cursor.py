#!/usr/bin/env python3
"""getnote-intel cursor — checkpoint-and-resume for long-running blogger/live polls."""
import sys, json, os, datetime
from pathlib import Path

def cursor_path():
    return Path.home() / "Obsidian" / "PKOS" / ".state" / "getnote-intel-state.yaml"

def load_cursor():
    """Returns {seen_blogger_posts, seen_lives, last_topic_id, last_blogger_idx, last_synced_at}."""
    import yaml
    p = cursor_path()
    if not p.exists():
        return {"seen_blogger_posts": [], "seen_lives": [],
                "last_topic_id": None, "last_blogger_idx": -1,
                "last_synced_at": None}
    with open(p) as f:
        data = yaml.safe_load(f) or {}
    return {
        "seen_blogger_posts": data.get("seen_blogger_posts", []),
        "seen_lives": data.get("seen_lives", []),
        "last_topic_id": data.get("last_topic_id"),
        "last_blogger_idx": int(data.get("last_blogger_idx", -1)),
        "last_synced_at": data.get("last_synced_at"),
    }

def save_cursor(state):
    """Atomic write — write to .tmp then rename."""
    import yaml
    p = cursor_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    with open(tmp, "w") as f:
        yaml.safe_dump({
            "seen_blogger_posts": list(state.get("seen_blogger_posts", []))[-500:],
            "seen_lives": list(state.get("seen_lives", []))[-500:],
            "last_topic_id": state.get("last_topic_id"),
            "last_blogger_idx": int(state.get("last_blogger_idx", -1)),
            "last_synced_at": state.get("last_synced_at") or datetime.datetime.now().isoformat(),
        }, f, allow_unicode=True)
    os.replace(tmp, p)

def mark_progress(topic_id, blogger_idx, post_ids=None, live_ids=None):
    """Update cursor after each blogger/live processed. Idempotent."""
    state = load_cursor()
    state["last_topic_id"] = topic_id
    state["last_blogger_idx"] = int(blogger_idx)
    if post_ids:
        seen = set(state["seen_blogger_posts"])
        seen.update(post_ids)
        state["seen_blogger_posts"] = list(seen)
    if live_ids:
        seen = set(state["seen_lives"])
        seen.update(live_ids)
        state["seen_lives"] = list(seen)
    state["last_synced_at"] = datetime.datetime.now().isoformat()
    save_cursor(state)
    return state

def resume_point():
    """Returns (topic_id, blogger_idx) where prior run stopped, or (None, -1) for fresh start."""
    state = load_cursor()
    return state.get("last_topic_id"), int(state.get("last_blogger_idx", -1))

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "show"
    if cmd == "show":
        print(json.dumps(load_cursor(), indent=2, default=str))
    elif cmd == "resume":
        tid, idx = resume_point()
        print(f"{tid}\t{idx}")
    elif cmd == "mark":
        topic_id = sys.argv[2]
        blogger_idx = int(sys.argv[3])
        mark_progress(topic_id, blogger_idx)
        print("ok")
    elif cmd == "reset":
        p = cursor_path()
        if p.exists(): p.unlink()
        print("cursor cleared")
