---
name: video-plan
description: "/video-plan — 规划跑步知识视频大纲（Stage 0）。读权威层快照文档 → dispatch planner persona 写大纲草案 → 用户 review 增删改 → 锁定 syllabus + 初始化游标，供 /video-make 出片消费。触发词：规划、大纲、课程计划、/video-plan。"
allowed-tools:
  - Bash
  - Read
  - Agent
---

# /video-plan — Stage 0 大纲规划（thin shell）

`/video-plan` 是 Stage 0 课程大纲规划入口。判断归 AI（planner persona），机械存盘/校验/游标初始化归 `lib/syllabus.promote_draft`（不靠 Claude 自律）。

## 前置：确认内容项目存在

`/video-plan` 在内容项目根目录运行（内含 `.video-studio/config.yaml`）。
若找不到配置（`ConfigNotFound`），询问用户：

> 找不到内容项目配置。请选择：
> A) 在当前目录建内容项目（scaffold）
> B) 把旧 `~/.video-studio/` 迁移到新方案（见 `/video-make` 迁移流程）

**选 A**：

```bash
cd /path/to/new-content-project
python3 -c "
from pathlib import Path
from lib.content_project import scaffold_content_project
scaffold_content_project(Path('.'), book='<书系名>')
print('DONE')
"
```

脚手架建好并填写 `.video-studio/config.yaml` 后再重跑 `/video-plan`。

## 步骤

### ① 收集快照 docs + 确认书系名

用 Bash 列出 `video-studio/syllabus/source/<book>/` 下所有 `.md` 文件的**绝对路径**，确认书系名（`book` 字符串，如 `runetic-training-science`）：

```bash
ls "${CLAUDE_PLUGIN_ROOT}/syllabus/source/"            # 列出已有书系目录
ls "${CLAUDE_PLUGIN_ROOT}/syllabus/source/<book>/"     # 列出该书系的快照文件
```

把文件路径列表和书系名带入下一步。

### ② dispatch planner persona 写草案

用 Agent 工具 dispatch `planner` persona，写 `syllabus_draft.yaml` 到 scratch：

```
用 Agent dispatch agents/planner.md，
输入：
  - 所有快照文件绝对路径（syllabus/source/<book>/*.md）
  - 草案输出绝对路径：${CLAUDE_PLUGIN_ROOT}/syllabus/<book>_draft.yaml

planner 会：读文档 → 按主题聚类 → 写 YAML 草案 → 简短总结供 review
```

**LLM dispatch 规则：**
- 用 `claude -p --model sonnet`（personal-os 标准路由）。
- **永不换直连 API。** 所有 LLM 调用经 `claude -p`，不走 `ANTHROPIC_API_KEY` 直连。

### ③ 展示草案，用户 review

草案写好后，用 Read 读取草案文件，按以下格式展示给用户（逐模块列期）：

```
模块 N：<模块名>
  期 NNN | <标题> | source=<Daniels|UESCA|Minetti|xlsx> | <✅|⚠️|🔧> | <learning_goal 摘要>
```

**溯源状态规则（DP-602/DP-603）：**
- ✅ — 已溯源到原始文献，客观可引。learning_goal 可直述「掌握 X 的科学原理」。
- ⚠️ — 工程经验估计，未直接溯源到原始文献。learning_goal 必须注明「工程估计，非源直出」。
- 🔧 — 产品设定 / 上限参数，Runetic 产品决策。learning_goal 必须注明「产品设定，非源直出」。

⚠️/🔧 内容可以成期（包含但标注，不是排除），但 `verify_status` 必须透传。

让用户增删改：可以调整标题、合并/拆分期、修改 learning_goal、删除不必要的期。确认前不落盘 syllabus。

**等用户点头后再执行步骤 ④。**

### ④ 用户点头 → 校验 + 锁定 syllabus + 初始化游标

用户确认草案后，用 Bash 调 `promote_draft`（校验 → 写 `syllabus/{book}.yaml` + 初始化 `cursor.yaml`）：

```bash
cd "${CLAUDE_PLUGIN_ROOT}"

python3 -c "
from pathlib import Path
from lib.syllabus import promote_draft

promote_draft(
    draft_path=Path('syllabus/<book>_draft.yaml'),
    syllabus_path=Path('syllabus/<book>.yaml'),
    cursor_path=Path('syllabus/<book>_cursor.yaml'),
    book='<book>',
)
print('LOCKED')
"
```

`promote_draft` 会：
1. 读草案 YAML（容 ```yaml fence）
2. 校验每期含 `source`/`verify_status`/`doc_refs`（缺字段 → 命名报错，不静默）
3. 写 `syllabus/{book}.yaml`
4. 初始化 `cursor.yaml` 到 `(module_idx=0, episode_idx=0)`

校验失败（坏草案、缺字段）→ 展示错误给用户，不落盘半截文件。

### ⑤ 锁定后告知用户

成功后报告：
- `syllabus/{book}.yaml` 路径（已锁定）
- `syllabus/{book}_cursor.yaml` 路径（已初始化，游标在第 1 期）
- 总模块数、总期数、⚠️/🔧 期数
- 下一步：`/video-make` 自动取第 1 期出片

## 相关资源

- **planner persona**：`agents/planner.md`（快照读取规则、源标注规则、YAML 格式）
- **syllabus 数据模型**：`lib/syllabus.py`（`promote_draft` / `load_syllabus` / `load_cursor`）
- **出片入口**：`/video-make`（Stage 1，消费 syllabus + cursor）
- **快照目录**：`syllabus/source/<book>/`（权威层训练科学文档）
