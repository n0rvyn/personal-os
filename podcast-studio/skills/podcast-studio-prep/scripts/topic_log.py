"""Stateful helper for topic_log.yaml. No PyYAML dependency."""
import json, os
from datetime import date, timedelta
from pathlib import Path

def _parse_yaml(text: str) -> dict:
    """Minimal YAML parser: handles the flat 'episodes:' + nested list-of-dicts shape we use.
    Format is intentionally constrained — if a future schema needs more, switch to ruamel.yaml.
    """
    # Strategy: convert the constrained YAML to JSON, then json.loads. Acceptable for our schema.
    # If text is empty or whitespace → empty doc.
    if not text.strip():
        return {"episodes": []}
    # Use a tiny line-by-line state machine; documented in skill README.
    result = {"episodes": []}
    current_ep = None
    current_topic = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        # Strip leading spaces; track indent level by spaces
        indent = len(line) - len(line.lstrip())
        stripped = line.strip()
        if indent == 0 and stripped == "episodes:":
            continue
        if indent == 2 and stripped.startswith("- "):
            # New episode entry; following 'key: value' at indent 4 are its fields
            current_ep = {}
            result["episodes"].append(current_ep)
            # First field on same line: "- date: 2026-05-19"
            kv = stripped[2:].split(":", 1)
            if len(kv) == 2:
                current_ep[kv[0].strip()] = kv[1].strip().strip('"')
        elif indent == 4 and ":" in stripped and current_ep is not None:
            k, v = stripped.split(":", 1)
            k, v = k.strip(), v.strip()
            if v == "":
                current_ep[k] = []  # Next lines populate this list
            else:
                current_ep[k] = v.strip('"')
        elif indent == 6 and stripped.startswith("- "):
            # Topic entry under topics:; format "- tag: x"
            current_topic = {}
            if "topics" in (current_ep or {}):
                if not isinstance(current_ep["topics"], list):
                    current_ep["topics"] = []
                current_ep["topics"].append(current_topic)
            kv = stripped[2:].split(":", 1)
            if len(kv) == 2:
                current_topic[kv[0].strip()] = kv[1].strip().strip('"')
        elif indent == 8 and ":" in stripped and current_topic is not None:
            k, v = stripped.split(":", 1)
            current_topic[k.strip()] = v.strip().strip('"')
    return result

def _dump_yaml(data: dict) -> str:
    """Inverse of _parse_yaml. Constrained format."""
    lines = ["episodes:"]
    for ep in data.get("episodes", []):
        lines.append(f"  - date: {ep.get('date', '')}")
        if "topics" in ep:
            lines.append("    topics:")
            for t in ep["topics"]:
                lines.append(f"      - tag: {t.get('tag', '')}")
                for k, v in t.items():
                    if k != "tag":
                        lines.append(f"        {k}: {v}")
        # Other top-level fields:
        for k, v in ep.items():
            if k not in ("date", "topics"):
                lines.append(f"    {k}: {v}")
    return "\n".join(lines) + "\n"

def load_topic_log(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {"episodes": []}
    return _parse_yaml(p.read_text(encoding="utf-8"))

def save_topic_log(path, data: dict) -> None:
    p = Path(path)
    # Phase 1 / Task 4-impl: ensure parent dir exists (first-run finalize would
    # otherwise FileNotFoundError when the podcast-studio config's
    # vault.output_dir/podcast-prep/ doesn't exist yet).
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_dump_yaml(data), encoding="utf-8")

def append_episode(path, ep_date: str, topics: list) -> None:
    data = load_topic_log(path)
    data["episodes"].append({"date": ep_date, "topics": topics})
    save_topic_log(path, data)

def recent_topic_tags(path, today: str, window_days: int) -> list:
    """Return list of topic_tags from episodes within the past `window_days` (inclusive of today)."""
    data = load_topic_log(path)
    today_d = date.fromisoformat(today)
    cutoff = today_d - timedelta(days=window_days)
    tags = []
    for ep in data["episodes"]:
        try:
            ep_d = date.fromisoformat(ep.get("date", ""))
        except ValueError:
            continue
        if cutoff <= ep_d <= today_d:
            for t in ep.get("topics", []):
                if "tag" in t:
                    tags.append(t["tag"])
    return tags
