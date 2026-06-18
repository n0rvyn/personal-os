---
type: crystal
status: active
tags: [paper-digest, multi-line-engine, isolation, faithfulness, arxiv]
refs: [docs/06-plans/2026-06-18-paper-digest-show-design.md]
---

# Decision Crystal: podcast-studio 新增「AI论文科普」档

Date: 2026-06-18

## Initial Idea

我想让 AI 每天解读一篇最近发布的 AI 相关的论文，在权威平台上来的，解读成普通
非专业人士也能读懂它的大义、框架，是放在 morning/evening 播客里混着比较好？还是
另起一档播客比较好？

硬约束：必须不影响早、晚两档播客；而且后续 2 条线优化的时候，也不会打架。

## Discussion Points

1. **混进早晚间 还是 另起一档**：读完早晚间编辑定位发现两者基因相反——早晚间是
   vault-leads/观点优先/反同质化的"观点机器"，论文科普是材料优先/讲清框架的
   "科普机器"。混进去会被反重复机器(量臣+covered-ground)当重复套路打掉、且四维
   评分尺(洞察/命名/跨域/思考问句)会惩罚忠实的好科普。决定：**另起一档**。
2. **新档定位**：在"主播视角解读 / 忠实科普为主 / 忠实+结尾一句态度"三者中，选
   **忠实科普为主**——准确/清晰/框架还原第一，主播观点退场（最贴合原话"让普通
   人读懂大义框架"）。
3. **容器**：在"挤进现有步骤表加 if 分支 / 同插件共享引擎按线分离 / 另起 sibling
   插件"中，选**同插件·共享与线无关的引擎·按线分离拓扑+人设+尺+连续性**。反直觉
   点：另起插件看似最隔离，反而复制整个引擎→bug 修两遍→基建层重新制造漂移。
4. **论文怎么进系统**：自主发现 + 自己抓**论文全文**做原文分析；不要二手论点、
   也不要只看摘要（含义=抓全文不是抓 abstract 页）。
5. **源池**：arXiv 为 v1 主源（接受预印本身份，AI 领域 arXiv 是事实标准）；实验室
   博客/顶会留作以后可配。点明张力：若要求必须同行评审，arXiv 不合格、源池得换
   顶会——用户选择接受预印本。
6. **选题**：每天 arXiv 新论文几十上百，派"选题判官"按【重要性+可解释性+新鲜度+
   对 paper-log 去重】选 1 篇；热度信号(如 HF Daily Papers)只做发现/排序（二手
   策展非二手分析，不碰红线）。
7. **忠实怎么保证**：复用现有 factcheck 的"声称→溯源"骨架，溯源对象从当日新闻
   背景换成论文全文，补两条科普专属检查——不夸大（"提升3%"不许讲成"解决了"）+
   保留作者自陈局限；采集先抽一份带原文锚点的"论文事实账"(问题/方法/关键结果数字/
   局限)。
8. **拓扑**：委员会-lite——并行 2-3 份解读稿，差异在讲法/比喻/切入点而非观点，用
   科普尺选最清楚最忠实的一份，忠实门对所有稿生效（清晰度有方差，挑最清楚直接
   提升听懂率；复用引擎并行+评分骨架）。
9. **声音**：论文线自带独立"讲解者"声音文件，不挂主播共享 Character Bible、不放
   bible 蒸馏站（bible 从开发日志现蒸馏，共享=为早晚间调它会连带改论文声音=打架；
   且观点退场本就不要主播世界观）。
10. **隔离与零变化的落地**：引擎抽成与线无关(按"线"注入 bundle)；早晚间零变化用
    四道验收门证明；"不打架"写成结构测试；先抽引擎+证零变化(Phase 1)、再加论文线。

## Rejected Alternatives

- **把论文解读混进 morning/evening**：Rejected because — 早晚间是观点机器、论文
  科普是科普机器，基因相反；固定"今日论文解读"段会被反重复机器判为重复套路，
  且现有四维评分尺会惩罚忠实科普、奖励加私货的版本。
- **新档定位为"主播视角解读"或"忠实+结尾一句态度"**：Rejected because — 用户要
  的是让外行读懂大义框架，主播观点应退场。
- **容器：挤进现有步骤表加 `if show=="papers"` 分支**：Rejected because — 以后改
  早晚间拓扑就在论文档同一函数里动刀，必打架。
- **容器：另起 sibling 插件**：Rejected because — 复制整个 runner 引擎(~2000 行)，
  引擎 bug 要修两遍，在基建层重新制造双系统漂移/打架。
- **源池要求必须同行评审(顶会)**：Rejected because — AI 领域重磅工作基本先/只发
  arXiv，预印本是事实标准；要求同行评审会让源变慢、变窄、全文难抓。Rejection
  scope：拒绝的是"v1 就上同行评审门槛"；不拒绝以后把顶会作为可配附加源。
- **声音复用主播 Character Bible**：Rejected because — 共享会让早晚间调 bible 时
  连带改论文声音(打架)，且违背"主播观点退场"。

## Decisions (machine-readable)

- [D-001] 在 podcast-studio 里新增第三档「AI论文科普」，不混进 morning/evening。
- [D-002] 新档定位为忠实科普：准确/清晰/框架还原第一，主播观点退场。
- [D-003] 容器=同插件、共享与线无关的引擎、按线分离拓扑+人设+评分尺+连续性。
- [D-004] 引擎抽成与线无关：走表/判门/派发/重试/并行/skip/fail_soft/resume 循环
  不变，把硬 import 的 stance/covered-ground/select_draft/长度门改为按"线"注入
  (每线 bundle = topology + gate_map + executor_map + editorial_loader + agent_dir)；
  观点线 bundle 复刻今天的接线。 (linked: D-003)
- [D-005] 论文线自主从 arXiv(v1 主源、可配分类、接受预印本)发现并抓取一篇论文的
  全文做原文分析；不用二手论点、不用只看摘要。
- [D-006] 实验室博客/顶会作为以后可配的附加源，不进 v1。
- [D-007] "选题判官"按【重要性+可解释性+新鲜度+对 paper-log 去重】每天选 1 篇；
  热度信号只用于发现/排序，不替代原文分析。
- [D-008] 采集阶段先从全文抽一份带原文锚点的"论文事实账"(问题/方法/关键结果数字/
  作者自陈局限)，作为后续忠实门的对照基准。
- [D-009] 忠实门(阻塞，retry=1)对定稿 body 检查：每个客观声称可溯源到事实账/全文
  + 不夸大 + 保留作者自陈局限；代码门 recompute、不信 agent 自标；二次失败停线、
  不发半成品。 (linked: D-008)
- [D-010] 拓扑=委员会-lite：并行 2-3 份解读稿(差异在讲法/比喻/切入点，非观点)，按
  科普四维尺(准确/清晰/框架还原/可读)选最清楚最忠实的一份；忠实门对所有稿生效。
- [D-011] 科普选稿用论文线自己的确定性 select 函数，物理隔离于观点线锁死的
  select_draft。 (linked: D-010)
- [D-012] 论文线自带独立"讲解者"声音文件，不挂主播共享 Character Bible、拓扑里
  不放 bible 蒸馏站。
- [D-013] 论文线连续性=paper-log(arXiv id/标题/日期/核心概念，append-only、无 bets)，
  写论文线自己的 state 文件，供选题去重 + 同日重跑护栏；不碰 stance/covered-ground。
- [D-014] 早晚间零变化用四道验收门证明：现有 490 pytest+8 bats 一字不改全绿；
  load_pipeline("morning"/"evening") 拓扑 byte-identical；06-14 回归样本断言确定性
  站点行为不变；真实 no-TTS e2e 跑早晚各一期产物结构等价。
- [D-015] "不打架"写成结构测试：论文线模块不 import 观点线专属模块
  (stance/covered-ground/magnitude/bible)，观点线也不 import 论文线模块。
- [D-016] 分阶段顺序：Phase 1 只做引擎抽取+回归护栏(证早晚间零变化)、不加任何论文
  功能；论文线进 Phase 2+。 (linked: D-014, D-015)
- [D-017] discovery feed / arXiv API / 全文抓取(HTML vs PDF 提取)实现前必须用真实
  样本验证结构再定。

## Constraints

- 必须不影响早、晚两档播客——它们现有行为零变化（D-014 是其验收）。
- 后续两条线各自优化时不打架——调一条碰不到另一条读的文件（D-015 是其防火墙）。
- 主播观点退场：论文档不掺主播的看法/世界观/偏好。
- 实现前必须按真实样本验证外部输入结构（arXiv 发现+全文抓取）。

## Scope Boundaries

- IN: 新增第三档「AI论文科普」(忠实科普为主)
- IN: 自主从 arXiv 发现 + 抓全文 + 论文事实账 + 选题判官
- IN: 委员会-lite 解读 + 科普四维评分选稿 + 忠实门 + 独立讲解者声音 + paper-log
- IN: 引擎抽成与线无关 + 回归护栏(证早晚间零变化) + 不打架结构测试
- OUT: 不混进 morning/evening
- OUT: 不改变早晚间行为(零变化)
- OUT: 论文线不碰 stance / covered-ground / magnitude / bible
- OUT: 不掺主播观点(主播观点退场)
- OUT: 不用二手解读/不用只看摘要
- OUT: 实验室博客/顶会 v1 不做(未来可配)
- OUT: Phase 1 不加任何论文功能

## Source Context

- Design doc: docs/06-plans/2026-06-18-paper-digest-show-design.md
- Design analysis: none
- Key files discussed: lib/pipeline.py, lib/runner.py, lib/episode.py,
  skills/podcast/SKILL.md, skills/podcast/references/{morning,evening}.md
