---
type: crystal
status: active
tags: [novel-forge, web-fiction, story-bible, rubric, serialized-writing]
refs: [docs/06-plans/2026-06-04-novel-forge-design.md]
---

# Decision Crystal: novel-forge(立项)

Date: 2026-06-04

## Initial Idea

照着 dev-workflow 的 write-dev-guide / run-phase 流程,在 personal-os 里做一个扩写、续写、干净写小说的 plugin。要有完整的 project-kickoff brainstorm 立项讨论,再 write-dev-guide 分章节,run-phase 分章节写,再用传播学/病毒传播学及好小说的标准来 Review。

## Discussion Points

1. **题材**:起初泛指"小说" → 提出网文 vs 文学的岔路 → 定 **中文网文/连载**,rubric 以病毒传播为核心、文学性为可配置次轴。
2. **模式**:起初三种(扩写/续写/干净写)→ 定 **全支持 8 种**,共享一条引擎、不同前门接入。
3. **rubric 语料**:用户说无参考 → 检索网文三本(完美世界/仙逆/剑来)→ 用户加严肃文学(呼啸山庄/雪国)→ AI 加金庸/红楼梦/基督山 → **keystone 重新框定**:金庸·基督山"日更连载下两端通吃"消解爽↔文对立。
4. **存储**:AI 提 novel_root 中央配置 → 用户指出"书目录即项目目录,联合 AI 创作就在那个目录" → 改为 **cwd 模型**(plugin 只认 cwd,不维护中央注册表)。
5. **命名**:候选 novel-forge / story-loom / web-novel-studio → 定 **novel-forge**。

## Rejected Alternatives

- **novel_root 中央配置**:Rejected because — 多余间接层,与 dev-workflow cwd-based 不一致。
- **execute 降级 Sonnet**:Rejected because — 违反经济反转,起草是全书手艺、锁 Opus。
- **数字打分(0-100)**:Rejected because — 散文打数字分是假精度、不可行动。
- **静默覆盖圣经矛盾**:Rejected because — 应升级为作者裁决点,不静默改写 canon。
- **雪国式纯留白驱动**:Rejected because — 对网文致命(无情节无追读)。Rejection scope:仅排除"以纯留白为驱动";雪国轴保留,但权重压最低,只喂场景级名场面意境。

## Decisions (machine-readable)

- [D-001] 题材锁定中文网文/连载;rubric 以病毒传播为核心、文学性为可配置次轴
- [D-002] 8 模式全支持,共享一条"逐章写+逐章圣经更新+多镜头评审"引擎
- [D-003] phase 粒度 = 单章或 2-3 章小弧(跟日更配额走)
- [D-004] rubric 三组分层(留存 / 结构桥 / 深度护城河),桥轴恒高权重、不随定位变
- [D-005] 定位 = 爽↔文滑块 + 流派 + 平台,导出每轴权重;默认锚点 = 金庸·基督山(linked: D-004 — 桥轴恒高即源于两端通吃锚点)
- [D-006] 评分定性三级(达标/偏弱/缺失)+ 强制证据,不打数字分;留存轴只给预测代理(真实追读率上线才有)
- [D-007] 故事圣经 8 块(定位/人物谱/世界观/金手指/伏笔账/剧情态/爽点节拍账/文风样本),每章定稿后从正文析出回灌;矛盾 → 作者裁决,不静默覆盖
- [D-008] 续写/拆书复用同一抽取引擎逆向生成圣经,输出分叉,作者校验闸必过
- [D-009] 7 核心 skill 对位 dev-workflow;唯一反转:execute 那格 = 起草 = Opus 不降级
- [D-010] 存储 = cwd 模型(书目录即项目目录,plugin 只认 cwd,不维护中央注册表)
- [D-011] 高频 4 模式(新写/续写/扩写/卡文)独立 skill;低频 4(拆书/写细纲/金手指/改写)复用现有环节
- [D-012] 4 镜头评审(留存/结构/深度/连续性);连续性矛盾恒 must-fix,不论权重
- [D-013] 成本:起草 + 留存/结构/深度镜头 + 立项 + 分章纲 = Opus;连续性镜头 + rubric 自评 + 圣经抽取 = Sonnet;逆向圣经 = Sonnet 抽取 + Opus 复核
- [D-014] plugin 名 = novel-forge
- [D-015] 不 clone dev-workflow 的 skill 文件,按模式新建、当参考读

## Constraints

- 不 clone dev-workflow skill 文件(personal-os 铁律:skills grow from use, not import)
- 起草不可降级 Sonnet(经济反转)
- 用户作品独立于 plugin 代码(cwd 模型,可 git init)
- rubric 不产真实追读率(只预测代理)
- 圣经矛盾不静默覆盖(升级作者裁决)

## Scope Boundaries

- IN: 8 种创作模式(新写/续写/扩写/卡文救场/拆书/写细纲/金手指设计/改写)
- IN: 中文网文连载题材
- IN: 立项 → 分卷章纲 → 逐章写 → 多镜头评审引擎 + 故事圣经 + 三组 rubric
- OUT: novel_root 中央配置(否决)
- OUT: execute 降级 Sonnet
- OUT: 数字打分
- OUT: 静默覆盖圣经矛盾
- OUT: 雪国式纯留白驱动(雪国轴保留但权重最低)

## Source Context

- Design doc: docs/06-plans/2026-06-04-novel-forge-design.md
- Design analysis: none
- Key files discussed: dev-workflow plugin (参考蓝本), personal-os CLAUDE.md (Plugin Lifecycle 注册流程)
