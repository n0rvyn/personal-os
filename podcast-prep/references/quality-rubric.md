# Podcast Quality Rubric — 4 KPI

Scoring rubric for daily podcast episodes. Used by:
- Phase 1: manual assessment of E2E test episodes against the enhanced pipeline.
- Phase 2: the 钱钟书 review-editor Role, scoring 3 parallel candidates and selecting one.

Each KPI is scored 1-5. The episode that best serves the creator's stated intent —
selection → insight → opinion → thought-provocation — scores high across all four.
The 5/20 evening episode 《信息平原》 is the calibration benchmark: it would score
roughly 5/5/4/5.

A supporting signal — **self-past dialectic** (a "我 X 天前是这么想的 → 为什么变了"
passage) — is not its own KPI; when present and genuine it lifts 洞察 and 思考问句.

---

## KPI 1 — 洞察密度 (Insight density)

Does the episode produce a penetrating take that goes beyond summarizing news?

| Score | Anchor |
|-------|--------|
| 1 | 纯新闻复述 + 行动建议，没有穿透性观点 |
| 2 | 有零散判断，但都停留在"值得关注"级别的弱断言 |
| 3 | 至少 1 个明确立场判断（含可证伪赌注），但停留在事实层 |
| 4 | 有 1 个跨事实的解释性洞察——解释"为什么"而不是罗列"是什么" |
| 5 | 《信息平原》级别：一个原创解释框架，让听众重新理解一整类现象 |

## KPI 2 — 命名 (Naming)

Did the episode name an emergent pattern with a reusable, vivid handle?

| Score | Anchor |
|-------|--------|
| 1 | 无命名尝试 |
| 2 | 只借用已有术语，无原创 |
| 3 | 有命名尝试，但抽象/通用/无画面感（如"AI 范式转移"） |
| 4 | 原创命名，有画面感，但复用性一般 |
| 5 | 《信息平原》级别：3-5 字、画面感强、听众能带走复用 |

## KPI 3 — 跨域 (Cross-domain collision)

Did a non-tech domain genuinely collide with the tech topic — not decorate it?

| Score | Anchor |
|-------|--------|
| 1 | 无跨域引用 |
| 2 | 提到非技术领域笔记，但只是点缀/装饰 |
| 3 | 跨域笔记被引用并挂到主线，但连接停留在表面 |
| 4 | 跨域笔记产生了真实的类比或张力，推进了论证 |
| 5 | 跨域碰撞本身就是这期的洞察来源（如 Karpathy 新闻 × Kahneman 可得性偏差） |

## KPI 4 — 思考问句 (Thought-provocation)

Does the episode close by leaving the listener actively thinking?

| Score | Anchor |
|-------|--------|
| 1 | 结尾是"持续观察"类泛泛收束 |
| 2 | 结尾是纯行动指令（"去 GitHub 跑 demo"） |
| 3 | 结尾有总结，但没有开放问题 |
| 4 | 结尾有一个引导思考的问句，但偏泛 |
| 5 | 《信息平原》级别："如果明天所有 AI 都消失，你脑子里还剩什么"——能让听众停下来的问句 |

---

## Scoring output format

```json
{
  "scores": {"洞察": 0, "命名": 0, "跨域": 0, "思考问句": 0},
  "total": 0,
  "self_past_dialectic_present": false,
  "notes": "<一句话点评：最强的一点 + 最弱的一点>"
}
```

`total` is the plain sum (max 20). When comparing candidates, break ties by 洞察 first,
then 跨域 — these two are the hardest to fake and the closest to the core KPI.
