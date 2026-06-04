---
name: novel-review
description: 网文章节多镜头评审。当用户说"评审这章""review 这章""审一下""多镜头评审""novel-review"时使用,也被 novel-write 的单章循环 Step 5 调用。并行 dispatch 4 镜头(留存/结构/深度/连续性),各产分级+证据+建议,整合成 must-fix / nice-to-have;连续性矛盾恒 must-fix。不适用:还没有定稿/草稿的章节。
model: opus
user_invocable: true
allowed-tools:
  - Read
  - Glob
  - Task
---

# novel-review:4 镜头并行评审

对应 dev-workflow 的 review-execution(并行多镜头 + 整合)。4 镜头 = rubric 三组轴各一镜 + 1 连续性镜。

**模型姿态**:model: opus —— 评审整合是判断与综合,orchestration 不降级。

## 输入(被 novel-write Step 5 调用时的接口)
`{chapter_draft_path, bible_path, 定位}`(定位含三组轴权重)。独立调用时:从 cwd 取本章草稿/定稿 + `bible.md` + `.novel/state.json` 的定位。

## 流程

1. 读 `${CLAUDE_PLUGIN_ROOT}/references/rubric.md` 取定位权重。
2. **一个 batch 并行 dispatch 4 镜头 agent**(Task 工具,单消息多 Task):
   - `lens-retention`(Opus,留存轴组)
   - `lens-structure`(Opus,结构桥轴组,对账 bible 伏笔账本)
   - `lens-depth`(Opus,护城河轴组)
   - `lens-continuity`(Sonnet,本章 vs 当前 bible 一致性)
   每个传:章节正文路径 + bible 路径 + 该镜负责的轴 + 权重。
3. 各 agent 返回该组每轴的 **分级(达标/偏弱/缺失)+ 证据(引文)+ 建议**。
4. **整合**(crystal D-012):
   - 高权重轴上"偏弱/缺失" → **must-fix**
   - **连续性矛盾(硬伤)→ 恒 must-fix,不论权重**
   - 其余偏弱 → nice-to-have
5. 输出 `{must_fix[], nice_to_have[]}`。独立调用时呈现给作者:分级表 + must-fix/nice-to-have 汇总。

## Agent 返回校验(claim ≠ fact)
4 镜头返回后,抽验各 agent 引用的证据(章节段落确实存在)再纳入整合。沿用 run-phase 的 agent dispatch 校验姿态。

## 边界
- 连续性矛盾恒 must-fix(D-012)。
- 每镜输出必含证据,无证据不算完成(D-006)。
- 不打数字分,只定性三级(D-006)。
