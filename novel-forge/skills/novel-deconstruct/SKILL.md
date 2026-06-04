---
name: novel-deconstruct
description: 拆书(拆解一本爆款网文,提炼结构模板+爽点节拍图谱当学习参考)。当用户说"拆书""拆解这本""分析这本爆款的套路""提炼节拍""deconstruct this novel""novel-deconstruct"时使用。复用逆向抽取引擎,但输出是方法论模板而非可续写圣经。不适用:要续写这本书(novel-continue)。
model: opus
user_invocable: true
allowed-tools:
  - Read
  - Write
  - Glob
  - Task
---

# novel-deconstruct:拆书(逆向提炼模板)

低频模式(crystal D-011)。与续写共享 `bible-reverse-extractor` 引擎,但**输出分叉**:拆书产出"学习模板",不是用来往下写的圣经。

## 流程

1. **定位目标作品**:用户给的章节文件/目录。
2. **逆向抽取(调用方分窗循环)** → 把目标作品切成窗口,循环 dispatch `bible-reverse-extractor`(**deconstruct 模式**),跨窗累积(同 novel-continue,防单 agent context 溢出)。
3. **产出结构模板 + 爽点节拍图谱**:
   - 卷章节奏(多少章一个高潮、卷的长度规律)
   - 爽点密度规律(爽点类型分布、距离)
   - 章末钩子套路
   - 金手指/世界观设定法
4. **主线 Opus 提炼**:把抽取结果归纳成可复用的流派模板,**写到 cwd**(如 `<cwd>/deconstruct-<书名>.md`)并返回给作者。
   > **不写进 plugin 安装目录**(cwd 模型,运行时 plugin 缓存只读)。若作者想把提炼结果沉淀进 plugin 的 `references/genre-templates.md` 流派库,那是 plugin 维护时的手动操作,不在运行时自动回灌。

## 与续写的区别
- novel-continue:逆向 → 可续写圣经 → 往下写。
- novel-deconstruct:逆向 → 方法论模板 → 喂流派模板库 / 给作者学习。

## 边界
- 拆书输出非可续写圣经,不写入某本书的 bible.md。
- 复用 reverse-extractor 引擎,不另起抽取实现(skills grow from use)。
