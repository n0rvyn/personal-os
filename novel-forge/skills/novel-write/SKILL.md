---
name: novel-write
description: 网文逐章写引擎(用于**已在 novel-forge 管理下、已有 bible.md 的在写书**)。当用户说"写下一章""逐章写""写第N章""novel-write""draft the next chapter"时使用。编排单章循环:章纲细化→起草→rubric自评→改→多镜头评审→定稿→圣经回灌。是 novel-forge 的主写作入口(对应 dev-workflow 的 run-phase)。不适用:还没分章纲(先 novel-outline);卡在某章要破点(用 novel-unblock);**接一本无 novel-forge 圣经的外部手稿(用 novel-continue,它先逆向生成圣经)**。
model: opus
user_invocable: true
allowed-tools:
  - Read
  - Write
  - Edit
  - Bash
  - Glob
  - Task
  - Skill
  - AskUserQuestion
---

# novel-write:逐章写引擎 + 起草

对应 dev-workflow 的 run-phase,但**经济模型反转**:run-phase 的 execute 那格是 Sonnet 机械执行;这里那格是**起草**——全书手艺所在,**锁 model: opus,绝不降级 Sonnet**(crystal D-009/D-013)。

## 前置

1. 读 cwd:`bible.md`(8 块)、`outline.md`(章纲)、`.novel/state.json`(进度 + `last_钩子`)。
   - 无 `bible.md` 但 `chapters/` 已有正文(外部导入手稿)→ 提示改用 `novel-continue`(先逆向生成圣经)。
   - 无 `outline.md` → 提示先 `novel-outline`。
2. 读契约:`${CLAUDE_PLUGIN_ROOT}/references/rubric.md`(按 bible 定位权重)、`${CLAUDE_PLUGIN_ROOT}/references/corpus-craft.md`(技法)。
3. 确定本章号:`state.current_章 + 1`(或用户指定)。读该章的 outline 章纲。

## 单章循环(crystal C3;每章定稿后默认停等作者确认再写下一章 —— P4 Chosen)

更新 `state.chapter_step` 于每步前(断点恢复)。

### 1. 章纲细化(Opus)
把 outline 的本章四要素(剧情/爽点/钩子/伏笔动作)展开成详细 beat sheet:场景序列、关键对话点、本章要埋/收的伏笔(对账 bible 伏笔账本)、目标字数(= 定位日更字数)。

### 2. 起草(Opus,主线,不降级) ★经济反转点
按 beat sheet 写正文。硬约束:
- **接住 `last_钩子`**:开头承接上一章章末悬念。**第 1 章例外**:`last_钩子` 为空时,不接钩子,改从开局钩子/黄金三章起笔(见 rubric 留存轴)。
- **命中目标爽点**:用 corpus-craft 对应流派的爽点类型。
- **章末留钩子**:画面化/悬念化,避免与近几章钩子重复(查 bible 爽点节拍账)。
- **伏笔动作**:按 outline 埋/收;用 corpus-craft 红楼五法。
- **文风一致**:匹配 bible 第 8 块文风样本(视角/语气/锚定段落)。
- **人物声音**:对照 bible 人物谱"声音特征",角色说话别串味。

### 3. rubric 自评(dispatch → self-eval,Sonnet)
**dispatch `self-eval` agent(Sonnet)**做快速自评——不在本 opus 主线内联跑(否则实际按 opus 计费,正是本 plugin 要消灭的成本错配)。传:本章草稿路径 + bible 路径 + 定位权重。返回每轴**定性三级(达标/偏弱/缺失)+ 证据 + 建议**(不打数字分;留存轴给预测代理)。这是改稿前的廉价预审,区别于 Step 5 的完整 4 镜头评审。

### 4. 改(Opus)
按自评的 must-fix(高权重轴偏弱/缺失)修订草稿。

### 5. 多镜头评审(dispatch → novel-review)
**[接口:P5 填实]** dispatch `novel-review`,传:本章修订稿路径 + bible 路径 + 定位权重。返回 4 镜头(留存/结构/深度/连续性)的 must-fix / nice-to-have。
- 接口签名:`输入 = {chapter_draft_path, bible_path, 定位}`;`输出 = {must_fix[], nice_to_have[]}`。
- must-fix(尤其连续性矛盾,恒 must-fix)→ 回到 Step 4 改;nice-to-have 记录。

### 6. 定稿
写 `chapters/ch-{NNN}.md`(三位补零)。更新 state:`current_章`、`total_words`、`chapters_done`、`last_钩子`(本章章末钩子原文)。

### 7. 圣经回灌(dispatch → bible-updater)
**[接口:P6 填实]** dispatch `bible-updater`,传:本章定稿正文 + 当前 bible。返回圣经 diff + 矛盾告警。
- 接口签名:`输入 = {chapter_final_path, bible_path}`;`输出 = {bible_diff, conflicts[]}`。
- **矛盾不静默覆盖**:`conflicts` 非空 → 列给作者裁决 canon(D-007),裁决后应用 diff。
- 同步 `state.pending_伏笔`(从更新后 bible 伏笔账本统计待收数)。

## 完成与下一步(默认每章停)

> 第 {N} 章定稿({字数}字),圣经已回灌(待收伏笔 {pending}条)。
> 下一步:再次 `novel-write` 写第 {N+1} 章;或 `novel-review` 单独复审本章。

## 边界(crystal)
- **起草锁 Opus,不降级**(D-009)。本 SKILL.md model 必须 opus。
- 连续性矛盾恒 must-fix(D-012),不论权重。
- 圣经矛盾→作者裁决,不静默覆盖(D-007)。
- 只写 cwd 内(D-010)。

## 占位接口说明(P4 阶段)
Step 5(评审)/ Step 7(回灌)在 P4 为 dispatch 占位:接口签名已定。P5 建 novel-review 填实 Step 5,P6 建 bible-updater 填实 Step 7。P4 验收只要求循环 5 步主干(细化/起草/自评/改/定稿)可追溯 + 占位签名对齐。
