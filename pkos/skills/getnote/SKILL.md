---
name: getnote
description: Use this skill when the user wants to save text, link, or image notes; search all GetNote notes; search within a knowledge base; list notes and get original content or transcription; manage tags; create knowledge bases and add or remove notes; read subscribed blogger or live content; share notes; or asks in Chinese for GetNote actions such as "记一下", "搜一下", "加标签", "加到知识库", "保存链接", "保存图片笔记", "读取直播内容", or "分享笔记".
model: sonnet
allowed-tools:
  - Read
  - Bash
---

## Overview

This skill routes user language to the GetNote OpenAPI client in `scripts/getnote.sh` and the response parsers in `scripts/getnote.py`.

The shared API contract, endpoints, request bodies, and response examples live in `references/api-contract.md`.

## Concept Glossary

- `GetNote App`: the product the user sees.
- `Note`: one saved item; API fields `note_id` and legacy `id`.
- `Tag`: note-level label; API field `tags[]`; not a knowledge base.
- `Knowledge base`: product term for `topic`; API field `topic_id`.
- `Subscribed knowledge base`: read-only shared topic.
- `Blogger content`: subscribed external creator content inside a topic.
- `Live content`: GetNote-processed live replay or transcript inside a topic.
- `Gag`: not an official GetNote/OpenAPI concept in the checked docs. If a user says it, ask whether they mean `Tag/标签` or another product feature.

## Environment

```bash
export GETNOTE_API_KEY="gk_live_xxx"
export GETNOTE_CLIENT_ID="cli_xxx"
```

Config shape:

```yaml
getnote_api:
  api_key: "gk_live_xxx"
  client_id: "cli_xxx"
  base_url: "https://openapi.biji.com/open/api/v1"
```

## Routing Table

| User wording | Command flow |
| --- | --- |
| URL, "保存链接", "save this link" | `getnote.sh save_link <link_url> [title] [tags_csv] [topic_id]` |
| Image file, "保存图片笔记" | `getnote.sh upload_image <file_path> [mime_type]`, then `getnote.sh save_image_note <image_url> [title] [content] [tags_csv] [topic_id]` |
| "记一下", "save a note" | `getnote.sh save_note <title> <content> [tags_csv] [topic_id]` |
| "搜一下", "search my notes" | `getnote.sh recall <query> [top_k]`, then `getnote.py filter-recall` |
| "在 X 知识库搜" | `getnote.sh list_topics`, choose `topic_id`, then `getnote.sh recall_knowledge <topic_id> <query> [top_k]` |
| "加标签" | `getnote.sh add_tags <note_id> <tags_csv>` |
| "加到知识库" | `getnote.sh list_topics`, choose `topic_id`, then `getnote.sh batch_add_to_topic <topic_id> <note_ids_csv>` |
| "分享笔记" | `getnote.sh share_note <note_id> [share_exclude_audio]` |
| "读取直播内容" | `getnote.sh list_lives <topic_id>`, then `getnote.sh live_detail <topic_id> <live_id>` |

## Common Commands

```bash
# List notes. 0 is a first-page compatibility call; cursor is the official continuation token.
getnote.sh list_notes 0
getnote.sh list_notes <cursor>

# Get detail, including original content and transcription fields when returned.
getnote.sh get_note <note_id> [image_quality]

# Save notes.
getnote.sh save_note "Meeting notes" "Discussed Q2 goals" "work,meeting"
getnote.sh save_link "https://example.com/post" "Post title" "web,reading"
getnote.sh upload_image ./diagram.png image/png
getnote.sh save_image_note "https://cdn.biji.com/images/diagram.png" "Diagram" "context" "image,work"

# Search.
getnote.sh recall "design patterns" | getnote.py filter-recall
getnote.sh recall "design patterns" | getnote.py filter-recall --include-external
getnote.sh recall_knowledge <topic_id> "design patterns" 5

# Knowledge bases.
getnote.sh list_topics | getnote.py parse-topics
getnote.sh list_topic_notes <topic_id> 1 | getnote.py parse-topic-notes
getnote.sh create_topic "Architecture Patterns" "Architecture notes"
getnote.sh batch_add_to_topic <topic_id> <note_ids_csv>
getnote.sh remove_note_from_topic <topic_id> <note_ids_csv>

# Blogger and live content.
getnote.sh list_bloggers <topic_id> 1 | getnote.py parse-bloggers
getnote.sh list_blogger_contents <topic_id> <follow_id> 1 | getnote.py parse-contents
getnote.sh list_lives <topic_id> 1 | getnote.py parse-lives
getnote.sh live_detail <topic_id> <live_id> | getnote.py parse-lives
getnote.sh follow_topic_live <topic_id> <dedao_live_link>

# Async processing.
getnote.sh get_note_task_progress <task_id> | getnote.py parse-note-tasks
getnote.sh poll_task <task_id> | getnote.py parse-note-tasks
```

## Response Shape Examples

Use `getnote.py` parser commands instead of repeating inline JSON parsing.

`save_note` and `save_link` return `data.note_id`:

```json
{
  "success": true,
  "data": {
    "note_id": "note-save-001"
  }
}
```

`get_note_task_progress` returns `data.tasks[]`:

```json
{
  "success": true,
  "data": {
    "tasks": [
      {
        "task_id": "task-001",
        "note_id": "note-save-001",
        "status": "processing"
      }
    ]
  }
}
```

`list_notes` returns `data.notes[]` plus `data.cursor`:

```json
{
  "success": true,
  "data": {
    "cursor": "cursor-next-001",
    "notes": [
      {
        "note_id": "note-001",
        "title": "Parser contract note",
        "tags": [
          {
            "name": "pkos"
          }
        ]
      }
    ]
  }
}
```

`list_topics` returns `data.topics[]`:

```json
{
  "success": true,
  "data": {
    "topics": [
      {
        "topic_id": "topic-001",
        "name": "PKOS Knowledge",
        "stats": {
          "note_count": 42
        }
      }
    ]
  }
}
```

`recall` returns `data.results[]`:

```json
{
  "success": true,
  "data": {
    "results": [
      {
        "note_id": "note-recall-001",
        "note_type": "NOTE",
        "title": "Stored note result"
      }
    ]
  }
}
```

Blogger contents use `post_id_alias`, `post_title`, `post_summary`, `post_media_text`, and `post_create_time`:

```json
{
  "success": true,
  "data": {
    "contents": [
      {
        "post_id_alias": "post-alias-001",
        "post_title": "Official blogger post",
        "post_summary": "Short blogger summary.",
        "post_media_text": "Full blogger media text.",
        "post_create_time": "2026-05-06T20:00:00+08:00"
      }
    ]
  }
}
```

Live details keep list metadata and processed content separate:

```json
{
  "success": true,
  "data": {
    "live_id": "live-001",
    "name": "Product Strategy Replay",
    "post_title": "Live replay title",
    "post_summary": "Processed live summary.",
    "post_media_text": "Full live transcript text."
  }
}
```

## Do Not Confuse

- Do not treat a tag name as `topic_id`.
- Do not add notes to a knowledge base unless the user asked for it or config `topic_map` maps the tag.
- Do not use `share_note` when an internal link `https://biji.com/note/{note_id}` is enough.
- Do not assume subscribed knowledge bases are writable.

## Verification

Run local unit tests after parser, shell client, or downstream documentation changes:

```bash
python3 pkos/skills/getnote/tests/test_getnote_parser.py
python3 pkos/skills/getnote/tests/test_getnote_shell.py
python3 pkos/skills/getnote/tests/test_downstream_docs.py
```

Run the credential-gated real API smoke test only when live GetNote writes are intended:

```bash
bash pkos/skills/getnote/tests/smoke_getnote_api.sh
RUN_GETNOTE_SMOKE=1 GETNOTE_API_KEY="gk_live_xxx" GETNOTE_CLIENT_ID="cli_xxx" bash pkos/skills/getnote/tests/smoke_getnote_api.sh
```

Without `RUN_GETNOTE_SMOKE=1`, the smoke script prints a `SKIP` line and exits 0. With credentials, it creates a plain text smoke note, lists notes, adds `pkos-smoke`, verifies `share_url`, and deletes the smoke note during cleanup.

Manual link smoke, because link processing creates async load and depends on external content:

```bash
NOTE_ID="$(
  bash pkos/skills/getnote/scripts/getnote.sh save_link "https://example.com/" "PKOS smoke link" "pkos-smoke" |
    python3 pkos/skills/getnote/scripts/getnote.py parse-save-response
)"
bash pkos/skills/getnote/scripts/getnote.sh get_note_tasks "$NOTE_ID" || true
bash pkos/skills/getnote/scripts/getnote.sh delete_note "$NOTE_ID"
```

Manual image smoke, because upload plus image-note processing depends on an actual local image and OSS upload availability:

```bash
IMAGE_JSON="$(bash pkos/skills/getnote/scripts/getnote.sh upload_image ./smoke.png image/png)"
IMAGE_URL="$(printf "%s" "$IMAGE_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["image_url"])')"
NOTE_ID="$(
  bash pkos/skills/getnote/scripts/getnote.sh save_image_note "$IMAGE_URL" "PKOS smoke image" "manual image smoke" "pkos-smoke" |
    python3 pkos/skills/getnote/scripts/getnote.py parse-save-response
)"
bash pkos/skills/getnote/scripts/getnote.sh get_note_tasks "$NOTE_ID" || true
bash pkos/skills/getnote/scripts/getnote.sh delete_note "$NOTE_ID"
```

## Parser Commands

All parser commands accept wrapped official API responses with `success` plus `data`, and preserve top-level legacy responses for internal callers.

```bash
getnote.py summarize-notes
getnote.py filter-untagged [exclude_tag]
getnote.py filter-recall [--include-external]
getnote.py parse-topics
getnote.py parse-bloggers
getnote.py parse-contents
getnote.py parse-lives
getnote.py parse-save-response
getnote.py parse-note-tasks
getnote.py parse-note-detail
getnote.py parse-upload-token
getnote.py parse-topic-notes
```
