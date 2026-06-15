---
type: plan
status: active
contract_version: 2
tags: [runner, orchestration, step-table, claude-p, no-tts]
refs: [docs/06-plans/2026-06-14-big-track-redesign-dev-guide.md]
---

# Phase 1: 传送带 — 代码编排 runner Implementation Plan

**Goal:** 让现有 17 步管线改由一个确定性 Python runner 串联,任何一站缺产物即停线、绝不静默跳过;新增「无-TTS 模式」;persona 创意行为本阶段不变。

**Architecture:** 三层新增,全在 `lib/` 确定性层。(1) `lib/pipeline.py` 把 SKILL.md 的 17 步拓扑声明为**数据**(step 表:每站 `name/kind/agent/inputs/artifact/gate/parallel/retry`)。(2) `lib/dispatch.py` 提供「headless `claude -p` 派一个 persona」原语——读 `agents/<name>.md` 当 system prompt、拼输入、指令写 artifact 到 scratch、`subprocess` 列表参数(非 shell=True)、回来跑 gate。(3) `lib/runner.py` 按 step 表逐站执行(code 站直接调 lib 函数;agent 站调 dispatch),校验产物,缺失即 halt;支持 A/B/C 并行扇出、12/12a 与 16/16a 重试环、`--no-tts` 模式。SKILL.md 缩为薄壳调 runner。**编排环零 LLM token**(顺序由代码决定,不靠会话自觉)。

**Tech Stack:** Python 3.13(`lib/`, pytest);headless `claude -p`(persona 派发,Sonnet→MiniMax M3);复用 `lib/{episode,stance,magnitude,bible,factcheck,config}.py`。

**Design doc:** docs/06-plans/2026-06-14-big-track-redesign-dev-guide.md(Phase 1)

**Design analysis:** none

**Crystal file:** none

**Bug diagnosis:** not applicable

**Threat model:** included

**Pre-flight risks:**
- `lib/*.py` 是**可导入模块、非 CLI**(CLAUDE.md 不变量):runner/pipeline/dispatch 必须以 `from lib.x import f` 被调用,入口只能是 SKILL.md(薄壳)或 `python -m lib.runner`(需给 runner 加 `__main__`,这是除 config.py/orchestrator.py 外新增的唯一带 `__main__` 的模块——计划内、需在 CLAUDE.md 记一笔)。
- gate 契约统一 `{"ok","reason",...}`(`lib/episode.py:137/236`、`factcheck.py:150`、`stance.py` check):runner 消费 gate 一律读 `.ok`/`.reason`,不得 fork 契约形状。
- `make_scratch` 返回的 `Path` 是唯一 scratch slot(`episode.py:324`,`.scratch-{safe_id}-{HHMMSS}[-N]`):runner 必须**透传**这个 Path 给每一站,绝不手拼 `.scratch-{date}-{show}`(commit 45a7a2d 的 per-invocation 修复;手拼会撞回旧的跨调用 artifact bleed)。
- **本阶段不改 persona 创意行为**(davinci 仍出五段+草稿头、kuaidao75 行仍五段)——Phase 1 e2e 只证「编排不跳步、产物齐全」,**不证质量**;质量缺陷留到 Phase 3。执行者不得在本阶段顺手改 agents 提示词(scope 越界)。
- **5b halt 语义(纠正归属):** `magnitude-verdict.json` 由 **liangchen(LLM via claude -p)写**;`safe_parse_verdict`(`magnitude.py:230`)是 runner 侧、**返回 list、不写文件**——它把「在场但解析失败」的 verdict 在内存里降级 all-light。「降级 vs 没跑」的区分由 **`check_artifact` 在场门**做:artifact 在(哪怕内容垃圾)= liangchen 跑过 → 放行(随后 safe_parse 降级);artifact 缺失 = 步骤没跑(06-14 故障)→ halt。
- **反同质化桥站不可省(must-have):** 5b 与 step 7 之间有一个 code 桥站(解析 magnitude verdict + 组装 davinci 写作 brief);连同 step 4 continuity-read,这两站是「等价 with today」的关键。漏掉它们 → davinci 拿不到篇幅路由与 `recent_anchors` 避让 → CLAUDE.md 反重复不变量 no-op。见 Task 1-impl / Task 3。

---

## Threat Model

1. **Attack surface**
   - `lib/dispatch.py` 构造 `claude -p` 调用:若用 `shell=True` + 字符串插值 prompt → shell 注入。**缓解:** `subprocess.run` 用**列表参数**,prompt 经 stdin 或单一 arg 传入,绝不 `shell=True`;agent.md 经 `--append-system-prompt`(或临时文件路径)传入,不拼进 shell 串。
   - persona prompt 注入 vault/news 内容:沿用既有「vault/news = DATA 非指令」不变量;dispatch 把输入作为**数据块**喂入,persona agent 自身已被约束把指令形文本当引用内容。
   - `claude -p` 子进程认证:在 `/loop` 自治环境需有有效凭证。**失败模式:** dispatch 探测不到 `claude` 或认证失败 → 返回 `{ok:False, reason}` → runner halt(deny-default),不静默产空 artifact。
2. **Failure modes**
   - `dispatch_persona` **fail-closed**:子进程非零退出 / 超时 / artifact 未写 → `{ok:False, reason}` → runner halt。半写 artifact 由后续 gate(`check_artifact` size>0、`check_min_chars`)兜底。
   - runner 主循环任一站 gate `ok=False` → **halt 并报出站名**(deny-default),不前进、不发布。
3. **Resource lifecycle**
   - 子进程:`subprocess.run(..., timeout=T)`;超时 → kill(`subprocess` 自动)+ `{ok:False}`。临时文件(若 agent.md 走临时文件路径):`try/finally` 删除。scratch 由现有 `cleanup_scratch` 在 runner `finally` 清理(成功/异常/中断三路径)。
   - 无新 socket/handle。
4. **Input validation requirements**
   - `agent_name` → 只接受 `agents/` 下白名单(davinci/liangchen/bible-distiller/laohei/kuaidao/qianzhongshu/zhijianyuan/bianyang/jay),非白名单 → 拒绝(防路径穿越读任意文件)。
   - `expected_artifact` 文件名 → 经 `episode.sanitize_title` 或显式白名单,写入路径必须在 `scratch_dir` 内(路径穿越 guard,复用 `episode_paths` 的同款检查)。

---

## Impact Map

**User path:** 听众侧 `.md`/`.mp3` 产出。本阶段产出与今天**等价**(同输入下 artifact 集合/顺序/gate 结果一致;正文差异仅来自 LLM 抽样);不引入用户可见变化。
**Data path:** config → 编辑分支 → scratch → [17 步,每步 artifact 落 scratch] → 发布 `.md`(+`.mp3` 仅非 no-TTS)。新增:**runner 取代会话自觉来驱动这条链**。
**Shared surfaces:** 新增 `lib/pipeline.py`/`lib/dispatch.py`/`lib/runner.py`;`skills/podcast/SKILL.md`(缩薄壳);`CLAUDE.md`(DP-001 描述更新 + runner `__main__` 记一笔)。复用(不改逻辑)`lib/{episode,stance,magnitude,bible,factcheck,config}.py`。
**Existing consumers:** `skills/podcast/SKILL.md` 是唯一编排者——它从「读 17 步散文自己派」变成「调 runner」。无其它 caller 调这些 lib gate。
**Must remain unchanged:** 6+ persona 的创意提示词(agents/*.md);所有 gate 函数的实现与 `{ok,reason}` 契约;stance 卡 append-only;`candidate_id` 严格匹配;现有 `lib/tests/` 全绿;TTS 单 vendor 路径(非 no-TTS 时)。
**Regression checks:** 现有 `lib/tests/` 仍全绿;no-TTS e2e dry run 产出 `.md`+口播稿+stance 卡且无 mp3;删任一站 artifact → runner halt 报站名;magnitude/bible artifact 跑后必在。

---

<!-- section: task-1 keywords: pipeline, step-table, topology, loader -->
### Task 1-tests: step 表数据结构与校验 — 测试

**Maps to Impact Map:** Shared surfaces(lib/pipeline.py)

**Files:**
- Test: `lib/tests/test_pipeline.py`

**Expected outcome:** 一组测试断言:17 步拓扑能被解析成结构化 step 列表;每个 step 有合法 `kind`(`code`/`agent`)、agent 站有合法 `agent` 名、声明了 `artifact` 与 `gate`;并行组(7/8/9)标了 `parallel` 扇出;重试站(12/12a、16/16a)标了 `retry`;非法表(缺字段/未知 kind/未知 agent)被 fail-closed 拒绝。

**Non-goals:** 不测 runner 执行;不测 dispatch。

**Touched surface:** 新增测试文件。

**Regression shield:** 不动现有测试。

**Task Contract:**
- Expected behavior: 开发者改拓扑时,一张结构错误的 step 表会被代码当场拒绝并指出错在哪——而不是运行到一半才炸。
- Automated verify: `python3 -m pytest lib/tests/test_pipeline.py -q` —— 当前必 FAIL(`ModuleNotFoundError: lib.pipeline` / 无 `load_pipeline`)。
- Real path verify: 不适用(纯数据层)。
- Manual/device verify: none

**Steps:**
1. 写 `test_pipeline.py`:导入 `from lib.pipeline import load_pipeline, STEPS`(或等价)。
2. 断言 `load_pipeline("morning")` / `("evening")` 返回有序 step 列表,长度覆盖 1–17(含 3a/5b/12a/15a/15b/16a)。
3. 断言每个 step 含 `name, kind, artifact, gate`;`kind=="agent"` 的含 `agent` ∈ 白名单;7/8/9 标 `parallel:["A","B","C"]`;12/12a、16/16a 标 `retry` 上限。
4. **断言两个 code 桥站存在且有序**:`continuity-read` 在 step 5 前;`assemble-briefs` 在 5b 之后、step 7 之前,且 `assemble-briefs` 的 inputs 引用 `magnitude-verdict.json`,artifact 产出 `writing-brief-A/B/C.json`。
5. **断言复合 gate**:step 7/9 的 `gate` 含 `check_artifact` 与 `check_min_chars`(args.min_chars=="floor")两项;step 12 的 `check_min_chars` 项 args 含 `json_field=="body"`。每个 gate 项是 `{"fn":...}` 形。
6. 断言注入一个 gate 项缺 `fn` / `kind="bogus"` / `agent="ghost"` 的 step → `load_pipeline` 或 `validate_pipeline` raise `ValueError` 点名字段。
7. 跑测试,确认按预期 FAIL(目标模块不存在)。

**Verify:**
Run: `python3 -m pytest lib/tests/test_pipeline.py -q`
Expected: 收集到用例并因 `lib.pipeline` 不存在而 FAIL(非 0 退出)。
<!-- /section -->

<!-- section: task-1-impl keywords: pipeline, step-table, topology, loader -->
### Task 1-impl: step 表数据结构与校验 — 实现

**Depends on:** Task 1-tests

**Maps to Impact Map:** Shared surfaces(lib/pipeline.py)

**Files:**
- Create: `lib/pipeline.py`

**Expected outcome:** 同 Task 1-tests 的用户可见结果:拓扑成为可校验的数据,错表当场被拒。

**Non-goals:** 不执行步骤;不派 agent。

**Touched surface:** 新增 `lib/pipeline.py`。

**Regression shield:** 不改任何现有 lib 模块。

**Task Contract:**
- Expected behavior: 同 1-tests。
- Automated verify: `python3 -m pytest lib/tests/test_pipeline.py -q` 全 PASS。
- Real path verify: `python3 -c "from lib.pipeline import load_pipeline; print(len(load_pipeline('morning')))"` 打印步数。
- Manual/device verify: none

**Steps:**
1. 建 `lib/pipeline.py`,把附录「17 步合同表」(来自 SKILL.md:571–595 + 步骤散文)声明为数据:每 step `{name, kind('code'|'agent'), agent|None, inputs:[...], artifact:str|None, gate:list[dict]|None, parallel:list|None, retry:int|None, skip_when:str|None}`。`skip_when="no_tts"` 标在 step 14(TTS)与 step 15 的 mp3 移动子步。
2. **gate 是复合列表**(advisory 修正):每项 `{fn, args}`。单门如 `[{"fn":"check_artifact"}]`;**复合门**如 step 7/9 草稿/润色 `[{"fn":"check_artifact"},{"fn":"check_min_chars","args":{"min_chars":"floor"}}]`、step 12 定稿 `[{"fn":"check_artifact"},{"fn":"check_min_chars","args":{"json_field":"body","min_chars":"floor"}}]`。哨兵 `"floor"` 由 runner 解析为 `floor_chars_for_show(show)`(`episode.py:207`)。pipeline.py 只存字符串名,不 import 执行层(保持纯数据)。
3. **显式包含两个 code 桥站(must-revise 修复,不可省)**:
   - **`continuity-read`(step 4,code)**:`due_bets(cards,today)` / `carried_open_questions(cards,today,show)` / `pick_to_deepen(load_obsessions(output_dir),cards)`;产出 continuity 数据(in-memory 或 `continuity.json`)。
   - **`assemble-briefs`(5b 与 7 之间,code)**:读 `magnitude-verdict.json`(`safe_parse_verdict`)+ davinci 采集的 brief-A/B/C(material-summary 内)+ continuity,按 `magnitude_to_airtime(magnitude)`(`magnitude.py:267`)给每候选算篇幅档、union `recent_anchors` 避让清单,写 `writing-brief-A.json`/`-B`/`-C` 到 scratch。gate `[{"fn":"check_artifact"}]` 各。**step 7 的三路 davinci 各 consume 对应 `writing-brief-X.json`**(这是把路由+避让送进写稿的唯一通道;漏了 = 反同质化 no-op)。
     - 注:Phase 1 **保留** `recent_anchors`(等价 with today);DP-001=A 在 **Phase 2** 才用 covered-ground 取代它。本阶段不动。
4. `load_pipeline(show)` 返回该 show 的有序 step 列表(morning/evening 仅编辑分支不同,步骤拓扑一致)。
5. `validate_pipeline(steps)` fail-closed:未知 kind / agent 不在白名单 / gate 项缺 `fn` / 缺必填字段 → raise `ValueError` 点名。`load_pipeline` 内部调它。
6. 跑测试至全绿。

**Verify:**
Run: `python3 -m pytest lib/tests/test_pipeline.py -q`
Expected: PASS。
<!-- /section -->

<!-- section: task-2 keywords: dispatch, claude-p, persona, subprocess -->
### Task 2-tests: persona 派发原语(claude -p) — 测试

**Maps to Impact Map:** Shared surfaces(lib/dispatch.py);Threat model(注入/失败模式)

**Files:**
- Test: `lib/tests/test_dispatch.py`

**Expected outcome:** 测试(注入一个 fake subprocess runner,不真调 claude)断言:`dispatch_persona` 用**列表参数**(非 shell=True)构造命令、把 `agents/<name>.md` 内容作为 system prompt 传入、把「写到 `<scratch>/<artifact>`」指令拼进 user prompt、子进程成功且 artifact 已写 → `{ok:True}`、子进程非零或 artifact 缺失 → `{ok:False, reason}`、`agent_name` 非白名单 → 拒绝、`expected_artifact` 路径穿越(`../`)→ 拒绝。

**Non-goals:** 不真跑 claude(那是 2-impl 的 real-path verify);不测 runner。

**Touched surface:** 新增测试文件。

**Regression shield:** 不动现有测试。

**Task Contract:**
- Expected behavior: 这个原语对外行为可预期——能成功就写出文件、失败就明确报错,且不可被恶意 agent 名或路径穿越利用。
- Automated verify: `python3 -m pytest lib/tests/test_dispatch.py -q` —— 当前必 FAIL(`lib.dispatch` 不存在)。
- Real path verify: 不适用(2-impl 负责真跑)。
- Manual/device verify: none

**Steps:**
1. 写 `test_dispatch.py`:`from lib.dispatch import dispatch_persona`。注入 `runner=` 一个 fake(记录被调的 argv、写出 artifact / 不写 / 返回非零)。
2. 断言 argv 是 list 且首元素是 claude 可执行名;断言**没有** `shell=True`(通过 fake 的签名捕获);断言 user prompt 含 artifact 绝对路径;system-prompt 注入含 agent.md 文本。
3. 成功路径(fake 写出 artifact + 退出 0)→ `{ok:True, artifact_path}`;失败路径(非零 / 未写 / 超时模拟)→ `{ok:False, reason}`。
4. `dispatch_persona("ghost", ...)` → 拒绝(`ValueError`/`{ok:False}`);`expected_artifact="../escape.json"` → 拒绝。
5. 跑测试确认 FAIL(模块不存在)。

**Verify:**
Run: `python3 -m pytest lib/tests/test_dispatch.py -q`
Expected: 因 `lib.dispatch` 不存在 FAIL。
<!-- /section -->

<!-- section: task-2-impl keywords: dispatch, claude-p, persona, subprocess -->
### Task 2-impl: persona 派发原语(claude -p) — 实现 + 真跑去险

**Depends on:** Task 2-tests

**Maps to Impact Map:** Shared surfaces(lib/dispatch.py);Threat model

**Files:**
- Create: `lib/dispatch.py`

**Expected outcome:** 一个真能用 `claude -p` 跑一个 persona 并把产物落到 scratch 的原语;且对错误输入安全(白名单 + 路径穿越 guard)。

**Non-goals:** 不串联多站(runner 的事);不改 agents 提示词。

**Touched surface:** 新增 `lib/dispatch.py`。

**Regression shield:** 不改现有 lib。

**Task Contract:**
- Expected behavior: 给定一个 persona 名 + 输入,这个函数真的会让一个 headless Claude 干那一步的活、把结果写到指定文件;干不成就明确报错。
- Automated verify: `python3 -m pytest lib/tests/test_dispatch.py -q` 全 PASS。
- Real path verify(去险,最关键):手动跑一次真实 `claude -p` 派 **bianyang**(最便宜的纯改写站):喂一段固定 finalize body,确认它把口播稿写到 scratch。命令见 Steps 第 6 步。
- Manual/device verify: none

**Steps:**
1. **先确认 CLI 契约:** 跑 `claude --help`,确认 headless 模式的实际 flag——系统提示注入(预期 `--append-system-prompt`)、工具白名单(预期 `--allowedTools`)、非交互输出(预期 `-p` / `--print`)。**以 `--help` 实测为准**,本计划假设的 flag 名若不符即按实测改(不静默)。
2. `dispatch_persona(agent_name, user_prompt, scratch_dir, expected_artifact, *, timeout=600, runner=subprocess.run, claude_bin="claude", model=None, plugin_root=<resolved>) -> dict`。
3. 白名单校验 `agent_name`;读 `{plugin_root}/agents/{agent_name}.md` 作 system prompt;`expected_artifact` 经路径穿越 guard 解析到 `scratch_dir` 内的绝对路径。
4. 拼最终 user prompt = `user_prompt` + 一行明确指令:「把你的产物写到 `<abs artifact path>`,只写该文件」。
5. `runner([claude_bin, "-p", final_prompt, "--append-system-prompt", agent_md, "--allowedTools", "Read,Write,Bash,WebSearch,WebFetch,Grep,Glob", *(["--model", model] if model else [])], cwd=plugin_root, capture_output=True, text=True, timeout=timeout)`。**列表参数、无 shell=True。**(agent_md 过长则写临时文件、传 `--append-system-prompt @file` 或等价,`finally` 删临时文件——按 step 1 的 `--help` 实测决定。)
6. 子进程返回后:非零退出 → `{ok:False, reason: stderr 摘要}`;否则 `check_artifact(artifact_path)`,`ok` 透传其结果 + `artifact_path`。
7. **真跑去险(real-path):** 写一个固定 finalize body 到临时 scratch,`dispatch_persona("bianyang", "<finalize body>...", scratch, "broadcast-script-test.txt")`,确认返回 `ok:True` 且文件非空。把命令与输出记进执行报告(不进单测,避免 CI 真调 claude)。
8. 跑单测至全绿。

**Verify:**
Run: `python3 -m pytest lib/tests/test_dispatch.py -q`
Expected: PASS。(真跑去险另见 Real path verify,产出 `broadcast-script-test.txt` 非空。)
<!-- /section -->

<!-- section: task-3 keywords: runner, sequencer, halt, no-tts, parallel -->
### Task 3-tests: runner 序列器 — 测试

**Maps to Impact Map:** Data path(runner 驱动全链);Regression checks(halt / no-TTS)

**Files:**
- Test: `lib/tests/test_runner.py`

**Expected outcome:** 测试(注入 fake dispatch + fake gates,不真跑)断言:runner 按 step 表顺序执行;某站 gate 返回 `ok:False`(或 artifact 缺失)→ **halt 并在结果里点名该站**、不执行后续站;`no_tts=True` → 跳过 step 14 与 step 15 的 mp3 移动、其余照跑;并行组 7/8/9 三路都被派;12a/16a 失败触发对应重试站、超过 retry 上限 → halt;**降级但 artifact 在的 5b** → 放行(不与「artifact 缺失」混淆)。

**Non-goals:** 不真跑 claude;不真写 .md/.mp3。

**Touched surface:** 新增测试文件。

**Regression shield:** 不动现有测试。

**Task Contract:**
- Expected behavior: 一站没产出,流水线当场停在那站并说清是哪站没跑;开了无-TTS 就不碰音频那两步;没有任何一站会被悄悄跳过。
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q` —— 当前必 FAIL(`lib.runner` 不存在)。
- Real path verify: 不适用(3-impl 后由 Phase 验收的 no-TTS e2e 覆盖)。
- Manual/device verify: none

**Steps:**
1. 写 `test_runner.py`:`from lib.runner import run_pipeline`;注入 `dispatch=` fake(按 step 名决定写/不写 artifact)、`gates=` fake。
2. 顺序断言:正常路径下 fake 记录的执行顺序 == `load_pipeline` 顺序。
3. halt 断言:让某站 gate `ok:False` → `run_pipeline` 返回 `{status:"halted", failed_step:<name>, reason}`,且该站之后的站**未被调用**。
4. no-TTS 断言:`run_pipeline(..., no_tts=True)` → step 14 与 mp3 移动**未被调用**;`.md`/口播稿/stance 路径仍走。
5. 并行断言:7/8/9 三路 A/B/C 都被 dispatch(检查 fake 调用计数)。
6. **反同质化桥站断言(must-revise 核心)**:给 fake 的 5b 写一个含 `magnitude:medium` + `recent_anchors:["1956苏伊士"]` 的 `magnitude-verdict.json`;断言 `assemble-briefs` 站产出 `writing-brief-A.json` 含算出的篇幅档(`magnitude_to_airtime("medium")=="segment"`)与该 `recent_anchors`;**断言 step-7 davinci fake 收到的 user_prompt/brief 里带上了这条 routing + `recent_anchors`**(漏了即视为 no-op,测试必须红)。
7. 复合 gate 断言:step 7 的某路 polish/draft 字数低于 floor → `check_min_chars` 项触发该站失败(即便 `check_artifact` 过)。
8. 重试断言:12a fake 头一次 `ok:False`、二次 `ok:True` → 触发一次 step-12 重派后通过;连续失败超 retry → halt。
9. 降级区分:5b artifact 存在但标 `degraded` → 放行;5b artifact 缺失 → halt。
10. 跑测试确认 FAIL。

**Verify:**
Run: `python3 -m pytest lib/tests/test_runner.py -q`
Expected: 因 `lib.runner` 不存在 FAIL。
<!-- /section -->

<!-- section: task-3-impl keywords: runner, sequencer, halt, no-tts, parallel -->
### Task 3-impl: runner 序列器 — 实现

**Depends on:** Task 1-impl, Task 2-impl, Task 3-tests

**Maps to Impact Map:** Data path;Regression checks

**Files:**
- Create: `lib/runner.py`

**Expected outcome:** 一个 `run_pipeline(show, *, date, no_tts=False, dispatch=..., gates=..., config=...)` 函数 + `__main__`,确定性串联全链、缺件 halt、支持 no-TTS、并行扇出、重试环。

**Non-goals:** 不改 persona;不改 gate 实现;不动 SKILL.md(Task 4)。

**Touched surface:** 新增 `lib/runner.py`(新增 `__main__` — 计划内例外,Task 4 在 CLAUDE.md 记一笔)。

**Regression shield:** 复用现有 gate/scratch/select_draft,**不改其实现**;`make_scratch` 返回的 Path 全程透传不手拼。

**Task Contract:**
- Expected behavior: 跑一条命令就能让整期节目从采集到发布走完,中途任一步没产出就停下报哪步;无-TTS 模式下产出文稿但不合成音频。
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q` 全 PASS。
- Real path verify: Phase 验收的 no-TTS e2e dry run(见下方「Phase 验收」),由主控在 execute 后运行,不作为本任务的阻塞 verify。
- Manual/device verify: none

**Steps:**
1. `run_pipeline`:`load_config()` → 解析编辑分支 → `make_scratch(output_dir, f"{date}-{show}")` 得 scratch Path → 跑 step 3a `stance_card_exists` 守卫(True 即早停,沿用现有用户文案)。
2. 把 gate `fn` 名映射到函数(`{"check_artifact":episode.check_artifact, "check_min_chars":episode.check_min_chars, ...}`),`gates` 参数可注入(测试用)。**复合 gate**:按 step 的 `gate` 列表逐项调用,任一 `ok=False` 即整站失败;解析 `args`——哨兵 `min_chars:"floor"` → `floor_chars_for_show(show)`,透传 `json_field` 等。
3. 主循环遍历 `load_pipeline(show)`:`kind=="code"` 站调对应 lib 函数 —— **`continuity-read`**(due_bets/carried_open_questions/pick_to_deepen)、**`assemble-briefs`**(读 `magnitude-verdict.json` 经 `safe_parse_verdict` + brief-A/B/C + continuity → 按 `magnitude_to_airtime` 算篇幅档 + union `recent_anchors` → 写 `writing-brief-{A,B,C}.json`)、select_draft / episode_paths / 写 .md / 15a/15b / write_card / cleanup;`kind=="agent"` 站调 `dispatch_persona`(`dispatch` 参数可注入)。**step 7 三路 davinci 的 user_prompt 必须带上对应 `writing-brief-X.json`**(把篇幅路由+`recent_anchors` 避让送进写稿;这是 must-revise 的核心通道)。
4. 每站执行后跑其 `gate`;`ok=False` → 立即返回 `{status:"halted", failed_step, reason}`,**不执行后续**。`skip_when=="no_tts"` 且 `no_tts=True` → 跳过该站/子步。
5. 并行组(7/8/9):对 A/B/C 各派一次(本阶段可顺序调用三次以求确定性;真正并发留作后续优化,不影响「不跳步」目标——若顺序调用,在注释标注 D-105-style 决策)。
6. 重试环(12↔12a、16↔16a):gate 失败 → 重派对应生成站,`retry` 上限内重试;超限 → halt。
7. `finally`: `cleanup_scratch(scratch)`(成功/异常/中断三路径)。
8. `if __name__ == "__main__":` 解析 `--show {morning|evening} [--date YYYY-MM-DD] [--no-tts]`,调 `run_pipeline`,打印结果状态,非零退出码表 halt。
9. 跑单测至全绿。

**Verify:**
Run: `python3 -m pytest lib/tests/test_runner.py -q`
Expected: PASS。
<!-- /section -->

<!-- section: task-4 keywords: skill-md, thin-wrapper, claude-md, dp-001 -->
### Task 4: SKILL.md 缩薄壳 + CLAUDE.md 更新

<!-- no-split: 纯文档/编排说明改动,无可执行逻辑;按 write-plan「pure config/.md」豁免 test-impl split -->

**Maps to Impact Map:** Shared surfaces(SKILL.md / CLAUDE.md);Existing consumers(唯一编排者改为调 runner)

**Files:**
- Modify: `skills/podcast/SKILL.md`
- Modify: `CLAUDE.md`

**Expected outcome:** `/podcast morning|evening` 触发后,SKILL.md 指示会话**调 `python -m lib.runner --show <show>`**(而非自己读 17 步散文逐个派);17 步合同表保留为「runner 拓扑的人读参照」并注明真相源是 `lib/pipeline.py`。CLAUDE.md 的 DP-001 从「orchestration is prose」更新为「编排顺序=代码(`lib/runner.py`+`lib/pipeline.py`)、persona 提示词=prose」,并在「Non-obvious invariants」记一笔 runner 是除 config.py/orchestrator.py 外新增的带 `__main__` 模块。

**Non-goals:** 不删 persona 提示词;不改 references/{morning,evening}.md;不改 agents/*.md。

**Touched surface:** SKILL.md、CLAUDE.md(纯说明)。

**Regression shield:** 保留 17 步合同表内容(作参照),不删既有 persona 描述(absence≠deletion)。

**Task Contract:**
- Expected behavior: 用户敲 `/podcast morning` 时,流程改由 runner 跑;读 SKILL.md 的人能看到「现在由 runner 编排」+ 拓扑真相源指针。
- Automated verify: N/A — 文档改动;`grep -n "lib.runner" skills/podcast/SKILL.md` 与 `grep -n "DP-001" CLAUDE.md` 命中新内容。
- Real path verify: Phase 验收 e2e 时,从 SKILL.md 指示走到 runner。
- Manual/device verify: none

**Steps:**
1. SKILL.md:在 spine 顶部把「读以下 17 步、依次派 persona」改为「调 `python -m lib.runner --show <morning|evening>`;runner 负责顺序、gate、halt」。保留 17 步合同表,加一行「真相源:`lib/pipeline.py`;本表为人读参照」。
2. CLAUDE.md:更新 DP-001 段落措辞(编排顺序降代码、提示词仍 prose);在 invariants 加 runner `__main__` 例外说明。
3. `grep` 确认两处改动落地。

**Verify:**
Run: `grep -n "lib.runner" skills/podcast/SKILL.md && grep -n "lib/runner.py\|编排顺序" CLAUDE.md`
Expected: 两处都命中新内容。
<!-- /section -->

---

## Phase 验收(execute 后由主控运行,非单个任务的阻塞 verify)

> 对应 dev-guide Phase 1 验收标准。在 execute-plan 完成、`lib/tests/` 全绿后,由主控在主上下文运行:

1. **no-TTS e2e dry run:** `python3 -m lib.runner --show morning --no-tts`(真调 claude -p 串全链)→ 确认 `output_dir` 出 `{date}-{title}.md` + scratch 出口播稿 + `{date}-morning.stance.yaml`,**无 `.mp3`**。
2. **不跳步:** 一次正常 run 后,scratch 里 `magnitude-verdict.json` 与 `character-bible.md` 必在(06-14 缺失场景不可再现)。
3. **halt 验证:** 在某站产出前注入故障(或删其 artifact)→ runner 返回 `halted` 并点名该站、不发布。
4. **等价:** 抽查产出 artifact 集合/顺序/gate 结果与现行路径一致(正文文本差异不算回归;**本阶段 persona 仍有 06-14 类质量缺陷,属预期,Phase 3 修**)。

## Decisions

None. Phase 1 的执行模型(完整代码 runner + `claude -p` 派发)已在 dev-guide Global Constraints 锁定;余下实现级选择(step 表载体=Python 模块;并行组本阶段顺序调用;CLI flag 以 `claude --help` 实测为准)已在任务步骤内就地决定并注明,无需用户阻断决策。

---
## Verification
- **Verdict:** Approved(revision cycle 1 后)
- **Date:** 2026-06-14
- **Verifier notes:** 反同质化桥站(continuity-read + assemble-briefs)已补全且 Task 3-tests step 6 锚定其非 no-op;复合 gate 强制长度下限;5b halt-vs-degrade 归属已正;scope 干净(未改 agents/*.md)。
