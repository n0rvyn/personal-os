---
name: novel-unblock
description: 卡文救场(写到一半卡住,突破单章卡点)。当用户说"卡文了""这章写不下去""卡住了帮我破""卡点""unblock this chapter""novel-unblock"时使用。聚焦当前卡住的章,不重跑全流程。不适用:常规写下一章(novel-write);重写整章(改写模式)。
model: opus
user_invocable: true
allowed-tools:
  - Read
  - Write
  - Glob
  - AskUserQuestion
---

# novel-unblock:卡文救场

高频模式之一(crystal D-011),续写的微型高频版。作者卡在某章某处,要快速破点,不走完整单章循环。

## 流程

1. **读现场**:cwd 的 `bible.md`(剧情态/未收伏笔/金手指)+ 卡住的章节草稿(到卡点为止)+ `outline.md` 该章纲。
2. **诊断卡点**:问作者卡在哪(剧情走不下去 / 不知道怎么转场 / 爽点起不来 / 人物动机断了),给 1-2 个针对性方向(基于 bible 的未收伏笔、金手指、人物动机找突破口;参 corpus-craft)。
3. **聚焦破点**:只续写卡点附近的段落/转场,把卡住的地方推过去,不重写全章、不重跑评审全流程。
4. **轻校连续性**:破点续写后,快速对照 bible 人物声音/设定,别引入新矛盾。

## 与 novel-write 的区别
- novel-write 写完整一章过完整循环;novel-unblock 只解一个卡点,产出片段让作者接着自己写或转 novel-write 收尾。
- 高频微型:用于"日更途中卡 5 分钟"的即时救场。

## 边界
- 不重跑全章评审(聚焦);破点后建议作者用 novel-write 收尾本章过完整循环。
- cwd 模型(D-010)。
