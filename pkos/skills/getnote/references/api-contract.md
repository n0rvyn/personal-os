# GetNote OpenAPI Contract

Base URL:

```text
https://openapi.biji.com/open/api/v1
```

Required headers:

```text
Authorization: {api_key}
X-Client-ID: {client_id}
Content-Type: application/json
```

The `Authorization` value is the raw `gk_live_xxx` API key. Do not prefix it with `Bearer`.

## Core Endpoints

| Command | Method and path | Notes |
| --- | --- | --- |
| `list_notes [cursor]` | `GET /resource/note/list` | Uses `cursor`; `0` means first page. |
| `get_note <note_id>` | `GET /resource/note/detail` | Sends `note_id`; optional `image_quality`. |
| `save_note` | `POST /resource/note/save` | Body uses `note_type: plain_text`. |
| `save_link` | `POST /resource/note/save` | Body uses `note_type: link` plus `link_url`. |
| `save_image_note` | `POST /resource/note/save` | Body uses `note_type: img_text` plus `image_urls[]`. |
| `get_note_task_progress` | `POST /resource/note/task/progress` | Alias: `poll_task`. |
| `list_topics` | `GET /resource/knowledge/list` | Returns knowledge bases under `data.topics[]`. |
| `list_topic_notes` | `GET /resource/knowledge/notes` | Sends `topic_id` and `page` as separate query params. |
| `recall` | `POST /resource/recall` | Returns `data.results[]`. |
| `recall_knowledge` | `POST /resource/recall/knowledge` | Sends `topic_id`, `query`, `top_k`. |
| `list_bloggers` | `GET /resource/knowledge/bloggers` | Sends `topic_id` and `page`. |
| `list_blogger_contents` | `GET /resource/knowledge/blogger/contents` | Sends `topic_id`, `follow_id`, `page`. |
| `blogger_content_detail` | `GET /resource/knowledge/blogger/content/detail` | Sends `topic_id` and `post_id_alias`. |
| `list_lives` | `GET /resource/knowledge/lives` | Sends `topic_id` and `page`. |
| `live_detail` | `GET /resource/knowledge/live/detail` | Sends `topic_id` and `live_id`. |
| `follow_topic_live` | `POST /resource/knowledge/live/follow` | Body uses `topic_id` plus `link`, not `live_id`. |
| `share_note` | `POST /resource/note/sharing` | Optional `share_exclude_audio`. |
| `get_upload_token` | `GET /resource/image/upload_token` | Sends `mime_type` and `count`. |

## Save Body Examples

Plain text:

```json
{
  "note_type": "plain_text",
  "title": "Meeting notes",
  "content": "Discussed Q2 goals",
  "tags": ["work", "meeting"],
  "topic_id": "topic-001"
}
```

Link:

```json
{
  "note_type": "link",
  "link_url": "https://example.com/post",
  "title": "Post title",
  "tags": ["web"]
}
```

Image note:

```json
{
  "note_type": "img_text",
  "image_urls": ["https://cdn.biji.com/images/demo.png"],
  "title": "Diagram",
  "content": "Image context",
  "tags": ["image"]
}
```

## Response Examples

Saved note:

```json
{
  "success": true,
  "request_id": "req-save-note",
  "data": {
    "note_id": "note-save-001"
  }
}
```

Async task:

```json
{
  "success": true,
  "data": {
    "tasks": [
      {
        "task_id": "task-001",
        "note_id": "note-save-001",
        "status": "processing",
        "progress": 45
      }
    ]
  }
}
```

List notes:

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
        ],
        "updated_at": "2026-05-07T09:00:00+08:00"
      }
    ]
  }
}
```

Topic list:

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

Recall:

```json
{
  "success": true,
  "data": {
    "results": [
      {
        "note_id": "note-recall-001",
        "title": "Stored note result",
        "note_type": "NOTE"
      },
      {
        "post_id_alias": "post-alias-001",
        "post_title": "Blogger result",
        "note_type": "BLOGGER"
      }
    ]
  }
}
```

Blogger content:

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

Live detail:

```json
{
  "success": true,
  "data": {
    "live_id": "live-001",
    "name": "Product Strategy Replay",
    "status": "completed",
    "follow_time": "2026-05-05T18:00:00+08:00",
    "post_title": "Live replay title",
    "post_summary": "Processed live summary.",
    "post_media_text": "Full live transcript text."
  }
}
```

Upload token:

```json
{
  "success": true,
  "data": {
    "tokens": [
      {
        "host": "https://oss-upload.example.com",
        "key": "images/demo.png",
        "OSSAccessKeyId": "oss-access-key",
        "policy": "oss-policy",
        "signature": "oss-signature",
        "callback": "oss-callback",
        "access_url": "https://cdn.biji.com/images/demo.png"
      }
    ]
  }
}
```

## Image Upload Contract

`upload_image <file_path> [mime_type]` first requests `GET /resource/image/upload_token`, then posts to the returned `host` using multipart fields in this order:

1. `key`
2. `OSSAccessKeyId`
3. `policy`
4. `signature`
5. `callback`
6. `Content-Type`
7. `file`

Supported image MIME types: `image/jpeg`, `image/png`, `image/gif`, and `image/webp`.
