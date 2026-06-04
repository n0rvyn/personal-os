---
name: novel-expand
description: 扩写(把已有骨架/短稿/细纲扩充到目标字数)。当用户说"扩写""把这个大纲写成正文""注水到X字""把短稿铺开""expand this outline""novel-expand"时使用。把已有骨架映射成章纲→逐章扩充,跳过立项。不适用:从零开新书(novel-new);接已写好的成稿往下续(novel-continue)。
model: opus
user_invocable: true
allowed-tools:
  - Read
  - Write
  - Glob
  - Skill
  - AskUserQuestion
---

# novel-expand:扩写(骨架→正文)

高频模式之一(crystal D-011)。作者已有骨架/细纲/短稿,要扩充到目标日更字数。

## 流程

1. **读骨架**:作者给的大纲/短稿(cwd 文件或粘贴)。
2. **建轻量圣经**(若无):从骨架抽出定位/主角/金手指/世界观要点,确认后写 `bible.md`。无需走完整 `novel-kickoff`(跳过立项的从零创意环节)。
3. **映射成章纲**:把骨架的每个节点映射成 `outline.md` 的章纲(补齐四要素:剧情/爽点/钩子/伏笔动作)。可调 `novel-outline` 的映射逻辑。
4. **逐章扩充** → 调 `novel-write`,每章把对应骨架节点扩写到目标字数,过自评+评审+回灌。

## 与新写的区别
- 不做从零创意立项;骨架已定剧情走向,扩写聚焦"把节点铺成有爽点有钩子的正文"。
- 扩写仍要喂 rubric:防止"注水"变成无爽点的流水账(留存轴照评)。

## 边界
- 扩写不等于稀释:每章仍需命中目标爽点 + 章末钩子(否则 rubric 留存轴判偏弱)。
- cwd 模型(D-010)。
