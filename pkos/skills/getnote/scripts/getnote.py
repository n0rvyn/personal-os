#!/usr/bin/env python3
"""
GetNote JSON parsing helpers for wrapped official OpenAPI responses.
"""

import json
import os
import re
import sys


def load_payload(text):
    data = json.loads(text)
    return data.get("data", data) if isinstance(data, dict) else data


def first_value(obj, *keys, default=""):
    for key in keys:
        value = obj.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _as_list(payload, *keys):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _loads_collection(text, *keys):
    return _as_list(load_payload(text), *keys)


def _string_dict(obj):
    return {key: "" if value is None else str(value) for key, value in obj.items()}


def _tags(note):
    return [tag.get("name") for tag in note.get("tags", []) if isinstance(tag, dict) and tag.get("name")]


def filter_notes_by_tag(notes_json, exclude_tag):
    notes = _loads_collection(notes_json, "notes", "results")
    filtered = []
    for note in notes:
        if exclude_tag not in _tags(note):
            filtered.append(note)
    return filtered


def format_note_summary(note):
    note_id = first_value(note, "note_id", "id")
    return {
        "note_id": note_id,
        "id": note_id,
        "title": note.get("title", "Untitled"),
        "note_type": note.get("note_type", "plain_text"),
        "tags": _tags(note),
        "updated_at": note.get("updated_at", note.get("created_at", "")),
    }


def parse_recall_results(recall_json, include_external=False):
    results = _loads_collection(recall_json, "results")
    if not include_external:
        results = [result for result in results if result.get("note_type") in ("NOTE", "FILE")]
    return results


def parse_topics(topics_json):
    topics = _loads_collection(topics_json, "topics")
    parsed = []
    for topic in topics:
        stats = topic.get("stats", {}) if isinstance(topic.get("stats"), dict) else {}
        parsed.append(
            {
                "topic_id": first_value(topic, "topic_id", "id"),
                "id": first_value(topic, "topic_id", "id"),
                "name": topic.get("name", ""),
                "description": topic.get("description", ""),
                "note_count": stats.get("note_count", topic.get("note_count", 0)),
            }
        )
    return parsed


def parse_bloggers(bloggers_json):
    bloggers = _loads_collection(bloggers_json, "bloggers", "follows")
    parsed = []
    for blogger in bloggers:
        parsed.append(
            {
                "follow_id": first_value(blogger, "follow_id", "id"),
                "account_name": first_value(blogger, "account_name", "name"),
                "account_icon": first_value(blogger, "account_icon", "avatar"),
                "notes_count": blogger.get("notes_count", blogger.get("following_count", 0)),
            }
        )
    return parsed


def parse_blogger_contents(contents_json):
    contents = _loads_collection(contents_json, "contents", "posts")
    parsed = []
    for content in contents:
        parsed.append(
            {
                "post_id_alias": first_value(content, "post_id_alias", "post_id", "id"),
                "post_title": first_value(content, "post_title", "title"),
                "post_summary": first_value(content, "post_summary", "summary", "content"),
                "post_media_text": first_value(content, "post_media_text", "content"),
                "post_create_time": first_value(content, "post_create_time", "created_at"),
            }
        )
    return parsed


def parse_lives(lives_json):
    payload = load_payload(lives_json)
    lives = _as_list(payload, "lives")
    if not lives and isinstance(payload, dict):
        lives = [payload]
    parsed = []
    for live in lives:
        parsed.append(
            {
                "live_id": first_value(live, "live_id", "id"),
                "name": first_value(live, "name"),
                "status": first_value(live, "status"),
                "follow_time": first_value(live, "follow_time", "created_at"),
                "post_title": first_value(live, "post_title", "title"),
                "post_summary": first_value(live, "post_summary", "summary", "ai_summary"),
                "post_media_text": first_value(live, "post_media_text", "content"),
            }
        )
    return parsed


def parse_upload_config(config_json):
    payload = load_payload(config_json)
    return {
        "upload_url": first_value(payload, "upload_url", "host"),
        "policy": first_value(payload, "policy"),
        "signature": first_value(payload, "signature"),
        "host": first_value(payload, "host"),
    }


def parse_upload_token(token_json):
    payload = load_payload(token_json)
    if isinstance(payload, dict) and isinstance(payload.get("tokens"), list):
        if not payload["tokens"]:
            raise ValueError("upload token response contains no tokens")
        token = payload["tokens"][0]
    elif isinstance(payload, dict):
        token = payload
    else:
        raise ValueError("upload token response has unsupported shape")
    if not isinstance(token, dict) or not token:
        raise ValueError("upload token response contains no usable token")
    return _string_dict(token)


def parse_quota(quota_json):
    payload = load_payload(quota_json)
    return {
        "used": payload.get("used", 0),
        "limit": payload.get("limit", 0),
        "reset_at": payload.get("reset_at", ""),
        "remaining": payload.get("remaining", 0),
    }


def parse_topic_notes(notes_json):
    notes = _loads_collection(notes_json, "notes")
    return [
        {
            "note_id": first_value(note, "note_id", "id"),
            "id": first_value(note, "note_id", "id"),
            "title": note.get("title", ""),
            "note_type": note.get("note_type", "plain_text"),
            "updated_at": note.get("updated_at", note.get("created_at", "")),
        }
        for note in notes
    ]


def parse_save_response(save_json):
    payload = load_payload(save_json)
    if isinstance(payload, dict):
        note = payload.get("note") if isinstance(payload.get("note"), dict) else {}
        return first_value(payload, "note_id", "id", default=first_value(note, "note_id", "id"))
    return ""


def parse_note_tasks(tasks_json):
    payload = load_payload(tasks_json)
    tasks = _as_list(payload, "tasks")
    return [
        {
            "task_id": first_value(task, "task_id", "id"),
            "status": first_value(task, "status"),
            "progress": first_value(task, "progress"),
            "note_id": first_value(task, "note_id"),
        }
        for task in tasks
    ]


def parse_note_detail(detail_json):
    payload = load_payload(detail_json)
    note = payload.get("note", payload) if isinstance(payload, dict) else {}
    audio = note.get("audio", {}) if isinstance(note.get("audio"), dict) else {}
    web_page = note.get("web_page", {}) if isinstance(note.get("web_page"), dict) else {}
    return {
        "note_id": first_value(note, "note_id", "id"),
        "title": first_value(note, "title", default="Untitled"),
        "note_type": first_value(note, "note_type", default="plain_text"),
        "content": first_value(note, "content"),
        "audio_original": first_value(audio, "original"),
        "audio_transcription": first_value(audio, "transcription", "content"),
        "web_page_content": first_value(web_page, "content"),
        "updated_at": first_value(note, "updated_at", "created_at"),
    }


def write_notes_to_obsidian(notes, vault_path, note_type="reference", source="getnote"):
    for note in notes:
        note_id = first_value(note, "note_id", "id", default="untitled")
        slug = (note.get("title") or note_id or "untitled")[:40].lower()
        slug = "".join(char if char.isalnum() or char in "-_" else "-" for char in slug)
        note_path = os.path.join(vault_path, f"getnote-{note_type}-{slug}.md")
        os.makedirs(os.path.dirname(note_path), exist_ok=True)
        with open(note_path, "w", encoding="utf-8") as handle:
            handle.write(
                f"""---
type: {note_type}
source: {source}
created: {note.get('updated_at', note.get('created_at', ''))}
tags: [getnote]
quality: 0
citations: 0
related: []
---

# {note.get('title', 'Untitled')}

{note.get('content', note.get('description', ''))}
"""
            )
        print(f"Wrote: {note_path}")
    return len(notes)


def _gn_slugify(text, maxlen=48):
    """Filename-safe slug. Keeps CJK (isalnum() is true for CJK in py3)."""
    s = re.sub(r"\s+", "-", (text or "").strip())
    s = "".join(c for c in s if c.isalnum() or c in "-_")
    return s[:maxlen] or "untitled"


def _gn_tag_names(note):
    """getnote tags are [{id,name,type}]; return plain name list."""
    out = []
    for t in note.get("tags") or []:
        if isinstance(t, dict) and t.get("name"):
            out.append(str(t["name"]))
        elif isinstance(t, str) and t:
            out.append(t)
    return out


def split_getnote_note(note):
    """Deterministically split one getnote note into Obsidian-bound parts.

    Per the vault directory contract:
    - `ref_content` (the 摘抄 / quoted excerpt)        -> type=reference -> 50-References/
    - `content` on a `ref`/`note`/`plain_text` note    -> the user's 心得 -> type=idea ->
                                                          20-Ideas/观点心得/
    - `content` on a `link`/`img` note (an AI 智能总结) -> type=reference -> 50-References/

    A note carrying both an excerpt and the user's reflection yields TWO parts.
    Returns a list of dicts: {kind, type, subdir, body}. kind is 'reference' or 'idea'.
    """
    note_type = (note.get("note_type") or "").strip()
    content = (note.get("content") or "").strip()
    ref_content = (note.get("ref_content") or "").strip()
    parts = []
    if ref_content:
        parts.append({"kind": "reference", "type": "reference",
                      "subdir": "50-References", "body": ref_content})
    if content:
        if note_type in ("link", "img_text", "img"):
            # content is GetNote's AI 智能总结 of external material — still reference.
            parts.append({"kind": "reference", "type": "reference",
                          "subdir": "50-References", "body": content})
        else:
            # ref / plain_text / note: content is the user's own reflection (心得).
            parts.append({"kind": "idea", "type": "idea",
                          "subdir": "20-Ideas/观点心得", "body": content})
    return parts


def _gn_frontmatter(note_type, created, tags, related):
    rel = "[" + ", ".join(f'"{r}"' for r in related) + "]" if related else "[]"
    tag_str = "[" + ", ".join(tags) + "]" if tags else "[getnote]"
    return (f"---\ntype: {note_type}\nsource: getnote\ncreated: {created}\n"
            f"tags: {tag_str}\nquality: 0\ncitations: 0\nrelated: {rel}\n"
            f"status: seed\n---\n")


def write_getnote_split(notes, vault_root):
    """Split each getnote note and write the parts to their contract directories,
    cross-linking the reference and idea siblings when a note yields both.
    Returns (counts {reference, idea}, processed_ids) — processed_ids lists the
    note_ids that actually produced at least one written part, so the caller only
    marks those as synced (a note with an empty async summary produces nothing and
    must stay in the queue for a later run)."""
    vault_root = os.path.expanduser(vault_root)
    counts = {"reference": 0, "idea": 0}
    processed_ids = []
    for note in notes:
        parts = split_getnote_note(note)
        if not parts:
            continue
        nid = first_value(note, "note_id", "id")
        if nid:
            processed_ids.append(nid)
        created = str(note.get("created_at") or note.get("updated_at") or "")[:10]
        tags = _gn_tag_names(note)
        title = (note.get("title") or "").strip()
        # Plan file paths first so siblings can cross-link.
        planned = []
        for idx, part in enumerate(parts):
            base = title or part["body"][:24]
            slug = _gn_slugify(base)
            if len(parts) > 1:
                slug = f"{slug}-{part['kind']}"
            rel_path = f"{part['subdir']}/{slug}.md"
            planned.append((part, rel_path))
        for part, rel_path in planned:
            related = [p for _, p in planned if p != rel_path]
            abs_path = os.path.join(vault_root, rel_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            fm = _gn_frontmatter(part["type"], created, tags, related)
            heading = title or part["body"][:24]
            with open(abs_path, "w", encoding="utf-8") as fh:
                fh.write(f"{fm}\n# {heading}\n\n{part['body']}\n")
            counts[part["kind"]] += 1
            print(f"Wrote [{part['type']}]: {rel_path}", file=sys.stderr)
    return counts, processed_ids


def _print_help():
    print("Usage: getnote.py <command> [args...]", file=sys.stderr)
    print("\nCommands accept wrapped official API responses with a success/data envelope:", file=sys.stderr)
    print("  filter-untagged [exclude_tag]      Filter notes by tag", file=sys.stderr)
    print("  summarize-notes                    Format note summaries as JSON lines", file=sys.stderr)
    print("  filter-recall [--include-external] Parse recall results", file=sys.stderr)
    print("  parse-topics                       Parse topics list", file=sys.stderr)
    print("  parse-bloggers                     Parse bloggers list", file=sys.stderr)
    print("  parse-contents                     Parse blogger contents", file=sys.stderr)
    print("  parse-blogger-contents             Alias for parse-contents", file=sys.stderr)
    print("  parse-lives                        Parse live list or detail", file=sys.stderr)
    print("  parse-save-response                Print saved note_id", file=sys.stderr)
    print("  parse-note-tasks                   Parse async task list", file=sys.stderr)
    print("  parse-note-detail                  Parse note detail", file=sys.stderr)
    print("  parse-quota                        Parse quota info", file=sys.stderr)
    print("  parse-upload-token                 Parse upload token", file=sys.stderr)
    print("  parse-topic-notes                  Parse topic notes", file=sys.stderr)
    print("  write-obsidian <vault_path> <type> [source]  Batch write notes to Obsidian", file=sys.stderr)
    print("  split-getnote <vault_root>         Split getnote notes into reference+idea by contract", file=sys.stderr)


def main(argv):
    if len(argv) < 2:
        _print_help()
        return 1

    cmd = argv[1]
    input_json = sys.stdin.read()

    if cmd == "filter-untagged":
        exclude_tag = argv[2] if len(argv) > 2 else "pkos-synced"
        for note in filter_notes_by_tag(input_json, exclude_tag):
            summary = format_note_summary(note)
            print(f"{summary['note_id']}\t{summary.get('title','Untitled')}\t{summary.get('note_type','plain_text')}")

    elif cmd == "summarize-notes":
        for note in _loads_collection(input_json, "notes", "results"):
            print(json.dumps(format_note_summary(note), ensure_ascii=False))

    elif cmd == "filter-recall":
        include_external = "--include-external" in argv
        print(json.dumps({"results": parse_recall_results(input_json, include_external)}, ensure_ascii=False))

    elif cmd == "parse-topics":
        for topic in parse_topics(input_json):
            print(f"{topic['topic_id']}\t{topic['name']}\t{topic.get('note_count', 0)}\t{topic.get('description', '')}")

    elif cmd == "parse-bloggers":
        for blogger in parse_bloggers(input_json):
            print(f"{blogger['follow_id']}\t{blogger['account_name']}\t{blogger.get('notes_count', 0)}")

    elif cmd in ("parse-contents", "parse-blogger-contents"):
        for content in parse_blogger_contents(input_json):
            print(
                f"{content['post_id_alias']}\t{content['post_title']}\t"
                f"{content.get('post_summary', '')}\t{content.get('post_media_text', '')}\t"
                f"{content.get('post_create_time', '')}"
            )

    elif cmd == "parse-lives":
        for live in parse_lives(input_json):
            print(
                f"{live['live_id']}\t{live['name']}\t{live.get('status', '')}\t"
                f"{live.get('follow_time', '')}\t{live.get('post_title', '')}\t"
                f"{live.get('post_summary', '')}\t{live.get('post_media_text', '')}"
            )

    elif cmd == "parse-save-response":
        print(parse_save_response(input_json))

    elif cmd == "parse-note-tasks":
        print(json.dumps({"tasks": parse_note_tasks(input_json)}, ensure_ascii=False))

    elif cmd == "parse-note-detail":
        print(json.dumps(parse_note_detail(input_json), ensure_ascii=False))

    elif cmd == "parse-quota":
        quota = parse_quota(input_json)
        print(f"Used: {quota['used']} / {quota['limit']} | Remaining: {quota['remaining']} | Resets: {quota['reset_at']}")

    elif cmd == "parse-upload-token":
        try:
            print(json.dumps(parse_upload_token(input_json), ensure_ascii=False))
        except ValueError as exc:
            print(f"parse-upload-token: {exc}", file=sys.stderr)
            return 1

    elif cmd == "parse-topic-notes":
        for note in parse_topic_notes(input_json):
            print(f"{note['note_id']}\t{note['title']}\t{note.get('note_type','plain_text')}")

    elif cmd == "write-obsidian":
        vault_path = argv[2] if len(argv) > 2 else os.path.expanduser("~/Obsidian/PKOS/50-References")
        note_type = argv[3] if len(argv) > 3 else "reference"
        source = argv[4] if len(argv) > 4 else "getnote"
        payload = load_payload(input_json)
        notes = _as_list(payload, "notes", "results")
        count = write_notes_to_obsidian(notes, vault_path, note_type, source)
        print(f"Wrote {count} notes to {vault_path}")

    elif cmd == "split-getnote":
        # Contract-aware: splits each getnote note into reference (摘抄) + idea (心得)
        # parts and writes them to their contract directories. See vault-directory-contract.
        # stdout = processed note_ids (one per line) for the caller to mark synced;
        # stderr = human-readable progress + summary.
        vault_root = argv[2] if len(argv) > 2 else os.path.expanduser("~/Obsidian/PKOS")
        notes = _as_list(load_payload(input_json), "notes", "results")
        counts, processed_ids = write_getnote_split(notes, vault_root)
        for nid in processed_ids:
            print(nid)
        print(f"Split {len(notes)} getnote notes -> {counts['reference']} reference, "
              f"{counts['idea']} idea ({len(processed_ids)} notes produced output)",
              file=sys.stderr)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
