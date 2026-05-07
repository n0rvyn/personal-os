#!/usr/bin/env bash
# GetNote OpenAPI client. Requires GETNOTE_API_KEY and GETNOTE_CLIENT_ID.
set -euo pipefail

BASE_URL="${GETNOTE_BASE_URL:-https://openapi.biji.com/open/api/v1}"
API_KEY="${GETNOTE_API_KEY:?need GETNOTE_API_KEY}"
CLIENT_ID="${GETNOTE_CLIENT_ID:?need GETNOTE_CLIENT_ID}"
CURL_BIN="${GETNOTE_CURL_BIN:-curl}"
CURL_TIMEOUT_SECONDS="${GETNOTE_TIMEOUT_SECONDS:-30}"

die() {
  echo "getnote.sh: $*" >&2
  exit 1
}

require_arg() {
  local usage="$1"
  local value="${2:-}"
  [[ -n "$value" ]] || die "usage: ${usage}"
}

validate_api_response() {
  local path="$1"
  python3 -c '
import json
import sys

path = sys.argv[1]
body = sys.stdin.read()
try:
    payload = json.loads(body)
except json.JSONDecodeError as exc:
    print(f"GetNote API error for {path}: malformed JSON response: {exc}", file=sys.stderr)
    sys.exit(1)
if isinstance(payload, dict) and payload.get("success") is False:
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = error.get("code", "")
    reason = error.get("reason", "")
    request_id = payload.get("request_id", "")
    print(f"GetNote API error for {path}: success:false code={code} reason={reason} request_id={request_id}", file=sys.stderr)
    sys.exit(2)
' "$path"
}

api_request() {
  local method="$1"
  local path="$2"
  shift 2
  local endpoint="${BASE_URL}${path}"
  local curl_output curl_exit status response validation_error validation_exit

  set +e
  curl_output=$("$CURL_BIN" -sS --connect-timeout 10 --max-time "$CURL_TIMEOUT_SECONDS" \
    -X "$method" "$endpoint" \
    -H "Authorization: ${API_KEY}" \
    -H "X-Client-ID: ${CLIENT_ID}" \
    -H "Content-Type: application/json" \
    "$@" \
    -w $'\n%{http_code}')
  curl_exit=$?
  set -e

  if [[ "$curl_exit" -ne 0 ]]; then
    if [[ "$curl_exit" -eq 28 ]]; then
      die "timeout for ${path} (curl exit ${curl_exit})"
    fi
    die "curl failed for ${path} (exit ${curl_exit})"
  fi

  status="${curl_output##*$'\n'}"
  response="${curl_output%$'\n'*}"
  if [[ ! "$status" =~ ^[0-9]{3}$ ]]; then
    die "missing HTTP status for ${path}"
  fi
  if (( status < 200 || status >= 300 )); then
    die "HTTP ${status} for ${path}: ${response}"
  fi

  set +e
  validation_error=$(printf "%s" "$response" | validate_api_response "$path" 2>&1)
  validation_exit=$?
  set -e
  if [[ "$validation_exit" -ne 0 ]]; then
    echo "$validation_error" >&2
    exit 1
  fi

  printf "%s\n" "$response"
}

api_get() {
  local path="$1"
  shift
  api_request GET "$path" "$@"
}

api_post() {
  local path="$1"
  local body="$2"
  shift 2
  api_request POST "$path" -d "$body" "$@"
}

json_note_body() {
  python3 - "$@" <<'PY'
import json
import sys

mode = sys.argv[1]
title = sys.argv[2] if len(sys.argv) > 2 else ""
content = sys.argv[3] if len(sys.argv) > 3 else ""
tags_csv = sys.argv[4] if len(sys.argv) > 4 else ""
topic_id = sys.argv[5] if len(sys.argv) > 5 else ""
extra = sys.argv[6] if len(sys.argv) > 6 else ""

body = {}
if mode == "plain_text":
    body = {"note_type": "plain_text", "title": title, "content": content}
elif mode == "link":
    body = {"note_type": "link", "link_url": title}
    if content:
        body["title"] = content
elif mode == "img_text":
    body = {"note_type": "img_text", "image_urls": [title]}
    if content:
        body["title"] = content
    if extra:
        body["content"] = extra
else:
    raise SystemExit(f"unknown note mode: {mode}")

if tags_csv:
    body["tags"] = [tag.strip() for tag in tags_csv.split(",") if tag.strip()]
if topic_id:
    body["topic_id"] = topic_id
print(json.dumps(body, ensure_ascii=False))
PY
}

json_update_note_body() {
  python3 - "$@" <<'PY'
import json
import sys

note_id, title, content, tags_csv = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
body = {"note_id": note_id}
if title:
    body["title"] = title
if content:
    body["content"] = content
if tags_csv:
    body["tags"] = [tag.strip() for tag in tags_csv.split(",") if tag.strip()]
print(json.dumps(body, ensure_ascii=False))
PY
}

json_tags_body() {
  python3 - "$@" <<'PY'
import json
import sys

print(json.dumps({"note_id": sys.argv[1], "tags": [tag.strip() for tag in sys.argv[2].split(",") if tag.strip()]}, ensure_ascii=False))
PY
}

json_note_ids_body() {
  python3 - "$@" <<'PY'
import json
import sys

print(json.dumps({"topic_id": sys.argv[1], "note_ids": [note_id.strip() for note_id in sys.argv[2].split(",") if note_id.strip()]}, ensure_ascii=False))
PY
}

json_file_or_stdin() {
  local source="$1"
  if [[ "$source" == "-" ]]; then
    python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin), ensure_ascii=False))'
  else
    python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1], encoding="utf-8")), ensure_ascii=False))' "$source"
  fi
}

normalize_mime() {
  case "${1:-}" in
    jpg|jpeg|image/jpg|image/jpeg) echo "image/jpeg" ;;
    png|image/png) echo "image/png" ;;
    gif|image/gif) echo "image/gif" ;;
    webp|image/webp) echo "image/webp" ;;
    *) return 1 ;;
  esac
}

get_upload_token_json() {
  local mime_type="${1:-image/png}"
  local count="${2:-1}"
  api_get "/resource/image/upload_token" \
    --data-urlencode "mime_type=${mime_type}" \
    --data-urlencode "count=${count}" \
    --get
}

extract_upload_token_fields() {
  python3 -c '
import json
import sys

payload = json.load(sys.stdin)
data = payload.get("data", payload) if isinstance(payload, dict) else {}
if isinstance(data, dict) and isinstance(data.get("tokens"), list):
    if not data["tokens"]:
        raise SystemExit("upload token response contains no tokens")
    token = data["tokens"][0]
elif isinstance(data, dict):
    token = data
else:
    raise SystemExit("upload token response has unsupported shape")

def first(*names):
    for name in names:
        value = token.get(name)
        if value not in (None, ""):
            return str(value)
    return ""

fields = [
    first("host"),
    first("key"),
    first("OSSAccessKeyId", "oss_access_key_id", "accessid", "access_key_id"),
    first("policy"),
    first("signature"),
    first("callback"),
    first("access_url", "image_url", "url"),
]
if not fields[0] or not fields[1]:
    raise SystemExit("upload token missing host or key")
print("\t".join(fields))
'
}

case "${1:-}" in
  list_notes)
    CURSOR="${2:-}"
    if [[ -n "$CURSOR" && "$CURSOR" != "0" ]]; then
      api_get "/resource/note/list" --data-urlencode "cursor=${CURSOR}" --get
    else
      api_get "/resource/note/list"
    fi
    ;;
  get_note)
    NOTE_ID="${2:-}"
    IMAGE_Q="${3:-}"
    require_arg "get_note <note_id> [image_quality]" "$NOTE_ID"
    if [[ -n "$IMAGE_Q" ]]; then
      api_get "/resource/note/detail" --data-urlencode "note_id=${NOTE_ID}" --data-urlencode "image_quality=${IMAGE_Q}" --get
    else
      api_get "/resource/note/detail" --data-urlencode "note_id=${NOTE_ID}" --get
    fi
    ;;
  save_note)
    TITLE="${2:-}"
    CONTENT="${3:-}"
    TAGS="${4:-}"
    TOPIC_ID="${5:-}"
    require_arg "save_note <title> <content> [tags_csv] [topic_id]" "$TITLE"
    require_arg "save_note <title> <content> [tags_csv] [topic_id]" "$CONTENT"
    api_post "/resource/note/save" "$(json_note_body plain_text "$TITLE" "$CONTENT" "$TAGS" "$TOPIC_ID")"
    ;;
  save_link)
    LINK_URL="${2:-}"
    TITLE="${3:-}"
    TAGS="${4:-}"
    TOPIC_ID="${5:-}"
    require_arg "save_link <link_url> [title] [tags_csv] [topic_id]" "$LINK_URL"
    [[ "$LINK_URL" =~ ^https?:// ]] || die "save_link requires an http or https link"
    api_post "/resource/note/save" "$(json_note_body link "$LINK_URL" "$TITLE" "$TAGS" "$TOPIC_ID")"
    ;;
  save_image_note)
    IMAGE_URL="${2:-}"
    TITLE="${3:-}"
    CONTENT="${4:-}"
    TAGS="${5:-}"
    TOPIC_ID="${6:-}"
    require_arg "save_image_note <image_url> [title] [content] [tags_csv] [topic_id]" "$IMAGE_URL"
    api_post "/resource/note/save" "$(json_note_body img_text "$IMAGE_URL" "$TITLE" "$TAGS" "$TOPIC_ID" "$CONTENT")"
    ;;
  save_note_json)
    JSON_SOURCE="${2:-}"
    require_arg "save_note_json <json_file_or_dash>" "$JSON_SOURCE"
    api_post "/resource/note/save" "$(json_file_or_stdin "$JSON_SOURCE")"
    ;;
  update_note)
    NOTE_ID="${2:-}"
    TITLE="${3:-}"
    CONTENT="${4:-}"
    TAGS="${5:-}"
    require_arg "update_note <note_id> [title] [content] [tags_csv]" "$NOTE_ID"
    api_post "/resource/note/update" "$(json_update_note_body "$NOTE_ID" "$TITLE" "$CONTENT" "$TAGS")"
    ;;
  add_tags)
    NOTE_ID="${2:-}"
    TAGS="${3:-}"
    require_arg "add_tags <note_id> <tags_csv>" "$NOTE_ID"
    require_arg "add_tags <note_id> <tags_csv>" "$TAGS"
    api_post "/resource/note/tags/add" "$(json_tags_body "$NOTE_ID" "$TAGS")"
    ;;
  delete_tag)
    NOTE_ID="${2:-}"
    TAG_ID="${3:-}"
    require_arg "delete_tag <note_id> <tag_id>" "$NOTE_ID"
    require_arg "delete_tag <note_id> <tag_id>" "$TAG_ID"
    api_post "/resource/note/tags/delete" "$(python3 -c 'import json,sys; print(json.dumps({"note_id":sys.argv[1],"tag_id":sys.argv[2]}))' "$NOTE_ID" "$TAG_ID")"
    ;;
  poll_task|get_note_task_progress)
    TASK_ID="${2:-}"
    require_arg "${1} <task_id>" "$TASK_ID"
    api_post "/resource/note/task/progress" "$(python3 -c 'import json,sys; print(json.dumps({"task_id":sys.argv[1]}))' "$TASK_ID")"
    ;;
  list_topics)
    PAGE="${2:-1}"
    api_get "/resource/knowledge/list" --data-urlencode "page=${PAGE}" --get
    ;;
  list_topic_notes)
    TOPIC_ID="${2:-}"
    PAGE="${3:-1}"
    require_arg "list_topic_notes <topic_id> [page]" "$TOPIC_ID"
    api_get "/resource/knowledge/notes" --data-urlencode "topic_id=${TOPIC_ID}" --data-urlencode "page=${PAGE}" --get
    ;;
  batch_add_to_topic)
    TOPIC_ID="${2:-}"
    NOTE_IDS="${3:-}"
    require_arg "batch_add_to_topic <topic_id> <note_ids_csv>" "$TOPIC_ID"
    require_arg "batch_add_to_topic <topic_id> <note_ids_csv>" "$NOTE_IDS"
    api_post "/resource/knowledge/note/batch-add" "$(json_note_ids_body "$TOPIC_ID" "$NOTE_IDS")"
    ;;
  recall)
    QUERY="${2:-}"
    TOP_K="${3:-3}"
    require_arg "recall <query> [top_k]" "$QUERY"
    api_post "/resource/recall" "$(python3 -c 'import json,sys; print(json.dumps({"query":sys.argv[1],"top_k":int(sys.argv[2])}, ensure_ascii=False))' "$QUERY" "$TOP_K")"
    ;;
  recall_knowledge)
    TOPIC_ID="${2:-}"
    QUERY="${3:-}"
    TOP_K="${4:-3}"
    require_arg "recall_knowledge <topic_id> <query> [top_k]" "$TOPIC_ID"
    require_arg "recall_knowledge <topic_id> <query> [top_k]" "$QUERY"
    api_post "/resource/recall/knowledge" "$(python3 -c 'import json,sys; print(json.dumps({"topic_id":sys.argv[1],"query":sys.argv[2],"top_k":int(sys.argv[3])}, ensure_ascii=False))' "$TOPIC_ID" "$QUERY" "$TOP_K")"
    ;;
  list_bloggers)
    TOPIC_ID="${2:-}"
    PAGE="${3:-1}"
    require_arg "list_bloggers <topic_id> [page]" "$TOPIC_ID"
    api_get "/resource/knowledge/bloggers" --data-urlencode "topic_id=${TOPIC_ID}" --data-urlencode "page=${PAGE}" --get
    ;;
  list_blogger_contents)
    TOPIC_ID="${2:-}"
    FOLLOW_ID="${3:-}"
    PAGE="${4:-1}"
    require_arg "list_blogger_contents <topic_id> <follow_id> [page]" "$TOPIC_ID"
    require_arg "list_blogger_contents <topic_id> <follow_id> [page]" "$FOLLOW_ID"
    api_get "/resource/knowledge/blogger/contents" --data-urlencode "topic_id=${TOPIC_ID}" --data-urlencode "follow_id=${FOLLOW_ID}" --data-urlencode "page=${PAGE}" --get
    ;;
  blogger_content_detail)
    TOPIC_ID="${2:-}"
    POST_ID_ALIAS="${3:-}"
    require_arg "blogger_content_detail <topic_id> <post_id_alias>" "$TOPIC_ID"
    require_arg "blogger_content_detail <topic_id> <post_id_alias>" "$POST_ID_ALIAS"
    api_get "/resource/knowledge/blogger/content/detail" --data-urlencode "topic_id=${TOPIC_ID}" --data-urlencode "post_id_alias=${POST_ID_ALIAS}" --get
    ;;
  list_lives)
    TOPIC_ID="${2:-}"
    PAGE="${3:-1}"
    require_arg "list_lives <topic_id> [page]" "$TOPIC_ID"
    api_get "/resource/knowledge/lives" --data-urlencode "topic_id=${TOPIC_ID}" --data-urlencode "page=${PAGE}" --get
    ;;
  live_detail)
    TOPIC_ID="${2:-}"
    LIVE_ID="${3:-}"
    require_arg "live_detail <topic_id> <live_id>" "$TOPIC_ID"
    require_arg "live_detail <topic_id> <live_id>" "$LIVE_ID"
    api_get "/resource/knowledge/live/detail" --data-urlencode "topic_id=${TOPIC_ID}" --data-urlencode "live_id=${LIVE_ID}" --get
    ;;
  quota)
    api_get "/resource/rate-limit/quota"
    ;;
  delete_note)
    NOTE_ID="${2:-}"
    require_arg "delete_note <note_id>" "$NOTE_ID"
    api_post "/resource/note/delete" "$(python3 -c 'import json,sys; print(json.dumps({"note_id":sys.argv[1]}))' "$NOTE_ID")"
    ;;
  create_topic)
    TOPIC_NAME="${2:-}"
    DESC="${3:-}"
    require_arg "create_topic <topic_name> [description]" "$TOPIC_NAME"
    api_post "/resource/knowledge/create" "$(python3 -c 'import json,sys; d={"name":sys.argv[1]}; desc=sys.argv[2] if len(sys.argv)>2 else ""; d.update({"description": desc} if desc else {}); print(json.dumps(d, ensure_ascii=False))' "$TOPIC_NAME" "$DESC")"
    ;;
  remove_note_from_topic)
    TOPIC_ID="${2:-}"
    NOTE_IDS="${3:-}"
    require_arg "remove_note_from_topic <topic_id> <note_ids_csv>" "$TOPIC_ID"
    require_arg "remove_note_from_topic <topic_id> <note_ids_csv>" "$NOTE_IDS"
    api_post "/resource/knowledge/note/remove" "$(json_note_ids_body "$TOPIC_ID" "$NOTE_IDS")"
    ;;
  get_upload_config)
    api_get "/resource/upload/config"
    ;;
  get_upload_token)
    MIME_TYPE="${2:-image/png}"
    COUNT="${3:-1}"
    get_upload_token_json "$MIME_TYPE" "$COUNT"
    ;;
  upload_image)
    FILE_PATH="${2:-}"
    REQUESTED_MIME="${3:-}"
    require_arg "upload_image <file_path> [mime_type]" "$FILE_PATH"
    [[ -f "$FILE_PATH" ]] || die "upload_image requires an existing regular file: ${FILE_PATH}"
    if [[ -z "$REQUESTED_MIME" ]]; then
      REQUESTED_MIME=$(file --mime-type -b "$FILE_PATH" 2>/dev/null || true)
    fi
    if ! MIME_TYPE=$(normalize_mime "$REQUESTED_MIME"); then
      die "unsupported image MIME type: ${REQUESTED_MIME}"
    fi
    TOKEN_JSON=$(get_upload_token_json "$MIME_TYPE" 1)
    TOKEN_FIELDS=$(printf "%s" "$TOKEN_JSON" | extract_upload_token_fields)
    IFS=$'\t' read -r OSS_HOST OSS_KEY OSS_ACCESS_KEY_ID OSS_POLICY OSS_SIGNATURE OSS_CALLBACK ACCESS_URL <<< "$TOKEN_FIELDS"
    set +e
    OSS_BODY=$("$CURL_BIN" -sS --connect-timeout 10 --max-time "$CURL_TIMEOUT_SECONDS" \
      -X POST "$OSS_HOST" \
      -F "key=${OSS_KEY}" \
      -F "OSSAccessKeyId=${OSS_ACCESS_KEY_ID}" \
      -F "policy=${OSS_POLICY}" \
      -F "signature=${OSS_SIGNATURE}" \
      -F "callback=${OSS_CALLBACK}" \
      -F "Content-Type=${MIME_TYPE}" \
      -F "file=@${FILE_PATH};type=${MIME_TYPE}")
    OSS_EXIT=$?
    set -e
    [[ "$OSS_EXIT" -eq 0 ]] || die "OSS upload failed for ${FILE_PATH} (exit ${OSS_EXIT})"
    printf "%s" "$OSS_BODY" | python3 -c 'import json,sys; print(json.dumps({"image_url": sys.argv[1], "oss_response": sys.stdin.read()}, ensure_ascii=False))' "$ACCESS_URL"
    ;;
  share_note)
    NOTE_ID="${2:-}"
    SHARE_EXCLUDE_AUDIO="${3:-false}"
    require_arg "share_note <note_id> [share_exclude_audio]" "$NOTE_ID"
    api_post "/resource/note/sharing" "$(python3 -c 'import json,sys; v=sys.argv[2].lower() in ("1","true","yes"); print(json.dumps({"note_id":sys.argv[1],"share_exclude_audio":v}))' "$NOTE_ID" "$SHARE_EXCLUDE_AUDIO")"
    ;;
  follow_topic_live)
    TOPIC_ID="${2:-}"
    LINK="${3:-}"
    require_arg "follow_topic_live <topic_id> <dedao_live_link>" "$TOPIC_ID"
    require_arg "follow_topic_live <topic_id> <dedao_live_link>" "$LINK"
    [[ "$LINK" =~ ^https?:// ]] || die "follow_topic_live requires an http or https link"
    api_post "/resource/knowledge/live/follow" "$(python3 -c 'import json,sys; print(json.dumps({"topic_id":sys.argv[1],"link":sys.argv[2]}, ensure_ascii=False))' "$TOPIC_ID" "$LINK")"
    ;;
  list_subscribe_topics)
    PAGE="${2:-1}"
    api_get "/resource/knowledge/subscribe/list" --data-urlencode "page=${PAGE}" --get
    ;;
  get_topic_detail)
    TOPIC_ID="${2:-}"
    require_arg "get_topic_detail <topic_id>" "$TOPIC_ID"
    api_get "/resource/knowledge/detail" --data-urlencode "topic_id=${TOPIC_ID}" --get
    ;;
  search_notes)
    KEYWORD="${2:-}"
    PAGE="${3:-1}"
    require_arg "search_notes <keyword> [page]" "$KEYWORD"
    api_get "/resource/note/search" --data-urlencode "keyword=${KEYWORD}" --data-urlencode "page=${PAGE}" --get
    ;;
  get_note_tasks)
    NOTE_ID="${2:-}"
    require_arg "get_note_tasks <note_id>" "$NOTE_ID"
    api_get "/resource/note/tasks" --data-urlencode "note_id=${NOTE_ID}" --get
    ;;
  *)
    echo "GetNote API Client" >&2
    echo "Usage: getnote.sh <command> [args...]" >&2
    echo "" >&2
    echo "Notes:" >&2
    echo "  list_notes [cursor]                  List notes with cursor pagination" >&2
    echo "  get_note <note_id> [quality]         Get note detail" >&2
    echo "  save_note <title> <content> [tags] [topic_id]" >&2
    echo "  save_link <link_url> [title] [tags] [topic_id]" >&2
    echo "  save_image_note <image_url> [title] [content] [tags] [topic_id]" >&2
    echo "  save_note_json <json_file_or_dash>   Post full OpenAPI note body" >&2
    echo "  update_note <note_id> [title] [content] [tags]" >&2
    echo "  delete_note <note_id>" >&2
    echo "  share_note <note_id> [share_exclude_audio]" >&2
    echo "" >&2
    echo "Tags:" >&2
    echo "  add_tags <note_id> <tags_csv>" >&2
    echo "  delete_tag <note_id> <tag_id>" >&2
    echo "" >&2
    echo "Search:" >&2
    echo "  recall <query> [top_k]" >&2
    echo "  recall_knowledge <topic_id> <query> [top_k]" >&2
    echo "  search_notes <keyword> [page]" >&2
    echo "" >&2
    echo "Knowledge bases:" >&2
    echo "  list_topics [page]" >&2
    echo "  get_topic_detail <topic_id>" >&2
    echo "  create_topic <name> [description]" >&2
    echo "  list_topic_notes <topic_id> [page]" >&2
    echo "  batch_add_to_topic <topic_id> <note_ids_csv>" >&2
    echo "  remove_note_from_topic <topic_id> <note_ids_csv>" >&2
    echo "  list_subscribe_topics [page]" >&2
    echo "" >&2
    echo "Blogger and live content:" >&2
    echo "  list_bloggers <topic_id> [page]" >&2
    echo "  list_blogger_contents <topic_id> <follow_id> [page]" >&2
    echo "  blogger_content_detail <topic_id> <post_id_alias>" >&2
    echo "  list_lives <topic_id> [page]" >&2
    echo "  live_detail <topic_id> <live_id>" >&2
    echo "  follow_topic_live <topic_id> <dedao_live_link>" >&2
    echo "" >&2
    echo "Image upload:" >&2
    echo "  get_upload_token [mime_type] [count]" >&2
    echo "  upload_image <file_path> [mime_type]" >&2
    echo "" >&2
    echo "Tasks and quota:" >&2
    echo "  poll_task <task_id>" >&2
    echo "  get_note_task_progress <task_id>" >&2
    echo "  get_note_tasks <note_id>" >&2
    echo "  quota" >&2
    echo "" >&2
    echo "Legacy / verify before use:" >&2
    echo "  get_upload_config" >&2
    exit 1
    ;;
esac
