---
name: novel-new
description: 从零开新书(干净写)。当用户说"开新书""从头写一本""干净写""new novel from scratch""novel-new"时使用。编排全流程:立项→分卷章纲→逐章写。不适用:接已有手稿(novel-continue);扩充已有骨架(novel-expand)。
model: opus
user_invocable: true
allowed-tools:
  - Read
  - Glob
  - Skill
---

# novel-new:从零开新书(干净写全流程)

高频模式之一(crystal D-011)。薄入口,按顺序编排引擎三段:

1. **立项** → 调 `novel-kickoff`(设定位/创意/金手指/主角,产初始 bible + state)。
2. **分卷章纲** → 调 `novel-outline`(卷章 + 伏笔依赖图)。
3. **逐章写** → 调 `novel-write`(从第 1 章起,单章循环)。

## 流程
1. 确认 cwd 是空/新书目录(无则提示 cd 到目标目录)。
2. 依次引导走完三段;每段产物落 cwd(bible.md / outline.md / chapters/)。
3. 段与段之间作者可停顿确认(立项后看 bible、章纲后看 outline)。

## 边界
- cwd 模型(D-010)。
- 各段沿用其 skill 的成本姿态(立项/章纲 Opus,起草 Opus,抽取/连续性 Sonnet)。
- 不重复实现引擎,只编排(skills grow from use)。
