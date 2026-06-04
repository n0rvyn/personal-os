# Per-book 状态文件 Schema(.novel/state.json)

每本书一份,放书目录的 `.novel/state.json`(隐藏,不碍作者浏览正文)。断点续写恢复 + 跨 session 知道写到哪。

```json
{
  "book": "书名",
  "定位": {
    "爽文档位": "均衡",
    "流派": "仙侠",
    "平台": "起点男频",
    "日更字数": 3000
  },
  "bible_path": "bible.md",
  "outline_path": "outline.md",
  "current_卷": 2,
  "current_章": 47,
  "chapter_step": "起草",
  "total_words": 188000,
  "chapters_done": 46,
  "last_钩子": "上一章章末悬念原文,供下一章开头接续",
  "pending_伏笔": 7,
  "last_updated": "2026-06-04T12:00:00"
}
```

## 字段说明

| 字段 | 含义 | 备注 |
|------|------|------|
| `book` | 书名 | |
| `定位` | 爽文档位/流派/平台/日更字数 | 立项时写入,与 bible 第 1 块同步 |
| `bible_path` / `outline_path` | 相对书目录的路径 | 通常 `bible.md` / `outline.md` |
| `current_卷` / `current_章` | 当前进度 | |
| `chapter_step` | 单章循环阶段 | `章纲细化` \| `起草` \| `自评` \| `改` \| `评审` \| `定稿` \| `圣经回灌` |
| `total_words` | 累计字数 | |
| `chapters_done` | 已定稿章数 | |
| `last_钩子` | 上一章章末钩子原文 | **单列**:下一章开头要接住它 |
| `pending_伏笔` | 未收伏笔计数 | 从 bible 伏笔账本同步;过高可触发"该收伏笔了"提醒 |
| `last_updated` | ISO 时间戳 | 每步更新前写 |

## 约束

- 只写书目录内(cwd 模型,D-010)。plugin 不维护跨书的中央注册表。
- 每个 chapter_step 推进前更新 `last_updated`,保证断点恢复。
