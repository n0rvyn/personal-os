---
type: plan
status: active
contract_version: 2
tags: [covered-ground, cross-episode-memory, embedding, distiller, anti-homogenization, dp-001a]
refs: [docs/06-plans/2026-06-14-big-track-redesign-dev-guide.md]
---

# Phase 2: 夜班管理员 — 跨期记忆 Implementation Plan

**Goal:** 每期发布后,一个隔离蒸馏器把本期用过的招牌锚/类比/框架更新进结构化「covered-ground」记忆;下一期 runner 从该记忆渲染「最近用滥、请避开」避让备忘录,每轮强制注入 davinci 写作 brief —— 同时按 DP-001=A 退役旧的 `recent_anchors` 单信号,让 covered-ground 成为 davinci 唯一的避让记忆。

**Architecture:** 新增三件套——`lib/embed.py`(macOS NLContextualEmbedding shell + cosine + n-gram 回退)、`lib/coveredground.py`(yaml 结构化 store,克隆 `lib.bible` 的隔离/原子写盘纪律,store 存 `{output_dir}/covered-ground.yaml`)、`agents/coveredground-distiller.md`(隔离蒸馏 persona)。runner 在 assemble-briefs 站把 `recent_anchors` union 换成 covered-ground 渲染的 `avoid_memo`;新增两站(发布之后、fail-soft):agent 蒸馏抽取 apparatus → scratch json,code 站算 embedding + 更新 store。DP-001=A 从 `lib/magnitude.py` / liangchen / davinci / SKILL / CLAUDE 原子移除 `recent_anchors`(保留 `gather_recent_bodies`——它现在喂 covered-ground)。

**Tech Stack:** Python 3.13(`lib/`, pytest);Swift CLI helper(macOS `NLContextualEmbedding`,中文 dim=512);Claude Code 子代理(headless `claude -p`,`agents/coveredground-distiller.md` 作 system prompt);PyYAML store。

**Design doc:** none — design captured inline in dev-guide Phase 2 + this plan(per project convention)

**Design analysis:** none

**Crystal file:** none on disk(dev-guide Global Constraints + DP-001=A〔本会话用户批准,记于 dev-guide〕承担 crystal 角色)

**Bug diagnosis:** not applicable

**Threat model:** included(新增 headless `claude -p` 派发站 + store 文件写盘 + Swift 子进程 shell-out;沿用 Phase 1 dispatch 威胁模型)

**Pre-flight risks:**
- `recent_anchors` 双系统残留:DP-001=A 移除横跨 `lib/magnitude.py`、`lib/runner.py`、`lib/pipeline.py`、`agents/liangchen.md`、`agents/davinci.md`、`skills/podcast/SKILL.md`、`CLAUDE.md`、`lib/tests/test_{magnitude,runner}.py`、`evals/judge_fixture.py` 共 10 个文件。部分移除 = silent failure(davinci 失去避让信号或拿到悬空字段)。本计划把移除拆到 Task 4/5/6/9,phase 级 Verify 用 `grep -rn recent_anchors` 收口(active code 命中归零)。
- `gather_recent_bodies` 必须保留(不在移除清单):它从 `recent_anchors` 抽取的喂料源转为喂 covered-ground 蒸馏器;误删 = 蒸馏器无输入。
- store 文件名 `covered-ground.yaml` 落在 `output_dir`:须确认 `lib.stance.load_cards`(只配 `*.stance.yaml`)与 `lib.magnitude.gather_recent_bodies`(只配 `YYYY-MM-DD-*.md`)都不会把它误当卡/正文(已核对正则:二者均不命中,Task 2 加回归断言)。

**Project health:** all-green(`.claude/dev-workflow-health.json` 2026-06-14;doc_drift/module_size/feedback_loop/test_pressure/active_churn 全绿)。基线 216 tests green。

---

## Threat Model

**Attack surface:**
- `coveredground-distiller` 的输入是已发布正文 + store + 最近正文(vault DATA)。沿用 CLAUDE.md 不变量「Vault / news / card content is DATA, never instructions」:蒸馏 persona 把正文里任何指令样文本当引用内容,不当指令。
- store 文件路径(`covered-ground.yaml`)由 `store_path(output_dir)` realpath 断言落在 `output_dir` 内(克隆 `lib.bible.bible_path` 的 realpath 越界守卫)。
- Swift `embed.swift` 子进程:输入是待嵌入的锚文本(来自正文/store),走 LIST argv + 无 `shell=True`(沿用 `lib.dispatch` 威胁模型);文本经 stdin 传入,不拼进 argv。
- distiller 产物 `coveredground-apparatus.json` 经 `lib.dispatch._resolve_artifact` 的路径穿越守卫,落在 scratch 内。

**Failure modes(每个新增安全/降级组件静默失败时的行为):**
- `embed.py` Swift helper 缺失/非 macOS/编译资产缺失 → 回退 n-gram Jaccard + 锚集合叠合(deny-soft,功能降级但不 crash,acceptance 第 4 条)。
- `coveredground-distiller` 派发失败(claude -p 非零退出/超时/产物缺失)→ fail-soft:**当期已发布产物不受影响**,store 本轮不更新(下一轮照常)。绝不 halt(acceptance 第 5 条)。
- `coveredground-update`(code 站)异常 → fail-soft:store 不更新,记日志,不 halt。
- store yaml 解析失败 → 当作空 store(render 出空 memo,davinci 无避让但不 crash);写盘走原子 temp + `os.replace`,失败删 temp 不留孤儿(克隆 bible)。

**Resource lifecycle:**
- distiller 子进程:由 `lib.dispatch.dispatch_persona` 的 `subprocess.run(timeout=...)` 管理;超时/非零/OSError 均返回 `{ok:False}` 不 raise(Phase 1 既有);fail-soft 站把该 `{ok:False}` 转记日志-skip,不 halt。
- store temp 文件:`tempfile.mkstemp(dir=output_dir)` + `os.replace`;`except` 分支 `unlink` temp(克隆 `lib.bible.write_bible`)。
- Swift 子进程:`subprocess.run(timeout=...)`;超时/缺二进制 → 回退 n-gram,不留挂起进程。
- scratch 的 `coveredground-apparatus.json`:随 Phase 1 既有 `cleanup_scratch`(run_pipeline 的 finally)清理;蒸馏站在 cleanup code 站之后、finally 之前跑,scratch 仍在。

**Input validation requirements:**
- 待嵌入文本经 stdin 传给 Swift(不进 argv),避免命令注入;`embed.py` 对 helper 输出按 JSON 解析,非法 JSON → 回退 n-gram。
- store yaml 经 `yaml.safe_load`(非 `load`),拒绝任意对象构造。
- `apparatus_used` 写卡前经 `lib.stance._validate_card_shape`:必须是 list[str],非法 → 写卡前 raise(但 step 16 抽取侧 fail-soft 到 `[]`,见 Task 7)。

---

## Impact Map

**User path:** 听众侧——节目不再一期期复用同一套招牌类比/锚(苏伊士、伊万、GPS、印刷术)。界面/格式无直接变化(dev-guide「用户可见的变化:间接」)。
**Data path:** 发布正文(`{output_dir}/{date}-{title}.md`)+ 最近正文 + 现有 store →〔post-publish 蒸馏站〕apparatus 抽取(scratch json)→〔update 站〕embedding + 衰减 → `covered-ground.yaml` store。下一期:store →`render_memo()`→ `avoid_memo` → writing-brief-{A,B,C}.json → step-7 davinci user_prompt。
**Shared surfaces:** `lib/magnitude.py`(DP-001=A 改 verdict 契约)、`lib/runner.py`(assemble-briefs/build_draft_prompt/stance-write/execute_step/新站)、`lib/pipeline.py`(step 表 + fail_soft 字段 + whitelist)、`lib/dispatch.py`(whitelist)、`lib/stance.py`(apparatus_used 字段)、`agents/{liangchen,davinci}.md`、`skills/podcast/SKILL.md`、`CLAUDE.md`(不变量)。
**Existing consumers:** davinci(读 brief 的避让信号)、liangchen(产 verdict)、runner 主循环(派发+gate+halt)、`lib.tests.test_{magnitude,runner,pipeline,stance,dispatch}`、`evals/judge_fixture.py`。
**Must remain unchanged:** 量臣的分量路由(none/light/medium/heavy → magnitude_to_airtime)、`gather_recent_bodies`、append-only 卡、温度原则(主观观点/下注不被 dedup/memo 削弱)、Phase 1 的「缺产物即 halt」核心不变量(仅对发布前的站;post-publish 蒸馏站是显式 fail-soft 例外)、stance 卡 schema 既有字段、no-TTS 模式、bible-distiller 隔离站。
**Regression checks:** `python3 -m pytest lib/tests/ -q`(216→更多 green)、`grep -rn recent_anchors lib/ agents/ skills/ CLAUDE.md evals/`(active code 归零)、温度盾测(纯主观 body 不被 memo/dedup 误伤)、distiller 故障注入测(发布产物不受影响)、`magnitude_to_airtime` 路由测保持绿。

---

<!-- section: task-1-tests keywords: embed, cosine, ngram-fallback -->
### Task 1-tests: embedding 接口(Swift shell + cosine + n-gram 回退)— 测试

**Maps to Impact Map:** Data path(embedding 计算);Shared surfaces(新 `lib/embed.py`)

**Files:**
- Create: `lib/tests/test_embed.py`

**Expected outcome:** `lib/embed.py` 的纯 Python 面(cosine 计算、n-gram Jaccard 回退、Swift helper shell 接口、macOS 探测)有可在任意平台跑的单测;未实现时测试编译并 FAIL。

**Non-goals:**
- 不测 Swift `embed.swift` 的真实 NLContextualEmbedding 数值(那是 ⚠️ 需设备验证;Python 面用 mock subprocess)。

**Touched surface:** `lib/tests/test_embed.py`(新)

**Regression shield:** 不动既有任何测试文件。

**Task Contract:**
- Expected behavior: 系统能把两段中文文本判为「语义相近 / 不相近」——有 macOS helper 时用向量 cosine,无 helper 时退回字面 n-gram 叠合;两条路径都给出 0..1 的相似度。
- Automated verify: `python3 -m pytest lib/tests/test_embed.py -q` —— 先 FAIL(`ModuleNotFoundError: No module named 'lib.embed'` 或 `AttributeError`)。
- Real path verify: Task 1-impl 后同命令转 PASS;真实 Swift 向量走 e2e(本计划末 phase 验收)。
- Manual/device verify: none(数值正确性在 e2e 段标 ⚠️ 需设备验证)。

**Steps:**
1. 写 `test_cosine_identical_is_one`:`embed.cosine([1,0,0],[1,0,0])` ≈ 1.0;`test_cosine_orthogonal_is_zero`:`[1,0],[0,1]` ≈ 0.0;`test_cosine_zero_vector_safe`:含零向量返回 0.0 不除零崩。
2. 写 `test_ngram_jaccard_*`:`ngram_similarity("1956苏伊士运河危机","苏伊士运河 1956")` 高于 `ngram_similarity("苏伊士","完全无关的句子")`;空串安全返回 0.0。
3. 写 `test_similarity_uses_helper_when_available`:注入一个 fake runner(返回 `{"vector":[...]}` JSON),断言 `embed.similarity(a, b, runner=fake)` 走向量路径;`test_similarity_falls_back_on_helper_failure`:fake runner 抛/返回非零 → 自动走 n-gram,不 raise。
4. 写 `test_macos_detection`:`embed._helper_available(...)` 在 helper 路径不存在时返回 False。
5. 运行第 1 条 Automated verify,确认 FAIL 且原因是缺 `lib.embed`(非语法错)。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_embed.py -q`
Expected: 收集到测试且全部 FAIL(import error / attribute error),无语法错。
<!-- /section -->

<!-- section: task-1-impl keywords: embed, swift, nlcontextualembedding -->
### Task 1-impl: embedding 接口(Swift helper + Python 面)— 实现

**Depends on:** Task 1-tests

**Maps to Impact Map:** Data path(embedding);Shared surfaces(`lib/embed.py`,`tools/embed.swift`)

**Files:**
- Create: `lib/embed.py`
- Create: `tools/embed.swift`

**Expected outcome:** `lib/embed.py` 提供 `cosine`、`ngram_similarity`、`similarity(a,b)`(优先向量,失败回退 n-gram)、`embed_text(text)`(shell `tools/embed.swift`,返回向量或 None);`tools/embed.swift` 用 `NLContextualEmbedding` 算中文向量,从 stdin 读文本、stdout 输出 `{"vector":[...]}` JSON。Task 1-tests 全 PASS。

**Non-goals:**
- 不在本任务接 covered-ground(Task 2 用 `embed.similarity`);不改 runner。

**Touched surface:** `lib/embed.py`(新)、`tools/embed.swift`(新)

**Regression shield:** 不动 Task 1-tests 写的测试(改测试=test tampering)。

**Task Contract:**
- Expected behavior: 同 Task 1-tests —— 给两段中文得到 0..1 相似度,macOS 走语义向量,否则字面回退。
- Automated verify: `python3 -m pytest lib/tests/test_embed.py -q` 全 PASS。
- Real path verify: e2e 段真跑 `tools/embed.swift`(标 ⚠️ 需设备验证:`swiftc tools/embed.swift -o /tmp/embed && echo "苏伊士" | /tmp/embed` 输出非空 vector,dim=512)。
- Manual/device verify: 编译 + 真实向量数值在 e2e 段验证。

**Steps:**
1. `tools/embed.swift`:`import NaturalLanguage`;读 stdin 全文;`NLContextualEmbedding(language: .simplifiedChinese)`(或按 `assignEmbedding` 探测);若 `hasAvailableAssets`/`load()` 成功 → 算向量 → `print(JSON {"vector":[Double]})`;失败 → stderr + 非零退出。仅作平台 API 透传(⚠️ No unit test for swift:平台 API 透传,Python 面 mock)。
2. `lib/embed.py`:`from __future__ import annotations`;`cosine(a,b)`(纯 math,零向量守 0.0);`_NGRAM_N=2`;`ngram_similarity(a,b)`(2-gram set Jaccard,空串 0.0)。
3. `embed_text(text, *, runner=subprocess.run, swift_bin=None, plugin_root=None)`:resolve `tools/embed.swift`(或预编译 bin),文本经 `input=` 走 stdin(不进 argv),`timeout`;解析 stdout JSON `vector`;任何失败(缺 swiftc/非 macOS/超时/非法 JSON)返回 None。
4. `_helper_available(plugin_root)`:`platform.system()=="Darwin"` 且 swift 源/bin 存在。
5. `similarity(a, b, *, runner=...)`:`va=embed_text(a); vb=embed_text(b)`;两者非 None → `cosine(va,vb)`;否则 `ngram_similarity(a,b)`。
6. 跑 Task 1-tests 转 PASS。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_embed.py -q`
Expected: 全 PASS。
<!-- /section -->

<!-- section: task-2-tests keywords: coveredground, store, decay, render-memo -->
### Task 2-tests: covered-ground 结构化 store(读写/衰减/render_memo)— 测试

**Maps to Impact Map:** Data path(store);Shared surfaces(新 `lib/coveredground.py`);Must remain unchanged(温度盾、store 不被卡/正文误读)

**Files:**
- Create: `lib/tests/test_coveredground.py`

**Expected outcome:** store 的读写、`update_store`(新锚入库 / 已有锚 count+1、last_used 更新 / episodes 追加)、`is_stale`(过热谓词:14 天内 count≥3 或 最近 3 期出现≥2 期)、`render_memo`(过热锚入 memo、温度盾:纯主观观点不入 memo)有单测;未实现时 FAIL。

**Non-goals:**
- 不测真实 embedding 数值(注入 `similarity_fn` fake)。

**Touched surface:** `lib/tests/test_coveredground.py`(新)

**Regression shield:** 不动既有测试。

**Task Contract:**
- Expected behavior: 系统记得「哪些招牌锚/类比最近反复用了」,并能渲染一句「这些最近用滥、换个说法」的备忘;只记招牌装置,不记宿主的主观判断/下注。
- Automated verify: `python3 -m pytest lib/tests/test_coveredground.py -q` —— 先 FAIL(缺 `lib.coveredground`)。
- Real path verify: Task 2-impl 后转 PASS;真实跨期在 e2e 段验。
- Manual/device verify: none。

**Steps:**
1. `test_store_path_under_output_dir`:`store_path(tmp)` == `tmp/covered-ground.yaml` 且 realpath 越界 raise(镜像 `test_bible.bible_path`)。
2. `test_store_roundtrip`:`write_store` 后 `load_store` 同构;空/缺文件 → `load_store` 返回空 store(不 raise)。
3. `test_update_new_anchor`:空 store + `update_store(store, anchors=["1956苏伊士"], date, episode)` → 锚入库 `{first_used,last_used,count:1,episodes:[ep]}`。
4. `test_update_existing_anchor_increments`:已有锚再 update → `count` 2、`last_used` 更新、`episodes` 追加(去重同 ep)。
5. `test_is_stale_count`:14 天内 count≥3 → True;count=2 → False。`test_is_stale_recency`:最近 3 期出现≥2 期 → True。两谓词任一满足即过热。
6. `test_render_memo_lists_hot_anchors`:store 含一个过热锚 → `render_memo(store, today)` 文本非空且含该锚 + 「避开/换说法」语义,不含未过热锚。
7. `test_render_memo_empty_when_none_hot`:无过热锚 → memo 为空串/「无需避让」。
8. `test_render_memo_targets_apparatus_not_opinions`(温度盾):store 只存招牌锚;断言 `update_store` 拒绝/不收录纯主观判断串(用约定:update 只吃 apparatus 列表;memo 文本不出现「别下注/别表态」类措辞)。
9. `test_reskin_detection_uses_similarity`:注入 `similarity_fn` 返回高分 → `update_store` 把「换皮锚」并入既有锚的 count(不新建);fake 返回低分 → 新建。
10. `test_store_ignored_by_card_and_body_loaders`(回归):在 tmp 写 `covered-ground.yaml` + 一张 `*.stance.yaml` + 一个 `YYYY-MM-DD-x.md`;断言 `lib.stance.load_cards` 不返回 store、`lib.magnitude.gather_recent_bodies` 不返回 store。
11. 跑确认 FAIL。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_coveredground.py -q`
Expected: 收集到且全 FAIL(缺模块),无语法错。
<!-- /section -->

<!-- section: task-2-impl keywords: coveredground, yaml-store, staleness -->
### Task 2-impl: covered-ground 结构化 store — 实现

**Depends on:** Task 1-impl, Task 2-tests

**Maps to Impact Map:** Data path(store);Shared surfaces(`lib/coveredground.py`)

**Files:**
- Create: `lib/coveredground.py`

**Expected outcome:** `lib/coveredground.py` 提供 `store_path`、`load_store`、`write_store`(原子,克隆 `lib.bible.write_bible`)、`update_store(store, anchors, date, episode, *, similarity_fn=embed.similarity)`、`is_stale(entry, today, *, window_days=14)`、`render_memo(store, today)`。store schema:`{anchors: {anchor: {first_used,last_used,count,episodes:[], embedding?}}}`。Task 2-tests 全 PASS。

**Design approach:** 克隆 `lib.bible` 的隔离/原子写盘纪律(realpath 守卫 + temp+`os.replace`),但 store 是结构化 yaml(非覆盖式 md);衰减 v1 谓词来自 dev-guide(14 天内 count≥3 或 最近 3 期≥2 期,对齐量臣 window_days=14)。

**Non-goals:**
- 不接 runner(Task 5/7);不做 LLM 抽取(蒸馏 persona 在 Task 8)。

**Touched surface:** `lib/coveredground.py`(新)

**Regression shield:** 不动 Task 2-tests。

**Task Contract:**
- Expected behavior: 同 Task 2-tests。
- Automated verify: `python3 -m pytest lib/tests/test_coveredground.py -q` 全 PASS。
- Real path verify: e2e 段真跑(一期后 store 落盘、含新锚)。
- Manual/device verify: none。

**Steps:**
1. `store_path(output_dir)` → `output_dir/covered-ground.yaml`,realpath 断言越界 raise(照抄 `bible.bible_path`,改文件名)。
2. `_STORE_FILENAME="covered-ground.yaml"`;`load_store(output_dir)`:缺/空文件 → `{"anchors":{}}`;`yaml.safe_load`,解析失败 → `{"anchors":{}}`(fail-soft)。
3. `write_store(output_dir, store)`:原子 temp+`os.replace`+错误删 temp(照抄 `bible.write_bible`,`yaml.safe_dump(allow_unicode=True, sort_keys=False)`)。
4. `is_stale(entry, today, *, window_days=14)`:解析 `episodes` 的日期;`count_in_window>=3` OR `distinct_episodes_in_last_3>=2` → True。最近 3 期按 episodes 排序的末 3 个 episode 日期界定。
5. `update_store(store, anchors, date, episode, *, similarity_fn=None)`:对每个 anchor,先用 `similarity_fn`(默认 `lib.embed.similarity`)对比 store 既有 key,>v1 阈值(`_RESKIN_THRESHOLD=0.82`)→ 并入既有锚(count+1、last_used、episodes 追加);否则新建 `{first_used=date,last_used=date,count=1,episodes=[episode]}`。同 episode 不重复计数。
6. `render_memo(store, today)`:筛 `is_stale` 的锚 → 输出人话避让备忘(列锚 + 「最近反复用过,本期能不用就不用、要用换个新的」);无过热 → 返回空串。**只列 apparatus 锚,绝不输出针对主观观点/下注的避让措辞(温度原则)。**
7. 跑 Task 2-tests 转 PASS。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_coveredground.py -q`
Expected: 全 PASS。
<!-- /section -->

<!-- section: task-3-tests keywords: stance, apparatus-used, card-schema -->
### Task 3-tests: stance 卡新增 apparatus_used 字段 — 测试

**Maps to Impact Map:** Data path(apparatus 入卡);Shared surfaces(`lib/stance.py`);Must remain unchanged(卡既有字段 + append-only)

**Files:**
- Modify: `lib/tests/test_stance.py`

**Expected outcome:** `apparatus_used` 作为可选 `list[str]` 字段被 `_validate_card_shape` 接受、被 `load_cards` 往返;非 list 被拒;缺失时不影响既有校验。未实现时新测 FAIL。

**Non-goals:**
- 不改既有 stance 测试断言(只新增)。

**Touched surface:** `lib/tests/test_stance.py`(增测)

**Regression shield:** 既有 stance 测试保持原样、保持绿。

**Task Contract:**
- Expected behavior: 一张卡可以记录「本期用过哪些招牌锚/类比/框架」,且不破坏卡的其它校验与 append-only。
- Automated verify: `python3 -m pytest lib/tests/test_stance.py -q` —— 新测先 FAIL(`apparatus_used` 被当未知字段或未往返)。
- Real path verify: Task 3-impl 后转 PASS。
- Manual/device verify: none。

**Steps:**
1. `test_write_card_accepts_apparatus_used`:card 含 `apparatus_used:["1956苏伊士","印刷术类比"]` → `write_card` 成功;`load_cards` 读回该列表。
2. `test_apparatus_used_must_be_list`:`apparatus_used:"x"`(字符串)→ `write_card` raise ValueError 含字段名。
3. `test_apparatus_used_optional`:不含该字段的卡仍正常写/读(向后兼容)。
4. `test_card_with_only_apparatus_not_placeholder`:含 `apparatus_used` 的卡不被 `_is_empty_card_placeholder` 当空卡跳过(确保 `episode`+`apparatus_used` 算有内容)。
5. 跑确认新测 FAIL、旧测仍绿。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_stance.py -q`
Expected: 新增 4 测 FAIL,既有 stance 测试 PASS。
<!-- /section -->

<!-- section: task-3-impl keywords: stance, apparatus-used, validate-card -->
### Task 3-impl: stance 卡 apparatus_used 字段 — 实现

**Depends on:** Task 3-tests

**Maps to Impact Map:** Data path(apparatus 入卡);Shared surfaces(`lib/stance.py`)

**Files:**
- Modify: `lib/stance.py:113-173`(`_validate_card_shape` 的 optional-typed 区块)

**Expected outcome:** `_validate_card_shape` 接受可选 `apparatus_used: list[str]`(镜像 `topics`/`named_concept` 的校验);非 list 或元素非 str → raise。Task 3-tests 全 PASS。

**Non-goals:**
- 不改 `write_card` 流程、不改 append-only 守卫;不在此任务往卡里写 apparatus(写入在 Task 7 的 step-16)。

**Touched surface:** `lib/stance.py`(`_validate_card_shape`)

**Regression shield:** 既有 stance 测试保持绿;不动 `write_card`/`load_cards` 的其它逻辑。

**Task Contract:**
- Expected behavior: 同 Task 3-tests。
- Automated verify: `python3 -m pytest lib/tests/test_stance.py -q` 全 PASS。
- Real path verify: e2e 段卡里出现 apparatus_used。
- Manual/device verify: none。

**Steps:**
1. 在 `_validate_card_shape` 的 optional-typed 段(`named_concept`/`topics` 旁)加:`if "apparatus_used" in card:` → 必须 `list`,每元素 `str`(非 bool),否则 raise 含字段名。
2. 确认 `_is_empty_card_placeholder`:含 `apparatus_used` 的非空卡的 `non_episode` 非空 → 不被当占位(无需改,断言覆盖)。
3. 跑 Task 3-tests 转 PASS。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_stance.py -q`
Expected: 全 PASS。
<!-- /section -->

<!-- 执行顺序说明:Task 5(runner avoid_memo)在文件中先于 Task 4(magnitude recent_anchors 移除)出现 —— compute_checkpoints 按文件顺序分批,故此处物理顺序即执行顺序。Task 5 整体转绿后 Task 4 才动 magnitude,消除 plan-verifier 标的「跨任务红区间」。任务编号保留(5 在前、4 在后,非单调,batcher 按位置+id 处理,不受影响)。 -->

<!-- section: task-5-tests keywords: runner, assemble-briefs, avoid-memo -->
### Task 5-tests: runner avoid_memo 取代 recent_anchors(DP-001=A 接线)— 测试

**Maps to Impact Map:** Data path(store→memo→brief→step7);Shared surfaces(`lib/runner.py`);Must remain unchanged(温度盾、路由)

**Files:**
- Modify: `lib/tests/test_runner.py:24-27,726,797-899,1129`

**Expected outcome:** `_assemble_briefs` 把 covered-ground 渲染的 `avoid_memo` 写进 `writing-brief-{tag}.json`(取代 `recent_anchors`/`recent_anchors_union`);`_build_draft_prompt` 把 `avoid_memo` 织进 step-7 davinci user_prompt;无 store/空 memo 时 brief 仍合法(memo 空)。改写断言先 FAIL(实现仍走 recent_anchors)。

**Non-goals:**
- 不测真实 LLM;不动 halt/路由相关测试。

**Touched surface:** `lib/tests/test_runner.py`(改 `test_assemble_briefs_*`)

**Regression shield:** runner 其它测试(halt-on-missing、no-tts skip、路由、resume、并行)保持绿。

**Task Contract:**
- Expected behavior: 写稿前,runner 把「最近用滥、请避开」备忘塞进每路 davinci 的写作 brief;旧的 recent_anchors 通道彻底换成 covered-ground 渲染的备忘。
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q -k "assemble or brief or memo"` —— 改写断言先 FAIL。
- Real path verify: Task 5-impl 后转 PASS。
- Manual/device verify: none。

**Steps:**
1. 把 `test_assemble_briefs_hands_recent_anchors_to_step7_davinci`(797)改名/改写为 `test_assemble_briefs_hands_avoid_memo_to_step7_davinci`:在 tmp output_dir 写一个含过热锚的 `covered-ground.yaml`;断言 `_assemble_briefs` 产出的 `writing-brief-A.json` 含非空 `avoid_memo`(含该锚),且 **不含** `recent_anchors`/`recent_anchors_union`;断言 step-7 davinci fake 收到的 user_prompt 含该 memo。
2. 改 726、1129 的 verdict fixture:去掉 `recent_anchors` 键(前向对齐——Task 5 先于 Task 4 跑,此时 `parse_verdict` 仍容忍该键缺失〔`item.get("recent_anchors",[])`〕;Task 4 之后该键被彻底移除。本步把 `test_runner.py` 里所有 recent_anchors 引用一次清干净,使 Task 4 移除 magnitude 字段时不再有 runner 测试转红)。
3. 加 `test_assemble_briefs_empty_memo_when_no_store`:无 `covered-ground.yaml` → brief 的 `avoid_memo` 为空串,davinci prompt 不报错。
4. 加温度盾 `test_avoid_memo_does_not_suppress_opinions`:断言 memo 文本不含「别下注/别表态」类措辞(只针对 apparatus)。
5. 确认改写断言 FAIL。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_runner.py -q -k "assemble or brief or memo or avoid"`
Expected: 改写/新增断言 FAIL,其余 runner 测试 PASS。
<!-- /section -->

<!-- section: task-5-impl keywords: runner, assemble-briefs, build-draft-prompt -->
### Task 5-impl: runner avoid_memo 注入 — 实现

**Depends on:** Task 2-impl, Task 5-tests（**不**依赖 Task 4-impl:`_assemble_briefs` 停读 recent_anchors、改注入 avoid_memo,无论 magnitude 是否仍产该字段都正确;Task 5 先跑、整体转绿后 Task 4 才动 magnitude,避免跨任务红区间——见 Task 4-tests 的执行顺序硬约束。）

**Maps to Impact Map:** Data path(store→memo→brief→step7);Shared surfaces(`lib/runner.py`)

**Files:**
- Modify: `lib/runner.py:424-509`(`_assemble_briefs`)
- Modify: `lib/runner.py:756-855`(`_build_draft_prompt`)
- Modify: `lib/runner.py:18-26`(模块 docstring 提及 recent_anchors 处)

**Expected outcome:** `_assemble_briefs` 不再 union `recent_anchors`;改为 `from lib.coveredground import load_store, render_memo` → `avoid_memo = render_memo(load_store(output_dir), date)` 注入每个 `writing-brief-{tag}.json` 的 `avoid_memo` 键(并入 ctx)。`_build_draft_prompt` 用 `avoid_memo` 段替换 `recent_anchors` 段(标题改「## 反复用过的招牌锚——本期避让(covered-ground)」)。Task 5-tests 全 PASS。

**Replaces:** `_assemble_briefs` 的 `union_anchors`/`recent_anchors`/`recent_anchors_union` + `_build_draft_prompt` 的 recent_anchors 段 → covered-ground `avoid_memo`。

**Non-goals:**
- 不在此任务加新站/fail-soft(Task 6/7);不动路由(magnitude_to_airtime)、continuity 注入。

**Touched surface:** `lib/runner.py`(`_assemble_briefs`、`_build_draft_prompt`、docstring)

**Regression shield:** halt/路由/no-tts/resume/并行测试保持绿;不动 Task 5-tests。

**Task Contract:**
- Expected behavior: 同 Task 5-tests。
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q` 全 PASS(含未改的既有 runner 测试)。
- Real path verify: e2e 段 davinci brief 含 avoid_memo。
- Manual/device verify: none。

**Steps:**
1. `_assemble_briefs`:删 `union_anchors`/`anchors`/`recent_anchors`/`recent_anchors_union` 逻辑(458-491、506);保留 candidate/magnitude/airtime/what_moved/recap_hook/degraded/continuity。
2. 顶部加 `from lib.coveredground import load_store, render_memo`(leaf import);算 `avoid_memo = render_memo(load_store(ctx["output_dir"]), ctx["date"])`;每个 brief 加 `brief["avoid_memo"]=avoid_memo`;`ctx["avoid_memo"]=avoid_memo`。
3. `_build_draft_prompt`(798-853):删读 `recent_anchors`/`recent_anchors_union` 的块;改读 `brief.get("avoid_memo","")`;无 memo → 「(无 covered-ground 避让约束)」;有 → 「## 反复用过的招牌锚——本期避让(covered-ground)\n{memo}\n历史锚按上面清单避让、换新的。」
4. 改模块 docstring(18-26)recent_anchors 措辞 → avoid_memo/covered-ground。
5. 跑全 runner 测试转 PASS。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_runner.py -q && grep -n recent_anchors lib/runner.py || echo "runner clean"`
Expected: 全 PASS;`grep` 命中 0(或仅历史注释)。
<!-- /section -->

<!-- section: task-4-tests keywords: magnitude, recent-anchors-removal, dp-001a -->
### Task 4-tests: DP-001=A 退役量臣 recent_anchors — 测试

**Depends on:** Task 5-impl（**执行顺序硬约束**:Task 5 先于 Task 4〔上方〕。Task 5-tests 已把 `test_runner.py` 全部 recent_anchors 引用改成 avoid_memo;一旦 Task 5 整体转绿,Task 4 从 magnitude 移除 recent_anchors 只触及 `test_magnitude.py`/`judge_fixture.py`〔Task 4 自有领域〕,不会让某个**别的**任务拥有的 runner 测试转红——消除 plan-verifier 标的「跨任务红区间」。Task 5-impl 不依赖 Task 4:`_assemble_briefs` 停读 recent_anchors、改读 avoid_memo,与 magnitude 是否仍产该字段无关。)

**Maps to Impact Map:** Shared surfaces(`lib/magnitude.py` verdict 契约);Must remain unchanged(分量路由 + `gather_recent_bodies`)

**Files:**
- Modify: `lib/tests/test_magnitude.py:79,137-168`
- Modify: `evals/judge_fixture.py:14,126-139`

**Expected outcome:** 量臣 verdict 不再产/校验 `recent_anchors`:`parse_verdict` 对无 `recent_anchors` 的 verdict 正常解析、输出 dict 无该键;`safe_parse_verdict` 降级 dict 无该键;`magnitude_to_airtime` 与 `gather_recent_bodies` 行为不变。judge_fixture 去掉 recent_anchors 断言。改后新断言先 FAIL(旧实现仍含该键)。

**Non-goals:**
- 不动 `gather_recent_bodies` 的返回结构;不删分量路由测试。

**Touched surface:** `lib/tests/test_magnitude.py`、`evals/judge_fixture.py`

**Regression shield:** 分量路由 + `build_judge_input` + `gather_recent_bodies` 的测试保持绿。

**Task Contract:**
- Expected behavior: 量臣只判「这条新闻的分量(none/light/medium/heavy)」决定篇幅,不再额外列「最近用过的锚」——避让记忆交给 covered-ground。
- Automated verify: `python3 -m pytest lib/tests/test_magnitude.py -q` —— 改写的断言先 FAIL(`parse_verdict` 仍在 out dict 放 `recent_anchors`)。
- Real path verify: Task 4-impl 后转 PASS。
- Manual/device verify: none。

**Steps:**
1. 改 `test_magnitude.py:137-144`(原断言 `by["霍尔木兹停火"]["recent_anchors"]==[...]`):改为断言 verdict dict **不含** `recent_anchors` 键、`magnitude`/`airtime` 仍正确;输入 fixture 去掉 `recent_anchors` 字段或断言其被忽略。
2. 改 `test_magnitude.py:79,168`(注释 + 降级断言):降级 verdict dict 不含 `recent_anchors`。
3. 改 `evals/judge_fixture.py:14,126-139`:删 recent_anchors 必含 1956苏伊士/1973石油 的断言;保留分量/路由断言(若有)。
4. 确认改后断言 FAIL(实现未改前 out dict 仍含该键)。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_magnitude.py -q`
Expected: 改写的断言 FAIL,其余 magnitude 测试 PASS。
<!-- /section -->

<!-- section: task-4-impl keywords: magnitude, parse-verdict, gather-recent-bodies -->
### Task 4-impl: DP-001=A 量臣 recent_anchors 退役 — 实现

**Depends on:** Task 4-tests

**Maps to Impact Map:** Shared surfaces(`lib/magnitude.py`);Must remain unchanged(分量路由 + `gather_recent_bodies`)

**Files:**
- Modify: `lib/magnitude.py:11-19,73-84,119-164,182-254`

**Expected outcome:** `parse_verdict` 不再校验/输出 `recent_anchors`(out dict 去键);`safe_parse_verdict` 降级 dict 去键;docstring 改述(量臣只做分量,`gather_recent_bodies` 改述为喂 covered-ground)。**`gather_recent_bodies` 函数体不动**(只改 docstring)。Task 4-tests 全 PASS。

**Replaces:** `recent_anchors`(量臣 anti-repeat 旧版,distilled-too-thin)→ 由 Phase 2 covered-ground 取代(DP-001=A)。

**Non-goals:**
- 不删 `gather_recent_bodies`/`build_judge_input`(它们继续喂蒸馏器);不改 `magnitude_to_airtime`/`_AIRTIME`。

**Touched surface:** `lib/magnitude.py`(parse/safe_parse + docstrings)

**Regression shield:** `gather_recent_bodies` 返回结构、`magnitude_to_airtime` 不变;相关测试保持绿。不动 Task 4-tests。

**Task Contract:**
- Expected behavior: 同 Task 4-tests。
- Automated verify: `python3 -m pytest lib/tests/test_magnitude.py -q` 全 PASS。
- Real path verify: e2e 段 verdict 无 recent_anchors。
- Manual/device verify: none。

**Steps:**
1. `parse_verdict`(182-227):删 `anchors=item.get("recent_anchors",[])` 校验块 + out dict 的 `"recent_anchors": [...]`。其余字段(candidate/magnitude/matches_prior/what_moved/recap_hook)不动。
2. `safe_parse_verdict`(230-254):降级 dict 删 `"recent_anchors": []`。
3. docstring(11-19、73-84、132):把「surface recent_anchors for the collector's anti-repeat guard」改为「recent bodies 喂 covered-ground 跨期记忆蒸馏(DP-001=A:量臣不再产 recent_anchors)」。`gather_recent_bodies` 函数体保持不变。
4. 跑 Task 4-tests 转 PASS。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_magnitude.py -q && grep -n recent_anchors lib/magnitude.py || echo "magnitude clean"`
Expected: 测试全 PASS;`grep` 在 magnitude.py 命中 0(或仅历史注释,理想为 0)。
<!-- /section -->

<!-- section: task-6-tests keywords: pipeline, fail-soft, distiller-station, whitelist -->
### Task 6-tests: pipeline fail_soft 字段 + 两个 post-publish 站 + whitelist — 测试

**Maps to Impact Map:** Shared surfaces(`lib/pipeline.py`、`lib/dispatch.py` whitelist);Must remain unchanged(既有 17 站拓扑 + 校验)

**Files:**
- Modify: `lib/tests/test_pipeline.py`
- Modify: `lib/tests/test_dispatch.py:40`(whitelist 镜像)

**Expected outcome:** step 表新增 `fail_soft: bool` 字段(`validate_pipeline` 校验:None/bool);新增两站 `coveredground-distill`(agent=coveredground-distiller, fail_soft=True, skip_when 无)+ `coveredground-update`(code, fail_soft=True),位于 cleanup(17)之后;`coveredground-distiller` 入 `AGENT_WHITELIST`(pipeline + dispatch 双处)。改后新断言先 FAIL。

**Non-goals:**
- 不在此任务测 runner 的 fail-soft 执行(Task 7);只测 step 表数据 + 校验 + whitelist。

**Touched surface:** `lib/tests/test_pipeline.py`、`lib/tests/test_dispatch.py`

**Regression shield:** 既有 17 站断言保持绿(站序/字段);whitelist 既有 9 个 persona 仍在。

**Task Contract:**
- Expected behavior: 流水线声明里多出「发布后跑、失败不阻断」的蒸馏与 store 更新两站;校验器认 `fail_soft` 字段;蒸馏 persona 进白名单。
- Automated verify: `python3 -m pytest lib/tests/test_pipeline.py lib/tests/test_dispatch.py -q` —— 新断言先 FAIL。
- Real path verify: Task 6-impl 后转 PASS。
- Manual/device verify: none。

**Steps:**
1. `test_pipeline.py`:断言 `load_pipeline("morning")` 含 `coveredground-distill`(kind=agent, agent=coveredground-distiller, fail_soft=True)与 `coveredground-update`(kind=code, fail_soft=True),且二者在 `cleanup` 之后。
2. 断言 `validate_pipeline` 对 `fail_soft` 非 bool 的 step raise;对每站要求 `fail_soft` 键存在(None 合法)。
3. 断言 `AGENT_WHITELIST` 含 `coveredground-distiller`。
4. `test_dispatch.py`:把测试镜像的 whitelist(40 行)加 `coveredground-distiller`,断言 `dispatch.AGENT_WHITELIST` 含它。
5. 确认新断言 FAIL。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_pipeline.py lib/tests/test_dispatch.py -q`
Expected: 新断言 FAIL,既有 PASS。
<!-- /section -->

<!-- section: task-6-impl keywords: pipeline, fail-soft, agent-whitelist -->
### Task 6-impl: pipeline fail_soft + post-publish 站 + whitelist — 实现

**Depends on:** Task 6-tests

**Maps to Impact Map:** Shared surfaces(`lib/pipeline.py`、`lib/dispatch.py`)

**Files:**
- Modify: `lib/pipeline.py:100-110`(AGENT_WHITELIST)
- Modify: `lib/pipeline.py:118-453`(`_build_steps`:每站加 `fail_soft` 字段 + 追加两站)
- Modify: `lib/pipeline.py:479-630`(`validate_pipeline`:加 `fail_soft` 校验 + required 字段列表)
- Modify: `lib/pipeline.py:52-90,202-243`(docstring + assemble-briefs 注释:recent_anchors → avoid_memo)
- Modify: `lib/dispatch.py:42-52`(AGENT_WHITELIST)

**Expected outcome:** step schema 多 `fail_soft`(默认 None,新两站 True);两站 `coveredground-distill`(agent)+`coveredground-update`(code)在 17 cleanup 之后;`validate_pipeline` 校验 `fail_soft`;`coveredground-distiller` 入两处 whitelist。导入期 `validate_pipeline(_build_steps())` 不报错。Task 6-tests 全 PASS。

**Non-goals:**
- 不写 runner 执行逻辑(Task 7);两站此处只是数据声明。

**Touched surface:** `lib/pipeline.py`、`lib/dispatch.py`

**Regression shield:** 既有 17 站字段/序不变(只追加键与两站);whitelist 既有项不删。不动 Task 6-tests。

**Task Contract:**
- Expected behavior: 同 Task 6-tests。
- Automated verify: `python3 -m pytest lib/tests/test_pipeline.py lib/tests/test_dispatch.py -q` 全 PASS。
- Real path verify: e2e 段 runner 走两新站。
- Manual/device verify: none。

**Steps:**
1. 两处 `AGENT_WHITELIST`(pipeline 100-110 + dispatch 42-52)加 `"coveredground-distiller"`。
2. `_build_steps`:给现有每个 step dict 加 `"fail_soft": None`(显式,fail-closed 校验要求该键存在)。
3. 在 cleanup(17)之后追加:
   - `coveredground-distill`:`kind=agent, agent=coveredground-distiller, inputs=["published.md","recent_bodies","covered-ground.yaml"], artifact="coveredground-apparatus.json", gate=[{"fn":"check_artifact"}], fail_soft=True`(其余键 None)。
   - `coveredground-update`:`kind=code, agent=None, inputs=["coveredground-apparatus.json","vault.output_dir"], artifact=None, gate=None, fail_soft=True`。
4. `validate_pipeline`:required 字段列表(519-522)加 `"fail_soft"`;加校验 `fail_soft is None or isinstance(bool)`,非法 raise 含字段名。
5. docstring + assemble-briefs 注释(52-90、202-243)recent_anchors → avoid_memo 措辞;补两新站说明。
6. 跑 Task 6-tests 转 PASS;`python3 -c "import lib.pipeline"` 不报导入期校验错。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -c "import lib.pipeline" && python3 -m pytest lib/tests/test_pipeline.py lib/tests/test_dispatch.py -q`
Expected: 导入无错;测试全 PASS。
<!-- /section -->

<!-- section: task-7-tests keywords: runner, fail-soft, post-publish, apparatus-extract -->
### Task 7-tests: runner 执行 post-publish 蒸馏(fail-soft)+ store 更新 + apparatus 入卡 — 测试

**Maps to Impact Map:** Data path(蒸馏→store;apparatus→卡);Shared surfaces(`lib/runner.py`);Must remain unchanged(发布产物在蒸馏失败时不受影响)

**Files:**
- Modify: `lib/tests/test_runner.py`

**Expected outcome:** runner 主循环把 `fail_soft=True` 站的 halt(派发/gate 失败)转为「记日志-skip」不中断;`coveredground-update` 站读 scratch apparatus json + 算 embedding 更新 store;step-16 stance-write 把 apparatus_used 写进卡(确定性抽取,fail-soft 到 [])。蒸馏故障注入 → run 仍 `status:ok`、已发布卡/正文在。改后新断言先 FAIL。

**Non-goals:**
- 不测真实 claude -p(注入 dispatch fake);不测 Swift(注入 similarity fake)。

**Touched surface:** `lib/tests/test_runner.py`(增 fail-soft + apparatus 测)

**Regression shield:** 既有 halt-on-missing 测试(发布前站)保持绿——证明 fail_soft 只豁免标记站。

**Task Contract:**
- Expected behavior: 蒸馏器即使失败,本期节目照常发布;成功时 store 更新、卡里记下本期用过的招牌锚。
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q -k "fail_soft or distill or apparatus or post_publish"` —— 新断言先 FAIL。
- Real path verify: Task 7-impl 后转 PASS;真实跨期在 e2e 段。
- Manual/device verify: none。

**Steps:**
1. `test_distiller_failure_does_not_halt`:注入 dispatch fake 让 `coveredground-distiller` 返回 `{ok:False}` → `run_pipeline(no_tts=True)` 返回 `status:ok`、`stance_card_path` 存在、发布 .md 存在。
2. `test_failsoft_only_exempts_marked_station`:让一个**发布前**的必产站缺产物 → 仍 halt(证明 fail_soft 不泄漏)。
3. `test_coveredground_update_writes_store`:dispatch fake 写一个 `coveredground-apparatus.json`(含 anchors)→ `coveredground-update` 站后 `covered-ground.yaml` 出现这些锚(注入 similarity fake)。
4. `test_stance_write_includes_apparatus_used`:给 store 预置某锚 + finalize body 含该锚 → 写出的卡 `apparatus_used` 含它;抽取失败 → `apparatus_used:[]`、卡仍写成功。
5. 确认新断言 FAIL。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_runner.py -q -k "fail_soft or distill or apparatus or post_publish or halt"`
Expected: 新断言 FAIL,既有 halt 测试 PASS。
<!-- /section -->

<!-- section: task-7-impl keywords: runner, execute-step, coveredground-update, stance-write -->
### Task 7-impl: runner post-publish 蒸馏执行 + store 更新 + apparatus 入卡 — 实现

**Depends on:** Task 2-impl, Task 3-impl, Task 6-impl, Task 7-tests

**Maps to Impact Map:** Data path(蒸馏→store;apparatus→卡);Shared surfaces(`lib/runner.py`)

**Files:**
- Modify: `lib/runner.py:955-1023`(`_execute_step`:fail_soft 转 skip)
- Modify: `lib/runner.py:1025-1088`(`_run_code_step`:加 `coveredground-update` 分支)
- Modify: `lib/runner.py:690-713`(`_stance_write_step`:加 apparatus_used 确定性抽取)
- Modify: `lib/runner.py:1207-1248`(`_build_step_prompt`:coveredground-distill 的 inputs 注入)

**Expected outcome:** `_execute_step` 对 `step.get("fail_soft")` 为真的站:派发/gate 返回 halt → 转 `{"status":"skipped","step":name,"fail_soft":True}` + 记日志,不传播 halt。`_run_code_step` 加 `coveredground-update`:读 scratch `coveredground-apparatus.json` → `update_store` → `write_store`(全 try/except fail-soft)。`_stance_write_step` 用 `load_store` 的已知锚 ∩ body + 卡 `named_concept` 算 `apparatus_used`(fail-soft 到 [])写卡。Task 7-tests 全 PASS。

**Design approach:** apparatus 权威抽取走 post-publish LLM 蒸馏站(catches 新锚,写 store);卡的 `apparatus_used` 是 step-16 确定性 best-effort 审计字段(对齐 `episode.select_draft` 的「never trust LLM self-label」——权威抽取来自定稿正文,不是 davinci 自报)。store 是机制真相源,卡字段是自描述审计。

**Non-goals:**
- 不让蒸馏失败影响发布(fail-soft);不改发布前站的 halt 语义。

**Touched surface:** `lib/runner.py`(`_execute_step`、`_run_code_step`、`_stance_write_step`、`_build_step_prompt`)

**Regression shield:** 发布前站 halt 不变(Task 7-tests 第 2 条守);不动 Task 7-tests。

**Task Contract:**
- Expected behavior: 同 Task 7-tests。
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q` 全 PASS。
- Real path verify: e2e 段一期后 store 更新 + 卡含 apparatus_used。
- Manual/device verify: none。

**Steps:**
1. `_execute_step`:在拿到 `result`(halt)后,若 `step.get("fail_soft")` 为真 → 不返回 halt,改返回 `{"status":"skipped","step":name,"fail_soft":True,"reason":...}`(并 `print` 到 stderr 记日志)。注意 gate-miss 与 dispatch-miss 两条 halt 路径都要走该豁免。
2. `_run_code_step` 加 `if name=="coveredground-update":` 分支:`from lib.coveredground import load_store, update_store, write_store`;读 `scratch/coveredground-apparatus.json`(缺/坏 → return None);抽 `anchors`;`store=load_store(output_dir)`;`update_store(store, anchors, date, {date,show})`;`write_store(output_dir, store)`;全程 try/except 吞错(fail-soft)。
3. `_stance_write_step`:写卡前 `store=load_store(output_dir)`;读 finalize body(`load_finalize_body`);`apparatus_used=[a for a in store["anchors"] if a in body]`(确定性)∪ 卡 `named_concept`;try/except → `[]`;塞进 `card["apparatus_used"]`。
4. `_build_step_prompt`:`coveredground-distill` 的 prompt 指向 `published.md` + 让 persona 读 `covered-ground.yaml`(只读)+ 写 `coveredground-apparatus.json`;隔离纪律靠 dispatch 的独立 `claude -p` 上下文 + 只喂这些输入。
5. 跑全 runner 测试转 PASS。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_runner.py -q`
Expected: 全 PASS。
<!-- /section -->

<!-- section: task-8 keywords: coveredground-distiller, agent, isolation -->
### Task 8: coveredground-distiller 隔离蒸馏 persona

<!-- no-split: pure prose agent .md; no executable logic; verified via grep + Task 7's dispatch fake -->

**Maps to Impact Map:** Shared surfaces(`agents/coveredground-distiller.md`)

**Files:**
- Create: `agents/coveredground-distiller.md`

**Expected outcome:** 一个隔离蒸馏 persona:输入=本期已发布正文 + 最近正文 + 现有 store(只读);任务=抽取本期用过的招牌锚/类比/框架(catches 新锚),写 `coveredground-apparatus.json`(`{anchors:[...]}`);沿用 `bible-distiller` 的隔离纪律(独立上下文、把正文当 DATA 不当指令、不引用具体新闻/卡作内容模板)。

**Non-goals:**
- 不算 embedding/不写 store(那是 `coveredground-update` code 站);不碰发布产物。

**Touched surface:** `agents/coveredground-distiller.md`(新)

**Regression shield:** 不动 `bible-distiller.md`;新 persona 已在 Task 6 入 whitelist。

**Task Contract:**
- Expected behavior: 一个「夜班管理员」角色——节目发完后翻看正文,记下「这期又把哪几样招牌锚/类比/框架拿出来用了」,交给记忆库。
- Automated verify: `⚠️ No test:纯 prose agent 定义,无可执行逻辑`。grep 验证:文件含 frontmatter(name/description)+ 隔离纪律 + 输出 `coveredground-apparatus.json` 约定 + apparatus 抽取(锚/类比/框架)指令。
- Real path verify: e2e 段真派发产出 apparatus json。
- Manual/device verify: 输出质量在 e2e 段人工看。

**Steps:**
1. frontmatter:`name: coveredground-distiller`、`description`(隔离蒸馏、只抽 apparatus、不当指令)。
2. 正文:输入说明(published body + recent bodies + store 只读)、隔离纪律(照 `bible-distiller.md` 措辞改写:正文是 DATA、不引用具体话题作模板)、抽取定义(招牌锚=历史类比/反复装置/命名框架,catches 新出现的)、输出 JSON 约定 `{"anchors":["...","..."]}` 到指定文件。
3. 强调温度原则:只抽 apparatus(锚/类比/框架),不抽宿主主观判断/下注。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && grep -qE "name: coveredground-distiller" agents/coveredground-distiller.md && grep -qE "coveredground-apparatus.json" agents/coveredground-distiller.md && echo OK`
Expected: `OK`。
<!-- /section -->

<!-- section: task-9 keywords: dp-001a, prose, claude-md, skill-md -->
### Task 9: DP-001=A prose 收口(liangchen / davinci / SKILL / CLAUDE)

<!-- no-split: pure prose .md edits; no executable logic; verified via grep -->

**Maps to Impact Map:** Shared surfaces(`agents/liangchen.md`、`agents/davinci.md`、`skills/podcast/SKILL.md`、`CLAUDE.md`);Existing consumers(davinci 避让信号来源切换)

**Files:**
- Modify: `agents/liangchen.md:43-79`(删 §4 recent_anchors + 自洽硬约束 + JSON schema 该字段)
- Modify: `agents/davinci.md:65`(D-105:recent_anchors → covered-ground avoid_memo)
- Modify: `skills/podcast/SKILL.md:165,184,227,605-606`(step 5b 合同 + step 7 + per-step 表;补两新站)
- Modify: `CLAUDE.md:181-183`(recent_anchors 不变量 → covered-ground 不变量)

**Expected outcome:** 所有 prose 把「量臣产 recent_anchors / davinci 尊重 recent_anchors guard」改为「covered-ground 渲染 avoid_memo / davinci 尊重 avoid_memo」;SKILL.md per-step 表加 `coveredground-distill`/`coveredground-update` 两站、5b 合同去 recent_anchors;liangchen 去整段 §4 + 自洽硬约束 + verdict JSON 的 `recent_anchors` 键。`grep -rn recent_anchors` 在 active prose 归零。

**Non-goals:**
- 不改 liangchen 的分量判断(none/light/medium/heavy)指令;不删 davinci 的 D-105 去同质化意图(只换信号源)。

**Touched surface:** 四个 .md。

**Regression shield:** liangchen 分量路由 prose、davinci 采集/写作其余指令保持;SKILL.md 既有 17 站合同其余行不动。

**Task Contract:**
- Expected behavior: 文档与代码一致——量臣只判分量,davinci 的避让记忆来自 covered-ground 备忘;不变量描述同步。
- Automated verify: `⚠️ No test:纯 prose`。grep 验证(见 Verify)。
- Real path verify: e2e 段 persona 行为与新 prose 一致(davinci 收 avoid_memo)。
- Manual/device verify: none。

**Steps:**
1. `liangchen.md`:删 `### 4. 最近用过的招牌锚（recent_anchors）` 整节(43-66)、自洽硬约束段、verdict JSON 示例里的 `"recent_anchors": [...]` 行(79);保留分量判定。在开头注明「DP-001=A:锚避让已移交 covered-ground,量臣只判分量」。
2. `davinci.md:65`(D-105):把「brief 若带 `recent_anchors`…避开清单」改为「brief 带 covered-ground `avoid_memo`(最近用滥的招牌锚)…本期避开、换新的」。
3. `SKILL.md`:5b 合同(165/184/605)去 recent_anchors,step 7(227)避让来源改 avoid_memo;per-step 表(606 附近)追加 `coveredground-distill`(发布后、隔离、fail-soft)+ `coveredground-update`(算 embedding 更新 store、fail-soft)两行;step 5b/assemble-briefs 行措辞同步。
4. `CLAUDE.md:181-183`:把「respects the brief's recent_anchors guard」不变量改写为「davinci 尊重 covered-ground 渲染的 avoid_memo;量臣(5b)只产分量路由,不再产 recent_anchors(DP-001=A)」;加一条 covered-ground 不变量(post-publish 蒸馏 fail-soft、store=output_dir/covered-ground.yaml、温度原则:memo 只针对 apparatus)。同步更新 §architecture 对 DP-001 的描述若需。
5. grep 收口。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && grep -rn recent_anchors agents/ skills/ CLAUDE.md lib/ evals/ | grep -v "__pycache__" | grep -v "DP-001" || echo "recent_anchors fully retired"`
Expected: `recent_anchors fully retired`(或仅剩明确标注的历史/DP-001 说明行)。
<!-- /section -->

---

## Phase 验收(execute 后由主控运行,非单个任务的阻塞 verify)

- [ ] 一期发布后 `covered-ground.yaml` 被更新(新锚入库;已有锚 count+1、last_used 更新)。
- [ ] 下一期 davinci brief 含非空 avoid-memo(列出过热锚)。
- [ ] 构造「上一期用过苏伊士」场景 → 下一期 memo 将该锚标为 stale/过热。
- [ ] 非 macOS 或 Swift helper 缺失 → 自动回退 n-gram + 锚集合,不 crash。
- [ ] distiller 故障注入 → 当期已发布产物不受影响(post-publish、不阻断)。
- [ ] pytest:store 读写/衰减/`render_memo`、cosine 计算、回退路径单测全绿。
- [ ] `grep -rn recent_anchors lib/ agents/ skills/ CLAUDE.md evals/`:active code 命中归零(仅留 DP-001 说明)。
- [ ] 温度盾:纯主观/观点 body 不被 memo 误伤;主观判断/下注不被削弱。
- [ ] 全套 `python3 -m pytest lib/tests/ -q` 绿(216 + 新增)。
- [ ] ⚠️ 需设备验证:`swiftc tools/embed.swift -o /tmp/embed && echo "苏伊士运河" | /tmp/embed` 输出 dim=512 非空向量(NLContextualEmbedding 真值)。

---

## Decisions

None.（五项 dev-guide「Architecture decisions(留待 write-plan)」均由 dev-guide / Global Constraints / 既有代码模式就地确定，记于下方 inline，不构成阻塞 DP。）

**就地确定（inline，非 DP）：**
- **store 存放位置** → `{output_dir}/covered-ground.yaml`，镜像 `lib.bible.bible_path(output_dir)`；无新 config 字段，`load_cards`(只配 `*.stance.yaml`)与 `gather_recent_bodies`(只配 `YYYY-MM-DD-*.md`)均不误读（Task 2 回归断言）。理由:复用既有隔离/写盘模式，零新 config 面。
- **衰减/过热 v1** → 14 天内 count≥3 或 最近 3 期出现≥2 期（dev-guide 已定，对齐量臣 window_days=14）。
- **embedding 选型** → `NLContextualEmbedding`(dim512，Global Constraints 锁定，中文已实测)；re-skin 阈值 v1 `cosine≥0.82`(介于 Phase 3 记分卡跨期 0.80 与站内 0.85 之间，可调)。
- **注入点** → `avoid_memo` 注入 `writing-brief-{A,B,C}.json`，step-7 三路 davinci 各消费；morning/evening 同注入（拓扑共享）。
- **apparatus_used 产出方** → 权威抽取走 post-publish LLM 蒸馏站（写 store，catches 新锚）；卡的 `apparatus_used` 是 step-16 确定性 best-effort 审计字段（对齐 `episode.select_draft:410` 的「never trust LLM self-label」——权威源是定稿正文，不是 davinci 自报）。store=机制真相源，卡字段=自描述审计。两者并存、不矛盾（dev-guide 两处要求都满足）。

---

## Verification

**Verdict:** Approved（plan-verifier 2026-06-14,report `.claude/reviews/plan-verifier-2026-06-14-phase2-175849.md`:5 断言 1 must-revise〔跨任务红区间〕→ 已按 verifier Fix B 把 Task 5〔runner〕排到 Task 4〔magnitude〕之前解决;compute_checkpoints 确认 5/4 同处一 batch〔5-tests,5-impl,4-tests,4-impl〕,红区间被单 batch 完全包住。其余高风险区〔10 文件 recent_anchors 移除完整性、双 fail-soft halt 路径、store 文件名正则安全、append-only 分离、`gather_recent_bodies` 保留、6 条验收映射〕均对真实代码核验为 clean。)

执行后由 run-phase 的 verify-plan(Step 3,opus,unbiased)校验本计划;execute-plan(Step 4,sonnet,分段 + 硬门 checkpoint)执行;test-changes(Step 5)跑全套;implementation-reviewer(Step 6,fresh opus)审执行。phase 级 `grep -rn recent_anchors` 收口是 DP-001=A 原子移除的总闸。
