---
type: plan
status: active
contract_version: 2
tags: [paper-digest, faithfulness, anchor, verify-anchors, ledger]
refs:
  - docs/11-crystals/2026-06-18-paper-digest-show-crystal.md
  - docs/06-plans/2026-06-18-paper-digest-show-design.md
  - docs/06-plans/2026-06-19-p4-handoff.md
---

# 论文线"溯源门"改判定 — anchor 从「逐字引用」→「数字+名字对得上、措辞放开」

**Goal:** 把 `verify_anchors` 的 anchor 判定从「全文逐字子串」改成「该 anchor 里的**数字+专有名词**必须真在原文里（确定性、抓编造），前后**措辞放开**（容忍连接词/大小写/pdftotext 重排）」，让真跑 sonnet 的事实账写手不再因为忠实改写而被误判 fabricated 停线。

**Architecture:** 只重写 `lib/paperline/ledger.py:verify_anchors` 的匹配核（保留遍历四段、`flagged` 形状、返回 dict 不变 → 两个调用方 `check_ledger_verify`(采集侧门) + `check_faithfulness`(忠实门溯源底线) 零改自动受益）。新判定 = 数字 token 全中（零容忍，抓编造数字/篡改名字）+ 纯词 token 含纳比例 ≥ 阈值（措辞放开）。配套放松 `agents/papers/ledger-writer.md` 的「逐字复制」提示词。`check_faithfulness` 的夸大底线 + 局限底线（agent-assessed）**不碰**。这是对 D-009「代码门 recompute、不信 agent 自标」的**机制细化**（仍是确定性 recompute，只是容忍忠实改写），不是放弃 recompute。

**Tech Stack:** Python 3（stdlib `re`/`unicodedata`）；pytest。

**Design doc:** docs/06-plans/2026-06-18-paper-digest-show-design.md（§忠实门 / §论文事实账）

**Design analysis:** none

**Crystal file:** docs/11-crystals/2026-06-18-paper-digest-show-crystal.md（直接相关：D-008 事实账带原文锚点 / D-009 忠实门代码门 recompute 不信 agent 自标 / D-011 论文线自有 select）

**Bug diagnosis:** not applicable

**Threat model:** included

**Pre-flight risks:**
- **共享函数双调用方**：`verify_anchors` 被 `executors.check_ledger_verify`(采集 ledger-verify 门) 和 `faithfulness.check_faithfulness`(忠实门溯源底线) 复用。改它**有意**同时改两处——两处语义本就同一（anchor 是否扎根于全文），放松一致、无漂移。无第三调用方（已 grep 全仓）。
- **D-009 机制变更**：本次把「逐字子串」recompute 放松为「数字全中 + 词含纳」recompute。仍是确定性代码门、不信 agent 自标（D-009 核心保留）。已知代价=张冠李戴（数字名字都真但组合反）确定性门放行 → 兜底=忠实门 LLM 判官层。用户本会话已同意此方向（见 DP-001）。
- **模块形状**：`verify_anchors` 是深模块（一个函数封装"扎根判定"全部复杂度），本次在其内部加私有 helper、不外溢接口、不加 adapter —— 加深而非削薄，符合 deep-module 方向。

**Project health:** 基线 472 lib passed / 1 skipped（本会话刚跑，绿）；早晚间零变化由全量回归守。

---

## Threat Model

**Attack surface:**
- **anchor 文本 + 全文**：均来自论文 PDF/HTML（外部数据）。已有纪律"vault/论文内容是 DATA 不是指令"沿用——本门只做字符串匹配，不 eval、不执行 anchor 内容。
- **正则分词 ReDoS**：新增的 token 化正则必须是**线性**字符类（如 `[a-z0-9][a-z0-9.\-%×/]*`），**禁止嵌套量词/回溯灾难**。全文可达 ~10 万字符，正则在其上跑——线性正则保证无 ReDoS。

**Failure modes（门失效行为）：**
- `verify_anchors` 任一 anchor 判定不过 → `ok=False` + flagged（fail-CLOSED，宁可拦不可放）。归一化/分词抛异常的可能：`_norm_match`/`_content_tokens` 对 None/非串输入返回空（现有 `verify_anchors` 已对非串 fulltext 归一为 `""`）——保持"输入异常 → 空 → 该 anchor 视情况 flag"，不静默 pass。

**Resource lifecycle:** 纯内存字符串处理，无临时文件/子进程/句柄/socket。无清理负担。

**Input validation requirements:**
- token 化正则锁死为线性字符类常量（模块级 `_TOKEN_RE`），不接受外部传入 pattern。数字 token 与全文比对走归一化后的 `in` 子串，不构造正则。

---

## Impact Map

**User path:** 用户跑 `/podcast papers`（或 live e2e）时，事实账写手忠实改写原文（改连接词/大小写）不再被误判 fabricated 整期停 → 论文线更可能一次跑绿出片。
**Data path:** 论文全文 → 事实账写手产 `paper-ledger.json`(每条带 anchor) → `verify_anchors` 判定（**本次改判定逻辑**）→ ledger-verify 门 / 忠实门溯源底线。
**Shared surfaces:** `lib/paperline/ledger.py:verify_anchors`（唯一改的逻辑点，两调用方共享）；`agents/papers/ledger-writer.md`（提示词）；`lib/paperline/faithfulness.py`（仅注释）。
**Existing consumers:** `lib/paperline/executors.py:check_ledger_verify`；`lib/paperline/faithfulness.py:check_faithfulness`。两者读 `verify_anchors` 的 `{ok, flagged}` —— 形状不变，零改自动跟随。
**Must remain unchanged:** `validate_ledger`(schema 仍要 text+anchor 非空)；`check_faithfulness` 的夸大底线(`_ABSOLUTE_STRENGTH`)+局限底线(agent-assessed)；`flagged` 条目形状 `{section,text,anchor}`；早晚间观点线（不碰 paperline 外任何文件）。
**Regression checks:** 现有 fabricated-anchor 用例仍 flag（原型已证）；早晚间全量回归绿（paperline 改动物理隔离，不 import 观点线）；真路 live e2e 不再 stage ledger 也跑绿。

---

## Decisions

### [DP-001] anchor 扎根判定法 + 阈值 + 张冠李戴取舍（recommended — 用户本会话已同意方向）

**Context:** 把 D-009 的「逐字子串」recompute 放松为「数字+名字对得上、措辞放开」。需定下具体判定法、词含纳阈值、以及承认其代价。
**Options:**
- A〔选中〕: **数字 token 全中 + 纯词 token 含纳比例 ≥ 0.8**。数字（含数字的 token：`50.5%`/`10×`/`72b`/`+33.4%`/`2025`/`qwen2.5-vl-72b`）零容忍，每个必须在归一化全文出现 → 抓编造数字/篡改名字；纯词比例 ≥0.8 → 容忍连接词/大小写/重排。代价：张冠李戴（数字名字都真但组合反）放行，交忠实门 LLM 判官兜。
- B: **维持逐字子串，只加大小写折叠 + 标点/unicode 归一**。改动更小、仍能过本次"On vs Notably on"，但仍是"近似逐字"——模型大改一句话措辞（如把整句换成另一种讲法但含纳同数字）仍会被误判。没真正解决"逼转述模型当复印机"。
**Chosen:** Option A — 原型在真全文已证：live 忠实 anchor 过、真 prose method/limitation 100%含纳过、假数字 flag、假 prose flag、真 ledger(preserve-run3)逐条 100%含纳；唯张冠井戴放行（已知、有判官兜）。B 只治了本次单一表象、没治根（`lib/paperline/faithfulness.py:44` 的夸大底线 + 判官层已是 claim-级第二层防线，A 的残余风险有兜底）。用户本会话明确同意 A 方向（"可以,我觉得可行"）。阈值 0.8 = 实现期对真 ledger + fabricated 用例标定（真 ledger 须全过、三个 fabricated 用例须全 flag）。

---

<!-- section: task-1-tests keywords: ledger, verify-anchors, grounding -->
### Task 1-tests: anchor 扎根判定 — 测试先行

**Maps to Impact Map:** Data path (verify_anchors 判定); Regression checks (fabricated 仍 flag + faithful paraphrase 转 pass)

**Files:**
- Test: `lib/tests/test_paperline_ledger.py`（加 paraphrase-pass + fabricated-number-flag 用例；保留现有 fabricated 用例）

**Expected outcome:**（测试态）忠实改写过原文措辞的 anchor 被判通过；编造数字/编造整句的 anchor 仍被拦下。新增用例此刻对**现行逐字实现 FAIL**（现行会把 paraphrase flag 掉）。

**Non-goals:** 不碰 faithfulness 的夸大/局限用例；不改 `validate_ledger` schema 用例。

**Touched surface:** test 文件

**Regression shield:** 现有 `test_verify_anchors_flags_fabricated`(99.9%) / `test_verify_anchors_flags_multiple` / `test_verify_anchors_pass` / `test_verify_anchors_whitespace_normalized_pass` 全部保留——新逻辑须让它们继续绿（原型已证 fabricated 仍 flag）。

**Task Contract:**
- Expected behavior:（测试态）写手把"Notably, on LVBench…"忠实写成"On LVBench…"算通过；把真数字改成假数字算不通过。
- Automated verify: `python3 -m pytest lib/tests/test_paperline_ledger.py -q` —— 新增 2 用例 FAIL（现行逐字实现会 flag paraphrase / 旧逻辑下 fabricated-number 用例语义不同），既有用例仍绿。
- Real path verify: N/A（纯测试任务）
- Manual/device verify: none

**Steps:**
1. 加 `test_verify_anchors_passes_faithful_paraphrase`：**全文 = 真 fixture** `.claude/p2-samples/arxiv-2606.19341-pdftotext.txt`（line 156-159 含该句；pdftotext 把 "On LVBench" 拆在句中、`10×`/`larger` 跨行——**不能手写 `_SAMPLE_FULLTEXT`，手写会错配真实归一化行为**，verifier must-revise#1）。anchor 写 live 写手当时那条 `"On LVBench, our 7B agent outperforms the 10× larger Qwen2.5-VL-72B (50.5% vs. 47.3%)"` → 断言 `verify_anchors(...)["ok"] is True`、flagged 为空。**此用例对现行逐字实现 FAIL**（现行会 flag）。
2. 加 `test_verify_anchors_flags_fabricated_number`：全文含真句，anchor 把数字改成原文没有的（如 `50.5%`→`60.5%`，名字保留）→ 断言 `ok is False` 且 flagged 含该 section。
3. 既有 fabricated 用例（99.9% / fabricated…anchor）不动——实现后须仍 flag。

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_ledger.py -q`
Expected: 新增 2 用例 FAIL（功能未实现）；既有用例绿。
<!-- /section -->

<!-- section: task-1-impl keywords: ledger, verify-anchors, grounding -->
### Task 1-impl: anchor 扎根判定 — 实现（DP-001=A）

**Depends on:** Task 1-tests

**Maps to Impact Map:** Data path; Shared surfaces (verify_anchors 双调用方); Must remain unchanged (flagged 形状/validate_ledger)

**Files:**
- Modify: `lib/paperline/ledger.py`（重写 `verify_anchors` 匹配核 + 新增私有 helper）

**Expected outcome:** `verify_anchors` 对每条 anchor：归一化（NFKC + lower + 空白压单空格 + 花引号/破折号转 ASCII）后取内容 token（去一小撮连接词/停用词）；数字 token（含数字者）每个必须在归一化全文出现，否则 flag；纯词 token 含纳比例 ≥ `_GROUND_THRESHOLD`(0.8) 否则 flag；两条都过才 pass。`flagged` 形状、返回 dict、遍历四段、空 anchor 跳过语义全部不变。`validate_ledger`/`_norm_ws`/`REQUIRED_SECTIONS` 不动。

**Non-goals:** 不改 schema；不碰 faithfulness 夸大/局限；不引入外部传入正则。

**Touched surface:** `lib/paperline/ledger.py`

**Regression shield:** `flagged` 形状字段 `{section,text,anchor}` byte-identical；`check_ledger_verify`/`check_faithfulness` 调用点零改；线隔离测试（paperline 不 import 观点线）不受影响。

**Task Contract:**
- Expected behavior: 忠实改写措辞过、编造数字/编造整句不过。
- Automated verify: `python3 -m pytest lib/tests/test_paperline_ledger.py -q` 全绿（Task 1-tests 新用例转 PASS + 既有 fabricated 仍 flag）。
- Real path verify: Task 4 live e2e 不 stage ledger 跑绿。
- Manual/device verify: none

**Steps:**
1. 加模块常量：`_GROUND_THRESHOLD = 0.8`；`_STOP`(连接词/停用词集，含 a/an/the/of/to/in/on/for/and/or/with/our/we/is/are/be/by/as/at/that/this/it/its/from/into…)；`_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.\-%×/]*")`（线性，无回溯灾难）。
2. 加 `_norm_match(s) -> str`：`unicodedata.normalize("NFKC", s)` → lower → 花引号/破折号转 ASCII → `_WS_RE` 压空白 → strip。
3. 加 `_content_tokens(s) -> list[str]`：`_TOKEN_RE.findall(_norm_match(s))` 去 `_STOP`。
4. 加 `_anchor_grounded(anchor, ft_norm) -> bool`：内容 token 分数字/纯词；数字每个 `in ft_norm`（缺任一 →False）；纯词 `present/len >= _GROUND_THRESHOLD`（无纯词且数字全中 →True；无内容 token →True 维持空跳过语义）。
5. `verify_anchors` 主体：把 `if norm_anchor not in norm_fulltext` 换成 `if not _anchor_grounded(anchor, norm_fulltext)`（`norm_fulltext` 改用 `_norm_match(fulltext)`）；flagged 追加逻辑不变。
6. 跑 `python3 -m pytest lib/tests/test_paperline_ledger.py lib/tests/test_paperline_executors.py -q` 全绿；如某 fabricated 用例因阈值边界没 flag，调 `_GROUND_THRESHOLD` 或确认该用例 token 确不在全文（标定）。

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_ledger.py lib/tests/test_paperline_executors.py -q`
Expected: 全绿（含新 paraphrase-pass + 既有 fabricated-flag）。
<!-- /section -->

<!-- section: task-2 keywords: ledger-writer, prompt, anchor -->
### Task 2: 放松事实账写手提示词（anchor 不再要求逐字）

**Maps to Impact Map:** Data path (写手产 anchor 的指令); Shared surfaces (agents/papers/ledger-writer.md)

**Files:**
- Modify: `agents/papers/ledger-writer.md`（"anchor 硬规则"段 + 输出前自检段）

**Expected outcome:** 提示词从「Ctrl-C 一字不差、禁止加 On/In/The」改为「anchor **仍用原文语言（英文）、贴着原文那一处写**；**关键数字和专有名词必须一字不差带上**（数字/名字错=判 fabricated、停线）；前后连接词、大小写、句首**可以是你的话，不必逐字复制**」。**保留**"anchor 用原文语言"+"含关键数字"两条（否则中文 anchor 永远对不上英文全文 / key_results 无数字会被判 fabricated）。

**Non-goals:** 不改 `text`(转述) 字段规则；不改 4 段 schema 说明。

**Touched surface:** `agents/papers/ledger-writer.md`

**Regression shield:** 仍要求 anchor 英文 + 含关键数字——守住新判定的两个前提（语言一致、数字在）。**「anchor 用原文语言（英文）」约束散落在 line 12/27/30/33/36/71-72 等多处（不止"anchor 硬规则"段）；实现期先 `grep -n "英文\|English" agents/papers/ledger-writer.md` 列全，确保只删"整句逐字复制"死规则、保留每一处"英文"约束**（verifier must-revise#2——漏改一处会让写手改用中文 anchor，新判定下中文永远对不上英文全文）。

**⚠️ No test:** 纯提示词(.md)改动，无条件逻辑；由 Task 4 live e2e 经真写手覆盖（live 写手产的 anchor 须过新门）。

**Task Contract:**
- Expected behavior: 写手可以忠实转述着写 anchor，只要数字名字准、语言对。
- Automated verify: N/A — 提示词改动；`grep -c "一字不差" agents/papers/ledger-writer.md` 确认旧死规则段已改写（仅"数字/名字一字不差"保留，"整句逐字复制"删除）。
- Real path verify: Task 4 live e2e。
- Manual/device verify: none

**Steps:**
1. 改写"anchor 硬规则（这是全文最重要的一条）"段：删"复制粘贴≠改写 / 一个字都不能动 / 禁止句首加词"的逐字死规则；写新规则（贴原文、英文、数字名字一字不差、措辞可放）。
2. 改写"输出 JSON 之前…逐条核对"自检段：自检项从"首尾字符与原文一致、没加 On/In/The"改为"每条 anchor 的数字和专有名词都能在原文里 grep 到、anchor 是英文且贴着原文那一处"。

**Verify:**
Run: `grep -n "数字" agents/papers/ledger-writer.md`
Expected: 新规则在位（数字/名字须准），逐字死规则已删。
<!-- /section -->

<!-- section: task-3 keywords: faithfulness, comment, traceability -->
### Task 3: 忠实门溯源底线注释同步（无逻辑改动）

**Maps to Impact Map:** Shared surfaces (faithfulness.py 复用 verify_anchors); Must remain unchanged (夸大/局限底线)

**Files:**
- Modify: `lib/paperline/faithfulness.py`（仅更新溯源底线注释，说明 anchor 现为"数字+名字含纳"而非逐字）

**Expected outcome:** 注释反映新语义；夸大底线(`_ABSOLUTE_STRENGTH`)、局限底线(agent-assessed)代码与注释**零改**。

**Non-goals:** 不改任何 faithfulness 逻辑。

**Touched surface:** `lib/paperline/faithfulness.py`（注释行）

**Regression shield:** `lib/tests/test_paperline_faithfulness.py` 全绿（逻辑未动）。

**⚠️ No test:** 纯注释改动，无逻辑；既有 faithfulness 测试守逻辑不变。

**Task Contract:**
- Expected behavior:（无用户可见变化）忠实门行为不变，注释与新判定一致。
- Automated verify: `python3 -m pytest lib/tests/test_paperline_faithfulness.py -q` 全绿（逻辑零改）。
- Real path verify: Task 4 live e2e 忠实门照常工作。
- Manual/device verify: none

**Steps:**
1. 在 `faithfulness.py` 溯源底线段（调 `verify_anchors` 处的 docstring/注释）注明：anchor 扎根现以"数字+专有名词含纳"判定（见 `ledger.verify_anchors`），逐字不再要求。

**Verify:**
Run: `python3 -m pytest lib/tests/test_paperline_faithfulness.py -q`
Expected: 全绿。
<!-- /section -->

<!-- section: task-4 keywords: e2e, live-ledger, real-path -->
### Task 4: live e2e 加 `LIVE_LEDGER` 开关 — 真路验收（事实账也真跑）

**Depends on:** Task 1-impl, Task 2

**Maps to Impact Map:** User path (论文线一次跑绿); Regression checks (真路 live 不 stage ledger 跑绿)

**Files:**
- Modify: `evals/paperline_full_e2e.py`（`LIVE_LEDGER` env 为真时不 stage ledger-writer，让其真跑 sonnet）

**Expected outcome:** `LIVE_LEDGER=1` 时 dispatch 包装器不再拦 `ledger-writer`（去掉 stage 分支，照常注入 `--model sonnet` 真跑）；默认（不设）仍 stage（保留可复现的隔离跑法）。真跑确认 live 写手的 anchor 现在过 `ledger-verify`、整期 `status=ok`、出 .md。

**Non-goals:** 不改 discovery/fetch 的 staging（网络 flaky 仍喂缓存真 19341）；不修 `--resume`。

**Touched surface:** `evals/paperline_full_e2e.py`

**Regression shield:** 默认行为（不设 `LIVE_LEDGER`）与现状一致（仍 stage ledger）——本次只加开关，不改默认。

**Task Contract:**
- Expected behavior: 打开开关后，事实账也由真模型当场写，整期还是能跑绿出片。
- Automated verify: N/A（真路 eval，非单测）——见 Real path verify。
- Real path verify: 清 papers 输出后 `no_proxy='*' LIVE_LEDGER=1 PODCAST_STUDIO_CONFIG=~/.personal-os/scratch/p3-live-sandbox/config-p3-live.yaml python3 evals/paperline_full_e2e.py` → `GREEN=True`、ledger-verify 不 halt、.md 落 papers/episodes。
- Manual/device verify: ⚠️ 抽查 live 写出的 anchor：英文、贴原文、数字名字准（不是中文转述、不是凭空编）。

**Steps:**
1. `_stage_network_and_model` 的 `_sonnet_dispatch`：把 ledger-writer stage 分支包在 `if not os.environ.get("LIVE_LEDGER")` 下；为真则落到正常 `--model sonnet` 真跑分支。
2. 头部 docstring + 启动 print 注明当前是否 stage ledger（可观测）。

**Verify:**
Run: `python3 -c "import ast; ast.parse(open('evals/paperline_full_e2e.py').read()); print('ok')"`
Expected: 解析通过；真路跑由上方 Real path verify 覆盖。
<!-- /section -->

---

## Recommended additions (not in scope)

- **张冠李戴硬化**（数字与相邻名字的局部 n-gram 共现窗）：把 DP-001 的残余风险（数字名字都真但组合反）再收一道确定性窗。本次先靠忠实门判官层兜；若实测判官漏过再单开。
- **阈值可配**：`_GROUND_THRESHOLD` 现为模块常量；若不同论文类型需不同松紧，后续提到 config。

---
## Verification
- **Verdict:** Approved
- **Date:** 2026-06-19
- **Verifier:** dev-workflow:plan-verifier (Sonnet) — approved with 2 minor revisions, both folded in (Task 1-tests 用真 fulltext fixture / Task 2 grep 保留多处"英文"约束). 0 design gaps, 0 crystal conflicts (D-008/D-009 recompute-refinement 确认准确), S3 16 语义点 0 gap. Report: `.claude/reviews/plan-verifier-2026-06-19-152318.md`.
