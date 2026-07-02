---
name: video-reset
description: "/video-reset — 复位当前期（破坏性，需确认）。把刚做完的那期从 done 拨回 pending + 游标回退到它 + 旧输出目录归档，让不满意的一期干净重跑。触发词：复位、重做当期、reset、/video-reset。"
allowed-tools:
  - Bash
  - AskUserQuestion
---

# /video-reset — 复位当前期（thin shell，user-gated）

**破坏性操作**：旧输出目录会被移入 `.archive/`，syllabus 该期状态回退为 pending，游标回退到该期。执行前必须用户确认（AskUserQuestion）。逻辑（reset_current / archive_episode_output）由 `lib/syllabus.py` / `lib/episode.py` 的单测覆盖；skill 是 thin wrapper。

## 步骤

### ① 读 config，探明将复位的期

```bash
python3 - <<'EOF'
import sys
from pathlib import Path
from lib.config import load_config, ConfigError
from lib.syllabus import load_syllabus, load_cursor, reset_current, derive_seq_slug

try:
    cfg = load_config()
except ConfigError as e:
    print(f"CONFIG_ERROR: {e}", file=sys.stderr)
    sys.exit(1)

syllabus = load_syllabus(Path(cfg.syllabus_path))
cursor   = load_cursor(Path(cfg.cursor_path))

# Dry-run: what would reset_current return?
new_syllabus, new_cursor = reset_current(syllabus, cursor)

# Find changed episode (now pending in new_syllabus, was done in original)
m_i = new_cursor.module_idx
e_i = new_cursor.episode_idx

orig_ep  = syllabus.modules[m_i].episodes[e_i]
orig_status = orig_ep.status

if orig_status != "done":
    # No done episode found — nothing to reset
    print("NO_DONE_EPISODE")
    sys.exit(0)

seq, slug = derive_seq_slug(syllabus, m_i, e_i)
title = orig_ep.title
print(f"RESET_TARGET|{seq}|{slug}|{title}")
EOF
```

- 输出 `RESET_TARGET|{seq}|{slug}|{title}` → 继续步骤 ②。
- 输出 `NO_DONE_EPISODE` → 告知用户"当前没有可复位的已完成期"，结束。
- `CONFIG_ERROR` → 展示错误，结束。

### ② AskUserQuestion 确认（破坏性，必做）

用 AskUserQuestion 向用户展示以下信息，等用户明确确认后才继续：

> **即将复位第 {seq} 期：{title}**
>
> 将执行以下操作：
> 1. 把 `episodes/{seq}-{slug}/` 移到 `episodes/.archive/{seq}-{slug}-{n}/`（旧输出归档，不可撤销）
> 2. 该期状态从 done → pending（可重跑）
> 3. 游标回退到该期
>
> **确认复位？（yes / no）**

用户回答 `no` / 取消 → 告知"已取消，未改动任何文件"，结束。
用户回答 `yes` → 继续步骤 ③。

### ③ 执行 reset_current + archive_episode_output + 落盘

```bash
python3 - <<'EOF'
import sys
from pathlib import Path
from lib.config import load_config, ConfigError
from lib.syllabus import load_syllabus, load_cursor, reset_current, save_syllabus, save_cursor, derive_seq_slug
from lib.episode import archive_episode_output, EpisodeError

try:
    cfg = load_config()
except ConfigError as e:
    print(f"CONFIG_ERROR: {e}", file=sys.stderr)
    sys.exit(1)

syllabus_path = Path(cfg.syllabus_path)
cursor_path   = Path(cfg.cursor_path)

syllabus = load_syllabus(syllabus_path)
cursor   = load_cursor(cursor_path)

# 1. reset_current — pure function, returns new objects
new_syllabus, new_cursor = reset_current(syllabus, cursor)

m_i = new_cursor.module_idx
e_i = new_cursor.episode_idx
ep  = syllabus.modules[m_i].episodes[e_i]

if ep.status != "done":
    print("NO_DONE_EPISODE")
    sys.exit(0)

seq, slug = derive_seq_slug(syllabus, m_i, e_i)

# 2. archive episode output (may return None if dir doesn't exist — OK)
try:
    archive_dst = archive_episode_output(cfg.output_dir, seq, slug)
except EpisodeError as e:
    print(f"ARCHIVE_ERROR: {e}", file=sys.stderr)
    sys.exit(1)

# 3. persist updated syllabus + cursor
save_syllabus(new_syllabus, syllabus_path)
save_cursor(new_cursor, cursor_path)

if archive_dst:
    print(f"ARCHIVED|{archive_dst}")
else:
    print(f"NO_ARCHIVE|episodes/{seq}-{slug} not found, skipped")
print(f"RESET_DONE|{seq}|{slug}|{ep.title}")
EOF
```

### ④ 报告结果

成功后向用户报告：

```
第 {seq} 期「{title}」已复位：
- 旧输出：归档到 episodes/.archive/{seq}-{slug}-{n}/（若目录原本存在）
- 状态：done → pending（可重跑）
- 游标：已回退到该期

下一步：运行 /video-make 重新出这一期。
```

若步骤 ③ 出现 `ARCHIVE_ERROR` → 展示错误并停止（未落盘前已出错，syllabus/cursor 未改动，安全）。

## 安全约束

- **先确认，后执行**：AskUserQuestion 在步骤 ② 拦截，用户不确认不执行步骤 ③。
- **纯函数先行**：reset_current 不改磁盘；archive 和落盘都在用户确认后才执行。
- **archive 不覆盖**：n 递增找空位，不会覆盖上一次 archive。
- **无 done 期保护**：步骤 ① 探明无 done 期则提前退出，不进入确认流程。
