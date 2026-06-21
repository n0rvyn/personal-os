---
type: plan
status: active
contract_version: 2
tags: [runner, factcheck, self-heal, pipeline]
refs: []
---

# Runner 去假门自愈 Implementation Plan

**Goal:** 让 `lib/runner.py` 的去假门(12a)在客观断言无法溯源时能自愈——重派定稿(快刀青衣)时把被旗标的断言 + "软化或补来源"指令带过去,使 12a→12 的重试不再用同样输入空转、不再 cap 耗尽 halt。

**Architecture:** 三点线缝,全在 `lib/runner.py`,不改任何 gate/select_draft 数学。(1) `_execute_step` 的 gate-halt 返回里带上 `flagged`(现已丢弃)。(2) 重试循环在 `name=="factcheck"` 时把 `result["flagged"]` 写入 `ctx["factcheck_flagged"]`、重派 parent(finalize)后立即 `pop` 清除(防泄漏到后续 finalize 调用、防污染 16a stance 重试)。(3) `_build_step_prompt` 在现有 finalize 分支(已注入编辑规范/6500字 floor 的那段 `parts` 末尾)追加一段软化指令——仅当 `ctx.get("factcheck_flagged")` 非空;为空时输出逐字节不变(happy path 零回归)。

**Tech Stack:** Python 3;pytest(`lib/tests/test_runner.py` 的 `_FakeDispatch` harness);`run_pipeline(show, *, date, no_tts, dispatch, gates, config, ...)` 可测入口(runner.py:2288)。

**Design doc:** none
**Crystal file:** none
**Bug diagnosis:** not applicable
**Threat model:** not applicable(无安全关键词;factcheck 是内容核验非安全边界)

**Pre-flight risks:**
- `_execute_step` 返回 dict 形状被重试循环 + `run_pipeline` 消费——给 halt 返回**增加** `flagged` 键(不改既有 status/failed_step/reason)。
- 重试循环被 12a(factcheck→finalize)与 16a(stance-card-gate→stance-write)共用 `_RETRY_PARENT`——`factcheck_flagged` 注入与清除必须**只在 `name=="factcheck"` 分支**,不可触碰 16a 路径。
- `ctx["factcheck_flagged"]` 是跨站可变状态——必须在 parent 重派被消费后**立即 pop**,否则泄漏到下一轮 / 后续 finalize。
- `_build_step_prompt` 被每个 agent 站调用——finalize 追加分支在 `ctx.get("factcheck_flagged")` 为空时必须不改变输出(保留 runner.py:2281-2284 的编辑规范/floor 注入)。
- **Stale-baseline 教训(本次已纠正):** resume 已存在(`--resume` / `_resolve_scratch_dir(resume=True)` / 测试 test_resume_skips_steps_with_existing_artifacts),本计划**不碰 resume**。

---

## Impact Map

**User path:** `/podcast morning|evening`。现状:12a 客观断言无源 → cap 耗尽 → halt(用户拿不到节目)。改后:12a 失败时重派的快刀青衣知道改哪几条、怎么改 → 自愈 → 节目继续。
**Data path:** check_factcheck `{ok,reason,flagged}`(lib/factcheck.py)→ `_run_gate` 透传 → `_execute_step` halt 返回(**加 flagged**)→ 重试循环 → `ctx["factcheck_flagged"]` → `_build_step_prompt` finalize 追加分支 → 重派 kuaidao 的 prompt。
**Shared surfaces:** `lib/runner.py`(`_execute_step` ~1520-1524;重试循环 ~2417-2437;`_build_step_prompt` finalize 段 ~2281-2284);`lib/tests/test_runner.py`。
**Existing consumers:** `_execute_step`(主循环 + 重试循环);`_build_step_prompt`(所有 agent 站);`run_pipeline`(_cli + 测试)。
**Must remain unchanged:** 16a stance 重试;所有非 factcheck 站 prompt 与 ctx 无 flagged 时的 finalize prompt(含编辑规范/floor);select_draft / gate 数学;resume;论文线。
**Regression checks:** `pytest lib/tests/test_runner.py` 全绿(含 clean-run 顺序测试、resume 测试、16a 测试);新增去假自愈测试;ctx 无 flagged 时 finalize prompt 字节不变。

---

<!-- section: task-1-tests keywords: runner, factcheck, selfheal, test_runner -->
### Task 1-tests: 去假自愈——失败测试先行

**Maps to Impact Map:** Data path / Shared surfaces / Regression checks

**Files:**
- Modify: `lib/tests/test_runner.py`(新增一个测试函数)

**Expected behavior:** 去假门判出一条无法溯源的客观断言时,流水线不再直接停摆——它把那条断言带回定稿步重做一次(软化),第二次过门,节目继续产出。用户看到"卡点能自己绕过去"。

**Touched surface:** `lib/tests/test_runner.py`
**Regression shield:** 不改 `_FakeDispatch` 既有用例;新测试独立。

**Task Contract:**
- Expected behavior: 见上(自愈:一次 untraceable → 软化重派 → 过门 → 完成)。
- Automated verify: `cd <SRC> && python -m pytest lib/tests/test_runner.py -k factcheck_selfheal -x` —— **本任务结束必须 FAIL**,失败信号为 `run_pipeline` 返回 `status=="halted" failed_step=="factcheck"`(重派 prompt 不含 flagged → fake 仍产 untraceable → cap 耗尽)。
- Real path verify: 不适用(单元层;真路径在 1-impl 后真跑覆盖)。
- Manual/device verify: none

**Steps:**
1. 读 `lib/tests/test_runner.py` 的 `_FakeDispatch`(:90)与一个已有重试/12a 测试,复用构造方式。
2. 写 `test_factcheck_selfheal_resoftens_flagged_claim(monkeypatch, tmp_path)`:
   - 让 `finalize` 站产物**取决于传入 prompt**:prompt 含旗标串(flagged 断言文本)→ 写"软化后" finalize-result.json(客观断言可被 material-summary 溯源);否则 → 写"原始"(含一条 cited 不到的断言)。
   - material-summary fixture 的 `## 当日新闻背景` 只覆盖软化版能溯源的事实。
   - factcheck 站用真实 `check_factcheck` gate(已注册)或按 body 重算 traceable 的 fake:原始 body→flagged 非空→ok=False;软化 body→flagged 空→ok=True。
   - 调 `run_pipeline("morning", date=..., no_tts=True, dispatch=fake, ...)`。
   - 断言:`result["status"]=="ok"`;fake 记录里 finalize 被调 ≥2 次,且第 2 次 prompt 含 flagged 断言文本。
3. 跑 `-k factcheck_selfheal -x`,确认 FAIL 且原因是 `halted/factcheck`(证明真在测自愈,非误绿)。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python -m pytest lib/tests/test_runner.py -k factcheck_selfheal -x`
Expected: 1 failed —— run_pipeline 返回 halted/factcheck。
<!-- /section -->

<!-- section: task-1-impl keywords: runner, factcheck, flagged, build_step_prompt -->
### Task 1-impl: 去假自愈——把 flagged 串到 finalize 重派

**Depends on:** Task 1-tests

**Maps to Impact Map:** Data path / Shared surfaces / Must remain unchanged

**Files:**
- Modify: `lib/runner.py`(三处:`_execute_step` halt 返回 ~1520-1524;重试循环 ~2417-2437;`_build_step_prompt` finalize 段 ~2281-2284)

**Expected behavior:** 同 1-tests。重派的快刀青衣收到被旗标断言 + "逐条软化为定性,或在 material-summary 补 `- **<lead>**: …(source:…)` 来源条目再引用"指令,知道改哪几条、怎么改。

**Non-goals:**
- 不改 select_draft / gate 数学。
- 不改 16a stance 重试。
- 不改 `check_factcheck`(已正确返回 flagged)。
- 不碰 resume。

**Touched surface:** `lib/runner.py`
**Regression shield:** `factcheck_flagged` 注入+pop 只在 `name=="factcheck"` 重试分支;finalize 追加分支在 `ctx.get("factcheck_flagged")` 为空时输出字节不变(保留 2281-2284 编辑规范/floor)。Do not modify the test files written in Task 1-tests。

**Task Contract:**
- Expected behavior: 见上。
- Automated verify: `cd <SRC> && python -m pytest lib/tests/test_runner.py -k factcheck_selfheal -x` —— 现在 PASS。
- Real path verify: `⚠️ 需用户验证`:真实 MiniMax/headless 环境跑一次有去假 miss 的真实 episode,确认自愈(单元层用 fake 覆盖逻辑;真路径需真环境)。
- Manual/device verify: none

**Steps:**
1. **`_execute_step` 带出 flagged**(runner.py:1520-1524):halt 返回 dict 增加 `"flagged": gate_result.get("flagged")`(其它键不动)。
2. **重试循环捕获+注入+清除**(runner.py:2417-2437):`parent_name = _RETRY_PARENT.get(name)` 后、重派 parent 前:
   ```python
   if name == "factcheck" and result.get("flagged"):
       ctx["factcheck_flagged"] = result["flagged"]
   ...
   if parent_step is not None:
       _execute_step(parent_step, ctx, gates, dispatch)
   ctx.pop("factcheck_flagged", None)   # 消费后立即清除,防泄漏/防污染 16a
   ```
3. **`_build_step_prompt` finalize 追加分支**(runner.py:2281-2284 的 `if name in ("polishes","finalize") and ctx.get("editorial")` 块**之后**、`return "\n".join(parts)` **之前**):
   ```python
   if name == "finalize" and ctx.get("factcheck_flagged"):
       parts.append("")
       parts.append("## 去假门退回:以下客观断言无法溯源到「当日新闻背景」,逐条二选一处理")
       for claim in ctx["factcheck_flagged"]:
           parts.append(f"- {claim.get('claim', claim)}")
       parts.append("处理方式(每条二选一):① 软化为定性——删掉数字/删掉具名事件,只留方向性表述;"
                    "② 在 material-summary 以 `- **<lead>**: <fact> (source: <url>, <date>)` 补来源条目,"
                    "并让正文引用该 lead。不得保留无源的量化/具名断言。"
                    "**软化后正文仍不得低于 6500 非空白字**——删数字腾出的篇幅靠收紧论证/补一层视角补足,不靠填充。")
   ```
   (用 `parts.append`,落在编辑规范/floor 注入之后 → floor 不被丢;为空时此分支不进入 → 字节不变。)
   **⚠️ 长度风险(verifier 提示):** 重试循环在 runner.py:~2436 丢弃了重派 finalize 的 gate 返回(三条 retry 路径都如此,非本次引入),而 `check_factcheck` 只查溯源不查长度——所以软化后若跌破 6500 floor,门不会拦,可能 ship 短稿。因此上面那句"不得低于 6500 字"是 prompt 侧的硬约束,且 1-impl 的 Verify 要显式查软化产物长度。
   **⚠️ name-gate 不可放宽:** `_RETRY_PARENT` 还有 `faithfulness→finalize`(论文线 runner.py:101)和 `stance-card-gate→stance-write`(16a)两条共用 parent;注入/清除的 `if name == "factcheck"` 必须**精确等于**,放宽成模糊匹配会把 flagged 注入论文线/stance 重派。
4. 跑 1-tests 转绿;再跑全量 `pytest lib/tests/test_runner.py` 确认 clean-run 顺序/resume/16a 测试未破。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python -m pytest lib/tests/test_runner.py -k "factcheck_selfheal or order or resume" -x`
Expected: 全 passed(自愈转绿 + 顺序/resume 测试仍绿)。
另:1-tests 的自愈用例需断言"软化后产物 ≥ 6500 非空白字"(覆盖 verifier 提示的 heal-path ship-短风险——门不查长度,测试补上)。
<!-- /section -->

<!-- section: task-2 keywords: version, plugin, marketplace -->
### Task 2: 版本号对齐(提交由用户触发)

**Maps to Impact Map:** Shared surfaces

**Files:**
- Modify: `.claude-plugin/plugin.json`(version → 下一个号)
- Modify: `/Users/norvyn/Code/Skills/personal-os/.claude-plugin/marketplace.json`(podcast-studio version 同步)

**Expected behavior:** 改完 Task 1 后,版本声明与代码一致(消除现状:plugin.json/marketplace 写 0.5.1,cache 0.5.2 代码已 == source)。**提交/发布/重装由用户触发**——代码改完时执行方提醒用户,不自动 commit(DP-002 Chosen)。

**Non-goals:** 不改任何 lib/ 逻辑;不自动 commit / 不自动发布。
**Touched surface:** plugin.json / marketplace.json。
**Regression shield:** 仅版本字符串。

**Task Contract:**
- Expected behavior: 见上。
- Automated verify: `grep '"version"' <SRC>/.claude-plugin/plugin.json` 与 marketplace.json 中 podcast-studio version 一致且 > 0.5.2。
- Real path verify: `⚠️ 需用户验证`:重装/发布机制由用户执行(DP-002 unverified)。
- Manual/device verify: none

**Steps:**
1. Task 1 代码改完、测试全绿后,bump plugin.json + marketplace.json 版本号(同步)。
2. 提醒用户:代码就绪,可提交/发布(机制由用户定,见 DP-002)。不自动 commit。

**⚠️ No test: 纯版本/元数据,无逻辑;验证靠 grep + 重装后核对。**

**Verify:**
Run: `grep -h '"version"' /Users/norvyn/Code/Skills/personal-os/podcast-studio/.claude-plugin/plugin.json`
Expected: 版本号 > 0.5.2 且与 marketplace.json 一致。
<!-- /section -->

---

## Decisions

### [DP-002] 版本 bump + 发布/重装机制 (resolved)

**Context:** source 代码 == cache 0.5.2,但 plugin.json/marketplace 声明 0.5.1。改完怎么进到 Claude Code 实际加载的版本?
**Chosen:** 执行阶段只改代码 + 版本字符串;**提交/发布由用户触发**(代码就绪时提醒,不自动 commit)。

> 注:原 DP-001(resume 发布后边界守卫)已移除——resume 已存在且本次只做 Task 1 去假自愈(用户 2026-06-21 决定);发布后窄边界守卫不在本计划范围。

---
## Verification
- **Verdict:** Approved
- **Date:** 2026-06-21
- **Verifier:** dev-workflow:plan-verifier (cycle 2; baseline 50 tests pass, 0 must-revise)
- **Report:** `.claude/reviews/plan-verifier-2026-06-21-085419.md`
- **Folded-in advisories:** heal-path 6500字 floor 约束(prompt + test 断言);name-gate 精确 `=="factcheck"`(防污染 faithfulness/16a 共用 parent)。
