---
name: getnote
description: This skill should be used when the user asks to "save a note", "search my notes", "list notes", "create a knowledge base", "add tags to a note", "delete a note", "update a note", "sync notes", "upload an image to getnote", "share a note", "getnote quota", "create a topic", "list topics", "get note detail", "recall notes", "semantic search getnote", "getnote bloggers", "getnote lives", "get live detail", "add notes to topic", or any explicit Get笔记 API call. Provides direct access to all 27 Get笔记 API endpoints via getnote.sh and getnote.py.
model: sonnet
allowed-tools:
  - Read
  - Bash
---

## Overview

This skill provides low-level access to all Get笔记 API endpoints via `getnote.sh` and `getnote.py`, including note CRUD, semantic search, knowledge base management, blogger/live subscriptions, and image upload.

## Environment Setup

Set credentials before use:
```bash
export GETNOTE_API_KEY="gk_live_xxx"      # From https://www.biji.com/openapi
export GETNOTE_CLIENT_ID="cli_xxx"
```

Or via `~/.claude/pkos/config.yaml`:
```yaml
getnote_api:
  api_key: "gk_live_xxx"
  client_id: "cli_xxx"
```

Read from config:
```bash
CONFIG_FILE=~/.claude/pkos/config.yaml
API_KEY=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG_FILE')).get('getnote_api',{}).get('api_key',''))")
CLIENT_ID=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG_FILE')).get('getnote_api',{}).get('client_id',''))")
```

## Script Reference

**`getnote.sh`** — API client wrapping all 27 Get笔记 endpoints.

**`getnote.py`** — JSON parsing utilities for `getnote.sh` output.

## Common Operations

### List Notes

```bash
# All notes (cursor-based pagination)
getnote.sh list_notes 0

# Since a specific note ID (incremental sync)
getnote.sh list_notes <since_id>
```

Pipe through Python for filtering:
```bash
getnote.sh list_notes 0 | getnote.py filter-untagged pkos-synced
getnote.sh list_notes 0 | getnote.py summarize-notes
```

### Get Note Detail

```bash
getnote.sh get_note <note_id> [image_quality]   # image_quality: low|medium|high
```

### Save Note

```bash
getnote.sh save_note <title> <content> [tags_csv]
# Example:
getnote.sh save_note "Meeting notes" "Discussed Q2 goals" "work,meeting"
```

Note types (default: `plain_text`):
- `plain_text` — plain text content
- `link` — URL link (triggers async processing, poll with `get_note_task_progress`)
- `img_text` — image note (requires `image_urls` field, use `upload_image` first)

### Update Note

```bash
getnote.sh update_note <note_id> [title] [content] [tags_csv]
# Partial update — omit args to leave unchanged
getnote.sh update_note <note_id> "" "new content"
```

### Delete Note

```bash
getnote.sh delete_note <note_id>
```

### Share Note

```bash
getnote.sh share_note <note_id>
```

## Tags

```bash
# Add tags
getnote.sh add_tags <note_id> <tags_csv>

# Remove a tag
getnote.sh delete_tag <note_id> <tag_id>
```

## Semantic Search

```bash
# Global search across all notes
getnote.sh recall <query> [top_k]   # default top_k=3

# Search within a knowledge base
getnote.sh recall_knowledge <topic_id> <query> [top_k]

# Keyword search (exact match)
getnote.sh search_notes <keyword> [page]
```

Filter recall results:
```bash
getnote.sh recall "design patterns" | getnote.py filter-recall
# Include external/linked types:
getnote.sh recall "design patterns" | getnote.py filter-recall --include-external
```

## Knowledge Bases (Topics)

```bash
# List all knowledge bases
getnote.sh list_topics [page]

# Get topic detail
getnote.sh get_topic_detail <topic_id>

# Create a knowledge base
getnote.sh create_topic <name> [description]

# List notes in a topic
getnote.sh list_topic_notes <topic_id> [page]

# Batch add notes to a topic
getnote.sh batch_add_to_topic <topic_id> <note_ids_csv>

# Remove notes from a topic
getnote.sh remove_note_from_topic <topic_id> <note_ids_csv>

# List subscribed topics
getnote.sh list_subscribe_topics [page]
```

Parse topic list:
```bash
getnote.sh list_topics | getnote.py parse-topics
```

## Bloggers

```bash
# List bloggers in a topic
getnote.sh list_bloggers <topic_id> [page]

# List blogger's posts
getnote.sh list_blogger_contents <topic_id> <follow_id> [page]

# Get full post content
getnote.sh blogger_content_detail <topic_id> <post_id>
```

Parse outputs:
```bash
getnote.sh list_bloggers <topic_id> | getnote.py parse-bloggers
getnote.sh list_blogger_contents <topic_id> <follow_id> | getnote.py parse-contents
```

## Lives

```bash
# List AI-processed lives in a topic
getnote.sh list_lives <topic_id> [page]

# Get live detail with AI summary
getnote.sh live_detail <topic_id> <live_id>

# Subscribe to live updates
getnote.sh follow_topic_live <topic_id> <live_id>
```

Parse lives:
```bash
getnote.sh list_lives <topic_id> | getnote.py parse-lives
```

## Image Upload

Complete flow for `img_text` notes:

```bash
# Step 1: Get upload token
UPLOAD_RESP=$(getnote.sh get_upload_token)
UPLOAD_TOKEN=$(echo "$UPLOAD_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('token',''))")

# Step 2: Upload image (returns URL)
IMG_RESP=$(getnote.sh upload_image <file_path> "$UPLOAD_TOKEN")
IMAGE_URL=$(echo "$IMG_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))")

# Step 3: Create img_text note with the URL
getnote.sh save_note <title> <content> <tags_csv>
# With img_text type, the API expects image_urls field — use update_note or pass via body
```

Check upload config first:
```bash
getnote.sh get_upload_config
```

## Tasks & Async Processing

Link notes (`note_type=link`) are processed asynchronously. Poll for completion:

```bash
# Poll task status
getnote.sh poll_task <task_id>
getnote.sh get_note_task_progress <task_id>

# Get tasks associated with a note
getnote.sh get_note_tasks <note_id>
```

## Rate Limits

```bash
getnote.sh quota
```

Daily limit: 50 knowledge bases per account (resets at Beijing time 00:00).

## Python Helper Commands

```bash
# Filter notes by tag (exclude notes with tag)
echo "$notes_json" | getnote.py filter-untagged [exclude_tag]

# Summarize notes as JSON
echo "$notes_json" | getnote.py summarize-notes

# Filter recall results
echo "$recall_json" | getnote.py filter-recall [--include-external]

# Parse various list types
echo "$json" | getnote.py parse-topics
echo "$json" | getnote.py parse-bloggers
echo "$json" | getnote.py parse-contents
echo "$json" | getnote.py parse-lives
echo "$json" | getnote.py parse-quota

# Batch write notes to Obsidian vault
echo "$notes_json" | getnote.py write-obsidian <vault_path> [note_type] [source]
```

## Error Handling

| HTTP Code | Meaning | Action |
|-----------|---------|--------|
| 401/403 | Auth failure | Check API key and client ID |
| 429 | Rate limited | Wait and retry; check `quota` |
| 5xx | Server error | Retry once after 5s |

## Config

For PKOS integration, credentials are stored in `~/.claude/pkos/config.yaml` under `getnote_api`:
```yaml
getnote_api:
  api_key: "gk_live_xxx"
  client_id: "cli_xxx"
  topic_map:               # optional: tag → topic_id mapping
    work: "topic_xxx"
    personal: "topic_yyy"
```
