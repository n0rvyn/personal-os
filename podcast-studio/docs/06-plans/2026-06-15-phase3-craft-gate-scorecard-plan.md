---
type: plan
status: active
contract_version: 2
tags: [craft-gate, scorecard, dedup, structlint, judge, anti-homogenization, prompt-consistency]
refs: [docs/06-plans/2026-06-14-big-track-redesign-dev-guide.md]
---

# Phase 3: 工艺门 + 达标尺 — 质检与记分卡 Implementation Plan

**Goal:** 每次 e2e run 产出一张设计达标记分卡（硬门 + 判官维度）；真实 06-14 早间作回归样本必须判为**不达标**且命中具体缺陷；在源头修掉提示词矛盾（kuaidao 五段残留 / SKILL 段数 / davinci 草稿头·⑤段）。温度原则不回归——dedup/结构门只作用于重复与招牌锚，绝不触碰宿主主观观点/下注。

**Architecture:** 新增三个确定性 lib（`lib/dedup.py` 站内+跨期重复、`lib/structlint.py` 段数/草稿标记/下注段/**念稿真实时长**、`lib/scorecard.py` 组装硬门+判官）+ 一个纯结构判官 persona（`agents/scorecard.md`，克隆 qianzhongshu「无叙事绑定」纪律）。pipeline 新增一站 **13a scorecard**（在 broadcast-rewrite(13) 之后、tts(14) 之前——这是 cleanup(17) 删 scratch 之前、念稿+factcheck-verdict+finalize body+score-verdict 同时在世、covered-ground store 尚未被 step-19 更新的唯一窗口）。记分卡 v1 **advisory**（写 verdict + 人读记分卡，不 halt），生产期 halt 由 `--enforce-scorecard` 开关控制。提示词三处段数矛盾原子修齐。

**Tech Stack:** Python 3.13（`lib/`, pytest）；复用 `lib/embed.py`(站内近似确认)、`lib/coveredground.py`(跨期 `is_stale` 过热谓词)、`lib/factcheck.py`(信息准确轴)、`lib/episode._count_script_chars`(念稿字数)；Claude Code 子代理(headless `claude -p`,`agents/scorecard.md` 作 system prompt,只判 3 个净新维度)。

**Design doc:** none — design captured inline in dev-guide Phase 3 记分卡表 + this plan（per project convention）

**Design analysis:** none

**Crystal file:** none on disk（dev-guide Global Constraints + 温度原则 + Phase-2 实测证据承担 crystal 角色）

**Bug diagnosis:** 回归样本根因已实测（非假设）：06-14 早间 reader `.md`=7131 非空白字（被 5段/草稿头/⑤段/苏伊士×12/17.2万·占GDP 各×2 撑高）通过 18 分钟字数门，但真实**念稿** `broadcast-script-2026-06-14.txt`=5455 字（~14.9 分钟）< 6570 floor。字数门量错对象（量 `.md` 不量念稿）= 短节目漏网根因。Phase 3 结构门改量念稿。

**Threat model:** included（新增 headless `claude -p` 判官派发站 + scratch verdict 写盘；沿用 Phase 1/2 dispatch 威胁模型）

**Pre-flight risks:**
- **嵌入短语级判别弱（Phase-2 实测）**：`cos(1956苏伊士,1973石油)=0.891`（不同锚，不该判重）、`cos(印刷术,活字印刷术)=0.884`（真换皮，该判重）——单一嵌入阈值分不开。**故跨期重复不用嵌入硬门**：用 covered-ground `is_stale` 过热谓词（count-based，Phase-2 证明可用的主信号）做「念稿含过热锚」的只读在场检查。代价：**换皮**（同一观点换词换锚）跨期检不出——**记为 v1 已知局限**（见 Decisions），不静默降级。
- **scorecard 站位置硬约束**：必须在 13(broadcast-rewrite) 之后、17(cleanup `shutil.rmtree(scratch)`) 之前。念稿 `broadcast-script-{date}.txt`、`factcheck-verdict.json`、`finalize-result.json`、`score-verdict.json` 全在 scratch；放到 cleanup 之后会读不到念稿 → 时长门静默退化/跳过（正是 Phase-2 那类静默失败，真跑数小时后才暴露）。已 Read 核实 `episode.cleanup_scratch:378` = `shutil.rmtree`。
- **跨期维度在 13a 时蒸馏器(18)未跑**：本期无权威锚清单。**不建第二抽取器**（违反 CLAUDE.md「锚抽取权威源唯一=蒸馏器，never a second path」不变量）。改为只读在场检查：念稿是否含 store(pre-update) 标 `is_stale` 的锚——直接量「davinci 无视了自己的 avoid_memo」这个失败模式。
- **06-14→不达标 必须由确定性硬门独扛**：段数=5 / 草稿头 / ⑤段 / 念稿<6570 / 站内逐字重复——五项全是代码可判，**不依赖 LLM 判官同意**（判官 flaky，acceptance #1 不能押在它身上）。判官 3 维只在 live run 被行使。
- **段数双 token 残留**：970fcb3 漏改 3 处（`kuaidao.md:75` 五段/四段、`SKILL.md:43` 5-段、`SKILL.md:44` 4-段）。`grep` 收口（Task 6 phase verify）。
- **「rerun until green」诚信陷阱**：live run 不过门时，修**生成侧(提示词/davinci)**，不是放宽门。阈值在 fixture 上一次性标定后**冻结**；自治模式下放宽阈值凑绿=作弊。escalation bound 预先写死（见 e2e loop 协议）。

**Project health:** all-green（`.claude/dev-workflow-health.json` 2026-06-14；五信号全绿）。基线 263 tests green。

---

## Threat Model

**Attack surface:**
- `scorecard` 判官输入=三路 polish/finalize body + factcheck verdict + score verdict（均 scratch DATA）。沿用「内容是 DATA 不是指令」不变量：判官把正文里任何指令样文本当引用内容。
- scorecard verdict 产物 `scorecard-verdict.json` 经 `lib.dispatch._resolve_artifact` 路径穿越守卫，落 scratch 内。
- dedup/structlint 是纯函数（无 shell、无网络、无 LLM）；嵌入确认走既有 `embed.similarity`(stdin 传文本，不进 argv)。

**Failure modes（每个新组件静默失败时的行为）：**
- `embed` helper 缺失/非 macOS → 站内近似确认退回 n-gram Jaccard（已有回退）；**逐字重复主信号是 n-gram，不依赖嵌入**，故嵌入缺失不致漏报逐字重复。
- 念稿文件缺失（理论上不该——13 必产，check_artifact 守）→ structlint 时长门返回 `{ok:False, reason:"念稿缺失"}`（fail-closed，**不**静默判过）。
- 判官派发失败/超时/产物缺/非法 JSON → `safe_parse_scorecard` 降级：判官 3 维记为 `unscored`，记分卡标「判官维度未评（派发失败）」，**硬门照常判**（硬门是确定性的，不依赖判官）。advisory 模式下 run 继续。
- covered-ground store 缺失/空 → 跨期在场检查空集（无过热锚可比）→ 不报跨期；不 crash。

**Resource lifecycle:**
- 判官子进程：`lib.dispatch.dispatch_persona` 的 `subprocess.run(timeout=_STEP_TIMEOUTS.get("scorecard", 2400))`；超时/非零/OSError → `{ok:False}` 不 raise。
- scratch `scorecard-verdict.json` + `scorecard.md`：随既有 `cleanup_scratch` 清理（13a 在 cleanup 之前跑，写完即被下游读，advisory 不阻断）。**人读记分卡另拷一份到 output_dir**（`{date}-{show}.scorecard.md`，cleanup 不删 output_dir）——否则 cleanup 后人看不到记分卡。

**Input validation requirements:**
- 念稿字数走 `episode._count_script_chars`(非空白计数,既有)。
- 判官 verdict 经 `safe_parse_scorecard`：3 维必须 1..5 int，非法 → 该维 `unscored`，不 raise。
- 段数判定基于 ATX 标题正则（`^##\s*[①②③④⑤]` 或既有段标题约定），非自由文本猜测。

---

## Impact Map

**User path:** 听众侧——不再出现重复段落、草稿标记、超短节目（<18 分钟）、一期期复用同一过热锚；段落结构稳定（早 4 / 晚 3）；**观点与温度不被削弱**（温度盾 acceptance #5）。
**Data path:** 〔13 broadcast-rewrite〕念稿 `broadcast-script-{date}.txt` →〔13a scorecard〕：structlint(读 reader body 段数/草稿头/⑤段 + 念稿时长) + dedup(读 reader body 站内逐字/近似 + 念稿∩store 过热锚跨期) + 复用 score-verdict 钱钟书 total + factcheck-verdict 信息准确 + 判官 3 维 → `scorecard-verdict.json`(scratch) + `{date}-{show}.scorecard.md`(output_dir,人读)。advisory：记分但不 halt（`--enforce-scorecard` 时硬门红 → halt）。
**Shared surfaces:** `lib/pipeline.py`(step 表加 13a + whitelist scorecard)、`lib/dispatch.py`(whitelist)、`lib/runner.py`(13a 执行 + `--enforce-scorecard` flag)、`agents/{kuaidao,davinci}.md` + `skills/podcast/SKILL.md`(段数/草稿头/⑤段 prompt 修复)。
**Existing consumers:** runner 主循环(派发+gate)、`lib.tests.test_{pipeline,dispatch,runner}`、`lib.embed.similarity`、`lib.coveredground.is_stale/load_store`、`lib.factcheck.check_factcheck`、`lib.episode._count_script_chars`、`lib.episode.select_draft`(score-verdict 复用)。
**Must remain unchanged:** 温度原则（主观观点/下注不被 dedup/结构门误伤——acceptance #5 温度盾）、Phase-1「缺产物即 halt」核心不变量（13a advisory 是显式 record-only，不改发布前站 halt 语义；`--enforce-scorecard` 仅对硬门、生产期）、蒸馏器(18)是唯一权威锚抽取源（13a 跨期只做只读在场检查，不抽取）、no-TTS 模式、append-only 卡、既有 17→19 站拓扑（13a 是插入，不改既有站序字段）、qianzhongshu 纯结构无绑定、covered-ground store 形态。
**Regression checks:** `python3 -m pytest lib/tests/ -q`(263→更多 green)、`grep -rnE "五段|5-段结构|4-段结构" agents/ skills/`(段数残留归零——用精确错误 token,**不**用 `grep -v 四段` 那种会被「早间五段/晚间四段」整行含「四段」反向漏掉的写法;references 的「4-段事件中心结构」不含「4-段结构」连续串,不误命中)、温度盾 fixture 绿、06-14 fixture 判不达标(确定性硬门)、clean fixture 判绿、既有 pipeline 站序测试绿。

---

<!-- section: task-1-tests keywords: dedup, intra-episode, cross-episode, jaccard -->
### Task 1-tests: dedup 站内+跨期重复检查 — 测试

**Maps to Impact Map:** Data path(站内/跨期重复分);Shared surfaces(新 `lib/dedup.py`);Must remain unchanged(温度盾)

**Files:**
- Create: `lib/tests/test_dedup.py`

**Expected outcome:** `lib/dedup.py` 的站内（逐字重复句/近似重复段）与跨期（念稿含 store 过热锚的只读在场检查）有可在任意平台跑的单测；未实现时 FAIL。

**Non-goals:**
- 不测真实嵌入数值（注入 `similarity_fn` fake）；不测真实 store 加载（直接传 store dict）。

**Touched surface:** `lib/tests/test_dedup.py`(新)

**Regression shield:** 不动既有测试。

**Task Contract:**
- Expected behavior: 系统能指出「这篇稿子里有逐字/近似重复的段落」和「念稿里用了最近用滥的过热锚」；纯主观观点的合理复述**不**被判为重复（温度盾）。
- Automated verify: `python3 -m pytest lib/tests/test_dedup.py -q` —— 先 FAIL(缺 `lib.dedup`)。
- Real path verify: Task 1-impl 后转 PASS;真实跨期在 e2e 段验。
- Manual/device verify: none。

**Steps:**
1. `test_intra_verbatim_repeat_flagged`:body 含两处逐字相同句子（仿 06-14 「17.2万」「占GDP」各×2）→ `check_intra_dup(body)` 的 `ok=False`、`hits` 含重复内容片段、`score`>0。
2. `test_intra_near_dup_paragraph_flagged`:两段近似（注入 `similarity_fn` 返回 ≥0.93）→ 判重；`similarity_fn` 返回 0.5 → 不判重（高 bar 确认）。
3. `test_intra_distinct_paragraphs_clean`:两段真不同主题 → `ok=True`、`hits` 空。
4. `test_intra_jaccard_catches_without_embedding`:不注入 `similarity_fn`（嵌入不可用）→ 逐字重复仍被 n-gram Jaccard(≥0.5)抓到（证明逐字主信号不依赖嵌入）。
5. `test_cross_hot_anchor_presence`:用 **`load_store` 同构的 store dict**(`{"anchors": {"1956苏伊士": {"episodes": [...3 期...], "count": 3, "last_used": "..."}}}`,或经 `coveredground.update_store` 构造)使「1956苏伊士」`is_stale` 为真 + 念稿含「苏伊士」→ `check_cross_dup(script, store, today)` 的 `ok=False`、`hits` 含「苏伊士」。**断言用真 schema,不用手搓 `{"1956苏伊士": True}` 这类简化形(那会让 dict-iter/entry-shape 错误漏网——Phase-2 GAP-2 教训)。**
6. `test_cross_clean_anchor_not_flagged`:store 含某锚但**未过热** → 念稿含它 → 不报（只查过热锚）。
7. `test_cross_no_store_safe`:空 store → `ok=True`、不 crash。
8. `test_temperature_shield_repeated_opinion_not_dup`(温度盾):body 把同一**主观判断/下注**用不同措辞强调两次（非逐字、非招牌锚）→ **不**被判站内重复（dedup 只抓逐字/近似**段落**与**招牌锚**，不抓观点复述）。
9. 跑确认 FAIL。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_dedup.py -q`
Expected: 收集到且全 FAIL(缺模块),无语法错。
<!-- /section -->

<!-- section: task-1-impl keywords: dedup, ngram, hot-anchor -->
### Task 1-impl: dedup 站内+跨期 — 实现

**Depends on:** Task 1-tests

**Maps to Impact Map:** Data path(重复分);Shared surfaces(`lib/dedup.py`)

**Files:**
- Create: `lib/dedup.py`

**Expected outcome:** `lib/dedup.py` 提供 `check_intra_dup(body, *, similarity_fn=None)`、`check_cross_dup(script, store, today)`、`check_dedup(body, script, store, today, *, similarity_fn=None)`(合并 → `{ok, reason, score, hits}`)。Task 1-tests 全 PASS。

**Design approach:**
- **站内**：(a) 逐字/近似句重复——把 body 切句/切段，找 2-gram Jaccard ≥ `INTRA_JACCARD_THRESHOLD=0.5` 的段对/重复句（catches 06-14 逐字复制，**主信号，不依赖嵌入**）；(b) 嵌入高 bar 确认——若 `similarity_fn` 可用，段对 cosine ≥ `INTRA_EMBED_CONFIRM=0.93` 也判重（捕近义换词）。两者并集。
- **跨期**：只读在场检查——**store schema 是 `{"anchors": {name: {episodes, count, last_used, ...}}}`(dict keyed by name)；`is_stale(entry, today)` 吃的是 per-anchor entry dict,不是 name 串**(权威模式照 `coveredground.render_memo:501-506` 的 `for name, entry in store["anchors"].items(): if is_stale(entry, today)`)。`hot = [name for name, entry in store.get("anchors", {}).items() if is_stale(entry, today)]`；`hits = [a for a in hot if a in script]`。**不抽取新锚**（蒸馏器(18)才是权威抽取源）。⚠️ 此处易踩 Phase-2 GAP-2 同类陷阱(单测注入手搓 dict 看不出 schema 错,真 `load_store` 才暴露)——Task 1-tests 第 5 条必须用 `load_store` 同构的 store dict。
- 温度盾：dedup 的判定单位是**段落/句子的字面/近义重叠**与**已知招牌锚**，绝不对「观点是否重复表达」下判（观点复述用不同措辞 → 段落 Jaccard 低、不命中锚 → 不报）。

**Non-goals:**
- 不接 runner/pipeline(Task 4/5);不做跨期换皮检测(v1 局限,见 Decisions)。

**Touched surface:** `lib/dedup.py`(新)

**Regression shield:** 不动 Task 1-tests。

**Task Contract:**
- Expected behavior: 同 Task 1-tests。
- Automated verify: `python3 -m pytest lib/tests/test_dedup.py -q` 全 PASS。
- Real path verify: e2e 段——06-14 fixture 站内逐字重复被抓、念稿过热锚被抓。
- Manual/device verify: none。

**Steps:**
1. `from __future__ import annotations`;常量 `INTRA_JACCARD_THRESHOLD=0.5`、`INTRA_EMBED_CONFIRM=0.93`(命名常量,fixture 标定后冻结)。
2. `_segments(body)`:按空行/ATX 标题切段;`_sentences(seg)`:按句末标点切句。
3. `check_intra_dup(body, *, similarity_fn=None)`:段对/句对 2-gram Jaccard ≥ 0.5 → hit;若 `similarity_fn` 非 None 且段对 cosine ≥ 0.93 → hit;`score`=命中对数 / 总对数 或命中字符占比;`{ok: not hits, reason, score, hits}`。
4. `check_cross_dup(script, store, today)`:`from lib.coveredground import is_stale`;`hot = [name for name, entry in store.get("anchors", {}).items() if is_stale(entry, today)]`(iterate `.items()`——store 是 name→entry dict,`is_stale` 吃 entry 不吃 name);`hits = [a for a in hot if a in script]`;`{ok: not hits, reason, score, hits}`。
5. `check_dedup(...)`:合并两者,`ok = intra.ok and cross.ok`,`hits = intra.hits + cross.hits`,`score` 取重。
6. 跑 Task 1-tests 转 PASS。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_dedup.py -q`
Expected: 全 PASS。
<!-- /section -->

<!-- section: task-2-tests keywords: structlint, sections, draft-header, betting, duration -->
### Task 2-tests: structlint 段数/草稿标记/下注段/念稿时长 — 测试

**Maps to Impact Map:** Data path(结构硬门);Shared surfaces(新 `lib/structlint.py`);Bug diagnosis(念稿时长量错对象修复)

**Files:**
- Create: `lib/tests/test_structlint.py`

**Expected outcome:** structlint 的段数(早4/晚3)、无草稿标记、无独立下注段、念稿真实时长(量念稿 `.txt` 非 `.md`)有单测;未实现时 FAIL。

**Non-goals:**
- 不接 runner;不测嵌入。

**Touched surface:** `lib/tests/test_structlint.py`(新)

**Regression shield:** 不动既有测试。

**Task Contract:**
- Expected behavior: 系统能判「早间是不是 4 段/晚间 3 段、有没有混进草稿头、有没有单列『我下注』段、念出来够不够 18 分钟」——时长量的是**念稿**不是 reader `.md`。
- Automated verify: `python3 -m pytest lib/tests/test_structlint.py -q` —— 先 FAIL(缺 `lib.structlint`)。
- Real path verify: Task 2-impl 后转 PASS;06-14 真 artifact 在 e2e/fixture 段验。
- Manual/device verify: none。

**Steps:**
1. `test_section_count_morning_ok`:4 段 body(`## ① … ## ④`)+ show="morning" → `check_sections` ok;`test_section_count_morning_five_fails`:5 段(含 `## ⑤`) → `ok=False` hit「段数=5 期望 4」。
2. `test_section_count_evening`:3 段 ok;4 段 fail。
3. `test_draft_header_flagged`:body 含 `# 草稿 C — …` → `check_no_draft_marker` `ok=False`;干净 body → ok。
4. `test_betting_section_flagged`:body 含 `## ⑤ 我下注什么`(或任意 `## …我下注` 标题) → `check_no_betting_section` `ok=False`;织入正文的可证伪判断(无独立标题) → ok。
5. `test_duration_measures_script_not_md`:传一个 5455 字的念稿文本 → `check_duration(script_text)` `ok=False`(<6570);传 6570+ → ok。**关键断言**:同一内容的 7131 字 `.md` 传进去会 pass,但 structlint 的时长入口约定吃**念稿文本**——测试用念稿短文本证明它会 fail(防 06-14 量错对象回归)。
6. `test_structlint_all(body, script, show)`:组合 → `{ok, reason, hits}`,06-14 式输入(5段+草稿头+⑤段+短念稿)→ 多 hit。
7. 跑确认 FAIL。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_structlint.py -q`
Expected: 收集到且全 FAIL(缺模块),无语法错。
<!-- /section -->

<!-- section: task-2-impl keywords: structlint, atx, count-script-chars -->
### Task 2-impl: structlint — 实现

**Depends on:** Task 2-tests

**Maps to Impact Map:** Data path(结构硬门);Shared surfaces(`lib/structlint.py`)

**Files:**
- Create: `lib/structlint.py`

**Expected outcome:** `lib/structlint.py` 提供 `check_sections(body, show)`、`check_no_draft_marker(body)`、`check_no_betting_section(body)`、`check_duration(script_text)`、`check_structlint(body, script_text, show)`(合并 → `{ok, reason, hits}`)。Task 2-tests 全 PASS。

**Design approach:**
- 常量 `SECTION_COUNT={"morning":4,"evening":3}`;**从语速派生**避免 magic number:`SPOKEN_CHARS_PER_MIN=365`、`MIN_BROADCAST_MINUTES=18` → `BROADCAST_MIN_CHARS = SPOKEN_CHARS_PER_MIN * MIN_BROADCAST_MINUTES`(=6570;命名常量,fixture 标定后冻结)。⚠️ **基础层一致性**:既有 `episode._FLOOR_CHARS_BY_SHOW`(=6500)是 **reader body 草稿下限**(gate finalize body),与本处 **念稿 18 分钟产品门**(gate broadcast .txt)是**不同对象不同用途**——念稿门(6570)> body 草稿门(6500)是有意的(产品时长门按真实念稿,严于草稿下限)。在 structlint 顶部加注释交代二者区别,防被误当漂移去「统一」。
- 段数:ATX 段标题正则计数(`^##\s*[①②③④⑤]` 优先;兜底既有段标题模式)。
- 草稿标记:`^#\s*草稿` 或 「草稿 [ABC]」H1 正则。
- 下注段:`^##\s*.*我下注` 标题正则(只禁**独立标题段**,不碰织入正文的判断)。
- 时长:复用 `from lib.episode import _count_script_chars`;**入口参数名 `script_text` 明确是念稿**,文档串写明「量念稿不量 reader .md(06-14 量错对象根因)」。

**Non-goals:**
- 不改 `episode.check_min_chars`(它对 finalize body 的 floor 仍正确——那是 reader 稿下限,与念稿时长是两件事);structlint 是**新增**念稿时长门,不替换既有门。

**Touched surface:** `lib/structlint.py`(新)

**Regression shield:** 不动 Task 2-tests;不改 `lib/episode.py`。

**Task Contract:**
- Expected behavior: 同 Task 2-tests。
- Automated verify: `python3 -m pytest lib/tests/test_structlint.py -q` 全 PASS。
- Real path verify: e2e/fixture 段——06-14 reader .md 判 5段/草稿头/⑤段,06-14 念稿判 <18min。
- Manual/device verify: none。

**Steps:**
1. 常量 + `from lib.episode import _count_script_chars`。
2. `check_sections(body, show)`:数 ATX 段标题;`== SECTION_COUNT[show]` → ok,否则 hit。
3. `check_no_draft_marker(body)` / `check_no_betting_section(body)`:正则,命中 → hit。
4. `check_duration(script_text)`:`n=_count_script_chars(script_text)`;`n >= BROADCAST_MIN_CHARS` → ok,否则 hit「念稿 {n} 字 < {floor}(~{n/365:.1f} 分钟)」。
5. `check_structlint(body, script_text, show)`:合并四查 → `{ok, reason, hits}`。
6. 跑 Task 2-tests 转 PASS。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_structlint.py -q`
Expected: 全 PASS。
<!-- /section -->

<!-- section: task-3-tests keywords: scorecard, assemble, judge, hard-gates -->
### Task 3-tests: scorecard 组装(硬门+判官维度)— 测试

**Maps to Impact Map:** Data path(记分卡组装);Shared surfaces(新 `lib/scorecard.py`);Must remain unchanged(钱钟书 total 复用、判官无绑定)

**Files:**
- Create: `lib/tests/test_scorecard.py`

**Expected outcome:** `lib/scorecard.py` 把硬门(structlint+dedup+必产 artifact)与判官维度(复用 score-verdict 钱钟书 total + factcheck 信息准确 + 新判官 3 维)组装成记分卡;判官 verdict 解析 fail-soft;06-14 式输入由**确定性硬门**独判不达标(不需判官)。未实现时 FAIL。

**Non-goals:**
- 不派真实判官 LLM(注入 judge verdict dict);不接 runner(Task 5)。

**Touched surface:** `lib/tests/test_scorecard.py`(新)

**Regression shield:** 不动既有测试。

**Task Contract:**
- Expected behavior: 一张记分卡:硬门(段数/草稿头/下注段/念稿时长/必产 artifact/站内重复)逐条判 + 跨期过热锚 + 钱钟书 total≥14 + 信息准确(过 factcheck)+ 判官 3 维(有观点/有温度/不同质化 各≥3);任一硬门红 → 整卡不达标。
- Automated verify: `python3 -m pytest lib/tests/test_scorecard.py -q` —— 先 FAIL(缺 `lib.scorecard`)。
- Real path verify: Task 7 fixture 段 + e2e 段。
- Manual/device verify: none。

**Steps:**
1. `test_06_14_fails_on_hard_gates_alone`:传 06-14 式(5段+草稿头+⑤段+短念稿+逐字重复)+ **judge verdict=None**(模拟判官未跑/失败)→ `build_scorecard(...)` 的 `passed=False`,`hard_gates` 命中:段数/草稿头/下注段/念稿时长/站内重复。**断言不达标不依赖判官**。
2. `test_clean_input_passes`:干净 body(4段/无草稿头/无下注段)+ 念稿≥6570 + score-verdict total=15 + factcheck ok + judge 3 维各=4 → `passed=True`。
3. `test_qianzhongshu_total_reused_not_rejudged`:`build_scorecard` 从传入的 score-verdict 读 `total`(≥14 → ok),**不**重新判钱钟书;total=12 → 该维红。
4. `test_factcheck_axis_from_verdict`:factcheck verdict `ok=False` → 信息准确轴红。
5. `test_judge_failsoft`:judge verdict 非法/缺维 → `safe_parse_scorecard` 把该维记 `unscored`,记分卡可渲染,硬门仍判(advisory 不崩)。
6. `test_scorecard_md_renders`:`render_scorecard_md(result)` 产出人读 markdown(硬门表 + 判官表 + 总判)。
7. 跑确认 FAIL。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_scorecard.py -q`
Expected: 收集到且全 FAIL(缺模块),无语法错。
<!-- /section -->

<!-- section: task-3-impl keywords: scorecard, build, render, safe-parse -->
### Task 3-impl: scorecard 组装 + scorecard 判官 persona — 实现

**Depends on:** Task 1-impl, Task 2-impl, Task 3-tests

**Maps to Impact Map:** Data path(记分卡);Shared surfaces(`lib/scorecard.py`、`agents/scorecard.md`)

**Files:**
- Create: `lib/scorecard.py`
- Create: `agents/scorecard.md`

**Expected outcome:** `lib/scorecard.py` 提供 `build_scorecard(body, script_text, show, *, score_verdict, factcheck_verdict, store, today, judge_verdict)` → `{passed, hard_gates:[...], judge_dims:[...], reason}`、`safe_parse_scorecard(raw)`(判官 3 维 fail-soft)、`render_scorecard_md(result)`。`agents/scorecard.md` 是纯结构判官(无叙事/speakAs 绑定,克隆 qianzhongshu 纪律),**只判 3 个净新维度**(有观点/有温度/不同质化),输出紧凑 JSON。Task 3-tests 全 PASS。

**Design approach:**
- 硬门(确定性)= `structlint.check_structlint` + `dedup.check_intra_dup` + 必产 artifact 在场 + `dedup.check_cross_dup`(过热锚)。任一红 → `passed=False`(advisory 仍渲染全卡)。
- 判官维度 = 复用入参 `score_verdict` 的钱钟书 `total`(≥`QZS_TOTAL_FLOOR=14`)+ `factcheck_verdict.ok`(信息准确)+ `judge_verdict` 的 3 维(`有观点`/`有温度`/`不同质化` 各 ≥ `JUDGE_DIM_FLOOR=3`)。**不重判钱钟书、不重跑 factcheck**(省 live-run 一个长 MiniMax 站)。
- `agents/scorecard.md`:输入=定稿正文 + 跨期过热锚清单(由 runner 注入,只读) → 只判 3 维,输出 `{"有观点":N,"有温度":N,"不同质化":N,"notes":"…"}`。紧凑 prompt = 短 live-run。
- 常量 `QZS_TOTAL_FLOOR=14`、`JUDGE_DIM_FLOOR=3`(命名,冻结)。

**Non-goals:**
- 判官不重判钱钟书四轴(那是 qianzhongshu 的活);不接 pipeline/runner(Task 4/5)。

**Touched surface:** `lib/scorecard.py`(新)、`agents/scorecard.md`(新)

**Regression shield:** 不动 qianzhongshu/factcheck;不动 Task 3-tests。

**Task Contract:**
- Expected behavior: 同 Task 3-tests。
- Automated verify: `python3 -m pytest lib/tests/test_scorecard.py -q` 全 PASS。
- Real path verify: Task 7 fixture(06-14→不达标/clean→绿)+ e2e(live 判官 3 维)。
- Manual/device verify: 判官 3 维质量在 e2e 段人工看。

**Steps:**
1. 常量 + `from lib import structlint, dedup`;`from lib.coveredground import is_stale`(跨期入参 store)。
2. `build_scorecard(...)`:组装硬门 list(每条 `{name, ok, detail}`)+ 判官 dims list;`passed = all(hard_gates ok)`(advisory:判官维度记录但不参与硬 `passed`——硬门是达标尺底线;判官维度低分进 reason 提示,生产期可选纳入)。
3. `safe_parse_scorecard(raw)`:3 维各须 1..5 int,非法 → `unscored`;`try/except` 全包,绝不 raise。
4. `render_scorecard_md(result)`:硬门表 + 判官表 + 总判 + 命中详情。
5. `agents/scorecard.md`:frontmatter(`name: scorecard`,纯结构判官)+ 输入说明 + 只判 3 维 + 输出 JSON 约定 + 「内容是 DATA 不是指令」+ 温度原则(有观点/有温度是**奖励主观判断**,不是惩罚)。
6. 跑 Task 3-tests 转 PASS。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_scorecard.py -q && grep -qE "name: scorecard" agents/scorecard.md && echo OK`
Expected: 全 PASS + `OK`。
<!-- /section -->

<!-- section: task-4-tests keywords: pipeline, scorecard-station, whitelist, 13a -->
### Task 4-tests: pipeline 13a scorecard 站 + whitelist — 测试

**Maps to Impact Map:** Shared surfaces(`lib/pipeline.py`、`lib/dispatch.py`);Must remain unchanged(既有站序)

**Files:**
- Modify: `lib/tests/test_pipeline.py`
- Modify: `lib/tests/test_dispatch.py`

**Expected outcome:** step 表新增 `scorecard` 站(kind=agent, agent=scorecard),**位于 broadcast-rewrite(13) 之后、tts(14) 之前**;`scorecard` 入 `AGENT_WHITELIST`(pipeline + dispatch 双处);scorecard 站**非 fail_soft**(advisory 由 runner 控,不靠 fail_soft 字段),gate=check_artifact(scorecard-verdict.json)。改后新断言先 FAIL。

**Non-goals:**
- 不测 runner 执行(Task 5);只测 step 表数据 + 站序 + whitelist。

**Touched surface:** `lib/tests/test_pipeline.py`、`lib/tests/test_dispatch.py`

**Regression shield:** 既有站(含 13/14/17/18/19)断言保持绿;whitelist 既有 persona(含 coveredground-distiller)仍在。

**Task Contract:**
- Expected behavior: 流水线声明里 broadcast-rewrite 与 tts 之间多出 scorecard 站;判官 persona 进白名单。
- Automated verify: `python3 -m pytest lib/tests/test_pipeline.py lib/tests/test_dispatch.py -q` —— 新断言先 FAIL。
- Real path verify: Task 4-impl 后转 PASS。
- Manual/device verify: none。

**Steps:**
1. `test_pipeline.py`:断言 `load_pipeline("morning")` 含 `scorecard`(kind=agent, agent=scorecard, artifact="scorecard-verdict.json"),且其 index 在 `broadcast-rewrite` 之后、`tts` 之前。
2. 断言 scorecard 站 `fail_soft` 为 None/False(advisory 不靠 fail_soft——硬门红时要能 halt〔enforce 模式〕,fail_soft=True 会吞掉 halt)。
3. 断言 `AGENT_WHITELIST`(pipeline)含 `scorecard`。
4. `test_dispatch.py`:断言 `dispatch.AGENT_WHITELIST` 含 `scorecard`。
5. 确认新断言 FAIL。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_pipeline.py lib/tests/test_dispatch.py -q`
Expected: 新断言 FAIL,既有 PASS。
<!-- /section -->

<!-- section: task-4-impl keywords: pipeline, 13a, whitelist -->
### Task 4-impl: pipeline 13a scorecard 站 + whitelist — 实现

**Depends on:** Task 3-impl, Task 4-tests

**Maps to Impact Map:** Shared surfaces(`lib/pipeline.py`、`lib/dispatch.py`)

**Files:**
- Modify: `lib/pipeline.py`(AGENT_WHITELIST + `_build_steps` 插入 13a + docstring 站序表)
- Modify: `lib/dispatch.py`(AGENT_WHITELIST)

**Expected outcome:** `_build_steps` 在 broadcast-rewrite 之后、tts 之前插入 `scorecard` 站(`kind=agent, agent=scorecard, inputs=[finalize-result.json, broadcast-script-{date}.txt, factcheck-verdict.json, score-verdict.json], artifact="scorecard-verdict.json", gate=[{"fn":"check_artifact"}], fail_soft=None`);两处 whitelist 加 `scorecard`;docstring 站序表加该站。导入期 `validate_pipeline(_build_steps())` 不报错。Task 4-tests 全 PASS。

**Non-goals:**
- 不写 runner 执行逻辑(Task 5);此处只是数据声明。

**Touched surface:** `lib/pipeline.py`、`lib/dispatch.py`

**Regression shield:** 既有站字段/序不变(只插入一站 + 该站 inputs 引用既有 artifact);whitelist 不删项。不动 Task 4-tests。

**Task Contract:**
- Expected behavior: 同 Task 4-tests。
- Automated verify: `python3 -c "import lib.pipeline" && python3 -m pytest lib/tests/test_pipeline.py lib/tests/test_dispatch.py -q` 全 PASS。
- Real path verify: e2e 段 runner 走该站。
- Manual/device verify: none。

**Steps:**
1. 两处 `AGENT_WHITELIST` 加 `"scorecard"`。
2. `_build_steps`:在 broadcast-rewrite step 之后、tts step 之前插入 scorecard step dict(全 10 字段,`fail_soft: None`)。
3. docstring 站序表(pipeline.py 顶部)加一行 `13a scorecard agent=scorecard — scorecard-verdict.json`。
4. `python3 -c "import lib.pipeline"` 确认导入期校验不报错。
5. 跑 Task 4-tests 转 PASS。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -c "import lib.pipeline" && python3 -m pytest lib/tests/test_pipeline.py lib/tests/test_dispatch.py -q`
Expected: 导入无错;测试全 PASS。
<!-- /section -->

<!-- section: task-5-tests keywords: runner, scorecard-execution, advisory, enforce-flag -->
### Task 5-tests: runner 执行 13a scorecard(advisory + enforce flag)— 测试

**Maps to Impact Map:** Data path(scorecard 产出);Shared surfaces(`lib/runner.py`);Must remain unchanged(advisory 不改发布前 halt 语义)

**Files:**
- Modify: `lib/tests/test_runner.py`

**Expected outcome:** runner 在 scorecard 站组装入参(念稿/finalize body/factcheck-verdict/score-verdict/pre-update store)→ 派判官 → `build_scorecard` → 写 `scorecard-verdict.json`(scratch) + `{date}-{show}.scorecard.md`(output_dir);**advisory**:硬门红时 run 仍 `status:ok`(记分但不 halt);`--enforce-scorecard` 时硬门红 → halt。判官派发失败 → 判官维度 unscored,硬门照判,advisory 不崩。改后新断言先 FAIL。

**Non-goals:**
- 不测真实 claude -p(注入 dispatch fake);不测真实嵌入。

**Touched surface:** `lib/tests/test_runner.py`(增 scorecard 执行测)

**Regression shield:** 既有 halt-on-missing(发布前站)、no-tts skip、resume、并行、avoid_memo、fail-soft distiller 测试保持绿。

**Task Contract:**
- Expected behavior: 每次 run 产一张记分卡(scratch verdict + output_dir 人读);默认 advisory(记分不挡发布),开 enforce 才在硬门红时停线。
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q -k "scorecard or enforce or advisory"` —— 新断言先 FAIL。
- Real path verify: Task 7 fixture + e2e 段。
- Manual/device verify: none。

**Steps:**
1. `test_scorecard_station_writes_verdict_and_md`:正常 run(no_tts,注入判官 fake 返回 3 维)→ scratch `scorecard-verdict.json` 在、output_dir `{date}-{show}.scorecard.md` 在。
2. `test_scorecard_advisory_does_not_halt_on_red`:注入使硬门红(短念稿)→ `run_pipeline(no_tts=True)` 仍 `status:ok`、发布产物在、记分卡标不达标。
3. `test_scorecard_enforce_halts_on_red`:`run_pipeline(no_tts=True, enforce_scorecard=True)` + 硬门红 → halt 报 `scorecard`。
4. `test_scorecard_reads_preupdate_store`:store 含过热锚 + 念稿含它 → 记分卡跨期命中(证明读的是 step-19 之前的 store)。
5. `test_scorecard_judge_failure_advisory`:判官 dispatch fake 返回 `{ok:False}` → run `status:ok`、记分卡判官维度 unscored、硬门仍判。
6. 确认新断言 FAIL。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_runner.py -q -k "scorecard or enforce or advisory or halt"`
Expected: 新断言 FAIL,既有 halt 测试 PASS。
<!-- /section -->

<!-- section: task-5-impl keywords: runner, scorecard-station, build-prompt, enforce -->
### Task 5-impl: runner 13a scorecard 执行 — 实现

**Depends on:** Task 3-impl, Task 4-impl, Task 5-tests

**Maps to Impact Map:** Data path(scorecard);Shared surfaces(`lib/runner.py`)

**Files:**
- Modify: `lib/runner.py`(`_execute_step`/`_run_code_step` 或新 `_scorecard_step` 分支;`_build_step_prompt` 的 scorecard 判官 prompt;`run_pipeline` + `__main__` 加 `enforce_scorecard`)

**Expected outcome:** scorecard 站:派 `scorecard` 判官(prompt=定稿正文 + 跨期过热锚清单,只判 3 维)→ `safe_parse_scorecard` → `build_scorecard`(组装硬门:从 scratch 读念稿/finalize body/factcheck-verdict/score-verdict;跨期读 `load_store(output_dir)` 的 pre-update store)→ 写 `scorecard-verdict.json`(scratch)+ `render_scorecard_md` 拷到 `output_dir/{date}-{show}.scorecard.md`。default advisory(硬门红记分不 halt);`enforce_scorecard=True` 时硬门红 → halt。Task 5-tests 全 PASS。

**Design approach:**
- 跨期在 13a 用 pre-update store(step-19 尚未跑)——正确:与 davinci 写稿时 avoid_memo 同源,量「是否无视了 avoid_memo」。
- advisory:scorecard 站**非 fail_soft**(fail_soft 会吞 halt);advisory 由专门逻辑实现——硬门红时,默认记 `scorecard-verdict.json` 标不达标但返回 ok 继续;`enforce_scorecard` 时返回 halt。判官派发失败单独 fail-soft(判官维度 unscored,硬门照判)。

**Non-goals:**
- 不改发布前站 halt 语义;不让 scorecard 影响 stance/distiller 站。

**Touched surface:** `lib/runner.py`

**Regression shield:** 既有 halt/no-tts/resume/并行/avoid_memo/distiller 测试保持绿;不动 Task 5-tests。

**Task Contract:**
- Expected behavior: 同 Task 5-tests。
- Automated verify: `python3 -m pytest lib/tests/test_runner.py -q` 全 PASS。
- Real path verify: e2e 段 live 判官 3 维 + 记分卡落 output_dir。
- Manual/device verify: 记分卡内容人工看。

**Steps:**
1. `run_pipeline(..., enforce_scorecard: bool=False)` + `__main__` 加 `--enforce-scorecard` flag;并入 ctx。
2. scorecard 站执行分支:resolve 念稿 `broadcast-script-{date}.txt`、`finalize-result.json`(`load_finalize_body`)、`factcheck-verdict.json`、`score-verdict.json`(scratch);`store=load_store(output_dir)`。
3. 派 `scorecard` 判官(`_build_step_prompt`:正文 + 过热锚清单 `[name for name, entry in store.get("anchors", {}).items() if is_stale(entry, date)]`〔iterate `.items()`,`is_stale` 吃 entry〕,只判 3 维)→ `safe_parse_scorecard`。
4. `result=build_scorecard(body, script_text, show, score_verdict=…, factcheck_verdict=…, store=…, today=date, judge_verdict=…)`;写 `scorecard-verdict.json`;`render_scorecard_md` 写 `output_dir/{date}-{show}.scorecard.md`。
5. `passed` 为 False:`enforce_scorecard` → return halt(`failed_step="scorecard"`);否则 print 警告 + return ok(advisory)。判官 dispatch 失败 → 判官维度 unscored,硬门照判,advisory ok。
6. 跑全 runner 测试转 PASS。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_runner.py -q`
Expected: 全 PASS。
<!-- /section -->

<!-- section: task-6 keywords: prompt-fix, kuaidao, skill-md, davinci, section-count -->
### Task 6: 提示词一致性修复(kuaidao 五段 / SKILL 段数 / davinci 草稿头·⑤段)

<!-- no-split: pure prose .md edits; no executable logic; verified via grep + Task 7 structlint fixture -->

**Maps to Impact Map:** Shared surfaces(`agents/kuaidao.md`、`skills/podcast/SKILL.md`、`agents/davinci.md`)

**Files:**
- Modify: `agents/kuaidao.md:75`(五段/四段 → 四段/三段、无下注段)+ `:69,:77`(防缩水规则豁免删逐字重复)
- Modify: `skills/podcast/SKILL.md:43`(5-段 → 4 段)+ `:44`(4-段 → 3 段)
- Modify: `agents/davinci.md`(写稿阶段加显式:草稿不写 `# 草稿 X` H1、不写独立 `## ⑤ 我下注` 段、早 4 段/晚 3 段、判断织入正文)

**Expected outcome:** 所有段数表述统一为早 4 / 晚 3、无独立下注段;kuaidao 防缩水规则明确「删逐字/近似重复不算缩水」;davinci 草稿显式禁草稿头 H1 与 ⑤段。`grep` 段数残留归零。这是 belt-and-suspenders——structlint 硬门(Task 2)是真正的安全网,prompt 修复防 LLM 习惯性复发。

**Non-goals:**
- 不改 kuaidao/davinci 的创意/质量指令(只修段数矛盾 + 防缩水豁免 + 草稿头禁令);不动 references/{morning,evening}.md(已是 4/3 段,Phase-1 清过)。

**Touched surface:** 三个 .md。

**Regression shield:** 三文件其余指令保持;references 不动。

**Task Contract:**
- Expected behavior: 文档段数口径自洽(早4/晚3);删重复不被防缩水规则反向保护;草稿不再吐草稿头/⑤段。
- Automated verify: `⚠️ No test:纯 prose`。grep 验证(见 Verify)+ Task 7 structlint fixture 是行为层安全网。
- Real path verify: e2e 段 live davinci 草稿无草稿头/⑤段、4 段(structlint 绿)。
- Manual/device verify: none。

**Steps:**
1. `kuaidao.md:75`:`保留早间五段 / 晚间四段结构` → `保留早间四段 / 晚间三段结构,可证伪判断织入正文、不单列「我下注」格式段`(对齐同文件 67/68 行)。
2. `kuaidao.md:69,77`(防缩水):加一句豁免「**删除逐字/近似重复段不算缩水**——重复内容删掉后无需在别处补回;防缩水只针对删**实质内容**(观点/洞察/张力),不保护重复」。
3. `SKILL.md:43`:`(event-centric, 5-段结构)` → `(event-centric, 4 段结构)`;`:44`:`(随笔中心, 4-段结构;` → `(随笔中心, 3 段结构;`。
4. `davinci.md` 写稿阶段(60-66 附近):加显式约束「草稿正文**不写** `# 草稿 X` 之类 H1 标题、**不写**独立 `## ⑤ 我下注` 段;早间 4 段(①②③④)/晚间 3 段(①②③),可证伪判断织入 ③/④(早)或 ②/③(晚)正文」。
5. grep 收口。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && grep -rnE "五段|5-段结构|4-段结构" agents/kuaidao.md skills/podcast/SKILL.md && echo "STILL HAS WRONG COUNT" || echo "section-count consistent"`
Expected: `section-count consistent`。
<!-- /section -->

<!-- section: task-7-tests keywords: fixtures, regression, 06-14, temperature-shield, integration -->
### Task 7-tests: 回归 fixtures + 集成测试(06-14→不达标 / clean→绿 / 温度盾)— 测试

**Maps to Impact Map:** Data path(记分卡端到端);Must remain unchanged(温度盾 acceptance #5)

**Files:**
- Create: `lib/tests/fixtures/2026-06-14-morning-regression.md`(vendored 06-14 reader .md)
- Create: `lib/tests/fixtures/2026-06-14-morning-broadcast.txt`(vendored 06-14 念稿,5455字)
- Create: `lib/tests/fixtures/clean-morning.md` + `clean-morning-broadcast.txt`(合成干净样本)
- Create: `lib/tests/fixtures/temperature-shield.md` + `temperature-shield-broadcast.txt`(重复主观立场 + 织入判断)
- Create: `lib/tests/test_scorecard_integration.py`

**Expected outcome:** 三个 fixture 喂进 `build_scorecard`:06-14 → 不达标且命中(段数=5/草稿头/⑤段/念稿<18min/站内逐字重复/苏伊士过热);clean → 绿;温度盾 → 绿(重复主观立场 + 织入可证伪判断**不**被 dedup/结构门误伤)。未实现 fixture/集成测试时 FAIL。

**Non-goals:**
- 不依赖仓库外绝对路径(fixture 拷进 `lib/tests/fixtures/`,违反「no absolute path」会被 reviewer 抓)。

**Touched surface:** `lib/tests/fixtures/*`、`lib/tests/test_scorecard_integration.py`(新)

**Regression shield:** 不动既有测试。

**Task Contract:**
- Expected behavior: 真实 06-14 早间被记分卡判不达标且点名缺陷;干净稿全绿;一篇「立场强、判断硬、还重复强调了观点」的稿子全绿(温度不被误伤)。
- Automated verify: `python3 -m pytest lib/tests/test_scorecard_integration.py -q` —— 先 FAIL(缺 fixture/scorecard)。
- Real path verify: live e2e(生成稿过记分卡)在 e2e 段。
- Manual/device verify: none。

**Steps:**
1. Vendor:`cp` 06-14 reader .md → `fixtures/2026-06-14-morning-regression.md`;06-14 念稿 `.scratch-2026-06-14-morning/broadcast-script-2026-06-14.txt` → `fixtures/2026-06-14-morning-broadcast.txt`(仓库内,无外部路径)。
2. 合成 `clean-morning.md`(4 段 ①②③④、无草稿头、无 ⑤段、织入一条可证伪判断)+ `clean-morning-broadcast.txt`(≥6570 字念稿)。
3. 合成 `temperature-shield.md`:4 段、把**同一主观下注**用两种措辞在 ③④ 各强调一次(非逐字段、非招牌锚)、含织入可证伪判断;念稿 ≥6570。
4. `test_06_14_regression_fails`:读 06-14 fixture(.md + 念稿)→ `build_scorecard`(score-verdict total 给个真实值、factcheck 给 ok、judge=None 模拟判官缺)→ `passed=False`,`hard_gates` 命中 段数/草稿头/下注段/念稿时长/站内重复;断言**不达标由硬门独扛**(judge=None 也不达标)。
5. `test_clean_fixture_passes`:clean fixture + judge 3 维各 4 → `passed=True`、hard_gates 全绿。
6. `test_temperature_shield_passes`:温度盾 fixture → `passed=True`(dedup 不把重复观点判重、结构门不误伤)。**这是 acceptance #5。**
7. 确认 FAIL。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_scorecard_integration.py -q`
Expected: 收集到且全 FAIL(缺 fixture/模块)。
<!-- /section -->

<!-- section: task-7-impl keywords: fixtures, integration, green -->
### Task 7-impl: fixtures 落地 + 集成测试转绿 — 实现

**Depends on:** Task 1-impl, Task 2-impl, Task 3-impl, Task 7-tests

**Maps to Impact Map:** Data path(端到端);Must remain unchanged(温度盾)

**Files:**
- (fixtures + test 已在 Task 7-tests 创建;本任务确保 scorecard 行为让三测转绿,必要时微调合成 fixture 内容使 clean/温度盾真绿、06-14 真红)

**Expected outcome:** Task 7-tests 三测全 PASS:06-14→不达标(硬门独扛)、clean→绿、温度盾→绿。若某测未如期,**修 scorecard/dedup/structlint 逻辑或 fixture 内容**(不放宽阈值凑绿——阈值 fixture 标定后冻结)。

**Design approach:** 标定时刻——这是阈值/正则与真实样本对齐的唯一窗口。06-14 真红、clean 真绿、温度盾真绿三者同时成立,即标定完成,常量冻结。此后 live run 不过门一律修生成侧,不再动门。

**Non-goals:**
- 不为凑绿放宽 `INTRA_JACCARD_THRESHOLD`/`BROADCAST_MIN_CHARS`/`QZS_TOTAL_FLOOR`(诚信:放宽=作弊)。

**Touched surface:** 可能微调 `lib/dedup.py`/`lib/structlint.py`/`lib/scorecard.py` 正则边界 + fixture 文本。

**Regression shield:** 三个 lib 的 Task 1/2/3 单测保持绿;不动 Task 7-tests 断言。

**Task Contract:**
- Expected behavior: 同 Task 7-tests。
- Automated verify: `python3 -m pytest lib/tests/test_scorecard_integration.py lib/tests/ -q` 全 PASS。
- Real path verify: e2e 段。
- Manual/device verify: none。

**Steps:**
1. 跑 Task 7-tests;06-14 未判红 → 查哪条硬门正则没命中真实 06-14 文本(它的 ATX 标题/草稿头格式),修正则使其命中**真实样本**(不是放宽)。
2. clean/温度盾未判绿 → 查是否误伤;修 dedup/structlint 边界使观点复述/织入判断不命中。
3. 全套 `python3 -m pytest lib/tests/ -q` 确认无回归。

**Verify:**
Run: `cd /Users/norvyn/Code/Skills/personal-os/podcast-studio && python3 -m pytest lib/tests/test_scorecard_integration.py -q && python3 -m pytest lib/tests/ -q`
Expected: 集成三测 PASS;全套 green(263 + 新增)。
<!-- /section -->

---

## Phase 验收(execute 后由主控运行,非单个任务的阻塞 verify)

- [ ] 真实 06-14 早间喂进记分卡 → 判定**不达标**,具体命中:逐字重复段、草稿头、⑤段、时长不足(念稿 5455<6570)、苏伊士过度复用。【确定性硬门独扛,不依赖判官】
- [ ] 一份干净 fixture → 记分卡全绿通过。
- [ ] kuaidao/davinci/SKILL 提示词改后,**新生成 morning 为 4 段、无草稿头、无 ⑤段**(structlint 通过)。【live e2e 验】
- [ ] dedup 检出 06-14 的 17.2万/占GDP 逐字重复。
- [ ] **温度回归盾:** 纯主观/观点 body(重复立场 + 织入判断)不被 dedup/结构门误伤 → 记分卡绿。
- [ ] pytest:dedup(站内/跨期/回退)、structlint、scorecard 组装、集成 三测 单测全绿。
- [ ] 全套 `python3 -m pytest lib/tests/ -q` 绿(263 + 新增)。
- [ ] 段数残留收口:`grep -rnE "五段|5-段结构|4-段结构" agents/ skills/` 归零。
- [ ] **REAL-green no-TTS e2e(用户硬门):** live `claude -p`/MiniMax 生成一期 → 记分卡硬门全绿、judge 3 维 ≥3。MiniMax 超时用 `--resume` 磨。

---

## Decisions

None.（dev-guide Phase 3「Architecture decisions(留待 write-plan)」四项 + 阈值问题,均由 dev-guide 记分卡表 / 温度原则 / Phase-2 实测证据就地确定,记于下方 inline,不构成阻塞 DP——用户已授权自治 /loop 自定节奏。）

**就地确定（inline,非 DP,evidence-grounded）：**
- **dedup 阈值（dev-guide 标 v1 可调）** → 站内:n-gram Jaccard 主信号 `INTRA_JACCARD_THRESHOLD=0.5`(抓逐字/近似复制,**不依赖嵌入**),嵌入仅高 bar 确认 `INTRA_EMBED_CONFIRM=0.93`(对齐 Phase-2 reskin 阈值)。跨期:**不用嵌入硬门**,改 covered-ground `is_stale` 过热锚的只读在场检查(count-based,Phase-2 证明可用的主信号)。**放弃 dev-guide 表的「跨期<0.80 / 站内<0.85 嵌入硬门」**——Phase-2 实测 `cos(苏伊士,石油)=0.891`(不同锚)证明短语级嵌入判别弱、0.80 会误报。阈值在 Task 7 fixture 上标定后**冻结**。
- **⚠️ v1 已知局限（不静默降级,显式记录）**:放弃嵌入跨期硬门 → **换皮重复**(同一观点换词换新锚)跨期检不出。当前覆盖:逐字/近似(站内 Jaccard)、过热锚复用(跨期 is_stale)。换皮检测留待后续(需更强的段级语义判别,NLContextualEmbedding 短语级不够)。
- **记分卡判官维度由谁出** → 复用 qianzhongshu 钱钟书 `total`(≥14)+ factcheck(信息准确),新判官 `agents/scorecard.md` **只判 3 个净新维度**(有观点/有温度/不同质化)。理由:不重判 = 省 live-run 一个长 MiniMax 站、判官 prompt 紧凑。
- **硬门不达标时 halt vs 记分** → v1 **advisory(记分不 halt)**,迭代期能看见记分卡;生产期 halt 由 `--enforce-scorecard` flag 控。理由:迭代要观测,生产要拦截,一个 flag 两用。
- **口播字/分钟率** → 365 非空白字/分(既有语速实测约定,kuaidao.md:45 / SKILL 已用);18 分钟 floor = `BROADCAST_MIN_CHARS=6570`。
- **scorecard 站位置** → 13a(broadcast-rewrite 之后、cleanup 删 scratch 之前);跨期读 pre-update store(step-19 之前)。理由:念稿+factcheck+finalize+score-verdict 同在 scratch 的唯一窗口;pre-update store 与 davinci 写稿时 avoid_memo 同源。
- **跨期不建第二抽取器** → 13a 只做过热锚只读在场检查,锚抽取权威源仍唯一=蒸馏器(18)(CLAUDE.md 不变量)。

---

## Verification

**Verdict:** Approved (with-nits, 全部已应用 2026-06-15;report `.claude/reviews/plan-verifier-2026-06-15-phase3-062221.md`)。plan-verifier(opus,unbiased)：0 must-fix、1 should-fix〔`check_cross_dup` store dict-iteration — Phase-2 GAP-2 同类〕+ 3 nits,均已改入计划(Task1-impl/Task5-impl/Task1-tests 改 `store["anchors"].items()` 迭代 + `is_stale(entry)`;Impact-Map grep 去 false-negative;`BROADCAST_MIN_CHARS` 由 365×18 派生 + 与 `_FLOOR_CHARS_BY_SHOW` 基础层区别注记)。5 个高风险区核实 clean:13a 站位(cleanup 是 loop 后 finally)、06-14 由确定性硬门独判不达标(judge=None 仍红)、温度盾有专测、无第二抽取器、无跨任务红区间。

原 verify 重点(供执行期参照):(1)scorecard 站位置硬约束(13a 在 cleanup 删 scratch 之前——念稿可读);(2)06-14→不达标由确定性硬门独扛(judge=None 仍红);(3)温度盾 acceptance #5 有专测;(4)阈值放弃嵌入跨期硬门的证据链(Phase-2 cos 实测)+ 换皮局限已显式记录;(5)念稿时长量念稿不量 .md(防 06-14 量错对象回归);(6)fixture vendored 进仓库无绝对路径。

执行后:execute-plan(分段 + 硬门 checkpoint)→ test-changes(全套)→ implementation-reviewer(fresh opus)→ **REAL-green no-TTS e2e loop**(06-14 fixture→不达标〔确定性,快〕→ clean→绿 → live 生成一期→记分卡过门;MiniMax 超时 `--resume` 磨,escalation bound:live run 连续失败且为唯一阻塞时停下报告,不放宽门凑绿)。
