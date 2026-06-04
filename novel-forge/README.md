# novel-forge

中文网文连载创作引擎。把 dev-workflow 的"立项 → 分章纲 → 逐章写 → 多镜头评审"流程移植到长篇小说创作,但反转经济模型:**起草是全书手艺,锁 Opus 不降级**。核心是一本**故事圣经**——每章定稿后从正文重新析出回灌,防 AI 长篇头号死因(连续性漂移);矛盾升级为作者裁决而非静默覆盖。

## 设计来源

- 设计文档:`docs/06-plans/2026-06-04-novel-forge-design.md`
- 决策 crystal:`docs/11-crystals/2026-06-04-novel-forge-crystal.md`
- 开发指南:`docs/06-plans/2026-06-04-novel-forge-dev-guide.md`

## 核心理念

| 维度 | 取舍 |
|---|---|
| 经济模型 | 与 dev-workflow 相反:判断力大头在"起草",起草锁 Opus;抽取/连续性核查/自评用 Sonnet |
| 评分 | 无编译器 → rubric 定性三级(达标/偏弱/缺失)+ 强制证据,不打数字分;留存轴只给预测代理 |
| 防漂移 | 故事圣经 8 块每章重新析出回灌,矛盾→作者裁决 |
| rubric | 三组分层(留存/结构桥/深度护城河),桥轴恒高;按定位(爽↔文/流派/平台)调权,锚点=金庸·基督山"两端通吃" |
| 存储 | cwd 模型:书目录即项目目录,plugin 只认 cwd、不维护中央书库 |

## Skills

| Skill | 模式 | 作用 | 模型 |
|---|---|---|---|
| `novel-kickoff` | — | 立项:设定位+创意+金手指+主角,产初始圣经 | opus |
| `novel-outline` | 含写细纲(outline-only) | 分卷章纲 + 伏笔 setup→payoff 依赖图 | opus |
| `novel-write` | 含改写(改环) | 逐章写引擎:章纲细化→起草→自评→改→评审→定稿→回灌 | opus |
| `novel-review` | — | 4 镜头并行评审 + 整合 must-fix/nice-to-have | opus |
| `novel-new` | 新写 | 编排全流程(kickoff→outline→write) | opus |
| `novel-continue` | 续写 | 逆向圣经→校验→续接章纲→逐章写 | opus |
| `novel-expand` | 扩写 | 骨架→章纲→逐章扩充 | opus |
| `novel-unblock` | 卡文救场 | 聚焦破单章卡点 | opus |
| `novel-deconstruct` | 拆书 | 逆向提炼结构模板+爽点节拍图谱 | opus |

8 模式映射:新写=novel-new;续写=novel-continue;扩写=novel-expand;卡文=novel-unblock;拆书=novel-deconstruct;写细纲=novel-outline 的 outline-only 模式;金手指设计=novel-kickoff 的金手指子流程;改写=novel-write 的改环。

## Agents

| Agent | 作用 | 模型 |
|---|---|---|
| `lens-retention` | 留存力镜头(开局钩子/爽点节拍/章末钩子/升级开图) | opus |
| `lens-structure` | 结构镜头(伏笔草蛇灰线对账/多线群像/复仇结构/视角) | opus |
| `lens-depth` | 深度护城河镜头(人物烈度/情感/主题/文笔意境) | opus |
| `lens-continuity` | 连续性镜头(本章 vs 圣经,矛盾恒 must-fix) | sonnet |
| `self-eval` | 改稿前快速 rubric 自评(预审,区别于 4 镜头完整评审) | sonnet |
| `bible-updater` | 定稿后析出圣经 diff + 矛盾告警 | sonnet |
| `bible-reverse-extractor` | 续写/拆书逆向批量抽取圣经 | sonnet |

## References

`story-bible-schema.md`(8 块圣经)、`state-file-schema.md`、`storage-contract.md`(cwd 模型)、`rubric.md`(三组轴+定位调权+评分形态)、`corpus-craft.md`(语料技法库)、`genre-templates.md`(流派模板,seed 仙侠/玄幻)。

## 引擎流程

```
立项(novel-kickoff)         → bible.md 初稿(定位/主角/金手指/世界观)
  ↓
分卷章纲(novel-outline)      → outline.md + 伏笔依赖图回写圣经
  ↓
逐章写(novel-write)循环:
  章纲细化 → 起草(Opus) → rubric自评(Sonnet) → 改
    → 多镜头评审(novel-review: 4 lens) → 定稿 → 圣经回灌(bible-updater)
  ↓(每章默认停等作者确认)
续写/拆书:bible-reverse-extractor 逆向生成圣经(作者校验闸)
```

## 用法

每本书一个目录,cd 进去跑 skill,所有产出(bible.md/outline.md/chapters/.novel/)落本目录。可 `git init` 让章节即 commit。

```bash
/plugin install novel-forge@personal-os
```
