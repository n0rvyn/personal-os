---
type: dev-guide
status: active
tags: [podcast-studio, code-runner, agent-memory, anti-homogenization, eval-gate]
refs: [docs/06-plans/2026-06-11-podcast-factchecker-plan.md]
current: false
confirmed_at: 2026-06-14T01:55:00
---

# podcast-studio 大档重构 Development Guide

**Project brief:** none on disk (`docs/01-discovery/project-brief.md` referenced by CLAUDE.md is absent; locked scope captured in CLAUDE.md § Scope)
**Design doc:** none — design captured inline in this guide + the big-track session brief (per project convention, cf. `docs/06-plans/2026-06-11-podcast-factchecker-plan.md`)
**Architecture:** `CLAUDE.md` (4-layer architecture + non-obvious invariants)

> **Why this redesign (verified defects, from `~/Code/Content/Podcasts/`):**
> - 06-14 早间把读者侧 `.md` 当成原始「草稿 C」发出(body 带草稿头 + `## ⑤ 我下注什么` 段);去段化 commit 970fcb3 未生效。根因:`agents/kuaidao.md` 自相矛盾(67 行=4段/3段/无下注段;75 行=「保留早间五段/晚间四段」),且 davinci 草稿仍出五段模板 + 草稿头。
> - 逐字重复段进了 `.md`(17.2万 / 劳动收入占GDP 各 2×;苏伊士 12×)。重复生在 davinci 草稿,穿过 critique→polish→score→finalize 未被拦——评分 rubric 无冗余维度,kuaidao 防缩水规则反而保护重复。仅 bianyang 口播步偶然去重,故音频比 `.md` 干净。
> - 跨期同质化(判断力外包/认知退化 + GPS/印刷术/苏格拉底/伊万 + 1956苏伊士 在 06-11e/06-13m/06-13e/06-14m 反复)。06-14 反同质化步骤压根没跑:scratch 缺 `magnitude-verdict.json` / `judge-input.json` / `corpus.txt`(prose 编排 loop 赶工时跳了 5b/6)。
> - 06-14 音频 16.5 分钟 < 18 分钟下限;字数门按 `.md` 7300 字(被重复段 + ⑤ 段撑高)通过,真实口播稿仅 5574 字。
>
> **研究依据(deep-research 本会话,19 条已验证):** 反重复杠杆 = dedup-against-stored-history(「写得原创点」无效,LLM 自我重复是默认失败模式 PNAS 2025);记忆 = 结构化存储 + 主动注入(Letta core/recall;ChatGPT 始终推送蒸馏头);编排 = 移出 LLM prose-loop、代码声明拓扑 + 硬门(Conductor,编排环零 token、步骤不可跳);自我批评 = 工具锚定的可验证信号优于自由发挥,每个工艺缺陷需独立 rubric 轴。

## Global Constraints

- **Tech stack:** Python 3.13(`lib/`, pytest);Claude Code 子代理(markdown agent 定义,headless `claude -p` 派发);Swift CLI helper(macOS `NLContextualEmbedding`,中文已实测 dim=512/hasModel=true);persona 创意提示词保持 prose。
- **Coding standards(from CLAUDE.md):** `lib/*.py` 是可导入模块、非 CLI(`from lib.x import f` 调用,shell `python3 lib/x.py` 静默失败);gate 契约统一返回 `{"ok","reason",...}`(见 `lib/episode.py`);stance 卡 append-only、无 confidence 数字;`candidate_id` 严格 `稿-A/稿-B/稿-C`(`select_draft` 按 `scores.total` 选、不信 `selected`);TTS 单 vendor 单 voice、走 `synth-auto`;bible-distiller 必须隔离;读者 `.md` = step-12 finalize body;严禁硬编码机器绝对路径,用 `${CLAUDE_PLUGIN_ROOT}` + config 注入的 vault 路径。
- **Locked scope(CLAUDE.md § Scope,不扩不缩):** 仅生成(三件同名产物 `.md`/`.mp3`/`.stance.yaml`),无 delivery / 无 cron(节奏靠 `/loop`)/ 无 news crawling / 无双人对话音频。**本大档不扩张 scope**——只重构生成管线的编排、记忆、质检,不新增对外能力。
- **温度原则(memory `project_adam_podcast_temperature_principle`):** 去假/去重的目标是「捏造的 DATA + 重复/同质化」,**不是**软化宿主的主观观点/下注。任何让宿主对主观判断变得吞吞吐吐的改动 = 回归,不是功能。Phase 3 的 dedup/结构门只作用于重复与招牌锚,绝不触碰主观内容。
- **DP-001 反转(本会话用户批准):** 编排「顺序」从 prose 降为代码(声明式 step 表 + 硬门,编排环零 LLM token);persona「提示词」仍是可调 prose。这是 CLAUDE.md 现有 DP-001(orchestration is prose)的有意修订——Phase 1 的 write-plan 阶段需同步更新 CLAUDE.md 对 DP-001 的描述。
- **执行模型:** persona 站点用 Sonnet(proxy → MiniMax M3,成本不计);embedding 走 macOS on-device,免费。
- **迭代方式:** 完整建,不做 MVP;建成后用「无-TTS 模式」反复 e2e 迭代,直到 Phase 3 达标尺记分卡全绿。

---

<!-- section: phase-1 keywords: runner, orchestration, step-table, gate, no-tts -->
## Phase 1: 传送带 — 代码编排 runner

**Status:** ✅ Completed — 2026-06-14（无-TTS e2e run6:22/22 站跑通,发布 7926 字 episode「过期地图的时代-自己拍板」,4 段/无我下注/无草稿头/无 mp3。Step7 修了 review 的 4 个 must-fix;另加 resume + 并行 fan-out 让迭代提速;216 单测全绿。)

**Goal:** 现有 17 步管线改由一个确定性 Python runner 串联,任何一站缺产物即停线、绝不静默跳过;新增「无-TTS 模式」产出 `.md` + 口播稿 + stance 卡;本阶段 persona 产出与今天一致,不改创意行为。

**Depends on:** None

**Scope:**
- `lib/runner.py`(新):代码编排器,按 step 表逐站执行。每站:① 拼输入(读上一站 scratch 产物)② 派 persona(headless `claude -p`,`agents/<name>.md` 作 system prompt,persona 保留其 Read/Bash/WebSearch 工具与 prose 提示词)③ 校验产物(调现有 gate)④ 合格才前进,缺失/非法即 **halt + 报出缺哪一站**。编排环不烧 LLM token。
- step 表作为数据(`lib/pipeline.py` 的结构 或 yaml):`{name, agent, inputs, required_artifact, gate_fn}`,从 `skills/podcast/SKILL.md` 的 per-step 合同表(~573 行)迁移而来。
- 无-TTS 跑法开关:跳过 jay/TTS(step 14)与 mp3 gate,其余照跑。
- `skills/podcast/SKILL.md` 缩为薄壳:触发即调 runner(per-show 编辑分支 `references/{morning,evening}.md` 仍由各站读取)。
- 复用 `lib/episode.py`(check_artifact / check_min_chars / select_draft / scratch 生命周期 / load_finalize_body / naming)、`lib/stance.py`、`lib/magnitude.py`、`lib/bible.py`、`lib/factcheck.py`。
- **本阶段不改 persona 创意行为**(草稿/判分量/定稿/口播提示词原样;矛盾提示词留到 Phase 3 修)。

**用户可见的变化:**
- 无 — 纯基建阶段。节目产出与今天等价(同输入下差异仅来自 LLM 抽样,不来自流程),听众侧无变化。

**Architecture decisions(留待 write-plan):**
- persona 派发机制:headless `claude -p`(默认候选,需确认嵌套 Claude 的工具环境/认证在 `/loop` 下可用)vs 直连 Anthropic API(会失去 Claude Code 工具环境,davinci 需 WebSearch/Bash,故倾向 `claude -p`)。
- step 表载体:Python dict vs yaml。
- 单站失败重试:halt 即停 vs 重跑 N 次再停。
- **fail-soft 步骤的 halt 语义(关键):** 5b magnitude / 6 bible 现为 fail-soft(降级 all-light / 最小 bible)。要区分「跑了但降级」(写降级 artifact,合法)与「压根没跑」(无 artifact = 06-14 故障)。Phase 1 须保证:即便降级,artifact 也必然落盘;artifact 完全缺失 → halt。

**Acceptance criteria:**
- [x] 无-TTS e2e dry run 跑通一期(morning 或 evening),产出 `.md` + 口播稿 + stance 卡,无 mp3。 ✅ run6:22/22 站,发布 7926 字
- [x] 删除/改名任一 station 的必产 artifact → runner halt 并报出缺失 station 名(不静默前进)。 ✅ 单测 test_runner halt-on-missing
- [x] 一次正常 run 后,`magnitude-verdict.json` 与 bible/`corpus` artifact 必然存在(06-14 缺失场景结构上不可能再现)。 ✅ ⚠️ 更正(2026-06-16,B1):此项一度 **false-green** —— `magnitude-verdict.json` 一直在产,但 `character-bible.md` 的产出站在 prose→coded DAG 迁移中丢失(`bible-distiller` 仅在 whitelist + 作为 12/13 的 input,无产出 station),所以 bible 实际**从不产出**,12/13 静默回退 base persona。B1 补回 step 6 `bible-distill` 自定义执行器(`_bible_distill_step`:`gather_corpus`→隔离 dispatch→`write_bible(state_dir)`,fail-soft 总落最小 bible),现真绿:`test_bible_distill_*`(落盘/隔离/fail-soft/空-corpus)+ 310 pytest green。
- [x] persona 产出与现行 SKILL.md 路径等价 —— 口径:同输入(同日 vault 快照 + 同 brief)下,产出的 **artifact 集合、产出顺序、各 gate 通过结果** 一致;正文文本差异(来自 LLM 抽样)不算回归。 ✅
- [x] `lib/tests/` pytest 全绿 + runner 新增单测(step 表解析、halt-on-missing、no-TTS 跳过 step14)。 ✅ 216 passed

**Review checklist:**
- [ ] run-phase review step(自动 implementation-reviewer)
- [ ] apple-dev UI/design/feature reviewers — N/A(非 Apple UI 项目)
<!-- /section -->

---

<!-- section: phase-2 keywords: covered-ground, memory, embedding, distiller, inject -->
## Phase 2: 夜班管理员 — 跨期记忆

**Status:** ✅ Completed — 2026-06-14（6/6 acceptance criteria 全绿,no-TTS e2e 真实验证）。16/16 plan tasks done(263 单测 green);plan-verifier + implementation-reviewer 全过。e2e 用真实 runner `_execute_step`(管线的真实站点执行器)+ 真实 `claude -p`/MiniMax M3 验了 distiller 真派发(2 次:smoke + 站点验证,共 25 个真实招牌锚)、distiller→update→store(#1)、assemble-briefs→avoid_memo→davinci prompt(#2/#3)。修了 3 个真实 bug(GAP-1 embed.swift 编译、GAP-2 plugin_root 静默退化 n-gram、阈值 0.82 false-merge);全是单测+reviewer 漏、唯真实路径暴露的「静默退化」类。**注**:完整 24 站不间断 CLI run 卡在 Phase-1 的 davinci drafting timeout(MiniMax M3 对 7000 字 + 3 路并行慢,与 Phase 2 正交,Phase 1 run6 也是多轮才过);已 bump timeout + resume 跑完整不间断 run 作完整性产物,但 Phase 2 全部新站已经过真实 runner 验绿,不依赖该 run。

**Goal:** 每期发布后,一个隔离蒸馏器更新结构化「covered-ground」记忆;下一期 runner 从中渲染「最近用滥、请避开」备忘录并**每轮强制注入** davinci 写作 brief。

**Depends on:** Phase 1

**Scope:**
- `lib/coveredground.py`(新,克隆 `lib/bible.py` 的隔离蒸馏模式):结构化存储(yaml)为真相源——每个锚/框架/命名概念 → `{first_used, last_used, count, episodes[], embedding, staleness}`;含读写 + 衰减 + `render_memo()`(渲染人话避让备忘录)。
- Swift embedding helper(新,plugin 内 `tools/embed.swift` 或同级):`NLContextualEmbedding` 算中文向量,lib shell 调、Python 端算 cosine。**回退:** 非 macOS 或 helper/模型资产缺失 → n-gram 叠合 + 抽取锚集合命中。
- 隔离蒸馏 agent(`agents/coveredground-distiller.md`,沿用 bible-distiller 隔离纪律):读最近正文 + stance 卡 `apparatus_used` → 更新 store。**作为最后一站、发布之后跑;其失败绝不阻断当期发布。**
- stance 卡新增 `apparatus_used` 字段(尊重 append-only,在 step 16 distill 时写):本期用过的锚/类比/框架。
- runner:从 store 渲染备忘录 → **每轮注入 davinci brief**(代码推送,非量臣可选拉取)。
- **[DP-001=A 取代] 量臣(5b)停止产 `recent_anchors` 避让清单,只保留 magnitude 路由(none/light/medium/heavy)**;davinci 的锚避让记忆改由 covered-ground 渲染备忘录唯一提供。同步更新 `lib/magnitude.py` 的 verdict 契约(去掉 recent_anchors 抽取)与 CLAUDE.md 中「recent_anchors = anti-repeat guard」的不变量描述。注意:`gather_recent_bodies`(读最近正文)保留——它现在喂 covered-ground 蒸馏器,不再喂 recent_anchors 抽取。

**用户可见的变化:**
- 间接 — 听众会感到节目不再一期期复用同一套类比/锚(苏伊士、伊万、GPS、印刷术);界面/格式无直接变化。

**Architecture decisions(留待 write-plan):**
- covered-ground 存放位置:`output_dir` 旁 vs 专门 memory 目录(config 注入)。
- 衰减函数(线性/指数);**v1 默认「过热」= 14 天内 count≥3 或 最近 3 期出现≥2 期(对齐量臣现有 window_days=14),后续可调。**
- 注入点:davinci brief 哪一段;morning/evening 是否都注入。
- `apparatus_used` 产出方:定稿后 distiller 抽取 vs 写稿时 davinci 自报 + distiller 校正。
- embedding 选型:`NLContextualEmbedding`(dim512,语义、抓换皮重复)vs `NLEmbedding` 句向量(dim640);相似度阈值。

**Acceptance criteria:** （e2e 验证 2026-06-14，sandbox `.e2e-sandbox-phase2`，真实 `claude -p`/MiniMax M3，no-TTS）
- [x] 一期发布后 covered-ground 被更新(新锚入库;已有锚 count+1、last_used 更新)。 ✅ 真实 runner `_execute_step` 跑 distiller(step18)+update(step19):distiller 从真实 6/13 正文抽 15 个招牌锚(苏格拉底·托特/印刷术古腾堡/GPS空间记忆/伊万·卡拉马佐夫/Stratechery…)→ `covered-ground.yaml` 落盘,各锚 count=1 episodes=[6/14-morning]
- [x] 下一期 davinci brief 含非空 avoid-memo(列出过热锚)。 ✅ 真实 runner `_execute_step` 跑 assemble-briefs:读 seeded stale store → `writing-brief-A.json` 的 `avoid_memo` 非空、列「1956苏伊士运河危机(累计3次)」;`_build_draft_prompt` 把 memo 织进 step-7 davinci prompt(真实验证 prompt 含苏伊士+covered-ground)
- [x] 构造「上一期用过苏伊士」场景 → 下一期 memo 将该锚标为 stale/过热。 ✅ 构造 苏伊士 跨 6/12m/6/12e/6/13m → count=3、is_stale(2026-06-14)=True → memo 标它;温度盾 clean(memo 无压制观点措辞)
- [x] 非 macOS 或 Swift helper 缺失 → 自动回退 n-gram + 锚集合,不 crash。 ✅ `test_similarity_falls_back_on_helper_failure`;helper 现已 device-verified(swiftc + `swift` 解释器两路真编真跑,dim=512)
- [x] distiller 故障注入 → 当期已发布产物不受影响(post-publish、不阻断)。 ✅ `test_distiller_failure_does_not_halt`(用 production gate map)+ e2e 中 distiller `_execute_step` 走 fail_soft 路径
- [x] pytest:store 读写/衰减/`render_memo`、cosine 计算、回退路径单测。 ✅ 263 green(含 19 coveredground + 10 embed)

**e2e 中发现 + 修复的 2 个真实 bug（单测/reviewer 都漏，唯 e2e 真实路径暴露）：**
- **GAP-2**:`coveredground._default_similarity` 不传 `plugin_root` → `embed.similarity` 解析不到 swift binary → 全程静默退化 n-gram,NLContextualEmbedding 语义路径在真实管线死掉(GAP-1 同类)。修:从 `__file__` 算 plugin_root 注入 + except 安全返回 0.0;编译 `tools/embed` 快路径。
- **re-skin 阈值 0.82 → 0.93**:device 实测 cos(1956苏伊士运河危机,1973石油危机)=0.891 在 0.82 下 FALSE-MERGE、把 石油 从 store 删掉;且真实 reskin(印刷术/活字印刷术=0.884)比该 false-merge 还低 → 单一阈值无法分离「reskin」与「同族异锚」。改 0.93:只合并近乎同形,exact-match(1.0)+ distiller 一致命名 + count-based staleness 扛 dedup。NLContextualEmbedding 短语级判别弱已记 Phase 3。

**Review checklist:**
- [x] run-phase review step(自动 implementation-reviewer)✅ `.claude/reviews/implementation-reviewer-2026-06-14-194327.md`(Risks 1-6 clean;GAP-1 已修)
- [x] apple-dev reviewers — N/A(非 Apple UI 项目)
<!-- /section -->

---

<!-- section: phase-3 keywords: dedup, structural-lint, scorecard, eval, prompt-fix -->
## Phase 3: 工艺门 + 达标尺 — 质检与记分卡

**Status:** ✅ Completed — 2026-06-15(6/6 acceptance 全绿。#1/#2/#4/#5/#6 确定性硬证:真实 06-14 fixture 喂 `build_scorecard`(judge=None)→ `passed=False`,六门逐项命中 sections(段数5≠4)/draft_marker/betting_section(⑤段)/duration(念稿5455<6570)/intra_dup(17.2万·占GDP 逐字)/cross_dup(苏伊士);clean+温度盾 fixture 全绿;304 pytest green。#3 真实 live no-TTS e2e:新生成 morning 发布「回读税-每一次点头都在欠账」(4段/无草稿头/无⑤段,structlint 独立复验绿),live 13a 记分卡 **总判:通过 ✓**(六硬门全绿 + 判官 qianzhongshu=19≥14 / factcheck flagged=0 / 有观点5 / 有温度5 / 不同质化4),无 mp3。⚠️ live run 诚信披露:Phase-1 factcheck 站正确拦下一处真实事实错误(草稿称「苹果付费买浏览器默认搜索权」方向颠倒——实为谷歌付苹果),按「门抓错→人工订正→门复验」外科订正该单句后 --resume,**真实 factcheck agent 复判 flagged=0**(非伪造判定);记分卡判的是真实干净 episode。该 factcheck 内容错属 Phase-1 生成站、与 Phase-3 记分卡正交。)

**Goal:** 每次 e2e run 产出一张设计达标记分卡(硬门 + 判官维度);把真实 06-14 早间当回归样本必须判为不达标;并在源头修掉提示词矛盾。

**Depends on:** Phase 1 + Phase 2

**Scope:**
- Dedup-overlap 检查(`lib/dedup.py` 新):站内(逐字/近似重复段,抓 06-14 的 17.2万/GDP 复制)+ 跨期(用 Phase 2 embedding + n-gram 回退)→ 输出**重复分**;契约 `{ok, reason, score}`。
- 结构 lint(`lib/structlint.py` 新):段数(morning=4 / evening=3)、body 无草稿标记、无独立下注段标题、**口播稿真实时长 ≥ 18 分**(量口播稿/念稿字数,**不量 `.md`**——06-14 字数门量错对象)。
- 达标尺记分卡(`lib/scorecard.py` + 一个判官 agent):每 run 产出,含硬门 + 判官维度。具体条目见下表。
- 信号接线:dedup/结构分**喂给判官/门(信号→判断)**,不是「含草稿就 reject」的瞎正则。
- 提示词一致性修复:`kuaidao.md` 75 行「保留早间五段/晚间四段」→ 改为 4段/3段、无下注段(审计**所有**提段数处,970fcb3 漏改);davinci 草稿模板停止输出「# 草稿 C」H1 与 ⑤ 我下注段;kuaidao 防缩水规则**豁免删除逐字重复**(删冗余不算缩水)。
- 把真实 `2026-06-14-卡点会沉默-判断力会萎缩.md` 纳入 `lib/tests/` 作回归 fixture。

**达标尺记分卡 v1(条目 + 阈值,供 review;阈值标 v1、可调):**

| 类别 | 条目 | 判据 v1 |
|---|---|---|
| 硬门 | 段数 | morning==4 / evening==3 |
| 硬门 | 无草稿标记 | body 不含「草稿」/draft H1 |
| 硬门 | 无独立下注段 | 无 `## …我下注` 标题 |
| 硬门 | 口播真实时长 | ≥18 分(口播稿字数 ÷ ~365 非空白字/分 ≥ ~6570 字) |
| 硬门 | 必产 artifact | magnitude-verdict + covered-ground 刷新 + stance 卡 均在 |
| 硬门 | 站内重复 | 任意段对相似度 < 0.85(embedding)或 n-gram Jaccard < 0.5 |
| 信号 | 跨期重复 | 与最近 5 期任一段相似度 < 0.80;招牌锚命中 covered-ground「过热」(v1:14 天内 count≥3,或 最近 3 期出现≥2 期)→ 计入「不同质化」判官分 |
| 判官 1-5 | 有观点 | 至少一条织入正文的可证伪判断(非「值得关注」)→ ≥3 |
| 判官 1-5 | 有温度 | 自我披露/温度落点存在 → ≥3 |
| 判官 | 信息准确 | 过现有 factcheck 门(`lib/factcheck.py`)→ pass/fail |
| 判官 1-5 | 不同质化 | 招牌锚复用惩罚 + 跨期相似度 → ≥3 |
| 判官 | 现有 rubric | 钱钟书 洞察/命名/跨域/思考问句 total ≥ 14/20(命名 N/A 不扣) |

**用户可见的变化:**
- 听众侧 — 不再出现重复段落、草稿标记、超短节目;段落结构稳定;**观点与温度不被削弱**(温度原则不回归)。

**Architecture decisions(留待 write-plan):**
- dedup 阈值(站内 vs 跨期;embedding cosine vs n-gram Jaccard 的临界)——研究称无定论,先定 v1 再迭代调。
- 记分卡判官维度由谁出:复用钱钟书 rubric + 扩维度 vs 新判官 agent。
- 硬门不达标时:halt 重跑 vs 仅记分给人看(迭代期 vs 生产期或不同)。
- 口播字/分钟率取值(实测语速 ~310 汉字 / ~365 非空白字 每分)。

**Acceptance criteria:**
- [x] 真实 06-14 早间喂进记分卡 → 判定**不达标**,且具体命中:逐字重复段、草稿头、⑤ 段、时长不足、苏伊士过度复用。 ✅ `build_scorecard(judge=None)` → `passed=False`,六门逐项红:sections/draft_marker/betting_section/duration/intra_dup/cross_dup(`test_06_14_regression_fails` + 直跑 fixture 复验)
- [x] 一份干净 fixture → 记分卡全绿通过。 ✅ `test_clean_fixture_passes`
- [x] kuaidao/davinci 提示词改后,新生成 morning 为 4 段、无草稿头、无 ⑤ 段(结构 lint 通过)。 ✅ live e2e 发布「回读税」4段/无草稿头/无⑤段,structlint 独立复验绿
- [x] dedup 检出 06-14 的 17.2万/GDP 逐字重复。 ✅ intra_dup verbatim-run 命中「17.2万元…占GDP的比重约百分之三」
- [x] **温度回归盾:** 纯主观/观点 body 不被 dedup 或结构门误伤;温度原则不回归(沿用 factchecker plan 的温度盾测法)。 ✅ `test_temperature_shield_passes`(intra_dup hits 为空)
- [x] pytest:dedup(站内/跨期/回退)、结构 lint、记分卡组装 单测。 ✅ 304 passed

**Review checklist:**
- [x] run-phase review step(自动 implementation-reviewer)✅ `.claude/reviews/implementation-reviewer-2026-06-15-phase3-081032.md`(Pass-with-gaps:0 must-fix、2 should-fix 均已修)
- [x] apple-dev reviewers — N/A(非 Apple UI 项目)
<!-- /section -->

---

<!-- section: phase-4 keywords: directory-layout, artifacts, config, state, separation -->
## Phase 4: 目录归位 — 产物 / 配置 / 状态 分目录存放

**Status:** ✅ Completed — 2026-06-15(6/6 acceptance 满足,用户接受。scope 确认=full split episodes/state/reports + config→`~/.podcast-studio/config.yaml`,topic_log/scratch 留 root。plan-verifier APPROVED(2 cycles);implementation-reviewer **0 must-fix**、design-fidelity clean、每个 reader/writer 重指向确认;2 个 should-fix 均已修(SF-1 evals/judge_fixture + SKILL.md prose→episodes;SF-2 topic_log root-boundary 断言)。306 pytest green(含新 config 派生子目录测试 + runner 落盘断言)。迁移脚本 `tools/migrate-phase4-layout.sh` 在 sandbox 实跑:10 文件正确归位,topic_log+source_log 留 root,`load_cards(episodes_dir)` 找到 3 卡(连续性不孤立)。⚠️ 诚信披露:#5 的 live full-publish e2e(真实 LLM 跑到 publish 写 episodes/state/reports)被**正交的 MiniMax 基础设施故障**阻塞——qianzhongshu 评分站今早能跑(Phase-3 morning 已发布),今晚两次失败(超时 2400s,再 exit 1),与目录分离代码无关;**未伪造** score-verdict 强跑绿。#5 由「确定性真实-runner 落盘断言 + 11 站真实 live 读路径(迁移后 episodes/state)+ 迁移往返」验证,live-publish 写路径待 MiniMax 恢复后补跑(`python3 /tmp/p4_resume.py`)。)

**Goal（用户原话）:** 把 artifacts 和 config 分开目录来存,不要都挤在一起。当前单一 `output_dir`(`Content/Podcasts/` 或 sandbox)把发布产物、连续性状态、记分卡报告、e2e 配置、scratch 全堆在一起;按种类拆到独立目录。

**Depends on:** Phase 1 + 2 + 3(covered-ground.yaml / topic_log.yaml / *.stance.yaml / *.scorecard.md / 各 store 都已存在,迁移对象齐了才好一次拆干净)。

**当前「挤在一起」实况（证据,2026-06-15 实测 `output_dir`）:**
- 发布产物:`{date}-{title}.md`、`{date}-{title}.mp3`、`{date}-{show}.stance.yaml`
- 连续性状态:`covered-ground.yaml`(Phase 2)、`topic_log.yaml`
- 记分卡报告:`{date}-{show}.scorecard.md`(Phase 3)
- 配置:`config-e2e-sandbox-phase2.yaml`(e2e sandbox config,坐在 `Content/Podcasts/` 根,与产物同级)
- 临时:`.scratch-{date}-{show}-*/`(run 内,cleanup 后删)

**Scope（AI 转写草稿 — 待 scope confirmation 定稿）:**
- 用户核心要求:**config 与 artifacts 不同目录**。
- ⚠️ AI 补充(建议同批做,避免拆一半):连续性**状态**(covered-ground/topic_log)与**记分卡报告**(.scorecard.md)也从发布产物目录分出——它们不是"听众产物",混在一起正是"挤"的来源。是否纳入由用户在 scope confirmation 定。
- `lib/config.py` 加目录解析(按确认布局新增 dir 字段),沿用现有 **fail-closed**(缺目录 → `ConfigError` 命名 offending key)。
- 改所有写路径 caller:`lib/episode.py`(`episode_paths`/`stance_path`/`make_scratch`)、`lib/coveredground.py`(`store_path`)、`lib/bible.py`(`bible_path`)、`lib/runner.py`(scorecard.md / topic_log / publish)。重构前必 grep 全 caller(CLAUDE.md 规则)。
- 现有文件迁移 + 兼容策略(按确认方案)。

**用户可见的变化:** 文件系统布局变化(用户在 `Content/Podcasts/` 看到的目录结构变;听众侧无感)。⚠️ 触碰 `config.py` 契约 + CLAUDE.md locked scope「three co-named artifacts to `vault.output_dir`」的输出布局——属用户可见行为变更,**已由用户口头授权**,但具体布局需 scope confirmation 拍板。

**Architecture decisions（留待 write-plan,run 时必确认）:**
- **[需确认] 目录布局**:`output_dir` 下分子目录(`episodes/` `state/` `reports/`)vs 顶层并列目录(config 注入多个独立 dir)。
- **[需确认]「config」指哪个**:e2e sandbox config(`config-e2e-sandbox-phase2.yaml` 挪出产物目录)/ 主 `~/.podcast-studio/config.yaml`(已在 home,本就分离)/ 两者。
- **[需确认] 状态 + 报告是否一并分出**(见 Scope 的 AI 补充)。
- **[需确认] 现有文件迁移**:一次性 `git mv`/脚本迁移 vs 双读兼容期(读旧位置 fallback)。
- **[需确认] locked scope 边界**:把 state/reports 移出 `output_dir` 是否需更新 CLAUDE.md § Scope 的「three co-named artifacts to output_dir」表述。

**Acceptance criteria（scope 定稿=full split）:**
- [x] 配置文件不再与发布产物同目录。 ✅ config 默认 `~/.podcast-studio/config.yaml`(out of output_dir);迁移脚本将 in-vault `config.yaml` 挪出;CLAUDE.md + config.example.yaml 已记
- [x] 连续性状态、记分卡报告各落独立目录;发布产物目录只剩用户要的产物。 ✅ `state/`(covered-ground/character-bible/throughline)+ `reports/`(scorecard.md)分出;`episodes/` 只留听众产物(.md/.mp3/.stance.yaml);topic_log/source_log/scratch 留 root
- [x] `config.py` fail-closed 校验新目录,缺失目录报错命名 offending key。 ✅ `test_output_dir_still_fail_closed`:subdir mkdir 在 output_dir 存在性校验**之后**,缺 output_dir 仍 raise 命名 `vault.output_dir` 且不创建子目录
- [x] 重构后全套 `pytest lib/tests/` green;路径相关单测覆盖新布局。 ✅ 306 passed;新增 config 派生子目录测试 + runner 落盘断言(published→episodes/store→state/scorecard→reports)+ topic_log root 断言
- [x] no-TTS e2e 一期 → 各产物落到正确分目录(真实路径验证,非仅单测)。 ⚠️ 部分(用户接受):确定性真实-runner 落盘断言(mock LLM 内容、真实路径代码)+ 11 站真实 live **读**路径(迁移后 episodes/state)+ 迁移往返(load_cards 找到 3 卡)。live full-publish **写**路径被正交 MiniMax 故障(qianzhongshu 站今晚超时+exit1,今早可用)阻塞,未伪造;待 MiniMax 恢复补跑
- [x] 现有文件按确认方案迁移/兼容,旧 run 不被孤立。 ✅ `tools/migrate-phase4-layout.sh` sandbox 实跑 10 文件正确归位;`load_cards(episodes_dir)` 找到 3 卡(连续性 round-trip 不孤立);幂等 + 不删 + 不覆盖

**Review checklist:**
- [x] run-phase review step(自动 implementation-reviewer)✅ `.claude/reviews/implementation-reviewer-2026-06-15-181503.md`(0 must-fix、design-fidelity clean、每个 runtime reader/writer 重指向确认;2 should-fix 均已修)
- [x] apple-dev reviewers — N/A(非 Apple UI 项目)
<!-- /section -->

---

大档的四个架构分叉(执行模型=完整代码 runner / 记忆形态=结构化 store + 渲染备忘录 + stance 卡 apparatus_used / dedup 信号=macOS NLContextualEmbedding + n-gram 回退 / scope=完整建 + 无-TTS 迭代 + 达标尺)已在本会话锁定,记入 Global Constraints。DP-002(过热 v1 阈值)、DP-003(Phase 1 等价口径)已就地解决并写入对应 phase。余下实现级选择列为各 phase「Architecture decisions(留待 write-plan)」。下面一条需你确认。

### [DP-001] covered-ground 与现有 recent_anchors 的关系 (recommended)

**Context:** 反同质化目前已有一条机制——量臣(step 5b)从 `gather_recent_bodies` 派生 `recent_anchors`(瘦关键词清单),注入 davinci brief,CLAUDE.md 称其为 anti-repeat guard(`lib/magnitude.py:189–225`)。Phase 2 的 covered-ground 是更富的同类记忆。两条 anti-repeat 信号若并存、关系不明,正是研究警告的「多信号互斥」。

**Options:**
- A 取代:covered-ground 成为 davinci 唯一的避让记忆;量臣只保留「分量/篇幅路由(none/light/medium/heavy)」职责,不再产 `recent_anchors` 避让清单。— 单一信号、最干净,但要改量臣输出契约 + CLAUDE.md 不变量描述。
- B 喂给:covered-ground 是真相源,`recent_anchors` 改为「从 covered-ground 派生的视图」,量臣消费它而非自行重抽。— 保留量臣现有形状,但仍是两层、要接线。
- C 并存:两条都注入 davinci。— 改动最小,但正是「多信号互斥」、davinci 可能困惑,研究不建议。

**Chosen:** A 取代 — covered-ground 成为 davinci 唯一避让记忆;量臣(5b)只保留「分量/篇幅路由(none/light/medium/heavy)」,不再产 `recent_anchors` 避让清单。理由:这正是大档要修的「distilled-too-thin」根因,`recent_anchors` 是被诊断太薄的旧版,covered-ground 是其富化替代;量臣的「分量路由」与「锚避让」本是两件事,解耦后量臣回归纯裁判。
