---
name: novel-outline
description: 网文分卷章纲。当用户说"分章纲""列大纲""outline""排卷章""写细纲""novel-outline"时使用。读故事圣经,把故事拆成卷→章,每章产出章纲(推进剧情/目标爽点/章末钩子/伏笔埋或收),并生成伏笔 setup→payoff 依赖图、回写圣经伏笔账本。带 outline-only 模式(写细纲:只产纲不进逐章写)。不适用:还没立项无圣经(先 novel-kickoff);已有章纲只想写正文(用 novel-write)。
model: opus
user_invocable: true
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - AskUserQuestion
---

# novel-outline:分卷章纲 + 伏笔依赖图

对应 dev-workflow 的 write-dev-guide:把整本书拆成可逐章执行的单元。章纲之于章节,如 dev-guide 的 phase 之于代码。

**模型姿态**:model: opus —— 结构判断(卷章节奏、伏笔布局)。

## 前置

1. 读 cwd 的 `bible.md`(定位/世界观/金手指/主角/已有伏笔账)。无则提示先 `novel-kickoff`。
2. 读契约:`${CLAUDE_PLUGIN_ROOT}/references/corpus-craft.md`(流派套路模板、伏笔五法)、`${CLAUDE_PLUGIN_ROOT}/references/rubric.md`(章纲要服务的轴)。
3. 读 `.novel/state.json` 的定位(流派决定套路模板)。

## 产出 outline.md

按卷→章组织。卷数/每卷章数与作者确认(基于流派给推荐,如长篇仙侠先排前 1-2 卷,后续滚动续排)。

**每章一条章纲,含四要素**(缺一不可,对应 rubric):
- **推进剧情**:本章主线/支线推进什么
- **目标爽点**:本章给什么爽点(类型来自 corpus-craft 流派库;参考 bible 爽点节拍账避免爽点荒/重复)
- **章末钩子**:本章末留什么悬念(画面化/悬念化;避免与近几章钩子类型重复)
- **伏笔动作**:本章埋哪条伏笔 / 收哪条伏笔(无则写"无")

## 伏笔 setup→payoff 依赖图(核心,对应 dev-guide 依赖图)

- 每条伏笔标:埋设章 → 计划回收章。
- **回写圣经伏笔账本**(bible 第 5 块):新增条目 `编号/埋设章/内容/计划回收章/状态=待收`。
- 形成依赖图:回收章依赖埋设章(payoff 不能早于 setup)。这张图供 novel-review 的结构镜头对账。
- 应用 corpus-craft 红楼草蛇灰线五法(谐音/谶语/影射/引文/化用)设计伏笔类型。

## 写细纲模式(outline-only,crystal D-011 低频模式)

当用户意图是"只要大纲不要代笔"(触发词含"写细纲""只列纲""outline only"):
- 正常产出 outline.md + 伏笔依赖图,**停在这里**,不提示也不进入 novel-write。
- 完成语:"细纲已出(含伏笔依赖图)。需要我接着逐章写,再用 novel-write。"

## 完成与下一步(常规模式)

> 分卷章纲已写入 `outline.md`,伏笔依赖图回写圣经伏笔账本({N} 条待收)。
> 下一步:`novel-write` 逐章写(从第 1 章起)。

## 边界
- 伏笔回收章不得早于埋设章(依赖图无悬空/倒置)。
- 每章纲四要素齐全。
- 只写 cwd 内(D-010)。
