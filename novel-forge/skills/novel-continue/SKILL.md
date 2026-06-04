---
name: novel-continue
description: 续写已有手稿(接一本断更/在写的连载)。当用户说"续写""接着写这本""往下写已有的书""continue this novel""novel-continue"时使用。先逆向析出故事圣经→作者校验→续接章纲→逐章写。不适用:从零开新书(novel-new);扩充短骨架(novel-expand)。
model: opus
user_invocable: true
allowed-tools:
  - Read
  - Write
  - Glob
  - Task
  - Skill
  - AskUserQuestion
---

# novel-continue:续写已有手稿

高频模式之一(crystal D-011)。续写的真正难点是"接住原作的设定/文风/未收伏笔"——靠逆向圣经把它结构化。

## 流程

1. **定位已有手稿**:cwd 的 `chapters/`(或作者指定的手稿路径/目录)。
2. **逆向析出圣经(调用方分窗循环)** → 把已有章节切成窗口(如每 50 章一窗),**循环 dispatch `bible-reverse-extractor`(continue 模式)**:每窗传该窗章节 + 上一窗返回的 `prior_bible`,累积。最后一窗返回完整 bible 草稿(突出**未收伏笔清单 + 文风样本**)。
   > 不要一次 dispatch 吞全书——百万字连载会撑爆单个 agent context。分窗循环、跨窗累积才是"全量分批"(crystal P6)。
3. **主线 Opus 复核**:复核文风样本是否抓准、未收伏笔是否齐全(抽取会漏)。
4. **作者校验闸**(必过,D-008):把逆向圣经草稿给作者确认/修正,再写入 `bible.md`。
5. **续接章纲** → 调 `novel-outline`,从当前剧情态往下排新卷/章(衔接未收伏笔的回收计划)。
6. **逐章写** → 调 `novel-write`,从下一章续写。

## 边界
- 作者校验闸不可省(逆向抽取必有漏判,D-008)。
- 文风样本是续写命门:起草严格锚定它,防止续写文风跳脱原作。
- cwd 模型(D-010)。
