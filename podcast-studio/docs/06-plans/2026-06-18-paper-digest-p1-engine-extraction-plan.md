---
type: plan
status: active
contract_version: 2
tags: [line-engine, line-registry, behavior-preserving-refactor, isolation, regression]
refs: [docs/06-plans/2026-06-18-paper-digest-show-dev-guide.md, docs/06-plans/2026-06-18-paper-digest-show-design.md, docs/11-crystals/2026-06-18-paper-digest-show-crystal.md]
---

# Phase 1: 引擎抽线无关 + 回归地基 Implementation Plan

**Goal:** 把 `lib/runner.py` 的执行循环改造成"与线无关的引擎"——线特定的绑定（拓扑/门映射/自定义执行器/editorial/长度门）改为经"线注册表"按线注入，早晚间经"观点线 bundle"驱动，**行为与重构前逐字不变**（四门证明），并就位"不打架"结构测试 harness。**不加任何论文功能。**

**Architecture:** 采用**就地参数化 + 线注册表**（不物理搬迁热路径）。新增 `lib/lines.py` 定义 `LineBundle`（D-004 钉的形状：topology / gate_map / executor_map / editorial_loader / agent_dir，+ floor_fn）与 `get_line(show)`；`OPINION_LINE` bundle **包裹现有函数/字典/分支对象本身**（同一对象，不复制逻辑），所以早晚间逐字不变。runner 的 5 个绑定点改为经 `get_line(show)` 取，而非硬 import/硬编码。物理搬迁 1000 行循环对 P1 零行为收益、却乘上回归风险，故选就地（dev-guide P1 开放项"新模块 vs 就地"的答案：就地参数化，引擎=被参数化的循环；未来第二条线坐实接缝后再考虑提模块）。

**Tech Stack:** Python 3, pytest, bats.

**Design doc:** docs/06-plans/2026-06-18-paper-digest-show-design.md
**Crystal file:** docs/11-crystals/2026-06-18-paper-digest-show-crystal.md
**Bug diagnosis:** not applicable
**Threat model:** not applicable

**Verify-plan revision (cycle 1, 2026-06-18):** fixed 3 must-revise findings — (1) DP-A2 gates ①/④ now wired into executable verifies + frozen baseline captured in Task 0 before any refactor; corrected stale "490"→**513 (329 lib + 184 prep) + 8 bats**; (2) Task 4 executor_map values now encapsulate the FULL dispatch block (ctx side-effects + return-shaping live at the call site, NOT in helpers — runner.py:1254/1262-1263/1270/1276/1282), + added chosen-body verify; (3) Task 2 GOLDEN is now a frozen committed fixture captured in Task 0, not a live `load_pipeline()` call (was tautological).

**Verify-plan revision (cycle 2, 2026-06-18):** prior 3 confirmed resolved; fixed 1 new defect the cycle-1 revision introduced — Task 4 must NOT do a generic `executor_map.get(name)` early-return at `_execute_step` (1124-1132): that returns BEFORE the gate check (1164), silently disabling halts on gated code stations (stance-card-exists / stance-card-gate / continuity-read / resonance / topic-log / stance-write). Fix: early-return interception stays for scorecard/bible-distill ONLY; all other code stations route via executor_map INSIDE `_run_code_step` so results reach the 1164 gate. Reclassified stance-card-exists/stance-card-gate as no-op-body-but-GATED (not plain no-op); added a gate-tripwire halt regression assertion.

**Pre-flight risks:**
- `lib/runner.py` 是被多轮 e2e 打磨过的热路径（2147 行）；任何改动必须保 morning/evening 逐字不变——靠 byte-identical pin（对**冻结基线**） + 现有 513 pytest+8 bats 一字不改全绿 + 真实 no-TTS e2e A/B diff 三重兜底。
- **ctx 副作用在派发块、不在 helper**（runner.py:1236-1283 实测）：continuity-read 设 `ctx["continuity"]` 且返回 `scratch/"continuity.json"` Path（helper 只返回 dict）；select-draft 设 `ctx["chosen_id"]/["chosen_path"]`；resonance/topic-log/stance-write 同理。executor_map 的值必须包**整段派发块**，只包 helper 会静默丢副作用。
- `_call_gate`（327-362）按 gate-fn-name 硬编码调用形状；论文线未来要加新 gate，本 P1 **不改** `_call_gate`（仅经 bundle.gate_map 注入"哪些 gate 可用"）；调用形状按线化留 P2/P3。verifier 已确认此 P1 不动成立。
- 双重 show 校验：`lib/pipeline.py:646` 与 `lib/runner.py:1915-1918` + CLI choices `2084-2089`，三处都要经注册表 gate（P1 只注册观点线，行为不变）。
- 循环 import：lines.py 引用 runner 的 executor 函数，runner 又 import get_line —— 用**惰性绑定**（get_line 内或首次调用时 import runner）破环。verifier 已确认此方案可行。

---

## Impact Map

**User path:** 无（纯基建；早晚间听众产物不变是验收点）。
**Data path:** `get_line(show)` → LineBundle → 引擎循环读 topology/gate_map/executor_map/editorial/floor。
**Shared surfaces:** `lib/runner.py`（引擎循环 + 5 绑定点）、`lib/pipeline.py`（load_pipeline show 校验）、新增 `lib/lines.py`。被引用的 `lib/episode.py`/`magnitude`/`stance`/`throughline`/`coveredground`/`bible` 函数**不改签名/逻辑**。
**Existing consumers:** `lib/runner.py` `_cli`/`run_pipeline`；`lib/tests/` 全部现有测试；`skills/podcast/SKILL.md`（仅文档）。
**Must remain unchanged:** morning/evening 的拓扑、门、执行器、editorial 注入、长度门、台账卡写入、covered-ground 注入、产物结构、所有现有测试断言。
**Regression checks:** ① 现有 **513 pytest（329 lib + 184 prep）+ 8 bats** 一字不改全绿（test-changes 跑全套；count floor ≥513，原 513 断言零改动）；② `get_line(show).topology(show)` 对**冻结基线 fixture** byte-identical（Task 2 pin）；③ 06-14 回归样本确定性站点行为不变（Task 6）；④ 真实 no-TTS e2e 早/晚各一期，对 Task 0 冻结的**重构前基线**做结构 diff（Task 6 Real-path）。

---

<!-- section: task-0 keywords: baseline, golden, freeze, pre-refactor -->
### Task 0: 冻结重构前基线（必须最先跑，先于任何改动）

**Maps to Impact Map:** Regression checks, Must remain unchanged

**Files:**
- Create: `lib/tests/fixtures/topology_golden.py`（或 `.json`）—— 冻结的拓扑快照
- Create: `.claude/self-pacing/p1-baseline/`（重构前 e2e 产物基线，非提交进库，run log 引用）

**Expected outcome:** 在**改任何代码之前**，把当前（重构前）的真相冻下来：① `load_pipeline("morning")` 与 `load_pipeline("evening")` 的输出序列化成提交进库的 golden fixture（Task 2 的 pin 对它比，而不是对 live `load_pipeline()`——否则同义反复）；② 用 e2e sandbox 跑一次重构前 no-TTS e2e（早/晚各一期），把产物结构（段数、有无草稿头、字数、台账卡、covered-ground 注入）记录成基线，供 Task 6 的 A/B diff。

**Non-goals:** 不改任何源码（这是只读快照步骤）。

**Touched surface:** 新增 fixture + 基线记录。

**Regression shield:** golden 必须从**当前未改**的 `load_pipeline` 取，且固化成静态文件——一旦 Task 1 起改动开始，这份 golden 不再随代码变。

**Task Contract:**
- Expected behavior: 有一份提交进库的拓扑 golden + 一份重构前 e2e 产物基线记录。
- Automated verify: `python3 -c "from lib.tests.fixtures.topology_golden import MORNING, EVENING; from lib.pipeline import load_pipeline; assert load_pipeline('morning')==MORNING and load_pipeline('evening')==EVENING; print('golden frozen OK')"` —— 在 Task 0 当下（代码未改）必然 PASS（这就是固化动作）。
- Real path verify: 重构前 no-TTS e2e 早/晚各一期跑通并记录结构（`PODCAST_STUDIO_CONFIG=<e2e-sandbox.yaml> python3 -m lib.runner --show morning --no-tts` / `--show evening --no-tts`）。若该 e2e 因 MiniMax 超时拿不到干净基线 → 记录此情况，gate ④ 降级为"尽力而为"，零变化证明主要由确定性门 ①②③ 承担（在 run log 显式标注降级，不静默）。
- Manual/device verify: none。

**Steps:**
1. 写脚本/REPL 取 `load_pipeline("morning")`、`load_pipeline("evening")`，把结果（list[dict]）序列化成 `lib/tests/fixtures/topology_golden.py` 的 `MORNING`/`EVENING` 字面量（或 JSON）。
2. commit 这份 golden（属基线，要进库）。
3. 跑重构前 no-TTS e2e 早/晚各一期，把产物结构摘要写进 `.claude/self-pacing/p1-baseline/` + run log。

**Verify:**
Run: `python3 -c "from lib.tests.fixtures import topology_golden; print(len(topology_golden.MORNING), len(topology_golden.EVENING))"`
Expected: 打印两条非空拓扑长度（与当前 STEPS 站数一致）。
<!-- /section -->

<!-- section: task-1 keywords: lines, line-bundle, registry, get-line -->
### Task 1-tests: LineBundle + get_line 注册表（测试先行）

**Depends on:** Task 0

**Maps to Impact Map:** Shared surfaces, Data path

**Files:**
- Test: `lib/tests/test_lines.py`

**Expected outcome:** 钉死线注册表契约：`get_line("morning")` 与 `get_line("evening")` 同属观点线 bundle；`get_line("papers")`/未知 show 抛异常命名 show；bundle 暴露 `topology/gate_map/executor_map/editorial_loader/agent_dir/floor_fn`；且 `bundle.topology("morning")` 对**冻结 golden**（Task 0）相等。

**Non-goals:** 不测 runner 接入。

**Touched surface:** `lib/tests/test_lines.py`（新）。

**Regression shield:** 仅新增测试文件。

**Task Contract:**
- Expected behavior: 在 `lib/lines.py` 不存在时 FAIL（ImportError），实现后 PASS。
- Automated verify: `python3 -m pytest lib/tests/test_lines.py -q` —— 实现前 `ModuleNotFoundError: lib.lines`，实现后 PASS。
- Real path verify: N/A。
- Manual/device verify: none。

**Steps:**
1. `test_get_line_morning_evening_same_opinion`：二者 `.line_id == "opinion"`。
2. `test_get_line_unknown_raises`：`get_line("papers")`/`get_line("xxx")` 抛异常含 show 名。
3. `test_bundle_topology_matches_frozen_golden`：`get_line("morning").topology("morning") == topology_golden.MORNING`（早+晚）。
4. `test_bundle_exposes_contract`：六个成员都在。

**Verify:**
Run: `python3 -m pytest lib/tests/test_lines.py -q`
Expected: 实现前 import 失败；Task 1-impl 后全绿。
<!-- /section -->

<!-- section: task-1-impl keywords: lines, line-bundle, opinion-line, wraps-existing -->
### Task 1-impl: 实现 lib/lines.py（观点线 bundle 包裹现有对象）

**Depends on:** Task 1-tests

**Maps to Impact Map:** Shared surfaces, Data path

**Files:**
- Create: `lib/lines.py`

**Expected outcome:** `LineBundle` + `get_line(show)` 落地；`OPINION_LINE` 引用**现有**对象：topology=`load_pipeline`、gate_map=`runner._default_gate_map`、executor_map=观点线现有 name→派发逻辑、editorial_loader=读 `references/{show}.md`、agent_dir=`"agents"`、floor_fn=`episode.floor_chars_for_show`。morning/evening 都映射 `OPINION_LINE`。

**Non-goals:** 不注册论文线；不改被引用函数。

**Touched surface:** `lib/lines.py`（新）。

**Regression shield:** OPINION_LINE 成员是**对现有对象的引用**，不重写逻辑。executor_map 的 name 全集 = runner 现有派发链（Task 4 详列），含 no-op 站点。**executor_map 的值在 Task 4 才真正接入 runner**；Task 1-impl 可先用"引用现有 helper 的占位 callable"让 test_lines 绿，Task 4 再把值替换为"封装整段派发块"的 callable（见 Task 4）。

**Task Contract:**
- Expected behavior: Task 1-tests 全绿。
- Automated verify: `python3 -m pytest lib/tests/test_lines.py -q` PASS。
- Real path verify: N/A。
- Manual/device verify: none。

**Steps:**
1. `@dataclass(frozen=True) class LineBundle`：`line_id, topology, gate_map, executor_map, editorial_loader, agent_dir, floor_fn`。
2. 惰性破环：`get_line` 内或模块级延迟 `from lib import runner` / `from lib.pipeline import load_pipeline` / `from lib.episode import floor_chars_for_show`。
3. `_LINE_REGISTRY = {"morning": OPINION_LINE, "evening": OPINION_LINE}`；`get_line(show)` 命中返回，否则 `raise ValueError(f"unknown line for show {show!r}")`。
4. Task 1-tests 转绿。

**Verify:**
Run: `python3 -m pytest lib/tests/test_lines.py -q`
Expected: PASS。
<!-- /section -->

<!-- section: task-2 keywords: load-pipeline, show-validation, frozen-golden, byte-identical -->
### Task 2: 拓扑与 show 校验经注册表（对冻结 golden 的 byte-identical pin）

**Depends on:** Task 1-impl

**Maps to Impact Map:** Shared surfaces, Must remain unchanged, Regression checks

**Files:**
- Modify: `lib/runner.py:1915-1918`（show 校验经 `get_line`）、`lib/runner.py:1992-1994`（topology 取自 bundle）
- Modify: `lib/runner.py:2084-2089`（CLI `--show` choices 注释：papers 留 P4）
- Test: `lib/tests/test_runner_line_topology.py`

**Expected outcome:** runner 经 `get_line(show).topology(show)` 取拓扑，show 合法性经注册表判定；morning/evening 拓扑对 **Task 0 冻结 golden** 逐字相等。

**Non-goals:** 不放开 CLI 接受 papers；不改任何站点。

**Touched surface:** runner 拓扑加载 + show 校验；新增 pin 测试。

**Regression shield:** **pin 测试的 GOLDEN 必须是 Task 0 的冻结 fixture（`lib.tests.fixtures.topology_golden`），严禁 `GOLDEN = load_pipeline(...)` 现场取（同义反复）。** 断言 `get_line(show).topology(show) == topology_golden.MORNING/EVENING`。

**Task Contract:**
- Expected behavior: 早晚间拓扑对冻结基线逐字一致；未知 show 仍 fail-closed。
- Automated verify: `python3 -m pytest lib/tests/test_runner_line_topology.py lib/tests/test_pipeline.py -q` PASS。
- Real path verify: 见 Task 6。
- Manual/device verify: none。

**Steps:**
1. 写 `test_runner_line_topology.py`：`from lib.tests.fixtures import topology_golden`；断言 `get_line("morning").topology("morning") == topology_golden.MORNING`（早+晚）。**不得**在测试里调 live `load_pipeline` 当期望值。
2. `runner.py:1992-1994`：`steps = get_line(show).topology(show)`。
3. `runner.py:1915-1918`：经 `get_line(show)` 判合法（保留 RunnerError 文案，兼容现有测试断言）。
4. CLI choices 保持 `("morning","evening")` + 注释。
5. 跑 pin + 现有 test_pipeline/test_runner 全绿。

**Verify:**
Run: `python3 -m pytest lib/tests/test_runner_line_topology.py lib/tests/test_pipeline.py lib/tests/test_runner.py -q`
Expected: PASS，0 改动现有断言。
<!-- /section -->

<!-- section: task-3 keywords: gate-map, bundle-injection, opinion-gates -->
### Task 3: 门映射经 bundle 注入

**Depends on:** Task 2

**Maps to Impact Map:** Shared surfaces, Must remain unchanged

**Files:**
- Modify: `lib/runner.py:~1928`（gates 默认值解析）；`_default_gate_map`（285-298）保留为观点线 gate_map 实现
- Test: `lib/tests/test_runner.py`（复用现有门测试）

**Expected outcome:** `run_pipeline` 当 `gates is None` 时取 `get_line(show).gate_map()`（观点线 = 现有 `_default_gate_map()`，同 8 项 dict、同函数对象）。现有传 `gates=` 的测试路径不变。

**Non-goals:** 不改 `_call_gate` 任何分支；不增删门。

**Touched surface:** gates 默认解析点。

**Regression shield:** 观点线 gate_map 返回与 `_default_gate_map()` 相同 8 键 + 相同函数对象。

**Task Contract:**
- Expected behavior: 早晚间门行为与重构前一致。
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q` PASS。
- Real path verify: 见 Task 6。
- Manual/device verify: none。

**Steps:**
1. gates 默认解析（~1928）：`if gates is None: gates = get_line(show).gate_map()`。
2. `OPINION_LINE.gate_map` = `_default_gate_map`。
3. 跑现有 runner 测试全绿。

**Verify:**
Run: `python3 -m pytest lib/tests/test_runner.py -q`
Expected: PASS。
<!-- /section -->

<!-- section: task-4 keywords: executor-map, full-dispatch-block, ctx-side-effects, chosen-body -->
### Task 4: 自定义执行器经 executor_map 派发（值=整段派发块）

**Depends on:** Task 3

**Maps to Impact Map:** Shared surfaces, Must remain unchanged

**Files:**
- Modify: `lib/runner.py:1124-1132`（layer-1：scorecard/bible-distill）、`lib/runner.py:1224-1328`（layer-2：code 站 name 链）
- Test: `lib/tests/test_runner.py` + 新增 `lib/tests/test_executor_map.py`

**Expected outcome:** code 站的 `if name==...` 硬链改为查 `get_line(show).executor_map.get(name)`，**且只在 `_run_code_step` 内做**（命中调对应 callable、未命中走通用分支）——这样结果仍回流到 `_execute_step` 的门检查（runner.py:1164）。

**关键（verifier 修订 cycle-2，阻塞）：不得在 `_execute_step`（1124-1132）层做通用 `executor_map.get(name)` 早返回。** 该拦截点 `return` 在门检查（1164）之前；若把带门的 code 站（stance-card-exists/continuity-read/resonance/topic-log/stance-write/stance-card-gate，它们的 step 都有 `gate` 字段）在此早返回，门**永不执行** = 静默关掉 halt = 行为变 = 违反 P1 红线。修法：`_execute_step` 的早返回拦截**仅保留 scorecard / bible-distill 两站**（它们本就在现状里绕过 1164 门——documentary gate，保此行为）；**所有其它 code 站经 executor_map 在 `_run_code_step` 内派发**，结果回流到 1164 门。

**关键（verifier 修订 cycle-1）：executor_map 的值必须封装现有派发块的*完整行为*，不是只调 helper。** 实测 runner.py:1236-1283，副作用与返回整形在派发块、不在 helper：
- `continuity-read`：try/except→halt envelope + `ctx["continuity"]=...` + 返回 `scratch/"continuity.json"`（Path）。helper `_continuity_read(ctx)` 只返回 dict。
- `select-draft`：`ctx["chosen_id"]/["chosen_path"]=...` + 返回 None。
- `resonance`：`ctx["resonance"]=...` + 返回 None。
- `topic-log`：`ctx["topic_log_path"]=...`（条件）+ 返回 None。
- `stance-write`：`ctx["stance_card_path"]=...`（条件）+ 返回 path。
- **真 no-op 站（config/editorial/scratch/cleanup，step `gate=None`）**：body 返回 None，无门。
- **no-op body 但**带门**站（stance-card-exists `gate=[check_stance_card_absent]`、stance-card-gate `gate=[check_stance_card]`）**：body 返回 None，但门在 1164 对 None 结果照跑（这两个门用 ctx 不用 path）。**必须经 `_run_code_step` 派发、不得在 `_execute_step` 早返回**，否则门被跳。（cycle-1 误把这两站列为普通 no-op，已纠正。）
- `assemble-briefs`/`publish-paths`：直接返回 helper 结果。
- `coveredground-update`（1291-1328 inline 块）：纯搬移成 helper（不改逻辑，移动代码块豁免）。
- layer-1 `scorecard`/`bible-distill`：签名 `(step, ctx, dispatch_fn)`，**保留 `_execute_step` 早返回拦截**（现状即绕过 1164 门，documentary gate）。

executor_map 的值统一为 `Callable[[step, ctx, dispatch_fn], Optional[dict|Path]]` 的包装；每个包装**逐字复制现有派发块**（含 try/except、ctx 写入、返回值），只是从 if-链搬进 dict 值。

**Non-goals:** 不改任何 helper 内部逻辑；不改站点集合。

**Touched surface:** 两层 name 派发点 + executor_map 值实现。

**Regression shield:** executor_map 的 name→行为必须与现有链逐项一致（含 ctx 副作用、返回形状、no-op 语义）。漏一个 ctx 写入 = 早晚间静默坏。

**Task Contract:**
- Expected behavior: 每站派发结果（含 ctx 副作用与返回值）与重构前逐字一致；早晚间产物不变。
- Automated verify: `python3 -m pytest lib/tests/test_runner.py lib/tests/test_executor_map.py -q` PASS。新增测试**显式断言**：(a) ctx 副作用——跑过 select-draft 后 `ctx["chosen_id"]/["chosen_path"]` 已设；跑过 continuity-read 后 `ctx["continuity"]` 已设且返回 Path；(b) publish 用的是**选中候选**的 body（断言 publish 读的是 chosen_path 对应草稿，而非任意草稿）；(c) **门-tripwire 仍生效**——stance-card-exists 在卡已存在时 `_execute_step` 返回 halt、stance-card-gate 在卡缺失时返回 halt（证明 executor_map 改造没把门早返回跳过）。
- Real path verify: 见 Task 6（端到端覆盖所有站点 ctx 流）。
- Manual/device verify: none。

**Steps:**
1. 把 layer-2 每个 `if name==...` 块的**整段**（含 try/except、ctx 写入、return）搬进 `lib/lines.py` 的 OPINION_LINE.executor_map 对应值（统一签名包装；内部逐字复制原块）。
2. coveredground-update inline 块抽成 `_coveredground_update_step(ctx)` helper（纯搬移），放进 executor_map。
3. **`_execute_step`（1124-1132）**：早返回拦截**只保留** `scorecard`/`bible-distill` 两站（保现状绕门行为）。不在此处加通用 executor_map 早返回。
4. **`_run_code_step`（1224-1328）**：把现有 `if name==...` 链换成 `fn = get_line(show).executor_map.get(name); if fn is not None: return fn(step, ctx, dispatch_fn)`。返回值（None / Path / dict）回流到 `_execute_step` 的 1164 门检查——带门 code 站的门由此保住。
5. 写 `test_executor_map.py`：断言 ctx 副作用 + chosen-body 流（如上 Automated verify）+ **门-tripwire 站仍 halt**：stance-card-exists 在卡已存在时返回 halt；stance-card-gate 在卡缺失时返回 halt（证明门没被早返回跳过）。
6. 跑现有 runner 测试 + 新测试全绿。

**Verify:**
Run: `python3 -m pytest lib/tests/test_runner.py lib/tests/test_executor_map.py -q`
Expected: PASS。
<!-- /section -->

<!-- section: task-5 keywords: editorial-loader, floor, bundle, fallback -->
### Task 5: editorial 与长度门经 bundle

**Depends on:** Task 4

**Maps to Impact Map:** Shared surfaces, Must remain unchanged

**Files:**
- Modify: `lib/runner.py:1941-1947`（editorial 读取）、`lib/runner.py:313-324`（`_resolve_gate_args` floor 解析）
- Test: `lib/tests/test_runner.py`

**Expected outcome:** editorial 经 `get_line(show).editorial_loader(show, plugin_root)`（观点线=读 `references/{show}.md`，OSError→""）；floor 经 `get_line(show).floor_fn(show)`（观点线=`floor_chars_for_show`，6500）。逐字不变。

**Non-goals:** 不改 editorial 文件；不改 floor 值。

**Touched surface:** editorial 加载点 + floor 解析点。

**Regression shield:** editorial 路径 + OSError→"" 回退不变；floor 值不变。

**Task Contract:**
- Expected behavior: 早晚间 editorial 注入与长度门与重构前一致。
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q` PASS。
- Real path verify: 见 Task 6。
- Manual/device verify: none。

**Steps:**
1. `runner.py:1941-1947`：`editorial_text = get_line(show).editorial_loader(show, plugin_root)`。
2. `_resolve_gate_args`（321）：`out[k] = get_line(show).floor_fn(show)`。
3. 跑现有测试全绿。

**Verify:**
Run: `python3 -m pytest lib/tests/test_runner.py -q`
Expected: PASS。
<!-- /section -->

<!-- section: task-6 keywords: isolation-harness, regression-suite, e2e-diff, zero-change -->
### Task 6: 不打架 harness + 全套回归 + 06-14 断言 + 真实 e2e A/B diff

**Depends on:** Task 5

**Maps to Impact Map:** Regression checks, Must remain unchanged

**Files:**
- Create: `lib/tests/test_line_isolation.py`
- Create/Modify: `lib/tests/test_regression_2026_06_14.py`

**Expected outcome:** ① 不打架 harness：断言观点线模块（runner/pipeline/episode/stance/coveredground/magnitude/bible/lines）源码不 import 论文线模块（`lib.pipeline_papers`/`lib.paperline.*`）；论文线侧反向断言写好但 `@pytest.mark.skip(reason="paper line lands in P2")`。② 06-14 回归样本喂确定性站点、行为不变。③ **DP-A2 四门全验**：① 全套 513 pytest+8 bats 一字不改全绿（由 self-pacing 的 test-changes 步骤跑，count floor ≥513）；② Task 2 byte-identical pin（对冻结 golden）；③ 本任务 06-14 断言；④ 真实 no-TTS e2e 早/晚各一期，对 **Task 0 冻结的重构前基线**做结构 diff（段数/草稿头/字数/台账卡/covered-ground 注入逐项对比，不是"看着合理"）。

**Non-goals:** 不实现论文线（仅占位 skip）。

**Touched surface:** 新增隔离测试 + 回归断言。

**Regression shield:** 隔离测试论文线侧 skip 带 reason，P2 去 skip。e2e diff 必须对 Task 0 基线、不是事后捏的基线。

**Task Contract:**
- Expected behavior: 隔离 harness 就位（观点线侧生效、论文线侧 skip）；06-14 确定性断言绿；DP-A2 四门全过。
- Automated verify: `python3 -m pytest lib/tests/test_line_isolation.py lib/tests/test_regression_2026_06_14.py -q` PASS（含 skip）。
- Real path verify: **DP-A2 门①** —— 全套套件：`python3 -m pytest lib/tests/ -q`（≥329 通过）、`python3 -m pytest skills/podcast-studio-prep/scripts/ -q`（184 通过）、`bats lib/tests/*.bats`（8 通过）；原 513 断言零改动。**DP-A2 门④** —— 真实 no-TTS e2e 早/晚各一期（`PODCAST_STUDIO_CONFIG=<e2e-sandbox.yaml> python3 -m lib.runner --show morning --no-tts` / `--show evening --no-tts`），与 Task 0 基线结构 diff，逐项一致；若 Task 0 e2e 基线降级，则门④随之降级并在 run log 显式标注。
- Manual/device verify: none。

**Steps:**
1. `test_line_isolation.py`：用 `ast`/源码扫描断言观点线模块不含 `import lib.pipeline_papers`/`from lib.paperline`；反向断言加 skip+reason。
2. `test_regression_2026_06_14.py`：复用/补 06-14 fixture，喂确定性站点，断言对 baseline 一致。
3. 跑隔离+回归测试绿。
4. （self-pacing test+review 步骤承接）跑全套 513+8 + 真实 e2e A/B diff。

**Verify:**
Run: `python3 -m pytest lib/tests/test_line_isolation.py lib/tests/test_regression_2026_06_14.py -q`
Expected: PASS（含 skip）；全套 + e2e A/B 由 test-changes/Real-path 承接。
<!-- /section -->

---

## Decisions

None. — 引擎抽取架构决策（bundle 形状）已由 crystal D-004 锁定；"就地参数化 vs 提新模块"按 Decision Necessity Gate 不构成 DP（已内联说明选就地 + 理由）。`_call_gate` 调用形状按线化是 P2/P3 的事，本 P1 不动（verifier 已确认成立）。

---
## Verification
- **Verdict:** Approved
- **Date:** 2026-06-18
- **Cycles:** 2 (cycle-0: 3 must-revise → fixed; cycle-1: 1 new gate-bypass defect → fixed; cycle-2: approved)
