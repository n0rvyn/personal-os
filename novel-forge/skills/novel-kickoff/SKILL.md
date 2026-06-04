---
name: novel-kickoff
description: 网文新书立项。当用户说"开新书""立项""novel-kickoff""新写一本小说""帮我起一本网文""create a new novel"时使用。交互式设定位(爽↔文/流派/平台/日更字数)+ 核心创意 + 金手指 + 主角,在当前书目录产出初始故事圣经(bible.md)和状态文件(.novel/state.json)。不适用:已有圣经只想往下写(用 novel-outline / novel-write);续写已有手稿(用 novel-continue)。
model: opus
user_invocable: true
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - AskUserQuestion
---

# novel-kickoff:网文新书立项

立项是 novel-forge 引擎的起点(对应 dev-workflow 的 brainstorm)。产出初始**故事圣经**——后续所有 skill(分卷章纲、逐章写、评审、圣经更新)都读它。

**模型姿态**:model: opus —— 创意与定位是判断密集型,起草全书风格的源头,不降级。

**存储**:cwd 模型。本 skill 在**当前工作目录**(作者 cd 进的书目录)产出 `bible.md` 和 `.novel/state.json`。不写 cwd 外、不维护中央书库(见 plugin `references/storage-contract.md`)。

## 前置

1. 读契约:`${CLAUDE_PLUGIN_ROOT}/references/story-bible-schema.md`(8 块结构)、`${CLAUDE_PLUGIN_ROOT}/references/rubric.md`(定位→权重表)、`${CLAUDE_PLUGIN_ROOT}/references/corpus-craft.md`(技法/流派参考)。
2. 检查 cwd:若已存在 `bible.md`,提示"本目录已有圣经,是否覆盖/换目录",不静默覆盖。

## 立项流程(一题一题,每题附推荐答案 + 1 句理由)

借鉴 brainstorm 的 recommended-answer 格式:每问给推荐答案 + 理由,作者审而非从零生成。一次只问一题,等回答再问下一题。

### Step 1:定位三维度(用 AskUserQuestion 锁,文本补充)

1. **爽↔文档位**(5 档):纯爽 / 偏爽 / **均衡(推荐,金庸·基督山锚点)** / 偏文 / 纯文。
   - 推荐均衡,理由:日更连载下留存与文学深度可同时拿(keystone),除非作者明确要纯爽快节奏或纯文学向。
2. **流派**:玄幻 / 仙侠 / 都市 / 科幻 / 言情 / 历史 / 其他。决定爽点类型库 + 套路模板(见 corpus-craft)。
3. **平台**:起点男频 / 番茄 / 晋江女频 / 其他。决定留存基准线。
4. **日更字数**:推荐 3000(主流日更配额),可改。

### Step 2:核心创意(一句话故事 + 卖点)
问:"这本书一句话讲什么?最大的卖点/钩子是什么?"
推荐:基于流派给 1-2 个常见母题示例(如仙侠→凡人流逆袭 / 复仇),让作者确认或改。

### Step 3:金手指设计(子流程,金手指设计模式复用此段)
依次问(每问附该流派常见做法作推荐):
- 金手指**是什么**?
- **机制与限制**?(强调限制——无限制 = 后期失控,见 corpus-craft 反面警示"主角光环过强")
- **升级路径**?(如何随剧情变强)

### Step 4:主角
问:姓名 / 外在目标(想要什么)/ 内在动机(为什么)/ 声音特征(说话方式,起草和连续性镜头都靠它)。

### Step 5:导出 rubric 权重
读 `rubric.md` 定位→权重表,按 Step 1 的爽↔文档位查出三组轴权重(结构桥轴恒高)。生成权重快照写入圣经定位块。

## 产出

1. **写 `bible.md`**(基于 `${CLAUDE_PLUGIN_ROOT}/templates/bible.md`):
   - 第 1 块定位:填爽↔文/流派/平台/日更字数 + 导出权重表
   - 第 2 块人物谱:填主角(其余角色留空待后续)
   - 第 3 块世界观:填核心创意涉及的力量体系/世界骨架(可粗)
   - 第 4 块金手指:填 Step 3 结果
   - 第 5-8 块:留空模板(伏笔账/剧情态/爽点节拍账/文风样本,随写作填充)
2. **初始化 `.novel/state.json`**(基于 `${CLAUDE_PLUGIN_ROOT}/templates/state.json`):填 book / 定位 / current_章=0 / chapter_step="章纲细化"。
3. **不静默覆盖**:若 cwd 已有 bible.md,先问。

## 完成与下一步

> 立项完成,圣经初稿已写入 `bible.md`(定位/主角/世界观骨架/金手指)。
> 下一步:`novel-outline` 分卷分章、铺伏笔依赖图。

## 边界(crystal)
- 只写 cwd 内文件(D-010)。
- 金手指必须设限制(防失控,反面警示)。
- 立项是创意判断,model: opus 不降级(D-009/D-013)。
