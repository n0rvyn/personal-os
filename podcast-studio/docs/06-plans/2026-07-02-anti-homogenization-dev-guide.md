---
type: dev-guide
status: active
tags: [anti-homogenization, thesis-ledger, covered-ground, scorecard, evening-skeleton]
refs:
  - docs/06-plans/2026-06-14-phase2-covered-ground-plan.md
  - docs/02-architecture/ubiquitous-language.md
current: true
confirmed_at: 2026-07-02T00:00:00
---

# 观点线反同质化·彻底修复 Development Guide

**Project brief:** none on disk（scope 锁定见本 guide「Global Constraints」+ 会话内用户批准，本 guide 承担 crystal 角色）
**Design doc:** none — 设计内联在本 guide 各 Phase（沿用 2026-06-14-phase2 的项目惯例）
**Architecture:** docs/02-architecture/ubiquitous-language.md（术语线隔离护栏）

## 背景与根因（为什么要做）

现行反同质化只给**装置层**（比喻/历史锚/命名框架）建了跨期记忆（covered-ground），
没给**论点骨架层**建。经真实产物核对，三个漏点：

1. **论点层重复对所有门全盲。** 54（避难所一直在搬家）与 57（底牌杂牌）是同一个论点
   （"好奇心想象力太轻，真正剩的是认清自己/判断"），但两期 covered-ground 锚**零重叠**
   （54 用照相术/印象派，57 用只会夸你的镜子/潮水退去露礁石）。covered-ground 按设计
   只抽装置、明令不抽论点（温度原则）；dedup 的 temperature shield 明确对"换词重述的同一
   下注"返回 ok=True。→ 论点重推没有任何门在看。
2. **covered-ground 过热判定按"天"数，跟不上一天两更。** 真实 store 里
   `加特林机枪/火力民主化` `count: 3`（6/22早、6/22晚、6/28早），但 `is_stale` 的
   count 判据数的是**不同日期**，6/22 早晚塌成 1 个日期 → 3 次用 = 2 个日期 < 3 →
   从未判热 → 55 自由复用。
3. **蒸馏静默缺勤，跨期记忆有洞。** store 缺 6/23、6/25-27 的条目（对应 53 信道脱手等），
   蒸馏站 fail-soft 但缺勤不 fail-loud，导致 count 系统性偏低、跨期比对瞎一天。

彻底修复 = 翻转激励：从"避让装置"升级成"**每期必须对某条论点产生可观测推进，否则不许
开这个骨架**"。手段是新增一条与 covered-ground 平级的**论点账本 thesis-ledger**，pre-draft
渲染续写/避让 memo steer davinci，post-draft 用**增量硬门**enforce；温度盾重划边界：保护
stance persistence（同一下注被坚持/结算/演进），不保护 thesis re-derivation（同一论点零增量
重推）。再加晚间**骨架模板池**打破单一结构。

## Global Constraints

- **技术栈：** Python 3（`lib/*.py` 确定性 helper）+ Claude 派发的 persona agents（`agents/*.md`）+ PyYAML；测试 `python3 -m pytest lib/tests/ -q`。
- **编码规范（CLAUDE.md）：**
  - `lib/*.py` 是 importable 模块**不是 CLI**（例外：config/runner/vendored orchestrator）。禁止 `python3 lib/foo.py`。
  - **温度原则**边界已由用户重划（[DP-001]=A，2026-07-02 批准）：界线是「**内容是否重复**」，不是「有没有观点」。memo/门**不得**因"这是个观点"而让主播 hedge——观点自由发挥；只拦"换新闻壳子把同一套内容重讲"。误伤自由表达 = 回归。
  - **Stance card append-only**，无置信度数字，`write_card` 是唯一写卡口。
  - **Config 单一真相源**，fail-closed；产物分层 `episodes/` `state/` `reports/`。
  - **Post-publish 蒸馏站是 fail-soft 的显式例外**（缺产物不 halt 已发布期）；pre-publish 站保持"缺产物即 halt"。
- **术语线隔离（ubiquitous-language.md）：** 新术语 `论点账本 / thesis-ledger` 属**观点线**，
  code 标识 `lib/thesis_ledger.py` / `state/thesis-ledger.yaml` / `agents/thesis-distiller.md`。
  **禁止**与论文线 `paper-log` 混用（论文线无 bets/无论点）。本 guide Phase 3 须把该词写进术语表 + Reserved 段。
- **不改动的既有行为：** 量臣 none/light/medium/heavy 分量路由、`gather_recent_bodies`、
  covered-ground 只抽装置的纯净性、bible-distiller 隔离站、no-TTS 模式、选稿 `select_draft`
  按 max total（不信 `selected` flag）。

---

<!-- section: phase-1 keywords: covered-ground, is_stale, staleness, cadence, date-show -->
## Phase 1: covered-ground 过热判定修复（装置层次要防线）

**Goal:** covered-ground 的 `is_stale` 计数口径从"14 天内不同日期数"改成"14 天内 (date,show) 使用次数"，同日早晚双更不再塌成一个日期；`加特林机枪/火力民主化`（count=3 跨 2 日期）在真实 store 上判热。
**Depends on:** None
**Scope:**
- `lib/coveredground.py`：`_episodes_in_window` / `is_stale` 计数改为按 (date,show) 去重后的**使用次数**（沿用 `episodes[]` 里 `_episode_signature` 的 (date,show) 元组），而非 distinct date；count 阈值 3 不变，recency 判据同步复核（`_RECENCY_LOOKBACK_DAYS` 在两更节奏下是否仍合理）。
- `lib/tests/test_coveredground.py`：新增"同日早晚双更计 2 次"回归用例 + "加特林 3 次跨 2 日期判热"用例。
- 复核 `dedup.check_cross_dup`（读同一 `is_stale`）无需改动，但加一条断言确认它现在能命中热锚。

**用户可见的变化:**
- 无听众可见变化（装置层内部防线）。运营侧：跑 `render_memo` 对真实 `state/covered-ground.yaml` 时，`加特林机枪/火力民主化` 会出现在"最近用滥、请避开"清单里。

**Architecture decisions:** 计数单位定为 (date,show)-use 还是 raw `count` 字段？（`count` 已含 reskin 折叠次数，可能高估；(date,show) 去重更贴"跨期"语义 — 留 /write-plan 定，倾向 (date,show)）。

**Acceptance criteria:**
- [ ] `is_stale` 对"同一锚、6/22-morning + 6/22-evening + 6/28-morning"返回 `True`（14 天窗内）。
- [ ] 对"同一锚、仅 6/22-morning + 6/22-evening"（2 次同日不同 show）计数为 2、未判热（阈值 3）。
- [ ] `python3 -m pytest lib/tests/test_coveredground.py -q` 全绿，含新增用例。
- [ ] 在真实 `state/covered-ground.yaml` 上跑 `render_memo(load_store(...), '2026-06-30')`，输出含 `加特林机枪/火力民主化`。

**Review checklist:**
- [ ] run-phase review step（auto-invokes implementation-reviewer）
- [ ] `grep -rn "distinct.*date\|_distinct_episode_dates" lib/coveredground.py` 确认口径改动无遗留双系统
<!-- /section -->

---

<!-- section: phase-2 keywords: distiller, fail-loud, resume, backfill, run-log -->
## Phase 2: 蒸馏可靠性 — fail-loud 缺勤 + resume 补跑 + 缺口回填

**Goal:** 每期发布后 covered-ground 蒸馏（step 18/19）与 stance 蒸馏（step 15c/16）**必然尝试**，缺勤/失败 fail-loud 记 run log（保持不 halt 已发布期的 fail-soft 语义，但不再静默）；提供回填工具补齐 store 已有缺口（6/23、6/25-27）。这是后续论点账本"每期一条记录"的前置血管。
**Depends on:** None（可与 Phase 1 并行，但须在 Phase 3 前完成）
**Scope:**
- `lib/runner.py`：现状精确化——"站跑了才失败"已 `print(...fail_soft — skipping...)` 到 stderr（runner.py:1461/1510），真正静默的是**缺勤**（站从未派发，如 resume 把"已发布"当"已完成"而跳过蒸馏）。本 Phase 补三件净新：(a) 一个**持久化结构化 run-log**（现仅 stderr print，无落盘 log 机制）含 (date/show/step/原因)；(b) **缺勤探测**（该跑没跑要 fail-loud）；(c) resume 路径确保未完成的蒸馏站会重跑（核对 checkpoint 是否把已发布=已完成而跳过蒸馏）。
- `tools/backfill-covered-ground.sh`（或 `lib` 内 importable + 薄 `__main__`，遵循既有 runner 例外惯例）：对指定日期区间的已发布 `.md` 重跑 coveredground-distiller + update，补齐 store。
- `lib/tests/test_runner.py`：蒸馏失败时 run log 出现 WARNING 且 run 不 halt；resume 场景蒸馏重跑的断言。

**用户可见的变化:**
- 无听众可见变化。运营侧：一期节目若蒸馏没跑成，run log 里会有明确一行 WARNING（现在是静默），运营能发现并回填。

**Architecture decisions:** 回填是独立工具一次性补历史，还是 runner 启动时自动探测缺口补跑？（倾向独立工具 + fail-loud，避免每次 run 扫全history；留 /write-plan 定）。缺勤 WARNING 是否也落一份到 `reports/`？

**Acceptance criteria:**
- [ ] 模拟 coveredground-distiller 派发失败：run 不 halt，run log 含一条含 (date,show,step,reason) 的 WARNING。
- [ ] resume 一个"已发布但蒸馏未完成"的期：蒸馏站重新执行（不因"已发布"跳过）。
- [ ] 回填工具对 6/23 单日跑通后，`state/covered-ground.yaml` 出现该期锚条目。
- [ ] `python3 -m pytest lib/tests/test_runner.py -q` 全绿含新增用例。

**Review checklist:**
- [ ] run-phase review step（auto-invokes implementation-reviewer）
- [ ] 确认 fail-soft 不变量未被破坏：pre-publish 站仍"缺产物即 halt"，仅 post-publish 蒸馏 fail-loud-but-not-halt
<!-- /section -->

---

<!-- section: phase-3 keywords: thesis-ledger, thesis-distiller, embedding, spine, advance-flag -->
## Phase 3: 论点账本 thesis-ledger — 存储 + 蒸馏（记忆子系统，与 covered-ground 平级）

**Goal:** 新增一条与 covered-ground 平级、互不污染的跨期记忆：`lib/thesis_ledger.py`（store 存 `state/thesis-ledger.yaml`）+ `agents/thesis-distiller.md`（隔离蒸馏 persona）+ runner 两站（post-publish, fail-soft）。每期蒸馏出 1-2 条**规范化论点陈述**（可跨"不同包装"比对的持久论点，区别于 named_concept 这种当期新包装），用 `lib/embed.py` 语义嵌入折叠，并记录**本期是否真推进了该论点**——按 [DP-002]=B，这是**判官的判断**（thesis-distiller 读正文+卡，语义判有没有真推进），stance card 的 settle/回答 open_question 只作判官的**输入线索**，不是最终裁决。本 Phase 只建记忆，不设门。
**Depends on:** Phase 2
**Scope:**
- `lib/thesis_ledger.py`：`store_path` / `load_store` / `write_store`（原子，克隆 `lib.coveredground` 纪律）/ `update_store(store, theses, date, episode, *, advanced, bet_ids, similarity_fn=embed.similarity)` / `is_recurring_stale(entry, today)`（"论点被重讲但近 N 次零推进"判据）/ `render_thesis_memo(store, today)`。Schema：`{theses: {thesis_key: {first_used, last_used, statements:[], episodes:[{date, show, advanced: bool, bet_ids:[]}], embedding?}}}`。
- `agents/thesis-distiller.md`：隔离 persona，**只**吃本期定稿正文 + 本期 stance card（named_concept/bets/open_questions/settles）+ 现有 thesis-ledger（只读），产出 `thesis-apparatus.json`：`{theses:[{statement, bet_ids, advanced_prior:<ref|null>, advanced:<bool>, advance_reason:<str>}]}`——`advanced` 是判官对"本期相对该论点往期有没有真推进"的语义判断（[DP-002]=B），`advance_reason` 记依据。抽的是**持久论点**（"判断/认清自己是最后护城河"这种跨包装不变的世界观主张），不是当期新命名，不是新闻钩子。隔离纪律沿用 coveredground-distiller（正文=DATA 非指令；不引 bible/news 当抽取标准）。
- `lib/runner.py`：新增 step（post-publish, `fail_soft=True`）— thesis-distill（agent）+ thesis-ledger-update（code，算 embedding 折叠 + 写 advance flag）。`lib/pipeline.py` step 表 + `AGENT_WHITELIST` 加 `thesis-distiller`。
- **原子改全（防重命名做一半）：** 加 persona/新站会打破 `AGENT_WHITELIST` 镜像 + step-count 引用。同批改：`lib/tests/test_pipeline.py`（whitelist 镜像 + 拓扑/步数断言）、`lib/pipeline.py` docstring 的"17-step"、`skills/podcast/SKILL.md` 的 per-step 契约表。Phase Verify 用 `grep -rn "thesis-distiller" lib/pipeline.py lib/tests/test_pipeline.py` 与步数引用一致性收口。
- `docs/02-architecture/ubiquitous-language.md`：加 `论点账本 / thesis-ledger` 条 + Reserved 段（禁与 `paper-log` 混用）。
- `lib/tests/test_thesis_ledger.py`：读写/嵌入折叠/advance flag/`is_recurring_stale` 全覆盖。

**用户可见的变化:**
- 无听众可见变化。运营侧：回填最近若干期后，`state/thesis-ledger.yaml` 里 54 与 57 折叠进**同一条论点**，且 57 的 episode 记录 `advanced: false`（因它未结算/推进任何该论点的到期注）。

**Architecture decisions:** 论点陈述的规范化粒度（一句话主张 vs 主张+可观测信号）？`is_recurring_stale` 的"零推进次数"阈值。（advance 由判官判已定，[DP-002]=B。）

**Acceptance criteria:**
- [ ] `update_store` 用注入的 fake similarity（≥阈值）把两条不同措辞的同义论点折叠进一个 key，各记一条 episode。
- [ ] 折叠后，某 episode 的 `advanced` flag 正确反映"该期是否推进该论点"。
- [ ] `is_recurring_stale` 对"折叠计数≥2 且最近 N 期均 advanced=false"返回 True；对"最近一期 advanced=true"返回 False。
- [ ] 论点账本蒸馏站失败时 run 不 halt（fail-soft），且经 Phase 2 记 WARNING。
- [ ] `python3 -m pytest lib/tests/test_thesis_ledger.py -q` 全绿；`grep -rn "paper-log" agents/thesis-distiller.md lib/thesis_ledger.py` 零命中（线隔离）。

**Review checklist:**
- [ ] run-phase review step（auto-invokes implementation-reviewer）
- [ ] 术语线隔离核对：thesis-ledger 未复用论文线术语；covered-ground 未被污染（仍只抽装置）
<!-- /section -->

---

<!-- section: phase-4 keywords: thesis-memo, pre-draft, steering, liangchen, continuation -->
## Phase 4: 选题前置 — 论点续写/避让 memo（pre-draft steering）

**Goal:** 从 thesis-ledger 渲染一条**论点 memo**，在采集/assemble-briefs 站 push-inject 进 davinci：若今晚脊柱问题命中一条"近 N 期零推进"的旧论点 → 要么换脊柱问题，要么必须以**续写姿态**开场并带来可观测新推进（结算某到期注/回答某 open_question）。这是 pre-draft 的"防"，对称于 covered-ground 的 avoid_memo。
**Depends on:** Phase 3
**Scope:**
- `lib/thesis_ledger.render_thesis_memo` 接进 runner 的 assemble-briefs 站（与 avoid_memo 并列，分开两条 memo，语义不同：装置换说法 / 论点换题或拿增量）。
- `agents/liangchen.md`（或新增轻量骨架判官）：判据从"新闻候选 vs 往期新闻分量"扩到"今晚脊柱问题（vault open_question）vs thesis-ledger"，三态输出：新论点 / 旧论点+有推进（放行且给续写 recap_hook）/ 旧论点+零推进（标 red-advisory，交硬门 Phase 5 兜底）。liangchen 已从 `build_judge_input` 拿到 recent_cards（bets/open_questions/named_concept），复用即可。
- `agents/davinci.md` + `skills/podcast/references/evening.md`：写稿 brief 增"论点续写"指令——命中旧论点时 ① 段必须承接并声明本期新推进点。
- `lib/tests/`：memo 渲染 + judge 三态解析（fail-soft 默认"当新论点放行"，不 deadlock）。

**用户可见的变化:**
- 听众可见：一个论点再次出现时，晚间 ① 段会以"上次我押 X，今天 Y 撞上了它"的续写口吻开场，而不是从零把同一论点再推一遍（54→57 那种消失）。
- 运营侧：run log/brief 里能看到本期注入的"论点 memo"。

**Architecture decisions:** memo 命中时是"强制换题"还是"强制续写+增量"二选一给 davinci，还是让 davinci 自选？（倾向给二选一，硬性要求二者之一）。是扩 liangchen 还是拆一个独立骨架判官（避免量臣职责膨胀）？

**Acceptance criteria:**
- [ ] 构造"今晚脊柱问题命中一条零推进旧论点"的输入：memo 渲染出该论点 + "换题或拿增量"指令。
- [ ] judge 对"命中旧论点+本期有 due-bet 结算"给 magnitude 放行 + 非空 recap_hook；对"命中+零推进"给 red-advisory。
- [ ] judge 失败 fail-soft 到"当新论点放行"，run 不 deadlock。
- [ ] 相关 `lib/tests/` 全绿。

**Review checklist:**
- [ ] run-phase review step（auto-invokes implementation-reviewer）
- [ ] 端到端听感核对（人工）：构造一个"论点再现"日，确认晚间 ① 段以续写口吻开场而非从零重推（apple-dev feature-reviewer 不适用——本仓是 Python 流水线、无 SwiftUI 用户旅程）
- [ ] 温度盾核对：memo 只约束"论点零增量重推"，不让 davinci 对观点本身 hedge
<!-- /section -->

---

<!-- section: phase-5 keywords: increment-gate, hard-gate, scorecard, temperature-shield, persistence -->
## Phase 5: 增量硬门 check_thesis_advance（post-draft enforce）

**Goal:** 新增**带牙的 thesis-advance 判官门**（[DP-002]=B：进展是判断题、规则算不出，故 LLM 判官判、不是纯 code 门）：发布前，先用 embedding 预筛"本期论点是否命中账本某条旧论点"（确定性、便宜）；命中才派判官读定稿正文+账本旧论点+本期在写的下注，语义判"这是真推进，还是换个新闻壳子把同一套内容重讲"。判官"确信零新意重推"→ 判红，进 `scorecard` 的达标判定触发 retry-then-regenerate。温度盾按 [DP-001]=A 重划：**界线是内容重不重复，不是有没有观点**——保护自由表达 + 一贯立场，只拦零新意重推。fail-soft：判官派发失败/超时 → 放行不 halt（不拖垮每日 run），只有判官明确"重推"裁决才拦。
**Depends on:** Phase 3（Phase 4 并行完成更佳：Phase 4 防、Phase 5 兜）
**Scope:**
- `agents/thesis-advance-judge.md`（新，或复用 thesis-distiller 的 advance 判逻辑另开发布前调用点）：隔离判官，读定稿正文 + 命中的账本旧论点陈述 + 本期在写的 bets，输出 `{verdict: "advance"|"re-derivation", reason}`。确定性信号（settle/open-question delta、bet.claim 与账本历史 claim 的嵌入比对）作判官**输入线索**，不作最终裁决。
- `lib/thesis_gate.py`（新）：`evaluate_thesis_advance(body, stance_in_progress, ledger, today, judge_verdict)` → `{ok, reason, hits}`；embedding 预筛（命中才劳判官）+ 解析判官 verdict + fail-soft（judge 缺/非法 → ok=True 放行，记 advisory）。
- `lib/scorecard.py`：`build_scorecard` 新增 thesis-advance 维度并**给它牙**——判官"确信重推"时进 `passed`（拦发布）；判官失败/未命中 → 不影响 passed（fail-soft）。这是对"能看懂语义重复但没牙"老问题的正解：不是把它塞进纯 code hard_gates，而是让判官维度在"确信"时才有否决权。
- `lib/runner.py`：发布前插判官门站；红 → retry-then-regenerate（重稿走 Phase 4 的续写/换题指令）；judge infra 失败 fail-soft 放行。
- CLAUDE.md：更新温度原则条目——新边界"拦内容重复、不拦观点"（[DP-001]=A），并写明这是判官门（带 fail-soft），不是纯 code 门。
- `lib/tests/`：persistence/自由表达 放行 / 判官"重推"裁决拦截 / 判官失败 fail-soft 放行 / 未命中账本放行。

**用户可见的变化:**
- 听众可见：一期"同一论点零增量重推"的稿子会在发布前被拦下重写，不再流出（54→57 那类从根不可能发布）。
- 运营侧：记分卡出现 `thesis_advance` 硬门行，红时 reason 一行定位。

**Architecture decisions:** 温度边界 [DP-001]=A（拦内容重复不拦观点）+ advance 判据 [DP-002]=B（判官判）已定。待定：embedding 预筛的"命中"阈值（fixture 冻结）；判官门放在发布前哪一步；判官维度进 passed 的触发条件（只"confident re-derivation"）。

**Acceptance criteria:**
- [ ] "同一论点 + 本期真推进了它"（判官裁 advance）→ 门 ok=True（放行）。
- [ ] "同一论点 + 换新闻壳子零新意重讲"（判官裁 re-derivation）→ 门 ok=False，`passed=False`，reason 定位。
- [ ] **自由表达不误伤**：一篇观点鲜明、但确实带来新推进的稿子 → 判官裁 advance、放行（温度盾核心 AC）。
- [ ] 账本为空 / embedding 不可用 / 判官派发失败 → 安全放行（fail-soft，不 halt、不误伤），不 crash。
- [ ] 门红触发 retry-then-regenerate（重稿走 Phase 4 续写/换题指令为增强；P5 先于 P4 落地时走通用重生成也可验：红→重生成→再判）。
- [ ] `python3 -m pytest lib/tests/test_scorecard.py lib/tests/test_thesis_gate.py -q` 全绿。

**Review checklist:**
- [ ] run-phase review step（auto-invokes implementation-reviewer）
- [ ] 温度不变量核对：persistence（坚持/结算/演进同一下注）绝不被误拦；仅 re-derivation 被拦
- [ ] `--enforce-scorecard` 生产开关下端到端验一次真实拦截
<!-- /section -->

---

<!-- section: phase-6 keywords: evening-skeleton, template-pool, rotation, structlint, structure -->
## Phase 6: 晚间随笔骨架模板池（3-4 种结构随机轮换）

**Goal:** 把晚间当前唯一固定骨架（冷开场→旧笔记自我怀疑→今日新闻→给现象命名→可证伪下注→散场留作业）拆成 3-4 种结构模板，runner 每期挑一种（避开最近 N 期用过的），打破"每期同一副骨架"的听感同质化。可证伪判断织入正文、无独立下注段等硬约束在每种模板里都成立。
**Depends on:** None（内容侧，独立于 1-5；置于末位作收尾）
**Scope:**
- `skills/podcast/references/evening.md`：把单一骨架扩成模板池（例：A 现有脊柱反转体 / B 双线对照体（两个域并置到 turn 处合流）/ C 时间轴倒叙体（从结局画面回溯）/ D 一问到底体（一个问题贯穿、层层逼近））。每模板保留晚间三段中心 + 冷开场 + 织入下注 + 上期成绩单结算段的**语义职责**，只换**结构编排**。
- `lib/runner.py` step-2 editorial loader：每期**随机**选一种模板、避开最近 N-1 期用过的（[DP-003]=B），把选中模板注入 davinci brief；选择记 run log。
- `state/skeleton-history.yaml`（新，或从 run-log 反推）：持久化"最近用过哪些模板"，供随机避重读取；原子写盘（克隆 covered-ground 纪律）。
- `lib/structlint.py`：`check_sections` 段数硬门须接受每种模板的合法结构（不是写死"三段"就通过——按选中模板的期望段形校验）。
- **把选中模板 identity 接到段数门（否则门校验不到模板）：** `check_sections` 现只吃 `(body, show)`（structlint.py:71），`scorecard._hard_gate_sections` 只透传 `(body, show)`（scorecard.py:57）。同批改：`build_scorecard` + `scorecard.py` + runner 的 13a 调用点，把选中模板 identity 一路穿到段数门；或让 `check_sections` 从 body 结构自推模板（二选一，/write-plan 定）。
- `lib/tests/`：模板选择避重（连续 N 期不重复）+ structlint 对每模板放行/对缺段拦截。

**用户可见的变化:**
- 听众可见：连追一周晚间不再是同一副起承转合；不同期以不同结构展开（有的双线对照、有的倒叙、有的一问到底）。
- 运营侧：run log 记录本期选用的骨架模板名。

**Architecture decisions:** 随机选已定（[DP-003]=B）；待定：模板数量 3 还是 4；随机种子建议用 (date,show) 派生，保证 resume 同一期选同一模板（避免重跑换模板）；是否给早间也做（本 Phase scope 只晚间；早间留后续）。

**Acceptance criteria:**
- [ ] 连续 N 期（N=模板数）选出的模板互不相同（避重生效）。
- [ ] 每种模板下 `structlint.check_sections` 对合法稿放行、对缺段稿拦截。
- [ ] 选中的模板名进 run log。
- [ ] 相关 `lib/tests/` 全绿。

**Review checklist:**
- [ ] run-phase review step（auto-invokes implementation-reviewer）
- [ ] design/feature 层核对：4 种模板确实结构不同（不是换皮同骨），且都不破坏"织入下注/无独立下注段"硬约束
<!-- /section -->

---

## Decisions

### [DP-001] 温度原则边界重划：增量硬门可否阻塞观点型内容（blocking）

**Context:** CLAUDE.md 现有强不变量——"让 davinci 对观点 hedge 的 memo 是 regression"，即门/记忆**不得**因内容"是个观点"而削弱它。本次要引入的增量硬门（Phase 5）会**因论点零增量重推而阻塞发布**，这直接改动该不变量。须用户显式批准这条边界如何重划（改基础层不变量按规则须 ask-first）。

**Options:**
- A: 硬门可阻塞 + persistence 豁免 — 重划边界为"保护 stance persistence（结算/推进/演进同一下注），拦 thesis re-derivation（零增量重推）"；零推进的论点重推会被 halt/重写。判据：本期是否对该论点做了 settle / 推进到期注 / 回答 open_question。 — 代价：温度不变量从"绝对不拦观点"变成"有条件拦"，实现里 persistence 判定若不准会误伤"坚持一贯观点"的好稿。
- B: 温度绝对，硬门降级为纯 advisory — thesis-advance 只进记分卡观察维、不进 `passed`、不 halt；只靠 Phase 4 pre-draft steering 防。 — 代价：回到"能看懂语义重复但没牙"的老问题，54→57 那类仍可能流出（steering 不是强制）。
**Recommendation:** A — 用户本轮明确要求"认知增量硬门进 hard_gates"；且 persistence 有确定性判据（stance card 的 `settles[]` / `status` / `open_question` delta，见 `lib/stance.py:424 due_bets`），可把误伤面压到"零 settle 且换词 standing claim"这一窄条，既满足硬门诉求又给温度盾留出 persistence 豁免。（blocking：须你确认这条不变量改法。）
**Chosen: A（用户批准 2026-07-02）** — 界线是「**内容是否重复**」，不是「有没有观点」。观点自由发挥、不设限；只拦"换个新闻壳子把同一套内容再讲一遍"。温度不变量据此重划：**保护自由表达 + 一贯立场，拦零新意重推**。关键约束：门只在"内容重复"上判，绝不因"这是个观点"而削弱它——误伤自由表达=回归。

### [DP-002] advance 信号的权威源（recommended）

**Context:** Phase 3/5 判"本期是否推进了该论点"需要一个信号源。stance card 已结构化记录 `settles[]`（结算到期注）、`bets[].status`、`open_questions`。

**Options:**
- A: 硬门用确定性 code-from-card，pre-draft steering 用 LLM 判 — 硬门（Phase 5）只认 stance card 的 settle/open-question delta（确定性、不受 LLM 心情影响、抗博弈）；pre-draft 的 steering（Phase 4）允许 LLM 判"算不算推进"以更灵活地 steer。 — 代价：两处判据不同源，须文档写清"门是 code、steer 是 LLM"，避免混淆。
- B: 两处都用 LLM 判 — thesis-distiller/judge 统一判 advance。 — 代价：把 LLM 变异引进硬门，硬门的"确定性"丧失，与 dedup/structlint 的 deterministic 硬门风格不一致。
**Recommendation:** A — 硬门须 deterministic（对齐 `lib/dedup.py` / `lib/structlint.py` 的既有硬门风格：code 判、fixture 冻结阈值）；`lib/stance.py` 已把 settle/status 结构化，code 判 advance 现成可用。steering 是软引导，容 LLM 灵活。
**Chosen: B（用户批准 2026-07-02）** — 用户明确否掉 A：「有没有进展」是判断题，规则算不出来。故 advance 由 **LLM 判官判**（看语义有没有真推进）；确定性信号（stance card 的 settle/open-question delta、embedding 同题预筛）只作判官的**输入线索**，不作最终裁决。硬门因此不是纯 code 门，而是**带牙的判官门**（见 Phase 5 改写）：判官"确信零新意重推"才拦；判官派发失败 → fail-soft 放行，不 halt（不拖垮每日 run）。代价：halt 路径引入 LLM 变异，用 fail-soft + retry 兜。

### [DP-003] 晚间骨架模板池的选择机制（recommended）

**Context:** Phase 6 每期要从 3-4 种模板挑一种，目标是听感不再单一。

**Options:**
- A: 确定性轮换（round-robin，按期序）— 实现最简、可测（连续 N 期必不重复）。 — 代价：4 期一个可预测循环，重度听众仍可能察觉周期。
- B: 种子随机 + 避开最近 N-1 期 — 用 (date,show) 派生种子随机选、排除最近用过的。 — 代价：需持久化"最近用过的模板"到 state，实现与测试略重。
**Recommendation:** A — runner 是 Python（无 workflow 脚本的 `random` 限制），但确定性轮换可测性最好、无需新增 state 持久化；"最近用过的模板"可直接从已发布 `.md`/run log 反推做避重，不必新建存储。周期性可接受（先上 A，若听感仍单一再升 B）。
**Chosen: B（用户批准 2026-07-02）** — 随机挑、避开最近 N-1 期用过的模板。需把"最近用过的模板"持久化到 state（`state/skeleton-history.yaml`，或从 run-log 反推——Phase 6 定）。

---

## 附：为什么这样分期（决策留痕）

- **Phase 1、2 先行**：都是既有系统的 bug/血管修复，独立可验，且 Phase 2 是"每期一条论点记录"的前置（账本依赖蒸馏每期都跑）。
- **Phase 3→4→5 是主干**：先建账本记忆（3），再 pre-draft 防（4），再 post-draft 硬门兜（5）——防/兜对称，镜像 covered-ground 的 avoid_memo（防）+ cross_dup 硬门（兜）。
- **Phase 6 独立收尾**：骨架模板池与论点记忆正交，作内容侧收尾。
- **stance card 是论点账本的底料**（非新建重复设施）：`named_concept`/`bets`/`settles`/`open_questions` 已在卡里，Phase 3 的账本是对卡的跨期嵌入投影 + advance 标注，thesis-distiller 只补"跨包装持久论点"这一层（named_concept 是当期新包装、无法跨包装比对，故须单独抽——已由 54≈57 零 named_concept 重叠验证）。
