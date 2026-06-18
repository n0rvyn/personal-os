---
type: design
status: active
tags: [paper-digest, multi-line-engine, isolation, faithfulness-gate, arxiv]
refs:
  - CLAUDE.md
  - skills/podcast/SKILL.md
  - lib/pipeline.py
  - lib/runner.py
  - docs/06-plans/2026-06-14-big-track-redesign-dev-guide.md
---

# 设计：podcast-studio 新增第三档「AI论文科普」

## 设计概念

在 podcast-studio 里加第三档播客「AI论文科普」。它和早/晚间共用一个被抽成
**与线无关**的引擎（走表 → 判门 → 派发 → 重试/并行/skip/fail_soft → resume），
但拥有自己独立的拓扑、人设、评分尺、声音、连续性存储。这档每天**自主从 arXiv
（可配源）发现并抓取一篇论文的全文**，由选题判官按重要性+可解释性+新鲜度+去重
选定，委员会-lite 并行产出 2-3 份"变讲法不变观点"的解读稿，过一道对论文全文
溯源、查夸大、保留局限的**忠实门**，选出最清楚最忠实的一份，用论文线自己的
讲解者声音定稿、TTS。硬约束靠"先抽引擎并证早晚间零变化(Phase 1)、再加论文线
(Phase 2+)"的顺序 + 一条断言两线零交叉依赖的结构测试来守住。

定位区别：早晚间是**观点机器**（vault-leads、观点优先、反同质化）；论文科普是
**科普机器**（材料优先、讲清框架、忠实第一、主播观点退场）。

## 硬约束（用户 2026-06-18 明确，不可妥协）

1. **必须不影响早、晚两档播客**——它们现有行为零变化。
2. **后续两条线各自优化时不打架**——调一条线碰不到另一条线读的文件。

## 已锁定决策（DP）

经 grill-protocol 逐条拍板（用户 2026-06-18）：

| DP | 决策 | 理由摘要 |
|----|------|----------|
| **DP-C1** | 自主发现 + 抓论文**全文**做原文分析；不要二手论点、不要只看摘要 | 忠实要求对原文核对；"AI 每天解读一篇"要求自动 |
| **DP-C2** | arXiv 为 v1 主源（cs.AI/cs.CL/cs.LG 等可配，**接受预印本**）；实验室博客/顶会留作以后可配附加源 | 只有 arXiv 同时满足"自动发现+抓全文+非二手"；AI 领域 arXiv 是事实标准 |
| **DP-C3** | "选题判官"persona 按【重要性 + 可解释性 + 新鲜度 + 对 paper-log 去重】选 1 篇；热度信号(如 HF Daily Papers)只做发现/排序 | 每天候选几十上百必须收敛；"讲给外行"决定可解释性本身是选题标准；热度=二手策展非二手分析，不碰红线 |
| **DP-C4** | 忠实门 = 声称→论文全文溯源 + 不夸大 + 保留作者自陈局限；采集先抽带原文锚点的"论文事实账" | "忠实"=不超出论文说的；科普两大失真源(夸大/漏局限)必须显式拦 |
| **DP-C5** | 委员会-lite：2-3 份解读稿"变讲法不变观点"，用科普尺选最清楚最忠实的一份，忠实门对所有稿生效 | 清晰度有方差，多生成挑最清楚直接提升听懂率；复用引擎并行+评分骨架；差异在讲法不在立场，不违背"观点退场" |
| **DP-C6** | 论文线自带独立"讲解者"声音文件，不挂主播共享 Character Bible、不放 bible 蒸馏站 | bible 从开发日志现蒸馏，共享=为早晚间调 bible 会连带改论文声音(打架)；观点退场要求不带主播世界观 |
| **DP-A1** | 引擎抽成**与线无关**：循环不变，把硬 import 的 stance/covered-ground/select_draft/长度门改为**按"线"注入**(每线交 bundle = topology+gate_map+executor_map+editorial_loader+agent_dir) | 共享一份引擎(无基建漂移) + 可调面物理分离 |
| **DP-A2** | 早晚间零变化的验收门：① 现有 490 pytest+8 bats 一字不改全绿；② `load_pipeline("morning"/"evening")` 拓扑 byte-identical；③ 06-14 回归样本断言确定性站点行为不变；④ 真实 no-TTS e2e 跑早晚各一期产物结构等价 | honor"必须不影响"——重构是行为保持式，必须有证明 |
| **DP-A3** | "不打架"写成结构测试：论文线模块不 import 观点线专属模块(stance/covered-ground/magnitude/bible)，反之亦然；论文人设/拓扑/连续性/输出各在自己位置 | 硬约束要可测，不能只靠自觉；边界 grep 可见 |
| **DP-A4** | 顺序：Phase 1 只做引擎抽取+回归护栏(不加任何论文功能)；论文线进 Phase 2+ | 先证零变化、再加新线是 honor 硬约束的关键顺序 |

## 架构

### "线(line)" 抽象

在 show 之上加一层"线"：早间、晚间是**观点线**上的两个 show（共用拓扑、只差
编辑文本）；论文科普是**论文线**上的一个 show。引擎按"线"注册，不再按 show 写死。
`load_pipeline(show)` 改为：show → 查所属线 → 返回该线拓扑。`load_pipeline(
"morning"/"evening")` 必须返回与重构前 byte-identical 的表。

### 引擎抽取（DP-A1）

`lib/runner.py` 的"走表 → 解析门参数 → 判门 → 派发(agent/code) → 重试/并行/
skip/fail_soft → resume"循环留作**与线无关引擎**。现被硬 import 的 stance /
covered-ground / select_draft / 长度门 改为**注册时注入**：

- **观点线 bundle** = 完整复刻今天的接线（同 `_build_steps()`、同 gate、同 custom
  executor、`agents/`、`references/{morning,evening}.md`）。
- **论文线 bundle** = 自己的拓扑 + 自己的 gate(忠实门/科普选稿/paper-log 门) +
  自己的 executor + `agents/papers/` + 论文线编辑文件。

### 隔离地图（DP-A3 落地）

| 关注点 | 论文线（新） | 观点线（不动） |
|--------|--------------|----------------|
| 人设 | `agents/papers/` | `agents/` |
| 拓扑 | `lib/pipeline_papers.py` | `lib/pipeline.py` |
| 线专属逻辑 | `lib/paperline/`（选题/抓全文/事实账/科普 select/忠实门/paper-log） | `lib/{stance,coveredground,magnitude,bible}.py` |
| 连续性 | `论文线输出/state/paper-log.yaml` | stance.yaml / covered-ground.yaml / character-bible.md |
| 输出 | 论文线自己的输出子目录 | 现有 output_dir |
| 引擎 | `lib/runner.py`（抽线无关 + 线注册表，**两线共用**） | 同左 |

结构测试断言：`lib/paperline/*` 与 `lib/pipeline_papers.py` 不 import
stance/coveredground/magnitude/bible；观点线模块不 import paperline。

## 论文线站点拓扑

| # | 站点 | 类型 | 产出 | 备注 |
|---|------|------|------|------|
| 1-3 | config / editorial / scratch | code（引擎共用） | 加载论文线配置+编辑、开 scratch | |
| 3a | 同日/同篇重跑护栏 | code | fail-fast | 今天本线已出 或 这篇已在 paper-log |
| 4 | 选题判官 | agent | 论文 id + 元数据 | 抓 arXiv 候选摘要 → 四条选 1 |
| 5 | 抓全文 | code/agent | 论文全文 text | HTML/PDF→text，实现期验证抓法 |
| 6 | 论文事实账 | agent | `paper-ledger.json` | 问题/方法/关键结果数字/作者自陈局限，挂原文锚点 |
| 7 | 委员会-lite 解读 ×2-3 | agent 并行 | `paper-draft-{A,B,C}.md` | 从事实账写，变讲法不变观点，过长度门 |
| 8 | 科普评分+选稿 | agent+code | 选中稿 | 准确/清晰/框架还原/可读；论文线自己的确定性 select |
| 9 | 定稿（讲解者声音） | agent | `{title, body}` | 用论文线讲解者声音统一 |
| 10 | 忠实门（阻塞 retry=1） | agent+code | `faithfulness-verdict.json` | 溯源+夸大+漏局限；不过打回 9，二次失败停线 |
| 11-12 | 口播稿 → TTS | agent（jay 共用，可 no-tts） | 纯文本稿 → mp3 | |
| 13 | 发布 | code | .md/.mp3 → 论文线输出子目录 | |
| 14-15 | paper-log 写入 + 门 | code | `paper-log.yaml` append | 阻塞门保证写入 |
| 16 | cleanup | code | — | |

**砍掉（观点机器）**：量臣分量判官、covered-ground 蒸馏/更新、bible 蒸馏、
台账卡(stance)、throughline、resonance 自评、当日新闻背景。
**换成**：选题判官、抓全文、论文事实账、忠实门、paper-log。
**复用引擎机制**（同引擎跑不同 bundle，非复制代码）：并行 fan-out、通用门
check_artifact/check_min_chars、命名/发布、TTS、retry 循环、resume。

## 内容规格

### 结构骨架（4 段忠实解读，写进论文线 editorial）

1. **① 冷开场钩子**：这篇在解决什么问题、外行为什么该在意（具体场景/比喻把问题
   讲活；不从"今天讲一篇论文"起手）。
2. **② 它怎么做的**：核心方法/框架，大白话+比喻翻译术语（框架还原主战场）。
3. **③ 结果说明了什么**：关键发现/数字，老实讲（"提升 X%"而非"解决了"）。
4. **④ 意义 + 局限**：对外行/行业意味着什么 + 作者自陈边界（不吹）。

### 科普评分尺四维（1-5；论文线自己的 select，物理隔离于观点线锁死的 select_draft）

- **准确**：1=有夸大/编造/漏关键局限；3=基本忠实但含糊；5=完全忠于论文、数字
  精确、局限齐全。（科普命门维度）
- **清晰**：1=术语堆砌外行读不懂；3=部分翻译；5=核心术语全翻成大白话/比喻，外行
  能复述大义。
- **框架还原**：1=碎知识点堆砌无骨架；3=有结构但断链；5=问题-方法-结果逻辑链
  完整、抓住论文骨架。
- **可读**：1=报菜名式陈列；3=平淡；5=有钩子/比喻/节奏，像人在讲。
- **选稿**：四维总分最高，破平先看准确、再看清晰。（论文线自己的确定性 select，
  不碰观点线的 select_draft）

### 忠实门细节（论文线 step-10）

- 输入：定稿 body + 论文事实账 + 全文。
- 检查：① 每个客观声称(事实/数字/结论)能溯源到事实账某条或全文某处；② 夸大
  检测(声称强度 > 论文原文强度 → flag)；③ 局限保留(论文自陈关键局限被 body
  丢掉 → flag)。
- 纪律：代码门 recompute，不信 agent 的 per-claim 自标（同 select_draft 不信
  `selected` 的纪律）。
- 失败：打回定稿(9)带 flagged，要么补溯源要么软化/补局限；retry=1；二次失败
  停线、不发半成品（此时 .md/.mp3 还没发）。

### paper-log 模型

- 存 `论文线输出/state/paper-log.yaml`（论文线自己的连续性文件，不碰
  stance/covered-ground）。
- 每条：arXiv id、标题、日期、核心概念 tags。
- 用途：选题判官查它去重（讲过的/太近的同概念）；同日重跑护栏。
- append-only（同 stance 纪律），但无 bets、无观点。

## 仍开放（不阻塞设计概念，实现期定，按真实样本验证）

- 具体 discovery feed / arXiv API / 全文抓取（HTML vs PDF 提取）→ 实现前用真实
  样本验证结构再定（规则：实现前必须验证输入样本）。
- paper-log 去重粒度 / 衰减策略。
- 同日重跑护栏精确语义（整线一天一篇 vs 一篇一讲）。
- 第三 slot 时段 + /loop 排法；命令名（`/podcast papers`? 还是新命令）；输出
  目录具体命名（独立 output_dir vs 现有 output_dir 下的论文线子目录）。

## 分阶段（依赖序）

- **Phase 1（地基，必须先且证零变化）**：引擎抽成线无关 + 线注册表(bundle) +
  观点线 bundle 复刻现有接线 + 回归护栏(DP-A2 四门) + 不打架结构测试骨架(DP-A3)。
  **不加任何论文功能。**
- **Phase 2（论文采集侧）**：选题判官 + 抓全文 + 论文事实账；真实样本验证 arXiv
  发现+抓取。
- **Phase 3（论文生成侧）**：委员会-lite 解读 + 科普评分选稿 + 讲解者定稿 +
  忠实门。
- **Phase 4（连续性+发布+收尾）**：paper-log + 输出目录 + 命令 + slot + 真实 e2e。

## 下一步

`/crystallize` 锁定上述 DP（供 plan-verifier 独立上下文审阅）→ `/write-dev-guide`
拆 4 阶段 → 逐阶段 `/run-phase`。Phase 1 是行为保持式重构，必须先跑通 DP-A2
四门证早晚间零变化，再开 Phase 2。
