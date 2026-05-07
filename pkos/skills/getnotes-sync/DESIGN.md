# GetNote Knowledge Base Sync Design

## Context

GetNote exposes user knowledge bases as `topic` resources. PKOS sync reads those knowledge bases and writes read-only MOC pages that reference external GetNote notes without duplicating note bodies into the vault.

## API Contract

Base URL:

```text
https://openapi.biji.com/open/api/v1
```

Auth headers:

```text
Authorization: {api_key}
X-Client-ID: {client_id}
```

Topic list response is wrapped under `data.topics[]`:

```json
{
  "success": true,
  "data": {
    "topics": [
      {
        "topic_id": "topic-001",
        "name": "Architecture Patterns",
        "description": "Software architecture patterns",
        "stats": {
          "note_count": 42
        }
      }
    ]
  }
}
```

Topic note response for `GET /resource/knowledge/notes` is wrapped under `data.notes[]` and uses `page` pagination. It is not a top-level notes field.

```json
{
  "success": true,
  "data": {
    "page": 1,
    "notes": [
      {
        "note_id": "note-001",
        "title": "Event Sourcing in Practice",
        "content": "Long-form note content...",
        "updated_at": "2026-03-15T10:30:00Z"
      }
    ]
  }
}
```

## MOC Page Template

Location:

```text
~/Obsidian/PKOS/80-MOCs/getnote-{topic-slug}.md
```

Template:

```markdown
---
type: moc
topic: getnote-{topic-slug}
tags: [getnote, external]
source: getnote
getnote_topic_id: "{topic_id}"
getnote_topic_name: "{topic_name}"
getnote_description: "{topic_description}"
note_count: {note_count}
last_synced: {YYYY-MM-DD}
---

# {Topic Name}

> [!abstract] GetNote Knowledge Base
> This MOC maps the "{topic_name}" knowledge base from GetNote.
> Source: getnotes://topic/{topic_id}

## Notes

- [{note_title}](getnotes://note/{note_id}) - {preview} ({timestamp})

## Related MOCs

None yet.
```

## State File

Location:

```text
~/Obsidian/PKOS/.state/getnotes-sync-state.yaml
```

Shape:

```yaml
last_full_sync: "2026-05-07T10:00:00"
last_incremental_sync: "2026-05-07T14:30:00"
api_key_hash: "{sha256 of api_key}"
topics:
  - topic_id: "topic-001"
    topic_name: "Architecture Patterns"
    topic_slug: "architecture-patterns"
    note_count: 42
    last_note_hash: "{sha256 of sorted note_id plus timestamp strings}"
    last_synced: "2026-05-07"
```

Hash inputs use `note_id` plus official timestamp fields, with legacy `id` fallback:

```python
def note_hash_input(note):
    note_id = note.get("note_id") or note.get("id") or ""
    timestamp = note.get("updated_at") or note.get("created_at") or note.get("create_time") or ""
    return f"{note_id}:{timestamp}"
```

## Sync Flow

1. Load credentials from `getnote_api.api_key` and `getnote_api.client_id`.
2. Fetch `GET /resource/knowledge/list`.
3. Parse `data.topics[]`.
4. For each topic, fetch `GET /resource/knowledge/notes` with separate query params `topic_id={topic_id}` and `page={page}`.
5. Parse `data.notes[]`.
6. Compute the topic note hash from `note_id` plus official timestamp fields, with legacy `id` fallback.
7. Create or update the MOC page.
8. Update `getnotes-sync-state.yaml`.

Pagination for `/resource/knowledge/notes` uses integer `page` values. The sync stops when the returned `data.notes[]` list is empty or repeats a page already seen.

## Error Handling

| Error | Action |
| --- | --- |
| 401 or 403 | Stop with a credential error. |
| 404 topic not found | Skip topic and keep prior state. |
| 429 rate limited | Wait 60 seconds, retry once, then skip topic. |
| 5xx | Log and continue with the next topic. |
| Network timeout | Retry once. |
| MOC write failure | Log and continue with the next topic. |

## Integration Points

| Component | Integration |
| --- | --- |
| `getnote.sh` | Handles OpenAPI auth, separate query params, and response errors. |
| `getnote.py` | Parses `data.topics[]`, `data.notes[]`, and note ID fallbacks. |
| PKOS entry point | Manual trigger: `pkos sync getnotes`. |
| Adam cron | Scheduled sync every 6 hours. |
| `80-MOCs/` | MOC pages written here. |
| `.state/getnotes-sync-state.yaml` | Topic registry and hash tracking. |

## Out Of Scope

- Importing GetNote notes as individual Obsidian notes.
- Writing back to GetNote.
- Syncing Obsidian deletions into GetNote.
- Assuming subscribed knowledge bases are writable.
