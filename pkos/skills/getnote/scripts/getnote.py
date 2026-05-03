#!/usr/bin/env python3
"""
Get笔记 JSON 解析辅助脚本
用于处理 getnote.sh 输出的 JSON 数据
"""
import sys
import json
import os
import subprocess


def filter_notes_by_tag(notes_json, exclude_tag):
    """过滤掉包含指定 tag 的笔记"""
    data = json.loads(notes_json)
    filtered = []
    for note in data.get('notes', []):
        tag_names = [t.get('name', '') for t in note.get('tags', [])]
        if exclude_tag not in tag_names:
            filtered.append(note)
    return filtered


def format_note_summary(note):
    """格式化笔记摘要"""
    return {
        'id': note.get('id'),
        'title': note.get('title', 'Untitled'),
        'note_type': note.get('note_type', 'plain_text'),
        'tags': [t.get('name') for t in note.get('tags', []) if t.get('name')],
        'updated_at': note.get('updated_at', ''),
    }


def parse_recall_results(recall_json, include_external=False):
    """解析 recall 结果，可选过滤外部类型"""
    data = json.loads(recall_json)
    results = data.get('results', [])
    if not include_external:
        results = [r for r in results if r.get('note_type') in ('NOTE', 'FILE')]
    return results


def parse_topics(topics_json):
    """解析 topics 列表"""
    data = json.loads(topics_json)
    topics = data.get('topics', [])
    return [{'id': t.get('id'), 'name': t.get('name', ''),
             'description': t.get('description', ''), 'note_count': t.get('note_count', 0)}
            for t in topics]


def parse_bloggers(bloggers_json):
    """解析 blogger 列表"""
    data = json.loads(bloggers_json)
    bloggers = data.get('bloggers', [])
    return [{'follow_id': b.get('follow_id'), 'name': b.get('name', ''),
             'avatar': b.get('avatar', ''), 'following_count': b.get('following_count', 0)}
            for b in bloggers]


def parse_blogger_contents(contents_json):
    """解析博主内容列表"""
    data = json.loads(contents_json)
    contents = data.get('contents', [])
    return [{'post_id': c.get('post_id'), 'title': c.get('title', ''),
             'content': c.get('content', ''), 'created_at': c.get('created_at', ''),
             'cover_image': c.get('cover_image', '')}
            for c in contents]


def parse_lives(lives_json):
    """解析直播列表"""
    data = json.loads(lives_json)
    lives = data.get('lives', [])
    return [{'live_id': l.get('live_id'), 'title': l.get('title', ''),
             'ai_summary': l.get('ai_summary', ''), 'created_at': l.get('created_at', ''),
             'status': l.get('status', '')}
            for l in lives]


def parse_upload_config(config_json):
    """解析上传配置"""
    data = json.loads(config_json)
    return {'upload_url': data.get('upload_url', ''), 'policy': data.get('policy', ''),
            'signature': data.get('signature', ''), 'host': data.get('host', '')}


def parse_upload_token(token_json):
    """解析上传 token 响应"""
    data = json.loads(token_json)
    return {'token': data.get('token', ''), 'expire_at': data.get('expire_at', '')}


def parse_quota(quota_json):
    """解析配额信息"""
    data = json.loads(quota_json)
    return {'used': data.get('used', 0), 'limit': data.get('limit', 0),
            'reset_at': data.get('reset_at', ''), 'remaining': data.get('remaining', 0)}


def parse_topic_notes(notes_json):
    """解析 topic 内的笔记列表"""
    data = json.loads(notes_json)
    notes = data.get('notes', [])
    return [{'id': n.get('id'), 'title': n.get('title', ''),
             'note_type': n.get('note_type', 'plain_text'), 'updated_at': n.get('updated_at', '')}
            for n in notes]


def write_notes_to_obsidian(notes, vault_path, note_type='reference', source='getnote'):
    """批量写笔记到 Obsidian vault"""
    for note in notes:
        slug = (note.get('title', note.get('id', 'untitled')) or 'untitled')[:40].lower()
        slug = ''.join(c if c.isalnum() or c in '-_' else '-' for c in slug)
        note_path = os.path.join(vault_path, f'getnote-{note_type}-{slug}.md')
        os.makedirs(os.path.dirname(note_path), exist_ok=True)
        with open(note_path, 'w') as f:
            f.write(f"""---
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
""")
        print(f"Wrote: {note_path}")
    return len(notes)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: getnote.py <command> [args...]", file=sys.stderr)
        print("\nCommands:", file=sys.stderr)
        print("  filter-untagged [exclude_tag]     Filter notes by tag (stdin: notes JSON, stdout: tab-separated)", file=sys.stderr)
        print("  summarize-notes                   Format note summaries (stdin: notes JSON, stdout: JSON lines)", file=sys.stderr)
        print("  filter-recall [--include-external] Parse recall results (stdin: recall JSON, stdout: JSON)", file=sys.stderr)
        print("  parse-topics                      Parse topics list (stdin: JSON, stdout: tab-separated)", file=sys.stderr)
        print("  parse-bloggers                    Parse bloggers list (stdin: JSON, stdout: tab-separated)", file=sys.stderr)
        print("  parse-contents                    Parse blogger contents (stdin: JSON, stdout: tab-separated)", file=sys.stderr)
        print("  parse-lives                       Parse lives list (stdin: JSON, stdout: tab-separated)", file=sys.stderr)
        print("  parse-quota                       Parse quota info (stdin: JSON, stdout: human-readable)", file=sys.stderr)
        print("  parse-upload-token                 Parse upload token (stdin: JSON, stdout: JSON)", file=sys.stderr)
        print("  parse-topic-notes                 Parse topic notes (stdin: JSON, stdout: tab-separated)", file=sys.stderr)
        print("  write-obsidian <vault_path> <type> [source]  Batch write notes to Obsidian (stdin: notes JSON)", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'filter-untagged':
        exclude_tag = sys.argv[2] if len(sys.argv) > 2 else 'pkos-synced'
        input_json = sys.stdin.read()
        filtered = filter_notes_by_tag(input_json, exclude_tag)
        for note in filtered:
            print(f"{note['id']}\t{note.get('title','Untitled')}\t{note.get('note_type','plain_text')}")

    elif cmd == 'summarize-notes':
        input_json = sys.stdin.read()
        data = json.loads(input_json)
        for note in data.get('notes', []):
            summary = format_note_summary(note)
            print(json.dumps(summary))

    elif cmd == 'filter-recall':
        include_external = '--include-external' in sys.argv
        input_json = sys.stdin.read()
        results = parse_recall_results(input_json, include_external)
        print(json.dumps({'results': results}))

    elif cmd == 'parse-topics':
        input_json = sys.stdin.read()
        topics = parse_topics(input_json)
        for t in topics:
            print(f"{t['id']}\t{t['name']}\t{t.get('note_count', 0)}\t{t.get('description', '')}")

    elif cmd == 'parse-bloggers':
        input_json = sys.stdin.read()
        bloggers = parse_bloggers(input_json)
        for b in bloggers:
            print(f"{b['follow_id']}\t{b['name']}\t{b.get('following_count', 0)}")

    elif cmd == 'parse-contents':
        input_json = sys.stdin.read()
        contents = parse_blogger_contents(input_json)
        for c in contents:
            print(f"{c['post_id']}\t{c['title']}\t{c.get('created_at', '')}")

    elif cmd == 'parse-lives':
        input_json = sys.stdin.read()
        lives = parse_lives(input_json)
        for l in lives:
            print(f"{l['live_id']}\t{l['title']}\t{l.get('status', '')}\t{l.get('ai_summary', '')[:50]}")

    elif cmd == 'parse-quota':
        input_json = sys.stdin.read()
        quota = parse_quota(input_json)
        print(f"Used: {quota['used']} / {quota['limit']} | Remaining: {quota['remaining']} | Resets: {quota['reset_at']}")

    elif cmd == 'parse-upload-token':
        input_json = sys.stdin.read()
        token = parse_upload_token(input_json)
        print(json.dumps(token))

    elif cmd == 'parse-topic-notes':
        input_json = sys.stdin.read()
        notes = parse_topic_notes(input_json)
        for n in notes:
            print(f"{n['id']}\t{n['title']}\t{n.get('note_type','plain_text')}")

    elif cmd == 'write-obsidian':
        vault_path = sys.argv[2] if len(sys.argv) > 2 else os.path.expanduser('~/Obsidian/PKOS/50-References')
        note_type = sys.argv[3] if len(sys.argv) > 3 else 'reference'
        source = sys.argv[4] if len(sys.argv) > 4 else 'getnote'
        input_json = sys.stdin.read()
        data = json.loads(input_json)
        notes = data.get('notes', data.get('results', []))
        count = write_notes_to_obsidian(notes, vault_path, note_type, source)
        print(f"Wrote {count} notes to {vault_path}")

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
