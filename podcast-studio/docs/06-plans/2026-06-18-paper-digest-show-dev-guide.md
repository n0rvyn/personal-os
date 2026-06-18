---
type: dev-guide
status: active
tags: [paper-digest, multi-line-engine, isolation, faithfulness-gate, arxiv]
refs: [docs/06-plans/2026-06-18-paper-digest-show-design.md, docs/11-crystals/2026-06-18-paper-digest-show-crystal.md]
current: true
confirmed_at: 2026-06-18T10:04:41
---

# 「AI论文科普」新档 Development Guide

**Project brief:** none on disk (`docs/01-discovery/project-brief.md` 不存在；locked scope 见 CLAUDE.md § Scope)
**Design doc:** docs/06-plans/2026-06-18-paper-digest-show-design.md
**Decision crystal:** docs/11-crystals/2026-06-18-paper-digest-show-crystal.md
**Ubiquitous language:** docs/02-architecture/ubiquitous-language.md
**Project context contract:** missing (`docs/00-AI-CONTEXT.md` 缺)

## Global Constraints

- **Tech stack:** Python 3（PyYAML）；测试 pytest + bats；TTS 需 ffmpeg + curl 在 PATH；
  persona 派发走 MiniMax M3 代理（成本约定：LLM 成本不计，TTS 是火山引擎独立额度、另算）。
- **Coding standards:** CLAUDE.md（全局 + 项目）；conventional commits + 自动 version bump。
- **项目特定约束（违反即 silent failure）：**
  - `lib/*.py` 是 importable 模块、不是 CLI（只有 `config.py`/`runner.py`/vendored `orchestrator.py` 有 `__main__`）。
  - config fail-closed：缺键/缺目录 raise，不静默兜底。
  - **硬约束（不可妥协）：① 不影响早、晚两档（零变化）；② 两条线后续各自优化不打架。**
  - 观点线 `select_draft` 四维数学锁死——论文线用自己的 select，绝不碰它。
  - 论文线不碰 stance / covered-ground / magnitude / bible；用 paper-log / 忠实门 / 讲解者声音替代（术语见 ubiquitous-language.md）。
  - **验证纪律：unit-green ≠ phase-validated。** phase-done 前必须真实 e2e（迭代期 no-TTS 跑到 step13 止，最后确认才跑含 TTS）。
  - **clone gotcha：** `/podcast` 解析到独立 HTTPS clone，working-repo 改动不被它看到。e2e 用 working-repo 直跑：`PODCAST_STUDIO_CONFIG=<sandbox.yaml> python3 -m lib.runner ...`。

---

<!-- section: phase-1 keywords: line-engine, line-registry, regression-gate, isolation-test, byte-identical -->
## Phase 1: 引擎抽线无关 + 回归地基

**Goal:** runner 变成一台"与线无关的引擎"，早晚间经由"观点线 bundle"驱动、行为与重构前完全一致（四门证明零变化），并就位"不打架"结构测试 harness。**本阶段不加任何论文功能。**

**Depends on:** None

**Scope:**
- 把 `lib/runner.py` 执行循环（走表/判门/派发/重试/并行/skip/fail_soft/resume）抽成与线无关的引擎；现在硬 import 的 stance / covered-ground / select_draft / 长度门 改为由"线"注入。
- 建线注册表：show → line 映射；**观点线 bundle 完整复刻现有接线**（同 `_build_steps()`、同 gate_map、同 custom executor、`agents/`、`references/{morning,evening}.md`）。
- `load_pipeline(show)` 改为经线注册表返回拓扑；`morning`/`evening` 返回的拓扑与重构前 **byte-identical**。
- 回归护栏（DP-A2 四门）。
- 不打架结构测试 harness（观点线侧立即生效；论文线侧 P2 模块落地后激活）。

**用户可见的变化:** 无 — 纯基建阶段（早晚间听众产物零变化，这正是验收点）。

**Architecture decisions（留给 /write-plan）:**
- 引擎抽成新模块（如 `lib/engine.py`）还是就地重构 `lib/runner.py`？
- 线注册表的数据结构与位置（独立 `lib/lines.py`？还是 `pipeline.py` 内？）。
- gate_map / executor_map / editorial_loader / agent_dir 注入的接口形状。

**Acceptance criteria:**
- [ ] 现有 490 pytest + 8 bats **一字不改**全绿（引擎重构后早晚间所有现存测试照过）。
- [ ] 新增 pin 测试：`load_pipeline("morning")` / `load_pipeline("evening")` 返回拓扑与重构前 byte-identical。
- [ ] 06-14 回归样本喂确定性站点，行为不变（断言）。
- [ ] 真实 no-TTS e2e 跑早/晚各一期，产物结构等价（段数 / 无草稿头 / 长度门 / 台账卡写入 / covered-ground 注入 都在）。
- [ ] 不打架结构测试 harness 就位：观点线模块不 import 论文线模块（论文线侧断言留待 P2 激活，本阶段标记 pending）。
- [ ] UT pass for 引擎抽取 + 线注册表。

**Review checklist:**
- [ ] run-phase review step（自动 dispatch implementation-reviewer，重点核对早晚间行为保持 + 注入接口正确）。
<!-- /section -->

---

<!-- section: phase-2 keywords: paper-curator, full-text-fetch, fact-ledger, arxiv, paperline-scaffold -->
## Phase 2: 论文采集侧

**Goal:** 论文线骨架落地，能自主从 arXiv 选出一篇、抓到全文、抽出带原文锚点的"论文事实账"——尚不产出听众节目。

**Depends on:** Phase 1

**Scope:**
- 论文线骨架：`lib/pipeline_papers.py`（拓扑）、`lib/paperline/`（线逻辑）、`agents/papers/`（人设）。
- 选题判官：抓 arXiv 候选摘要 → 按【重要性 + 可解释性 + 新鲜度 + 对 paper-log 去重】选 1 篇。
- 抓全文站：拉选中论文全文（HTML/PDF→text），**非摘要、非二手**。
- 论文事实账抽取：从全文抽 问题 / 方法 / 关键结果数字 / 作者自陈局限，每条挂原文锚点 → `paper-ledger.json`。
- `config.py` 加 `papers.*`（源分类等）。
- **真实 arXiv 样本验证**发现 + 抓取结构（实现前验证输入，按规则）。

**用户可见的变化:** 无 — 后台采集，尚未产出听众节目。

**Architecture decisions（留给 /write-plan）:**
- arXiv 发现用 arXiv API 还是 RSS/HTML 列表？是否叠加 HuggingFace Daily Papers 做热度排序？
- 全文抓取：arXiv HTML（ar5iv / arxiv html）vs PDF 提取（pdftotext）——哪个对中文/公式更稳？（真实样本定）
- 论文事实账 schema（字段 + 原文锚点格式）。

**Acceptance criteria:**
- [ ] 真实 arXiv 样本验证：选题判官能从真实候选选出 1 篇、抓到**全文**（确证非摘要、非二手报道）。
- [ ] 论文事实账结构正确：问题/方法/关键结果数字/局限齐全，每条带可回溯的原文锚点（真实论文跑通）。
- [ ] **不打架结构测试激活并通过**：`lib/paperline/*`、`lib/pipeline_papers.py` 不 import stance/coveredground/magnitude/bible；观点线不 import 论文线。
- [ ] 早晚间回归四门仍全绿（P2 未碰观点线 → 零变化保持）。
- [ ] UT pass for 采集侧。

**Review checklist:**
- [ ] run-phase review step（implementation-reviewer，重点核对采集真抓全文、事实账锚点可溯源、跨线零 import）。
<!-- /section -->

---

<!-- section: phase-3 keywords: committee-lite, digest-rubric, explainer-voice, faithfulness-gate, paper-draft -->
## Phase 3: 论文生成侧

**Goal:** 从一篇真实论文产出一期忠实科普解读稿（no-TTS），讲法清楚、不掺观点，且忠实门拦得住夸大/漏局限。

**Depends on:** Phase 2

**Scope:**
- 委员会-lite：并行 2-3 份解读稿（差异在讲法/比喻/切入点、非观点），从事实账写，过长度门。
- 科普评分尺（准确 / 清晰 / 框架还原 / 可读）+ 论文线自己的确定性 select（**物理隔离于 select_draft**）。
- 讲解者定稿（论文线独立声音文件，不挂 Character Bible）。
- 忠实门（阻塞，retry=1）：每个客观声称溯源到事实账/全文 + 不夸大 + 保留局限；代码门 recompute、不信 agent 自标；二次失败停线、不发半成品。
- 4 段结构骨架（问题→方法→结果→意义+局限）写进论文线 editorial。

**用户可见的变化:** 第一次能产出一篇科普解读 `.md`（no-TTS）——读者看到"问题→方法→结果→意义+局限"的忠实解读，讲解者口吻、无主播观点掺入。

**Architecture decisions（留给 /write-plan）:**
- 忠实门"夸大检测"的判定机制（声称强度对比：另起一个 judge？规则匹配？）。
- 科普 select 破平规则的代码位置（论文线自有 select 模块）。

**Acceptance criteria:**
- [ ] 真实 no-TTS e2e：从真实论文产出一期忠实科普解读 `.md`（4 段 / 讲解者声音 / 无观点掺入 / 过长度门）。
- [ ] 忠实门拦截力（确定性证）：构造一份夸大稿（"提升3%"→"解决了"）+ 一份漏局限稿，门能 flag 并打回；二次失败停线。
- [ ] 科普 select 物理隔离：不 import / 不调用观点线 `select_draft`（结构测试覆盖）。
- [ ] 早晚间回归四门仍全绿。
- [ ] UT + E2E pass for 生成侧。

**Review checklist:**
- [ ] run-phase review step（implementation-reviewer，重点核对忠实门 recompute 不信自标、select 隔离、声音不挂 bible）。
<!-- /section -->

---

<!-- section: phase-4 keywords: paper-log, output-dir, command, slot, full-e2e -->
## Phase 4: 连续性 + 发布 + 收尾

**Goal:** 跑通完整一期（含 TTS），有自己的输出目录、命令和时段；paper-log 去重生效；早晚间零变化最终确认。

**Depends on:** Phase 3

**Scope:**
- paper-log 连续性（论文线 `state/paper-log.yaml`）：写入 + 接入选题去重 + 同日重跑护栏。
- 输出目录隔离（论文线自己的目录）。
- 命令 + 第三 slot（/loop 排法）。
- 口播稿 + TTS（jay 共用）接入。
- 真实 full e2e（含 TTS）。

**用户可见的变化:** 跑论文档命令（名称待定）产出完整一期（`.md` + `.mp3`），落在论文线自己的目录；讲过的论文不会被重复选中。

**Architecture decisions（留给 /write-plan）:**
- 命令名：复用 `/podcast papers` 还是新命令？
- 输出目录：独立 `output_dir` 还是现有 `output_dir` 下的论文线子目录？
- paper-log 去重粒度（arXiv id 精确 vs 概念近似）+ 衰减策略。
- 同日重跑护栏语义：整线一天一篇 vs 一篇一讲。
- 第三 slot 时段。

**Acceptance criteria:**
- [ ] 真实 full e2e（含 TTS）：产出完整一期（`.md` + `.mp3`），发到论文线自己的输出目录。
- [ ] paper-log 去重生效：同一篇/太近概念不会被选题判官二次选中（真实跑两期验证）。
- [ ] 同日重跑护栏：今天本线已出一期则 fail-fast，不 ship-then-orphan。
- [ ] 论文线输出与观点线输出物理隔离（不同目录），互不覆盖。
- [ ] **早晚间回归四门 + 完整 e2e 仍全绿**（论文线全部接好后，早晚间零变化最终确认）。
- [ ] UT + E2E pass for 连续性 + 发布。

**Review checklist:**
- [ ] run-phase review step（implementation-reviewer，重点核对输出隔离、去重生效、早晚间最终零变化）。
<!-- /section -->

---

## Decisions

None. — 所有 blocking 决策已由 crystal `docs/11-crystals/2026-06-18-paper-digest-show-crystal.md`（D-001..D-017）锁定；各 Phase 的 "Architecture decisions" 是实现期（/write-plan）才需定的开放问题，不阻塞本 dev-guide。
