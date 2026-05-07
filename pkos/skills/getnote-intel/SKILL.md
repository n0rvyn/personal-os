---
name: getnote-intel
description: "PKOS intelligence feed from Get笔记 — polls blogger/live subscriptions for new content, captures to Notion Pipeline or Obsidian. Triggered via Adam cron or /pkos intel getnote."
model: sonnet
allowed-tools:
  - Read
  - Bash
---

## Overview

从 Get笔记 的博主订阅和直播订阅中主动发现新内容，推送到 PKOS。与 inbox 的主动 capture 正交——这是被动发现管道。

## Arguments

- `--dry-run`: 显示将获取的内容，不写入
- `--source SOURCE`: 仅处理指定类型（blogger | live | all）

## Prerequisites

1. Get笔记 API credentials in `~/.claude/pkos/config.yaml`（已在 getnote_api 节配置）
2. 每个 topic 的 blogger/live 订阅需用户在 Get笔记 app 内完成

## Process

### Step 1: Load Config and State

```bash
GETNOTE_SCRIPT="${CLAUDE_PLUGIN_ROOT}/../../pkos/skills/getnote/scripts/getnote.sh"
GETNOTE_PARSER="${CLAUDE_PLUGIN_ROOT}/../../pkos/skills/getnote/scripts/getnote.py"
CURSOR_SCRIPT="${CLAUDE_PLUGIN_ROOT}/scripts/cursor.py"

# 读取 config
CONFIG_FILE=~/.claude/pkos/config.yaml
API_KEY=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG_FILE')).get('getnote_api',{}).get('api_key',''))" 2>/dev/null)
CLIENT_ID=$(python3 -c "import yaml; print(yaml.safe_load(open('$CONFIG_FILE')).get('getnote_api',{}).get('client_id',''))" 2>/dev/null)
[[ -z "$API_KEY" ]] && echo "[getnote-intel] ERROR: getnote_api.api_key not set in ~/.claude/pkos/config.yaml" && exit 1

# 读取 cursor state (checkpoint from prior run, if any)
CURSOR_JSON=$(python3 "$CURSOR_SCRIPT" show 2>/dev/null || echo "{}")
LAST_BLOGGER_IDS=$(echo "$CURSOR_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(' '.join(d.get('seen_blogger_posts',[])))" 2>/dev/null)
LAST_LIVE_IDS=$(echo "$CURSOR_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(' '.join(d.get('seen_lives',[])))" 2>/dev/null)
RESUME_TOPIC=$(echo "$CURSOR_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('last_topic_id','') or '')" 2>/dev/null)
RESUME_IDX=$(echo "$CURSOR_JSON" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('last_blogger_idx',-1))" 2>/dev/null)

if [[ -n "$RESUME_TOPIC" && "$RESUME_IDX" -ge 0 ]]; then
  echo "[getnote-intel] Resuming from topic=${RESUME_TOPIC}, blogger_idx=${RESUME_IDX} (prior run incomplete)"
fi
```

### Step 2: 获取 Topic 列表

```bash
TOPICS_JSON=$($GETNOTE_SCRIPT list_topics 1 2>/dev/null)
# 解析 data.topics[]，提取 topic_id 和 topic_name 到数组
TOPIC_IDS=()
TOPIC_NAMES=()
while IFS= read -r line; do
  IFS=$'\t' read -r tid tname _count _desc <<< "$line"
  [[ -n "$tid" ]] && TOPIC_IDS+=("$tid") && TOPIC_NAMES+=("$tname")
done < <(echo "$TOPICS_JSON" | "$GETNOTE_PARSER" parse-topics 2>/dev/null)
echo "[getnote-intel] Found ${#TOPIC_IDS[@]} topics"
[[ "${#TOPIC_IDS[@]}" -eq 0 ]] && echo "[getnote-intel] No topics found. Exiting." && exit 0
```

### Step 3: 处理博主内容

```bash
# 对每个 topic 调用 list_bloggers（fix C1: 命令名匹配 getnote.sh）
SKIP_THIS_TOPIC=0
for ((i=0; i<${#TOPIC_IDS[@]}; i++)); do
  TOPIC_ID="${TOPIC_IDS[$i]}"
  TOPIC_NAME="${TOPIC_NAMES[$i]}"

  # Skip-to-resume: if RESUME_TOPIC is set, skip all topics until we reach it
  if [[ -n "$RESUME_TOPIC" && "$TOPIC_ID" != "$RESUME_TOPIC" ]]; then
    echo "[getnote-intel] Skipping topic ${TOPIC_NAME} (waiting for resume topic ${RESUME_TOPIC})"
    continue
  fi
  # Once we reach the resume topic, clear the flag so subsequent topics process normally
  if [[ -n "$RESUME_TOPIC" && "$TOPIC_ID" == "$RESUME_TOPIC" ]]; then
    echo "[getnote-intel] Reached resume topic ${TOPIC_NAME} — clearing RESUME_TOPIC"
    RESUME_TOPIC=""
  fi

  echo "[getnote-intel] Topic: ${TOPIC_NAME}"

  # 3a. list_bloggers 获取博主列表
  BLOGGERS_JSON=$($GETNOTE_SCRIPT list_bloggers "$TOPIC_ID" 1 2>/dev/null)

  # 解析每个 blogger 的 follow_id、account_name、notes_count
  FOLLOW_IDS=()
  while IFS= read -r line; do
    IFS=$'\t' read -r fid account_name notes_count <<< "$line"
    [[ -n "$fid" ]] && FOLLOW_IDS+=("$fid")
  done < <(echo "$BLOGGERS_JSON" | "$GETNOTE_PARSER" parse-bloggers 2>/dev/null)

  # 3b. 对每个 blogger 获取内容
  for ((bi=0; bi<${#FOLLOW_IDS[@]}; bi++)); do
    FOLLOW_ID="${FOLLOW_IDS[$bi]}"

    # Skip bloggers before the resume index within the resumed topic
    if [[ -n "$RESUME_IDX" && "$bi" -lt "$RESUME_IDX" ]]; then
      echo "[getnote-intel] Skipping blogger idx ${bi} (before resume index ${RESUME_IDX})"
      continue
    fi
    # Clear RESUME_IDX after the first blogger in the resumed topic is processed
    if [[ -n "$RESUME_IDX" && "$bi" -eq "$RESUME_IDX" ]]; then
      RESUME_IDX=""
    fi

    CONTENTS_JSON=$($GETNOTE_SCRIPT list_blogger_contents "$TOPIC_ID" "$FOLLOW_ID" 1 2>/dev/null)
    CONTENTS_TSV=$(echo "$CONTENTS_JSON" | "$GETNOTE_PARSER" parse-contents 2>/dev/null)

    # 3c. 过滤新内容（不在 LAST_BLOGGER_IDS 中）并写入
    # Collect POST_IDS_CSV for cursor update
    POST_IDS_CSV=$(printf "%s\n" "$CONTENTS_TSV" | LAST_BLOGGER_IDS="$LAST_BLOGGER_IDS" python3 -c "
import os
import sys

last_ids = set(os.environ.get('LAST_BLOGGER_IDS', '').split())
pids = []
for line in sys.stdin:
    parts = line.rstrip('\n').split('\t')
    post_id_alias = parts[0] if parts else ''
    if post_id_alias not in last_ids:
        pids.append(post_id_alias)
print(','.join(pids))
" 2>/dev/null)

    printf "%s\n" "$CONTENTS_TSV" | LAST_BLOGGER_IDS="$LAST_BLOGGER_IDS" TOPIC_NAME="$TOPIC_NAME" TOPIC_ID="$TOPIC_ID" python3 -c "
import os
import subprocess
import sys

last_ids = os.environ.get('LAST_BLOGGER_IDS','').split()
for line in sys.stdin:
    parts = line.rstrip('\n').split('\t')
    if len(parts) < 5:
        continue
    post_id_alias, post_title, post_summary, post_media_text, post_create_time = parts[:5]
    if post_id_alias in last_ids:
        continue
    title = post_title or f'Blogger post {post_id_alias}'
    content = (post_summary or post_media_text)[:200]
    slug = title.lower().replace(' ','-')[:40]
    topic_name = os.environ.get('TOPIC_NAME','')
    topic_id = os.environ.get('TOPIC_ID','')

    # Write Obsidian note
    note_path = f\"50-References/getnote-blogger-{slug}.md\"
    vault_path = os.path.expanduser(f\"~/Obsidian/PKOS/{note_path}\")
    os.makedirs(os.path.dirname(vault_path), exist_ok=True)
    with open(vault_path, 'w') as f:
        f.write(f\"\"\"---
type: reference
source: getnote-blogger
created: {post_create_time or 'unknown'}
tags: [getnote, blogger, {topic_name}]
quality: 0
citations: 1
related: []
---

# {title}

> [!abstract] Source
> via Get笔记 [{topic_name}](getnotes://topic/{topic_id})
> Date: {post_create_time or 'unknown'}

{content}...

## Connections
- [[MOC-{topic_name}]]
\"\"\")
    print(f\"Wrote: {note_path}\")

    # Create Notion Pipeline entry
    subprocess.run([
        'python3', '-c',
        f\"import subprocess; subprocess.run(['NO_PROXY=*','python3',os.path.expanduser('~/.claude/skills/notion-with-api/scripts/notion_api.py'),'create-db-item','32a1bde4-ddac-81ff-8f82-f2d8d7a361d7',f'{title}','--props','{{\\\"Status\\\":\\\"intel\\\",\\\"Source\\\":\\\"getnote-blogger\\\",\\\"Topics\\\":\\\"{topic_name}\\\",\\\"Priority\\\":\\\"low\\\"}}'])\"
    ], capture_output=True)
    print(f\"Notion: {title}\")
"

    # Persist cursor after each blogger: topic + idx + collected post IDs
    # POST_IDS_CSV is a comma-separated list of post UIDs collected for this blogger
    python3 - <<PYEOF
import sys
sys.path.insert(0, "${CURSOR_SCRIPT}".rsplit("/", 1)[0])
import cursor
post_ids = [p for p in "${POST_IDS_CSV}".split(",") if p]
cursor.mark_progress("${TOPIC_ID}", ${bi}, post_ids=post_ids)
PYEOF
  done
done
```

### Step 4: 处理直播内容

```bash
# 对每个 topic 调用 list_lives
for ((i=0; i<${#TOPIC_IDS[@]}; i++)); do
  TOPIC_ID="${TOPIC_IDS[$i]}"
  TOPIC_NAME="${TOPIC_NAMES[$i]}"

  # 4a. list_lives 获取已完成的 AI 处理直播列表
  LIVES_JSON=$($GETNOTE_SCRIPT list_lives "$TOPIC_ID" 1 2>/dev/null)
  LIVES_TSV=$(echo "$LIVES_JSON" | "$GETNOTE_PARSER" parse-lives 2>/dev/null)

  # 解析并过滤新直播（不在 LAST_LIVE_IDS 中）
  printf "%s\n" "$LIVES_TSV" | LAST_LIVE_IDS="$LAST_LIVE_IDS" TOPIC_NAME="$TOPIC_NAME" TOPIC_ID="$TOPIC_ID" python3 -c "
import os
import subprocess
import sys

last_ids = os.environ.get('LAST_LIVE_IDS','').split()
for line in sys.stdin:
    parts = line.rstrip('\n').split('\t')
    if len(parts) < 7:
        continue
    lid, name, status, follow_time, post_title, post_summary, post_media_text = parts[:7]
    if lid in last_ids:
        continue
    title = post_title or name or f'Live {lid}'
    ai_summary = post_summary or post_media_text or title
    slug = title.lower().replace(' ','-')[:40]
    topic_name = os.environ.get('TOPIC_NAME','')
    topic_id = os.environ.get('TOPIC_ID','')

    # Write Obsidian note
    note_path = f\"50-References/getnote-live-{slug}.md\"
    vault_path = os.path.expanduser(f\"~/Obsidian/PKOS/{note_path}\")
    os.makedirs(os.path.dirname(vault_path), exist_ok=True)
    with open(vault_path, 'w') as f:
        f.write(f\"\"\"---
type: reference
source: getnote-live
created: {follow_time or 'unknown'}
tags: [getnote, live, {topic_name}]
quality: 0
citations: 1
related: []
---

# {title}

> [!abstract] Get笔记 AI Summary
> {ai_summary}
> 来源：[完整直播转写](getnotes://live/{lid}) via Get笔记 [{topic_name}](getnotes://topic/{topic_id})

## Connections
- [[MOC-{topic_name}]]
\"\"\")
    print(f\"Wrote: {note_path}\")

    # Create Notion Pipeline entry
    subprocess.run([
        'python3', '-c',
        f\"import subprocess; subprocess.run(['NO_PROXY=*','python3',os.path.expanduser('~/.claude/skills/notion-with-api/scripts/notion_api.py'),'create-db-item','32a1bde4-ddac-81ff-8f82-f2d8d7a361d7',f'{title}','--props','{{\\\"Status\\\":\\\"intel\\\",\\\"Source\\\":\\\"getnote-live\\\",\\\"Topics\\\":\\\"{topic_name}\\\",\\\"Priority\\\":\\\"low\\\"}}'])\"
    ], capture_output=True)
    print(f\"Notion: {title}\")
"

  # Persist live progress: topic + index (no post IDs in live track)
  python3 "$CURSOR_SCRIPT" mark "$TOPIC_ID" $i 2>/dev/null || true
done
```

### Step 5: Final State Consolidation

```bash
# All blogger/live processing is complete. Clear the checkpoint cursor to indicate
# a clean run — the intermediate per-blogger writes from Step 3 already persisted
# post IDs into the cursor file; here we reset topic/idx so the next run starts fresh.
python3 "$CURSOR_SCRIPT" reset
echo "Checkpoint cursor cleared — clean completion"
```

### Step 6: Report

```
Get笔记 Intel Feed — {date}
  Topics scanned: {count}
  New blogger posts: {count} → Obsidian + Notion
  New lives: {count} → Obsidian + Notion
  Errors: {count}
```

## Error Handling

| Error | Action |
|-------|--------|
| API 401/403 | Log fatal, exit 1 |
| API 429 | Log warning, wait 60s retry once |
| API 5xx | Log error, skip topic, continue |
| State write failure | Log error, continue（内容已写入，不丢数据）|

## Config

`~/.claude/pkos/config.yaml` 中 `getnote_api` 节已在 `pkos-config.template.yaml` 定义。
