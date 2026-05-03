#!/bin/bash
# Get笔记 API 封装 — 环境变量: GETNOTE_API_KEY, GETNOTE_CLIENT_ID
set -euo pipefail
BASE_URL="https://openapi.biji.com/open/api/v1"
API_KEY="${GETNOTE_API_KEY:?需要设置 GETNOTE_API_KEY}"
CLIENT_ID="${GETNOTE_CLIENT_ID:?需要设置 GETNOTE_CLIENT_ID}"

api_get() {
  local path="$1"; shift
  curl -s -X GET "${BASE_URL}${path}" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "X-Client-ID: ${CLIENT_ID}" \
    -H "Content-Type: application/json" \
    "$@"
}

api_post() {
  local path="$1"; local body="$2"; shift 2
  curl -s -X POST "${BASE_URL}${path}" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "X-Client-ID: ${CLIENT_ID}" \
    -H "Content-Type: application/json" \
    -d "$body" "$@"
}

case "${1:-}" in
  list_notes)
    # list_notes [--since-id ID]
    SINCE_ID="${2:-0}"
    api_get "/resource/note/list" --data-urlencode "since_id=${SINCE_ID}" --get
    ;;
  get_note)
    NOTE_ID="${2:-}"; IMAGE_Q="${3:-}"
    Q="id=${NOTE_ID}"
    [[ -n "$IMAGE_Q" ]] && Q+="&image_quality=${IMAGE_Q}"
    api_get "/resource/note/detail" --data-urlencode "$Q" --get
    ;;
  save_note)
    # save_note <title> <content> [tags_csv]
    TITLE="${2:-}"; CONTENT="${3:-}"; TAGS="${4:-}"
    BODY=$(python3 -c "import sys,json; d={'note_type':'plain_text','title':sys.argv[1],'content':sys.argv[2]}; print(json.dumps(d))" "$TITLE" "$CONTENT")
    [[ -n "$TAGS" ]] && BODY=$(python3 -c "import sys,json,yaml; d=json.loads(sys.argv[1]); d['tags']=sys.argv[2].split(','); print(json.dumps(d))" "$BODY" "$TAGS")
    api_post "/resource/note/save" "$BODY"
    ;;
  update_note)
    # update_note <note_id> [title] [content] [tags_csv]
    NOTE_ID="${2:-}"; TITLE="${3:-}"; CONTENT="${4:-}"; TAGS="${5:-}"
    BODY='{}'
    [[ -n "$TITLE" ]] && BODY=$(python3 -c "import sys,json; d=json.loads(sys.argv[1]); d['note_id']=sys.argv[2]; d['title']=sys.argv[3]; print(json.dumps(d))" "$BODY" "$NOTE_ID" "$TITLE")
    [[ -n "$CONTENT" ]] && BODY=$(python3 -c "import sys,json; d=json.loads(sys.argv[1]); d['content']=sys.argv[2]; print(json.dumps(d))" "$BODY" "$CONTENT")
    [[ -n "$TAGS" ]] && BODY=$(python3 -c "import sys,json; d=json.loads(sys.argv[1]); d['tags']=sys.argv[2].split(','); print(json.dumps(d))" "$BODY" "$TAGS")
    api_post "/resource/note/update" "$BODY"
    ;;
  add_tags)
    # add_tags <note_id> <tags_csv>
    NOTE_ID="${2:-}"; TAGS="${3:-}"
    BODY=$(python3 -c "import sys,json; print(json.dumps({'note_id':sys.argv[1],'tags':sys.argv[2].split(',')}))" "$NOTE_ID" "$TAGS")
    api_post "/resource/note/tags/add" "$BODY"
    ;;
  delete_tag)
    # delete_tag <note_id> <tag_id>
    NOTE_ID="${2:-}"; TAG_ID="${3:-}"
    BODY=$(python3 -c "import sys,json; print(json.dumps({'note_id':sys.argv[1],'tag_id':sys.argv[2]}))" "$NOTE_ID" "$TAG_ID")
    api_post "/resource/note/tags/delete" "$BODY"
    ;;
  poll_task)
    TASK_ID="${2:-}"
    BODY=$(python3 -c "import sys,json; print(json.dumps({'task_id':sys.argv[1]}))" "$TASK_ID")
    api_post "/resource/note/task/progress" "$BODY"
    ;;
  list_topics)
    PAGE="${2:-1}"
    api_get "/resource/knowledge/list" --data-urlencode "page=${PAGE}" --get
    ;;
  list_topic_notes)
    TOPIC_ID="${2:-}"; PAGE="${3:-1}"
    api_get "/resource/knowledge/notes" --data-urlencode "topic_id=${TOPIC_ID}&page=${PAGE}" --get
    ;;
  batch_add_to_topic)
    # batch_add_to_topic <topic_id> <note_ids_csv>
    TOPIC_ID="${2:-}"; NOTE_IDS="${3:-}"
    BODY=$(python3 -c "import sys,json; print(json.dumps({'topic_id':sys.argv[1],'note_ids':sys.argv[2].split(',')}))" "$TOPIC_ID" "$NOTE_IDS")
    api_post "/resource/knowledge/note/batch-add" "$BODY"
    ;;
  recall)
    # recall <query> [top_k]
    QUERY="${2:-}"; TOP_K="${3:-3}"
    BODY=$(python3 -c "import sys,json; print(json.dumps({'query':sys.argv[1],'top_k':int(sys.argv[2])}))" "$QUERY" "$TOP_K")
    api_post "/resource/recall" "$BODY"
    ;;
  recall_knowledge)
    # recall_knowledge <topic_id> <query> [top_k]
    TOPIC_ID="${2:-}"; QUERY="${3:-}"; TOP_K="${4:-3}"
    BODY=$(python3 -c "import sys,json; print(json.dumps({'topic_id':sys.argv[1],'query':sys.argv[2],'top_k':int(sys.argv[3])}))" "$TOPIC_ID" "$QUERY" "$TOP_K")
    api_post "/resource/recall/knowledge" "$BODY"
    ;;
  list_bloggers)
    TOPIC_ID="${2:-}"; PAGE="${3:-1}"
    api_get "/resource/knowledge/bloggers" --data-urlencode "topic_id=${TOPIC_ID}&page=${PAGE}" --get
    ;;
  list_blogger_contents)
    TOPIC_ID="${2:-}"; FOLLOW_ID="${3:-}"; PAGE="${4:-1}"
    api_get "/resource/knowledge/blogger/contents" --data-urlencode "topic_id=${TOPIC_ID}&follow_id=${FOLLOW_ID}&page=${PAGE}" --get
    ;;
  blogger_content_detail)
    TOPIC_ID="${2:-}"; POST_ID="${3:-}"
    api_get "/resource/knowledge/blogger/content/detail" --data-urlencode "topic_id=${TOPIC_ID}&post_id=${POST_ID}" --get
    ;;
  list_lives)
    TOPIC_ID="${2:-}"; PAGE="${3:-1}"
    api_get "/resource/knowledge/lives" --data-urlencode "topic_id=${TOPIC_ID}&page=${PAGE}" --get
    ;;
  live_detail)
    TOPIC_ID="${2:-}"; LIVE_ID="${3:-}"
    api_get "/resource/knowledge/live/detail" --data-urlencode "topic_id=${TOPIC_ID}&live_id=${LIVE_ID}" --get
    ;;
  quota)
    api_get "/resource/rate-limit/quota"
    ;;
  delete_note)
    # delete_note <note_id>
    NOTE_ID="${2:-}"
    BODY=$(python3 -c "import sys,json; print(json.dumps({'note_id':sys.argv[1]}))" "$NOTE_ID")
    api_post "/resource/note/delete" "$BODY"
    ;;
  create_topic)
    # create_topic <topic_name> [description]
    TOPIC_NAME="${2:-}"; DESC="${3:-}"
    BODY=$(python3 -c "import sys,json; d={'name':sys.argv[1]}; print(json.dumps(d))" "$TOPIC_NAME")
    [[ -n "$DESC" ]] && BODY=$(python3 -c "import sys,json; d=json.loads(sys.argv[1]); d['description']=sys.argv[2]; print(json.dumps(d))" "$BODY" "$DESC")
    api_post "/resource/knowledge/create" "$BODY"
    ;;
  remove_note_from_topic)
    # remove_note_from_topic <topic_id> <note_ids_csv>
    TOPIC_ID="${2:-}"; NOTE_IDS="${3:-}"
    BODY=$(python3 -c "import sys,json; print(json.dumps({'topic_id':sys.argv[1],'note_ids':sys.argv[2].split(',')}))" "$TOPIC_ID" "$NOTE_IDS")
    api_post "/resource/knowledge/note/remove" "$BODY"
    ;;
  get_upload_config)
    # get_upload_config
    api_get "/resource/upload/config"
    ;;
  get_upload_token)
    # get_upload_token
    api_post "/resource/upload/token" "{}"
    ;;
  upload_image)
    # upload_image <file_path> <upload_token>
    FILE_PATH="${2:-}"; UPLOAD_TOKEN="${3:-}"
    if [[ ! -f "$FILE_PATH" ]]; then
      echo "Error: file not found: $FILE_PATH" >&2
      exit 1
    fi
    CONTENT_TYPE=$(file --mime-type -b "$FILE_PATH" 2>/dev/null || echo "image/jpeg")
    curl -s -X POST "https://upload.biji.com/open/api/v1/upload" \
      -H "Authorization: Bearer ${UPLOAD_TOKEN}" \
      -H "Content-Type: ${CONTENT_TYPE}" \
      --data-binary "@$FILE_PATH"
    ;;
  share_note)
    # share_note <note_id>
    NOTE_ID="${2:-}"
    BODY=$(python3 -c "import sys,json; print(json.dumps({'note_id':sys.argv[1]}))" "$NOTE_ID")
    api_post "/resource/note/share" "$BODY"
    ;;
  follow_topic_live)
    # follow_topic_live <topic_id> <live_id>
    TOPIC_ID="${2:-}"; LIVE_ID="${3:-}"
    BODY=$(python3 -c "import sys,json; print(json.dumps({'topic_id':sys.argv[1],'live_id':sys.argv[2]}))" "$TOPIC_ID" "$LIVE_ID")
    api_post "/resource/knowledge/live/follow" "$BODY"
    ;;
  list_subscribe_topics)
    # list_subscribe_topics [page]
    PAGE="${2:-1}"
    api_get "/resource/knowledge/subscribe/list" --data-urlencode "page=${PAGE}" --get
    ;;
  get_note_task_progress)
    # get_note_task_progress <task_id>
    TASK_ID="${2:-}"
    BODY=$(python3 -c "import sys,json; print(json.dumps({'task_id':sys.argv[1]}))" "$TASK_ID")
    api_post "/resource/note/task/progress" "$BODY"
    ;;
  list_topic_notes)
    # list_topic_notes <topic_id> [page]
    TOPIC_ID="${2:-}"; PAGE="${3:-1}"
    api_get "/resource/knowledge/notes" --data-urlencode "topic_id=${TOPIC_ID}&page=${PAGE}" --get
    ;;
  get_topic_detail)
    # get_topic_detail <topic_id>
    TOPIC_ID="${2:-}"
    api_get "/resource/knowledge/detail" --data-urlencode "topic_id=${TOPIC_ID}" --get
    ;;
  search_notes)
    # search_notes <keyword> [page]
    KEYWORD="${2:-}"; PAGE="${3:-1}"
    api_get "/resource/note/search" --data-urlencode "keyword=${KEYWORD}&page=${PAGE}" --get
    ;;
  get_note_tasks)
    # get_note_tasks <note_id>
    NOTE_ID="${2:-}"
    api_get "/resource/note/tasks" --data-urlencode "note_id=${NOTE_ID}" --get
    ;;
  *)
    echo "Get笔记 API Client" >&2
    echo "Usage: getnote.sh <command> [args...]" >&2
    echo "" >&2
    echo "Note CRUD:" >&2
    echo "  list_notes [since_id]          List all notes" >&2
    echo "  get_note <id> [quality]        Get note detail" >&2
    echo "  save_note <title> <content> [tags]  Create note" >&2
    echo "  update_note <id> [title] [content] [tags]  Update note" >&2
    echo "  delete_note <id>               Delete note" >&2
    echo "  share_note <id>                Share note publicly" >&2
    echo "" >&2
    echo "Tags:" >&2
    echo "  add_tags <id> <tags_csv>       Add tags to note" >&2
    echo "  delete_tag <id> <tag_id>       Remove tag from note" >&2
    echo "" >&2
    echo "Search:" >&2
    echo "  recall <query> [top_k]         Semantic search all notes" >&2
    echo "  recall_knowledge <topic_id> <query> [top_k]  Search in knowledge base" >&2
    echo "  search_notes <keyword> [page]   Keyword search notes" >&2
    echo "" >&2
    echo "Knowledge Bases:" >&2
    echo "  list_topics [page]             List knowledge bases" >&2
    echo "  get_topic_detail <id>          Get topic details" >&2
    echo "  create_topic <name> [desc]     Create knowledge base" >&2
    echo "  list_topic_notes <id> [page]   List notes in topic" >&2
    echo "  batch_add_to_topic <tid> <ids> Add notes to topic" >&2
    echo "  remove_note_from_topic <tid> <ids>  Remove notes from topic" >&2
    echo "  list_subscribe_topics [page]   List subscribed topics" >&2
    echo "" >&2
    echo "Bloggers:" >&2
    echo "  list_bloggers <topic_id> [page]  List bloggers in topic" >&2
    echo "  list_blogger_contents <tid> <follow_id> [page]  List blogger posts" >&2
    echo "  blogger_content_detail <tid> <post_id>  Get blogger post detail" >&2
    echo "" >&2
    echo "Lives:" >&2
    echo "  list_lives <topic_id> [page]   List AI-processed lives in topic" >&2
    echo "  live_detail <topic_id> <live_id>  Get live detail with AI summary" >&2
    echo "  follow_topic_live <tid> <live_id>  Subscribe to live updates" >&2
    echo "" >&2
    echo "Upload:" >&2
    echo "  get_upload_config              Get OSS upload endpoint and policy" >&2
    echo "  get_upload_token              Get one-time upload token" >&2
    echo "  upload_image <path> <token>   Upload image to OSS" >&2
    echo "" >&2
    echo "Tasks & Quota:" >&2
    echo "  poll_task <task_id>            Poll async task status" >&2
    echo "  get_note_tasks <note_id>      Get tasks for a note" >&2
    echo "  get_note_task_progress <tid>  Get async task progress" >&2
    echo "  quota                          Get API rate limit quota" >&2
    exit 1
    ;;
esac
