---
type: ubiquitous-language
status: active
last_updated: 2026-06-18
---

# Ubiquitous Language

podcast-studio 的术语表。多数论文线条目的 code identifier 标 `(planned)` —— 论文线
尚未实现，标识符来自设计文档 `docs/06-plans/2026-06-18-paper-digest-show-design.md`
的文件布局，实现后回填。这张表也是"两条线不打架"在**语言层**的护栏：同一个词不许
跨线复用（见 Reserved / Forbidden）。

## Domain Terms

| Term (canonical) | Code identifier(s) | Plain meaning | Aliases to avoid |
|------------------|--------------------|---------------|------------------|
| 线 / Line | line-registry in `lib/runner.py` (planned) | 把若干 show 归到一条线；一条线有自己的拓扑/人设/评分尺/连续性。引擎按线注册 | "show"(线≠档), "频道" |
| 档 / show | `--show morning\|evening\|papers` | 一档具体节目；多个 show 可属同一条线（早晚间同属观点线） | "line"(档≠线) |
| 观点线 / Opinion line | `lib/pipeline.py`, `agents/` | 早间+晚间所在的线：vault-leads、观点优先、反同质化 | — |
| 论文线 / Paper line | `lib/pipeline_papers.py`, `lib/paperline/`, `agents/papers/` (planned) | AI论文科普所在的线：材料优先、讲清框架、忠实第一、主播观点退场 | — |
| 引擎 / Engine | `lib/runner.py`(抽线无关后) | 走表/判门/派发/重试/并行/skip/fail_soft/resume 的与线无关执行器；两线共用一份 | "runner"(特指文件), "pipeline" |
| bundle | line-registry entry (planned) | 一条线交给引擎的零件清单：topology + gate_map + executor_map + editorial_loader + agent_dir | "config", "插件" |
| 选题判官 / Paper curator | `agents/papers/` (planned) | 从 arXiv 候选按 重要性+可解释性+新鲜度+对 paper-log 去重 选 1 篇的 persona | "采集器"(采集是抓全文那步) |
| 论文事实账 / Paper fact-ledger | `paper-ledger.json` (planned) | 从论文全文抽的结构化账：问题/方法/关键结果数字/作者自陈局限，每条挂原文锚点；忠实门的对照基准 | "摘要"(账≠摘要), "笔记" |
| 忠实门 / Faithfulness gate | `lib/paperline` check (planned) | 论文线阻塞门：成稿每个客观声称可溯源到事实账/全文 + 不夸大 + 保留局限 | "factcheck"(那是观点线的) |
| 委员会-lite / Committee-lite | papers topology (planned) | 并行 2-3 份解读稿(差异在讲法/比喻/切入点，非观点)，科普尺选最清楚最忠实的一份 | "委员会"(观点线那套含观点) |
| 讲解者声音 / Explainer voice | `agents/papers/` voice spec (planned) | 论文线自己的静态嗓音文件，清楚口语爱打比方；不挂主播 Character Bible | "主播声音", "Character Bible" |
| paper-log | `论文线输出/state/paper-log.yaml` (planned) | 论文线连续性：讲过的论文(arXiv id/标题/日期/概念)，供去重+同日重跑护栏；无 bets、无观点 | "台账卡", "stance" |
| 科普评分尺 / Digest rubric | papers select (planned) | 准确/清晰/框架还原/可读 四维(1-5)；论文线自己的确定性 select，隔离于 select_draft | "四维评分"(观点线那个是 洞察/命名/跨域/思考问句) |

## Reserved / Forbidden（跨线复用即语言层打架）

| Term | Why forbidden | Use instead |
|------|---------------|-------------|
| "factcheck" 指论文线 | factcheck 专指观点线 step-12a 的当日新闻溯源门 | 论文线说"忠实门" |
| "Character Bible" 指论文线 | Character Bible 是观点线从开发日志蒸馏的主播声音；论文线声音独立、静态 | 论文线说"讲解者声音" |
| "台账卡"/"stance" 指论文线 | stance card 是观点线含 bets/观点的连续性；论文线无 bets | 论文线说"paper-log" |
| 单说"评分尺" | 两条线评分尺不同，混说会错配 | 指明"科普评分尺(论文线)"或"四维评分(观点线)" |
| 单说"档" 当"线"用 | 早晚间是两个档、同一条线；档≠线会让隔离推理错位 | 区分"档(show)"与"线(line)" |
