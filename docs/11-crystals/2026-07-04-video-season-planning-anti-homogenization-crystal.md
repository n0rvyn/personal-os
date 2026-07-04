---
type: crystal
status: active
tags: [season-planning, anti-homogenization, covered-ground, digest-writer]
refs: [docs/06-plans/2026-07-04-video-season-planning-anti-homogenization-design.md]
---

# Decision Crystal: 整季大纲规划 + 台本去同质化（跑者学堂 / video-studio）

Date: 2026-07-04

## Initial Idea

用户："先解决大纲规划，去除同质化的问题，然后再做 P7 验收。插个 phase。" 后续厘清："大纲里写话题，在一档节目开始的时候应该根据教材给定固定下来；而转写层，应该是 AI 导演吧，它在写台本的时候，需要读跨期记忆来避免同质。这是一套。" 层级："教材=多季 一季=多模块 一模块=多期。"

## Discussion Points

1. **同质化的根**：前 5 期都落进「你以为的 vs 真相」神话破除同一模板。核对代码发现 `digest-writer` 写台本 inputs 只有 `[extract(本期教材), config]`、看不到兄弟期 → 类比/例子/钩子一期期趋同。决定：治根不打补丁。
2. **两层分工**：AI 曾把"大纲层"框成"给每期硬派差异化角度/结构"。用户纠正 → 大纲层就是"照教材把题目定死、开季固定"，差异化不在大纲、在写台本层。
3. **注入点命名对齐**：用户说"转写层是 AI 导演"，核对管线后厘清——写台本的是 `digest-writer`，`director`(导演) 是其后的分镜/画面步。用户确认："指的就是写台本的那步，并非把做镜头的导演提前。"跨期记忆加在 `digest-writer`。
4. **proven 分析**：查到 podcast-studio 已解决同一问题（covered-ground 四件套 + 隔离蒸馏器 + 免费本地 embedding）。方向 = 照搬到 video-studio，而非从零设计。
5. **大纲=复用现有 planner**：查到 `agents/planner.md` 已是整季课程规划器（读教材 source→模块化大纲）。running-class 只出了 5 期是因为没跑全。决定：复用它、扩季级、加锁定动作。
6. **规划粒度**：用户"一次铺满一季"、"一套教材出几季"。决定：补全第一季（未覆盖 source 01/02/03/07/09→~10-11 期），不一次铺满全教材 ~28 概念。
7. **内容层级四层**：教材→季→模块→期。现有 schema 缺"季"层，需在模块之上插入 season。

## Rejected Alternatives

- **大纲层给每期硬派差异化角度/结构**：Rejected because — 用户明确大纲只按教材定题、固定；差异化交给写台本层的跨期记忆，不在大纲阶段做。
- **简化 covered-ground（去掉隔离蒸馏器等）**：Rejected because — 用户"照搬可以"选四件套全套；隔离蒸馏器是 podcast 用真教训换来的唯一能切断"回声锁死"的设计，简化即废。
- **关键词相似度（替代 embedding）**：Rejected because — 反同质价值在抓"换了词的同一套"，关键词漏改词复用；且 Apple 本地 embedding 免费，无成本理由降级。
- **预生成所有期 transcript**：Rejected because — 贵 + 会过时 + 削弱忠实门；台本仍每期现生成、靠跨期记忆去重。
- **一次铺满全教材 ~28 概念全季**：Rejected because — review 负担重、远期题目会过时；一次一季。

## Decisions (machine-readable)

- [D-1] syllabus 插入 `season` 层：`教材→季→模块→期`；cursor 从 `(module,episode)` 变 `(season,module,episode)`；现有 running-class 5 期归第一季。
- [D-2] 大纲=复用现有 `agents/planner.md`，扩为 season 级输出（一次规划一个完整季）+ 加锁定动作（用户 review 后 `pending`→`locked`，锁前不可被 select_next 跑、不进 covered-ground）。
- [D-3] 去同质=照搬 podcast covered-ground 四件套进 video-studio（copy 不跨插件 import）：① store `covered-ground.yaml` ② 隔离蒸馏器（新 agent，台本定稿后独立上下文跑）③ 相似度合并 code 站 ④ 推 avoid-memo。
- [D-4] 招牌装置三类：类比/比喻、具体例子/场景、钩子型。
- [D-5] 相似度用免费本地 embedding（照搬 `podcast-studio/lib/embed.py` = Apple `NLContextualEmbedding` + cosine + n-gram fallback），零 API 成本。
- [D-6] 触发点=台本定稿后跑蒸馏器（视频无自动发布信号，"台本定稿"等同 podcast 的 post-publish）。
- [D-7] avoid-memo 注入点=`digest-writer`（写台本步）；`director`/画面层不碰。(linked: D-4 — 注入的就是这三类装置的 avoid-memo)
- [D-8] covered-ground store 按整个节目跨季长存，不按季重置（第二季不重搬第一季用滥的装置）。
- [D-9] 只避"包装"（类比/例子/钩子），数字/结论不避重（客观数字由忠实门管）。
- [D-10] 视觉同质化不在本 scope（同类 b-roll/图表的视觉去重已由 `visual_ledger` 管）；本 phase 只管台本内容层。

## Constraints

- 隔离蒸馏器必须隔离（只读台本+最近几期+store，不见指令形文本）——否则回声锁死（podcast 教训）。
- covered-ground 全链 fail-soft：蒸馏/合并/注入任一失败不阻断出片。
- embedding 必须免费本地（Apple NLContextualEmbedding），不引入付费 API。
- LLM 调用一律 `claude -p`（项目硬规则，planner/蒸馏器/digest-writer 皆是）。

## Scope Boundaries

- IN: season 层 schema + cursor 改造；planner 扩季级输出 + 锁定动作；covered-ground 四件套 copy 进 video-studio（含免费 embed）；running-class 第一季锁定大纲（补全 01/02/03/07/09）；回填现有 5 期装置作 store 基线。
- OUT: 视觉层去同质（visual_ledger 已管）；预生成所有期 transcript；一次铺满全教材 ~28 概念；P7 的写实渲染链 / 忠实门改动。

## Source Context
- Design doc: docs/06-plans/2026-07-04-video-season-planning-anti-homogenization-design.md
- Design analysis: none
- Key files discussed: video-studio/agents/planner.md, video-studio/agents/digest-writer.md, video-studio/lib/pipeline.py, video-studio/lib/syllabus.py, podcast-studio/agents/coveredground-distiller.md, podcast-studio/lib/embed.py, podcast-studio/lib/dedup.py
