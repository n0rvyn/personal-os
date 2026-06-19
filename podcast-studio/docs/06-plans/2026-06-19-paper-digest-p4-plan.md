---
type: plan
status: active
contract_version: 2
tags: [paper-digest, paper-log, publish, tts, output-isolation, slot]
refs:
  - docs/06-plans/2026-06-18-paper-digest-show-dev-guide.md
  - docs/06-plans/2026-06-18-paper-digest-show-design.md
  - docs/11-crystals/2026-06-18-paper-digest-show-crystal.md
  - docs/06-plans/2026-06-19-p4-handoff.md
---

# Paper-digest Phase 4「连续性 + 发布 + 收尾」Implementation Plan

**Goal:** 让论文线跑通完整一期——从忠实门后的成稿继续走到口播稿→TTS→发布一篇 `.md`+`.mp3` 到论文线自己的目录，写 paper-log 连续性、接入选题去重与同日护栏、暴露 `/podcast papers` 命令，并最终确认早晚间零变化。

**Architecture:** 论文线拓扑现止于 `faithfulness`（生成侧终点 = `finalize-result.json` body）。P4 在 `lib/pipeline_papers.py` 续接发布尾段（broadcast-script → tts → publish → paper-log-write → cleanup）+ 前段护栏（same-day guard + paper-log-read 喂选题判官），新逻辑全部落在 `lib/paperline/`（D-015 防火墙），**共享引擎 `lib/runner.py` 零行为改动**。输出物理隔离用 `LineBundle` 新增的"输出根解析"——和 P1/P3 给 bundle 加 `floor_fn` 同款扩展机制（D-004），opinion bundle 解析到现有 `output_dir`（byte-identical），paper bundle 解析到 `output_dir/papers/`。TTS 复用 opinion 的 `jay` persona + `skip_when="no_tts"` 机制，不引入跨厂混音。

**Tech Stack:** Python 3 (PyYAML); pytest + bats; TTS = 火山引擎经 `tts-toolkit` 的 `synth-auto`（ffmpeg + curl 在 PATH）；persona dispatch 经 MiniMax M3 代理（**必须 `--model sonnet`**，见 Pre-flight）。

**Design doc:** docs/06-plans/2026-06-18-paper-digest-show-design.md（§论文线站点拓扑 + §paper-log 模型 + §仍开放）

**Design analysis:** none

**Crystal file:** docs/11-crystals/2026-06-18-paper-digest-show-crystal.md（D-001..D-017；P4 直接相关：D-009 不发半成品 / D-012 讲解者独立 / D-013 paper-log / D-014 早晚间零变化 / D-015 不打架防火墙）

**Bug diagnosis:** not applicable

**Threat model:** included

**Pre-flight risks:**
- **环境硬约束 ×4（handoff §5，违反即重踩坑）**：(a) persona dispatch 必须钉死 `--model sonnet`——`runner._default_dispatch` 不传 `--model`，CLI 默认解析为 Opus（非 MiniMax）；含 TTS 的 e2e 须 monkeypatch `lib.dispatch.dispatch_persona` 注入 `model="sonnet"`（memory `helper-subagents-use-sonnet`）。(b) arXiv 网络须 `export no_proxy='*' NO_PROXY='*'` 直连（系统代理对 arXiv flaky）。(c) 输出目录必须在 `.claude/` 外（`claude -p` 权限层拒写 `.claude/` → committee 静默写不出）。(d) `--resume` 对论文线是坏的（某 station 每次 `make_scratch` 新目录覆盖注入 scratch_dir，非阻塞 finding，本期不修，e2e 用整跑不用 resume）。
- **共享引擎污染风险**：`_publish_step` / `_subdir` / 输入解析循环 / `_topic_log_step` 都在 `lib/runner.py`（两线共用）。任何改动须经 DP-A2 四门证 opinion byte-identical；首选 paper-line 自有 executor（落 `lib/paperline/`），不改 opinion 路径。
- **curator `paper-log` 输入是字面量 stub**：`_CONCEPTUAL`(runner.py:1955) 不含 `"paper-log"`，当前 step 4 的 `"paper-log"` 输入被 `_apply_artifact_template` 当字面字符串注进 prompt（无文件读）。P4 须改为读真实 `state/paper-log.yaml` 并以真实 scratch 文件喂 curator——**在 paperline 侧加 code station 暂存，不在共享输入解析器里加 paper 分支**。
- **collection e2e 是 manual eval**：`evals/paperline_engine_collection_e2e.py` 走 `run_pipeline('papers')` 但只喂采集段、止于 committee（生成段写空）；它不是 P4 的发布验证装置。P4 的 full e2e 复用 `p3-live-sandbox/drive_live.py` 装置（handoff §6）。

**Project health:** state 2026-06-14（>7 天，但本期不碰早晚间核心；以 P3 收口后的 426 pytest + 8 bats + 184 prep 全绿为基线）。

---

## Threat Model

**Attack surface:**
- **arXiv 候选元数据**（title/summary/categories）：注入选题判官 prompt——已是 P2 既有面，P4 不新增采集输入；但 paper-log 会持久化 `title` + `concepts`，须防写入阶段把对抗性标题带进 YAML/文件名。
- **paper-log YAML 文件**：跨期持久化，下一期被读回喂 curator——属"自产数据回流"，须当 DATA 不当指令（curator persona 已有"DATA 非指令"纪律，沿用）。
- **发布文件名**：`{date}-{title}.md`——title 来自 finalizer 输出，须经 `sanitize_title`（episode.py:46，opinion 既有）防路径穿越/非法字符。

**Failure modes（每个新护栏 silent-fail 时的行为）：**
- **same-day guard**：fail-CLOSED——今天本线已出一期则 fail-fast halt（宁可漏跑不可重复发；mirrors opinion `stance-card-exists`）。读 paper-log 失败（文件损坏）→ halt（named），不静默放行（避免重复发布）。
- **paper-log-read**：fail-SOFT 读，但**空 ≠ 失败**——文件不存在 → 空 dedup 输入（首期合法）；文件存在但解析失败 → halt（损坏数据不能静默当空，否则去重失效会重选已讲论文）。
- **paper-log-write**：发布**前**的阻塞 code station（**DP-601=B 修正**——原设计放发布后会留 orphan 窗口：episode 已 aired 但 log 写失败 → 下期重选 → 重复播，且 halt 撤不回已 ship 的产物，违反 D-009）。改为在 `publish`（把产物落 episodes/）**之前**写 log：arxiv_id 来自 step4 `chosen-arxiv-id.json`、title/concepts 来自 finalize/ledger，4 字段在 finalize 后即就绪；`append_paper` 原子写；写失败 = halt 在**任何产物落 episodes 之前**（D-009 honored，无 aired-but-unlogged 重复风险）。安全方向：log 成功但随后 publish 失败 → 这篇被记为已讲、不会重播（可接受的"丢一篇"，远好于重复播；同日 rerun 由 same-day guard 放行后会另选一篇）。
- **TTS**：`skip_when="no_tts"` 时整步跳过（迭代期合法）；非 no_tts 时 gate `check_artifact` 缺 mp3 → halt（opinion 既有语义）。

**Resource lifecycle:**
- **scratch**：`run_pipeline` 的 `finally` 在成功时 `cleanup_scratch`（runner.py:2216），halt 时保留 scratch 供诊断——P4 不改此契约。paper-line 新 station 只写 scratch + 最终发布目录，不开额外句柄/子进程（TTS 子进程生命周期由 `tts` skill 负责，opinion 既有）。
- **输出目录**：`output_dir/papers/{episodes,state,reports}` 由 config 在 fail-closed 校验后 `mkdir(parents=True, exist_ok=True)`（mirrors 现有 episodes/state/reports 派生）。

**Input validation requirements:**
- **paper-log 条目**：`arxiv_id`（正则 `^\d{4}\.\d{4,5}(v\d+)?$` 或 arXiv id 格式校验）、`title`（去换行/控制字符）、`date`（ISO `YYYY-MM-DD`）、`concepts`（list[str]）。写入前校验，非法 → halt（fail-closed），不写脏数据进去重命脉。
- **发布文件名**：`sanitize_title` 处理 title（episode.py 既有，opinion 复用）——空 slug 回退 `{date}-papers`。

---

## Impact Map

**User path:** 用户跑 `/podcast papers`（新）→ 论文线产出一篇 `.md`+`.mp3` 落在 `output_dir/papers/episodes/`；讲过的论文不再被选题判官重选；今天已出一期则再跑 fail-fast。**早晚间 `/podcast morning|evening` 行为完全不变。**
**Data path:** arXiv 全文 → 事实账 → 委员会 → 选稿 → 讲解者定稿（finalize body）→【P4 新增】口播稿 → TTS(scratch mp3) → **paper-log 追加(arxiv_id/title/date/concepts)〔发布前，DP-601=B〕** → 发布(.md+.mp3 落 papers 目录) →（下一期）paper-log-read 喂选题判官去重。
**Shared surfaces:** `lib/runner.py`（引擎——**只读不改行为**，新 paper executor 经 bundle 注册）；`lib/config.py`（加 papers 输出子目录派生，gated on `papers.*` 存在）；`lib/lines.py`（`LineBundle` + `PAPER_LINE` 加输出根解析）；`skills/podcast/SKILL.md`（加 papers 命令块）；CLI `--show` choices。
**Existing consumers:** opinion line（morning/evening）经同一引擎 + 同一 `_publish_step`/`_subdir`/输入解析器/`_topic_log_step`——**必须零变化**。`lib/pipeline_papers.py` 拓扑（P3 止于 faithfulness）。`evals/paperline_generation_e2e.py`（生成侧 e2e，会随拓扑延长而需更新预期）。
**Must remain unchanged:** 早晚间 17 站拓扑 byte-identical（golden）；opinion `_publish_step`/`_subdir`/`_topic_log_step` 行为；opinion `output_dir/{episodes,state,reports}` 路径；P3 长度门（finalize body floor + retry=3）；忠实门 recompute 纪律。
**Regression checks:** DP-A2 四门——① 现有 pytest（426 lib + 184 prep）+ 8 bats 全绿；② `topology_golden.json`(morning/evening) byte-identical；③ 06-14 回归样本确定性站点不变；④ opinion no-TTS e2e 产物结构等价（缓验）。+ `test_line_isolation`（paperline 不 import opinion 专属模块，反之亦然）。

---

## Decisions

> 以下 4 项是 dev-guide 留给 /write-plan 的开放架构决策（第 5 项"slot 时段"无代码门、folded 进 Task 8 文档，见下）。均为 `recommended`（有证据支撑的默认，请确认或改）。计划正文按推荐默认书写；若改选，相应 Task 调整。

### [DP-401] `/podcast papers` 命令名（recommended）

**Context:** 论文线 show 怎么暴露给用户。CLI `--show` 现 `choices=("morning","evening")`（runner.py:2237），论文线只能 `run_pipeline('papers')` 程序化跑。
**Options:**
- A: 复用 `/podcast papers` — 把 `"papers"` 加进 `--show` choices + SKILL.md 加命令块。沿用既有 thin-wrapper 路由；改动最小。
- B: 新独立命令（如 `/papers`） — 单独 SKILL/命令，命名空间更干净，但要复制一套 wrapper 路由 plumbing。
**Chosen:** Option A — `docs/02-architecture/ubiquitous-language.md:19` 已把 `--show morning|evening|papers` 定为"档/show"的 canonical 形式，`lib/lines.py:210` 的 `_LINE_REGISTRY` 已用 `"papers"` 键注册 PAPER_LINE；A 是对既有 show-路由模式的直接延伸，B 重造 plumbing。

### [DP-402] 论文线输出目录布局（recommended）

**Context:** 论文线输出须与 opinion 物理隔离（D-015 防火墙 + dev-guide"互不覆盖"）。config 现只有单个 `vault.output_dir` 派生 `episodes/state/reports`。
**Options:**
- A: `output_dir` 下子目录 — `output_dir/papers/{episodes,state,reports}`。复用现有 `output_dir` fail-closed 校验；config 加 papers 子目录派生（gated on `papers.*` 存在 → opinion-only config 零变化）；隔离 = opinion 写 `output_dir/episodes`、papers 写 `output_dir/papers/episodes`，互不覆盖。
- B: 独立根 — 新 config key `vault.papers_output_dir`，完全分离（可不同盘/vault），但多一个必填校验 + 用户多配一项。
**Chosen:** Option A — design doc:79 隔离地图明写论文线输出 = "论文线自己的输出**子目录**"，design doc:144 paper-log 在"论文线输出/state/paper-log.yaml"；A 复用现有 fail-closed 机制、对 opinion-only config 零影响，已满足隔离。

### [DP-403] paper-log 去重粒度 + 衰减（recommended）

**Context:** 选题判官按 D-007【…+ 对 paper-log 去重】选 1 篇；paper-log 存 `{arxiv_id, title, date, concepts}`（D-013）。去重该多严、要不要按概念近似 + 时间衰减。
**Options:**
- A: arXiv-id 精确（代码硬门）+ 概念由 curator 软避（agent 判断） — 代码层：candidates 里 arXiv id 已在 paper-log 的直接剔除（确定性、不会误判）；概念近似：把 paper-log 的 `concepts` 注进 curator prompt，由选题判官 persona 软避太近的（D-007 本就把"去重"列为 curator 判据）。v1 不上 embedding 概念门。
- B: 概念近似代码门 + 衰减 — 用 `lib.embed.similarity` 对 `concepts` 算相似度 + 时间衰减窗（mirrors coveredground `_RESKIN_THRESHOLD=0.93` + `is_stale(window_days=14)`），相似超阈直接剔除。
**Chosen:** Option A — arXiv-id 精确是"这篇literally讲过"的硬事实，确定性去重不会误杀；概念近似是判断题，curator persona 已被设计来做（D-007）。B 的 embedding 概念门更重、且会误杀"共享概念但实质不同"的新论文；"衰减策略"design 明列为开放，v1 先不引入，留作后续可配。（与 P3 长度门同款判断：硬事实走代码门，判断题走 agent。）

### [DP-404] 同日重跑护栏语义（recommended）

**Context:** design topology 3a = "今天本线已出 OR 这篇已在 paper-log → fail-fast"。dev-guide 把"同日重跑"语义列为开放：整线一天一篇 vs 一篇一讲。
**Options:**
- A: 整线一天一篇 — 论文线今天已发布一期（papers/episodes 有今天的产物）则再跑 fail-fast。mirrors opinion `stance-card-exists`（一档一天一期）。"这篇已讲"由 DP-403 的 arXiv-id 去重独立兜（跨期）。
- B: 一篇一讲 — 护栏只按 arXiv id（这篇没讲过就放行），允许一天多篇。
**Chosen:** Option A — opinion 既有同日护栏就是"一档一天一期"（`stance-card-exists`），论文线沿用同纪律最一致、防 ship-then-orphan；"不重复讲同一篇"是另一回事，由 DP-403 的 paper-log id 去重跨期兜住。两道护栏各司其职（A = 今天别重发、id 去重 = 这篇别再讲），正是 design 3a 的复合语义。

### [DP-601] paper-log-write vs publish 顺序（plan-verifier 提出，已解）

**Context:** 原计划把阻塞的 paper-log-write 放在 publish（产物落 episodes/）**之后**。失败反推：若 publish 成功、log-write 失败 → episode 已 aired 但未入 log → 下期 curator 重选同篇 → 重复播；且 halt 撤不回已落盘的 .md/.mp3（违反 D-009 不发半成品）。这是 plan-verifier 的 must-revise#1（D-013 去重完整性无法兑现）。
**Options:**
- A: 维持"发布后写 log"+阻塞门 — 留 aired-but-unlogged 的重复播窗口（缺陷）。
- B: 改"发布前写 log"（log → publish） — arxiv_id 在 step4、title/concepts 在 finalize 后即就绪；`append_paper` 原子写；写失败 halt 在任何产物 aired 之前（D-009 honored）。安全方向反转为"丢一篇"（log 成功但 publish 失败 → 不重播），远好于重复播。
**Chosen:** Option B — A 在数据完整性上严格更差（有重复播洞 + 违 D-009），不是真权衡；B 关掉 orphan 窗口、把失败导向安全方向。计划据此把拓扑尾段定为 `…faithfulness → broadcast-script → tts → paper-log-write → publish → cleanup`（**有意偏离 design doc §line 99-100 的 publish(13)→log(14-15) 顺序**——design 那个顺序即此缺陷源；design 是历史快照，本计划为执行权威）。

---

<!-- section: task-1-tests keywords: config, papers-output, line-bundle -->
### Task 1-tests: 论文线输出隔离 — 测试先行

**Maps to Impact Map:** Shared surfaces (config.py, lines.py); Must remain unchanged (opinion output paths); Regression checks (门②golden + opinion config 零变化)

**Files:**
- Test: `lib/tests/test_config.py`（加 papers 输出子目录派生用例）
- Test: `lib/tests/test_lines.py`（加 PAPER_LINE 输出根解析 + opinion 解析到 output_dir 根的 byte-identical 断言）

**Expected outcome:** 测试断言：(1) 含 `papers.*` 的 config 解析后，论文线输出根解析到 `output_dir/papers`，且 `output_dir/papers/{episodes,state,reports}` 被创建；(2) opinion 线输出根仍解析到 `output_dir`（episodes/state/reports 直接在根下，零变化）；(3) **不含** `papers.*` 的 opinion-only config 不创建 `papers/` 子目录（零副作用）。测试此刻必须 FAIL（解析器/派生未实现）。

**Non-goals:** 不碰 opinion 的 `episodes_dir/state_dir/reports_dir` 既有派生。

**Touched surface:** test 文件

**Regression shield:** opinion-only config 用例断言无 `papers/` 副作用——守住"早晚间零变化"。

**Task Contract:**
- Expected behavior: （测试态）跑论文线时它的文件落在 papers 子目录，早晚间的文件还落在原地，互不串。
- Automated verify: `python3 -m pytest lib/tests/test_config.py lib/tests/test_lines.py -q` —— 新用例 FAIL（功能未实现，预期 `AttributeError`/`KeyError`/路径不存在）。
- Real path verify: N/A（纯测试任务）
- Manual/device verify: none

**Steps:**
1. 在 `test_config.py` 加 `test_papers_output_subdirs_derived_when_papers_section_present`：构造含 `papers.categories/max_candidates` 的 config，断言解析后存在 papers 输出根（如 `cfg.vault` 暴露的 papers 子目录访问器）+ 三子目录被 mkdir。
2. 加 `test_opinion_only_config_has_no_papers_subdir`：opinion-only config 解析后 `output_dir/papers` 不被创建。
3. 在 `test_lines.py` 加 `test_paper_line_output_root_is_papers_subdir` + `test_opinion_line_output_root_is_output_dir`（byte-identical 锚点）。

**Verify:**
Run: `python3 -m pytest lib/tests/test_config.py lib/tests/test_lines.py -q`
Expected: 新增用例 FAIL（未实现）；既有用例仍全绿。
<!-- /section -->

<!-- section: task-1-impl keywords: config, papers-output, line-bundle -->
### Task 1-impl: 论文线输出隔离 — 实现（DP-402=A）

**Depends on:** Task 1-tests

**Maps to Impact Map:** Shared surfaces (config.py, lines.py); Must remain unchanged (opinion output paths)

**Files:**
- Modify: `lib/config.py`（papers 输出子目录派生，gated on `papers.*` 存在）
- Modify: `lib/lines.py`（`LineBundle` 加输出根解析字段；`PAPER_LINE`→papers 子目录，`OPINION_LINE`→output_dir 根）

**Expected outcome:** config 在 `papers.*` 存在时，于 fail-closed 校验后派生并创建 `output_dir/papers/{episodes,state,reports}`；`LineBundle` 暴露输出根解析（如 `output_root_fn(ctx)` 或 `output_subdir` 字段），opinion 解析到 `output_dir`（byte-identical），paper 解析到 `output_dir/papers`。opinion-only config 无 papers 副作用。

**Non-goals:** 不改 opinion 的 episodes/state/reports 派生；不引入 `papers_output_dir` 独立根 key（那是 DP-402=B）。

**Touched surface:** `lib/config.py`、`lib/lines.py`

**Regression shield:** opinion bundle 输出根解析返回与现状 byte-identical 的路径；`topology_golden.json` 不受影响（输出根不在拓扑里）。

**Task Contract:**
- Expected behavior: 论文线产物落 `output_dir/papers/...`，早晚间产物仍落 `output_dir/...`，物理隔离。
- Automated verify: `python3 -m pytest lib/tests/test_config.py lib/tests/test_lines.py -q` 全绿（Task 1-tests 用例转 PASS）。
- Real path verify: Task 9 full e2e 确认产物真落 papers 子目录。
- Manual/device verify: none

**Steps:**
1. `lib/config.py`：在现有 episodes/state/reports 派生（config.py:246-254）之后，若 `papers` section 存在，派生 `papers/{episodes,state,reports}` 并 mkdir；暴露访问器（与现有 `episodes_dir` 等同款 frozen 字段或 `papers_*_dir`）。**gated on `papers.*` 存在**——opinion-only config 不触发。
2. `lib/lines.py`：`LineBundle` 加输出根解析（参照 `floor_fn` 注入模式）。`_opinion_output_root(ctx)` 返回 `output_dir`（根）；`_paper_output_root(ctx)` 返回 `output_dir/papers`。绑进 `OPINION_LINE` / `PAPER_LINE`。
3. 确认 opinion bundle 解析路径与现状一致（与 Task 1-tests 的 byte-identical 锚点对齐）。

**Verify:**
Run: `python3 -m pytest lib/tests/test_config.py lib/tests/test_lines.py -q`
Expected: 全绿（含 Task 1-tests 新用例 + opinion byte-identical 锚点）。
<!-- /section -->

<!-- section: task-2-tests keywords: paperlog, paperline, continuity -->
### Task 2-tests: paper-log 存储模块 — 测试先行

**Maps to Impact Map:** Data path (paper-log 追加/读回); Shared surfaces (新 lib/paperline/paperlog.py); 不碰 opinion

**Files:**
- Test: `lib/tests/test_paperline_paperlog.py`（新）

**Expected outcome:** 测试断言一个新模块 `lib/paperline/paperlog.py` 的契约：`load_paperlog(state_dir)`（缺文件→空 list；损坏→raise，不静默当空）；`append_paper(state_dir, entry)`（append-only，校验 `{arxiv_id,title,date,concepts}` schema + arxiv_id 格式 + 去换行/控制字符；重复 arxiv_id 拒绝或幂等——见 steps）；`is_covered(paperlog, arxiv_id)`（精确 id 命中，DP-403=A）。此刻 FAIL（模块不存在）。

**Non-goals:** 不做概念 embedding 相似度（DP-403=A 不上）；不碰 stance/coveredground（D-015）。

**Touched surface:** test 文件

**Regression shield:** `test_module_does_not_import_episode`-同款断言：`paperlog.py` 不 import stance/coveredground/magnitude/bible（线隔离）。

**Task Contract:**
- Expected behavior: （测试态）讲过的论文记进一个 append-only 的清单，下次能查出"这篇讲过"。
- Automated verify: `python3 -m pytest lib/tests/test_paperline_paperlog.py -q` —— FAIL（`ModuleNotFoundError: lib.paperline.paperlog`）。
- Real path verify: N/A
- Manual/device verify: none

**Steps:**
1. 写 `load_paperlog` 用例：缺文件→`[]`；合法 YAML→list[dict]；损坏 YAML→raise。
2. 写 `append_paper` 用例：追加一条→`load_paperlog` 读回含它；schema 缺字段/arxiv_id 非法/title 含换行→raise（fail-closed）；append-only（不覆盖既有条目）。
3. 写 `is_covered` 用例：id 在 log→True；不在→False。
4. 写线隔离断言（import 探针）。

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_paperlog.py -q`
Expected: 全 FAIL（模块未实现）。
<!-- /section -->

<!-- section: task-2-impl keywords: paperlog, paperline, continuity -->
### Task 2-impl: paper-log 存储模块 — 实现（D-013）

**Depends on:** Task 2-tests

**Maps to Impact Map:** Data path; Shared surfaces (lib/paperline/paperlog.py)

**Files:**
- Create: `lib/paperline/paperlog.py`

**Expected outcome:** `paperlog.py` 提供 `load_paperlog` / `append_paper` / `is_covered`，读写 `state_dir/paper-log.yaml`，schema `{arxiv_id,title,date,concepts}`、append-only、无 bets/观点（D-013）。写入校验 fail-closed（Threat Model § Input validation）。原子写（temp + `os.replace`，mirrors coveredground `write_store`）。

**Non-goals:** 不 import opinion 专属模块；不做概念相似度门。

**Touched surface:** `lib/paperline/paperlog.py`（新）

**Regression shield:** 只 import `lib.paperline.*` / stdlib / yaml；线隔离测试守住。

**Task Contract:**
- Expected behavior: 讲过的论文进 append-only 清单，可查重，脏数据写不进去。
- Automated verify: `python3 -m pytest lib/tests/test_paperline_paperlog.py -q` 全绿。
- Real path verify: Task 9 e2e 跑两期，第二期 curator 拿到第一期写的 paper-log。
- Manual/device verify: none

**Steps:**
1. 实现 `load_paperlog(state_dir) -> list[dict]`：缺文件→`[]`；`yaml.safe_load`；损坏→raise。
2. 实现 `append_paper(state_dir, entry)`：校验 schema + arxiv_id 正则 + sanitize title/concepts；append-only；原子写。
3. 实现 `is_covered(paperlog, arxiv_id) -> bool`：精确 id 命中。
4. **Regression shield:** 不 import stance/coveredground/magnitude/bible/episode。

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_paperlog.py -q`
Expected: 全绿。
<!-- /section -->

<!-- section: task-3-tests keywords: same-day-guard, paperlog-read, topology -->
### Task 3-tests: 同日护栏 + paper-log-read 前段站点 — 测试先行（DP-404=A, DP-403=A）

**Maps to Impact Map:** User path (同日 fail-fast); Data path (paper-log 喂 curator); Existing consumers (pipeline_papers 拓扑 + curator 输入)

**Files:**
- Test: `lib/tests/test_pipeline_papers.py`（加拓扑站点断言）
- Test: `lib/tests/test_paperline_executors.py`（加 same-day guard + paper-log-read executor 用例；若文件不存在则 Create）

**Expected outcome:** 断言：(1) 论文拓扑在 `curator` 之前有 `paper-log-read`（code）站点，产出 scratch 真实文件（如 `paper-log.json`），且 `curator` 的 `inputs` 含该真实文件名而非字面 `"paper-log"`；(2) 有 `same-day-guard`（code）站点（fail-closed：今天 papers/episodes 已有产物→halt）；(3) executor 行为：paper-log-read 缺文件→空、损坏→halt；same-day guard 命中→halt、未命中→放行。此刻 FAIL。

**Non-goals:** 不在共享 `runner._CONCEPTUAL` 里加 paper 分支（在 paperline 侧暂存真实文件，curator 正常解析它）。

**Touched surface:** test 文件

**Regression shield:** 拓扑断言只针对 papers；opinion 拓扑 golden 不受影响。

**Task Contract:**
- Expected behavior: 今天已出一期再跑会被拦下（不重复发）；讲过的论文清单真喂给了选题判官。
- Automated verify: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_paperline_executors.py -q` —— 新用例 FAIL。
- Real path verify: N/A
- Manual/device verify: none

**Steps:**
1. 拓扑断言：`paper-log-read` 在 `curator` 前；`curator.inputs` 含真实暂存文件名（不含字面 `"paper-log"`）；`same-day-guard` 站点存在（在 curator 前或 design 3a 位置）。
2. executor 用例：`_paper_log_read_executor`（缺文件→写空 paper-log.json / 损坏→halt dict）；`_same_day_guard_executor`（papers/episodes 有今天产物→halt dict / 无→None）。
3. DP-403=A 锚点：discovery/curator 侧 arXiv-id 精确去重——candidates 含已 covered id 时被剔除（用例构造 paper-log 含某 id + candidates 含该 id，断言 curator 输入候选不含它，或 curator 产出不选它的代码前置过滤）。

**Verify:**
Run: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_paperline_executors.py -q`
Expected: 新用例 FAIL（站点/executor 未实现）；既有 papers 拓扑用例仍绿。
<!-- /section -->

<!-- section: task-3-impl keywords: same-day-guard, paperlog-read, topology -->
### Task 3-impl: 同日护栏 + paper-log-read 前段站点 — 实现

**Depends on:** Task 3-tests, Task 2-impl

**Maps to Impact Map:** User path; Data path; Existing consumers (拓扑 + curator)

**Files:**
- Modify: `lib/pipeline_papers.py`（在 curator 前插 `same-day-guard` + `paper-log-read` 站点；curator `inputs` `"paper-log"`→真实暂存文件名）
- Modify: `lib/paperline/executors.py`（`_same_day_guard_executor`、`_paper_log_read_executor`；arXiv-id 去重前置过滤）
- Modify: `lib/lines.py`（paper executor_map / gate_map 注册新 code station；如需 gate）

**Expected outcome:** 论文线在选题前：(a) same-day guard fail-closed 拦今天重跑（DP-404=A）；(b) paper-log-read 把 `state/paper-log.yaml` 读成 scratch 真实文件喂 curator；(c) arXiv-id 精确去重剔除已讲论文（DP-403=A）。共享引擎零行为改动。

**Non-goals:** 不改 opinion；不在 `runner._CONCEPTUAL` 加分支；不做概念 embedding 门。

**Touched surface:** `lib/pipeline_papers.py`、`lib/paperline/executors.py`、`lib/lines.py`

**Regression shield:** opinion 拓扑/executor 不碰；`test_line_isolation` + golden 守住。

**Task Contract:**
- Expected behavior: 今天已出一期→fail-fast；选题判官真拿到讲过的论文清单并避开它们。
- Automated verify: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_paperline_executors.py -q` 全绿。
- Real path verify: Task 9 e2e 跑两期，第二期不重选第一期的论文 + 同日二跑 fail-fast。
- Manual/device verify: none

**Steps:**
1. `pipeline_papers.py`：curator 前插 `same-day-guard`(code) + `paper-log-read`(code)；curator `inputs` 改 `["candidates.json", "paper-log.json"]`（真实暂存）。
2. `executors.py`：`_paper_log_read_executor`（用 Task 2 `load_paperlog`→写 scratch `paper-log.json`；缺→空、损坏→halt dict）；`_same_day_guard_executor`（查 paper 输出根的 episodes 是否有今天产物→halt dict / None；用 Task 1 的输出根解析）；discovery/curator 侧用 `is_covered` 前置过滤候选。
3. `lines.py`：注册新 code station 到 paper executor_map（gate 如需）。

**Verify:**
Run: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_paperline_executors.py -q`
Expected: 全绿。
<!-- /section -->

<!-- section: task-4 keywords: broadcast-script, tts, jay, topology -->
### Task 4: 口播稿 + TTS 站点（论文线尾段）

**Maps to Impact Map:** Data path (finalize body → 口播稿 → mp3); Existing consumers (jay persona, no_tts 机制); Must remain unchanged (opinion tts 步)

**Files:**
- Modify: `lib/pipeline_papers.py`（faithfulness 后加 `broadcast-script`(agent) + `tts`(agent, skip_when="no_tts") 站点；**`PAPER_AGENT_WHITELIST`(line 42) 必须加 `jay` + 新口播 persona 名**——否则 `load_papers_pipeline`→`validate_pipeline`(pipeline.py:739) 在 **load 时** raise "agent not in whitelist"）
- Modify: `lib/lines.py`（确认 paper agent_dir = `agents/papers/`，既有）
- Create: `agents/papers/<口播 persona>.md`（论文线口播稿 persona——**不复用 opinion `bianyang`**，那会引主播声音；论文线观点退场，用讲解者声音）

**Expected outcome:** 忠实门通过后，论文线把 finalize body 改写成口播稿（讲解者声音，不引主播观点），再经 `jay` TTS 产 `audio-files.mp3`；`no_tts=True` 时整 tts 步跳过（迭代期）。`load_papers_pipeline('papers')` 不 raise（broadcast persona + jay 已入 PAPER_AGENT_WHITELIST）。

**Non-goals:** 不跨厂混音（单 vendor/单 voice，opinion 既有约束）；不改 opinion tts 步。

**Touched surface:** `lib/pipeline_papers.py`、`agents/papers/`、`lib/lines.py`

**Regression shield:** 复用 opinion `skip_when="no_tts"` 语义（runner.py:1175 既有，不改）；opinion tts 步 byte-identical。

**Task Contract:**
- Expected behavior: 论文线能产出能听的 mp3；调试时 `--no-tts` 跳过 TTS 只出文字。
- Automated verify: `python3 -c "from lib.pipeline_papers import load_papers_pipeline; load_papers_pipeline('papers')"`（load 不 raise——证 broadcast persona + jay 已入 PAPER_AGENT_WHITELIST，关掉 load-time unknown-agent 崩）+ `python3 -m pytest lib/tests/test_pipeline_papers.py -q`（断言 tts 步 `skip_when=="no_tts"` + agent=="jay" + broadcast-script 站点存在）。
- Real path verify: Task 9 含 TTS full e2e 产真 mp3。
- Manual/device verify: ⚠️ 需真跑验证：mp3 可播、是讲解者声音（Task 9 听一段）。

**Steps:**
1. `pipeline_papers.py`：faithfulness 后加 `broadcast-script`(agent，输入 finalize body + 讲解者声音)，产 `broadcast-script-{date}.txt`；再加 `tts`(agent="jay"，输入 broadcast-script，artifact="audio-files.mp3"，`skip_when="no_tts"`，gate `check_artifact`)——mirror opinion pipeline.py:478-491。
2. **whitelist（load-time 硬要求，verifier must-revise#2）**：口播稿不复用 opinion `bianyang`（会引主播声音）→ 用论文线自己的口播 persona（`agents/papers/`，讲解者声音）。**把该 persona 名 AND `jay` 都加进 `PAPER_AGENT_WHITELIST`**(pipeline_papers.py:42)——`jay` 当前只在 opinion 的 `AGENT_WHITELIST`(pipeline.py)、**不在** paper whitelist；任一 agent step 的 agent 不在 PAPER_AGENT_WHITELIST → `load_papers_pipeline` 在 `validate_pipeline`(pipeline.py:739) **load 时**崩（不是 runtime）。
3. `lines.py`：确认 paper agent_dir 解析到 `agents/papers/`（既有）。

**Verify:**
Run: `python3 -m pytest lib/tests/test_pipeline_papers.py -q`
Expected: 绿（tts/broadcast 站点断言通过）。
<!-- /section -->

<!-- section: task-5-tests keywords: publish, paperline, episode-paths -->
### Task 5-tests: 发布站点 — 测试先行（D-009）

**Maps to Impact Map:** User path (.md/.mp3 落 papers 目录); Data path (finalize body → published .md); Must remain unchanged (opinion _publish_step)

**Files:**
- Test: `lib/tests/test_paperline_executors.py`（加 publish executor 用例）

**Expected outcome:** 断言论文线 publish executor：从 `finalize-result.json` 读 title+body，写 `{papers_episodes}/{date}-{slug}.md`，`no_tts=False` 时移 `audio-files.mp3`→`.mp3`；title 经 `sanitize_title`、空 slug 回退 `{date}-papers`。**写到 papers 输出根**（Task 1），不写 opinion episodes。此刻 FAIL。

**Non-goals:** 不复用/不改 `runner._publish_step`（那是 opinion 共享路径——除非 DP 决定参数化；本计划走 paperline 自有 executor 保零变化）。

**Touched surface:** test 文件

**Regression shield:** 断言写入路径在 papers 子目录（不污染 opinion episodes）。

**Task Contract:**
- Expected behavior:（测试态）论文成稿变成 papers 目录里的一篇 .md（+mp3）。
- Automated verify: `python3 -m pytest lib/tests/test_paperline_executors.py -q` —— publish 用例 FAIL。
- Real path verify: N/A
- Manual/device verify: none

**Steps:**
1. 用例：构造 scratch 含 `finalize-result.json {title,body}` + `audio-files.mp3`；调 paper publish executor；断言 `{papers_episodes}/{date}-{slug}.md` 内容==body、`.mp3` 被移入；no_tts=True 时不移 mp3。
2. 用例：title 含非法字符→sanitize；空 title→`{date}-papers`。

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_executors.py -q`
Expected: publish 用例 FAIL。
<!-- /section -->

<!-- section: task-5-impl keywords: publish, paperline, episode-paths -->
### Task 5-impl: 发布站点 — 实现

**Depends on:** Task 5-tests, Task 1-impl

**Maps to Impact Map:** User path; Data path; Must remain unchanged (opinion _publish_step)

**Files:**
- Modify: `lib/paperline/executors.py`（`_paper_publish_executor`）
- Modify: `lib/pipeline_papers.py`（tts 后加 `publish`(code) 站点）
- Modify: `lib/lines.py`（注册 publish executor 到 paper executor_map）

**Expected outcome:** 论文线 publish code station 把 finalize body 写成 `{papers_episodes}/{date}-{slug}.md`、移 mp3，复用 `episode.episode_paths`/`sanitize_title`/`load_finalize_body`（纯函数，非 opinion 专属）但用 paper 输出根。opinion `_publish_step` 不动。

**Non-goals:** 不改 `runner._publish_step`；不跨线写。

**Touched surface:** `lib/paperline/executors.py`、`lib/pipeline_papers.py`、`lib/lines.py`

**Regression shield:** 复用 `lib.episode` 的纯路径/标题/body helper（这些被 opinion+paper 共用，无线状态）；写入根来自 Task 1 paper 输出根；golden + isolation 守住。

**Task Contract:**
- Expected behavior: 论文成稿落 papers 目录一篇 .md（+mp3），早晚间发布不变。
- Automated verify: `python3 -m pytest lib/tests/test_paperline_executors.py lib/tests/test_pipeline_papers.py -q` 全绿。
- Real path verify: Task 9 e2e 真发布到 papers 目录。
- Manual/device verify: none

**Steps:**
1. `executors.py`：`_paper_publish_executor(ctx)`——用 Task 1 paper 输出根算 episodes 目录→`episode_paths`→写 body→移 mp3（`if not no_tts`）。镜像 `_publish_step` 逻辑但 paper 根、落 paperline。
2. `pipeline_papers.py`：加 `publish`(code) 站点，置于 `paper-log-write` **之后**（canonical 尾序 `…tts → paper-log-write → publish → cleanup`；DP-601=B——发布前已写 log）。
3. `lines.py`：注册。

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_executors.py lib/tests/test_pipeline_papers.py -q`
Expected: 全绿。
<!-- /section -->

<!-- section: task-6-tests keywords: paperlog-write, gate, topology -->
### Task 6-tests: paper-log 写入站点 + 阻塞门 — 测试先行（D-013, DP-601=B）

**Maps to Impact Map:** Data path (发布前追加 paper-log); User path (下期去重生效)

**Files:**
- Test: `lib/tests/test_pipeline_papers.py`（拓扑：`tts` 后、`publish` 前有 `paper-log-write`(code) + 阻塞 gate）
- Test: `lib/tests/test_paperline_executors.py`（write executor 用例）

**Expected outcome:** 断言：(1) 拓扑在 `tts` **后、`publish` 前**有 `paper-log-write`(code) 站点（**DP-601=B：发布前写 log，关 orphan 窗口**），带保证写入的**阻塞** gate（非 coveredground 式 fail-soft——去重命脉）；(2) executor 从 finalize/ledger 抽 `{arxiv_id,title,date,concepts}` 追加进 `state/paper-log.yaml`；写失败→halt（在任何产物 aired 之前）。此刻 FAIL。

**Non-goals:** 不 fail-soft（与 coveredground post-publish 不同——D-013 去重依赖它）。

**Touched surface:** test 文件

**Regression shield:** 拓扑断言只针对 papers。

**Task Contract:**
- Expected behavior:（测试态）发布完这篇就进了讲过清单，下期查得到。
- Automated verify: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_paperline_executors.py -q` —— 新用例 FAIL。
- Real path verify: N/A
- Manual/device verify: none

**Steps:**
1. 拓扑断言：canonical 尾序 `…faithfulness → broadcast-script → tts → paper-log-write → publish → cleanup`——`paper-log-write` 在 `tts` 后、`publish` 前；gate 非 fail_soft。
2. executor 用例：调 write executor→`load_paperlog` 读回含本期 `{arxiv_id,title,date,concepts}`；concepts 来源（finalize/ledger）；写失败路径→halt dict。

**Verify:**
Run: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_paperline_executors.py -q`
Expected: 新用例 FAIL。
<!-- /section -->

<!-- section: task-6-impl keywords: paperlog-write, gate, topology -->
### Task 6-impl: paper-log 写入站点 + 阻塞门 — 实现（DP-601=B）

**Depends on:** Task 6-tests, Task 2-impl, Task 5-impl（仅为拓扑插入锚点——paper-log-write 站点须插在 Task 5 的 publish 站点**之前**；运行序 = log→publish）

**Maps to Impact Map:** Data path (发布前追加 paper-log); User path (下期去重)

**Files:**
- Modify: `lib/paperline/executors.py`（`_paper_log_write_executor` + write gate）
- Modify: `lib/pipeline_papers.py`（在 `publish` 站点**前**插 `paper-log-write`(code)；`cleanup`(code) 置尾——canonical 尾序 `…tts → paper-log-write → publish → cleanup`）
- Modify: `lib/lines.py`（注册 executor + gate）

**Expected outcome:** **发布前**把本期论文 `{arxiv_id,title,date,concepts}` 经 Task 2 `append_paper` 追加进 `papers/state/paper-log.yaml`；阻塞 gate 保证写入（写失败 halt **在产物 aired 之前** → 无 aired-but-unlogged 重复播洞，D-009 honored）。concepts 从 ledger/finalize 抽（核心概念 tags）。arxiv_id 来自 step4 `chosen-arxiv-id.json`。

**Non-goals:** 不 fail-soft；不碰 stance/coveredground。

**Touched surface:** `lib/paperline/executors.py`、`lib/pipeline_papers.py`、`lib/lines.py`

**Regression shield:** 线隔离 + golden。

**Task Contract:**
- Expected behavior: 这篇发布后进讲过清单，下期不会重选。
- Automated verify: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_paperline_executors.py -q` 全绿。
- Real path verify: Task 9 跑两期，第二期 paper-log 含第一期论文。
- Manual/device verify: none

**Steps:**
1. `executors.py`：`_paper_log_write_executor`——读 chosen-arxiv-id/ledger/finalize 抽 `{arxiv_id,title,date,concepts}`→`append_paper`（Task 2）。写 gate（`check_paper_log_appended` 类，mirror `check_topic_log_appended`）。
2. `pipeline_papers.py`：在 `publish` 站点**前**插 `paper-log-write`(code，gate 非 fail_soft)；`cleanup`(code) 置尾（尾序 `…tts → paper-log-write → publish → cleanup`）。
3. `lines.py`：注册。

**Verify:**
Run: `python3 -m pytest lib/tests/test_pipeline_papers.py lib/tests/test_paperline_executors.py -q`
Expected: 全绿。
<!-- /section -->

<!-- section: task-7 keywords: cli, command, skill-md, slot -->
### Task 7: 命令暴露 + slot 文档（DP-401=A）

**Maps to Impact Map:** User path (/podcast papers); Shared surfaces (CLI --show choices, SKILL.md)

**Files:**
- Modify: `lib/runner.py`（`_cli` 的 `--show` `choices=("morning","evening")` → 加 `"papers"`）
- Modify: `skills/podcast/SKILL.md`（加 `/podcast papers` 命令块 + 第三 slot 建议时段）

**Expected outcome:** `python -m lib.runner --show papers [--date] [--no-tts]` 可跑；`/podcast papers` 在 SKILL.md 有命令块（含 4 段结构说明、忠实门、与早晚间的定位区别）；slot 时段作为 usage 约定写明推荐默认（无代码门——cadence 经 `/loop`）。

**Non-goals:** 不改 morning/evening 命令块；不加 cron（scope：cadence 经 `/loop`）。

**Touched surface:** `lib/runner.py` `_cli`、`skills/podcast/SKILL.md`

**Regression shield:** `--show morning|evening` 路径不变（只往 choices tuple 加一项）；CLI 既有 arg 行为不变。

**Task Contract:**
- Expected behavior: 用户能 `/podcast papers` 跑论文线一期。
- Automated verify: `python3 -c "import lib.runner"` 通过 + `python3 -m lib.runner --show papers --help` 退出 0（choices 含 papers）；grep SKILL.md 含 `papers` 命令块。
- Real path verify: Task 9 经 CLI/`run_pipeline('papers')` 跑通。
- Manual/device verify: none

**Steps:**
1. `runner.py:_cli`：`choices=("morning","evening","papers")`；`--show` help 文案加 papers。
2. `SKILL.md`：加 `/podcast papers` 命令块（镜像 morning/evening，写明科普定位 + 4 段 + 忠实门 + 论文线独立目录）；slot 时段写推荐默认（如午间，介于 morning/evening 之间）+ 注明用户可自定 `/loop` 时段。

**⚠️ No test:** CLI choices + SKILL.md 是 config/doc 改动（无条件逻辑）；由 Task 9 e2e 经命令路径覆盖。

**Verify:**
Run: `python3 -m lib.runner --show papers --help; grep -c "podcast papers" skills/podcast/SKILL.md`
Expected: help 退出 0 且 `papers` 在 choices；grep ≥1。
<!-- /section -->

<!-- section: task-8 keywords: e2e, tts, zero-change, full-pipeline -->
### Task 8: 含 TTS full e2e + 早晚间零变化最终确认 + 生成 e2e 更新

**Depends on:** Task 1-impl, Task 3-impl, Task 4, Task 5-impl, Task 6-impl, Task 7

**Maps to Impact Map:** User path (完整一期); Regression checks (DP-A2 四门 + 完整 e2e)

**Files:**
- Modify: `evals/paperline_generation_e2e.py`（拓扑延长到 publish/paper-log——更新预期，使生成 e2e 仍覆盖到新尾段或明确止于 faithfulness 不被新站点打断）
- Create: `evals/paperline_full_e2e.py`（或扩 `p3-live-sandbox/drive_live.py`）——含 TTS 的真实一期
- Test: `lib/tests/test_pipeline_papers.py`（全拓扑站点顺序 golden 锚点）

**Expected outcome:** (1) 真实 full e2e（含 TTS，`--model sonnet` + `no_proxy='*'` + 输出在 `.claude/` 外）产出 `.md`+`.mp3` 落 papers 目录；(2) 跑两期验 paper-log 去重生效（第二期不重选第一期论文）+ 同日二跑 fail-fast；(3) 论文线输出与 opinion 物理隔离（不同目录）；(4) **早晚间 DP-A2 四门 + 完整 e2e 全绿**（最终零变化确认）；(5) UT/E2E 全绿。

**Non-goals:** 不修 `--resume`（handoff §5.d 非阻塞 finding，e2e 用整跑）。

**Touched surface:** `evals/*`、`lib/tests/test_pipeline_papers.py`

**Regression shield:** opinion golden（门②）+ `test_line_isolation` + 426 lib + 8 bats + 184 prep 全绿。

**Task Contract:**
- Expected behavior: 跑 `/podcast papers` 出一篇能听的论文科普；讲过的不重讲；今天别重发；早晚间完全没变。
- Automated verify: `python3 -m pytest lib/tests/ -q`（全绿）+ `python3 evals/paperline_generation_e2e.py`（exit 0）+ `python3 evals/paperline_engine_collection_e2e.py`（exit 0）。
- Real path verify: `SANDBOX_DIR=… PODCAST_STUDIO_CONFIG=… no_proxy='*' python3 evals/paperline_full_e2e.py`（含 TTS，注入 `--model sonnet`）——产真 .md+.mp3 落 papers 目录；跑两期验去重 + 同日护栏。**输出在 `.claude/` 外**（handoff §5.c）。
- Manual/device verify: ⚠️ 需真跑/真听验证：mp3 可播、是讲解者声音、.md 是 4 段忠实解读（人工抽听一段 + 读一遍）。

**Steps:**
1. 更新 `paperline_generation_e2e.py`：拓扑延长后，确认 Part B/C 仍按预期（happy 走到 publish/paper-log，block 仍 halt 在 faithfulness）；按需补 publish/paper-log 站点的 staging。
2. 写 `paperline_full_e2e.py`（或扩 `drive_live.py`）：monkeypatch 注入 `model="sonnet"`；`no_proxy='*'`；output_dir 在 `.claude/` 外；跑一期含 TTS→断言 papers/episodes 有 .md+.mp3；跑第二期→断言不重选 + paper-log 增长；同日二跑→断言 fail-fast。
3. 拓扑 golden 锚点：`test_pipeline_papers.py` 断言完整站点顺序（config→…→faithfulness→broadcast-script→tts→**paper-log-write→publish**→cleanup；DP-601=B：log 在 publish 前）。
4. 早晚间零变化：跑 `test_lines.py`/`test_line_isolation.py`/`test_pipeline.py`（门①②③）+ opinion no-TTS e2e 缓验（门④）。

**Verify:**
Run: `python3 -m pytest lib/tests/ -q && python3 evals/paperline_generation_e2e.py && python3 evals/paperline_engine_collection_e2e.py`
Expected: pytest 全绿、两 e2e exit 0；full e2e（real-path，手动跑）产 .md+.mp3 + 去重 + 同日护栏证毕。
<!-- /section -->

---

## Recommended additions (not in scope)

- **修 `--resume` 论文线坏**（handoff §5.d）：某 station 每次 `make_scratch` 新目录覆盖注入 scratch_dir → 论文线无法续跑。非阻塞，本期 e2e 用整跑规避；建议后续单开一个 fix。
- **概念 embedding 去重 + 衰减**（DP-403=B 的内容）：v1 走 arXiv-id 精确 + curator 软避；概念相似度门 + 衰减留作后续可配增强。

---
## Verification
- **Verdict:** Approved
- **Date:** 2026-06-19
- **Verifier:** dev-workflow:plan-verifier (Opus) — 2 must-revise items resolved (DP-601 publish→log reorder; Task 4 load-time whitelist), re-check confirmed orphan window closed + no new defect. Reports: `.claude/reviews/plan-verifier-2026-06-19-p4.md` + `-p4-recheck.md`.
