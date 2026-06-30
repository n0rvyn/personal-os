# 05 · 量增量、恢复节律与长跑上限

> 源：UESCA Cert (3-16-26) Module 15 + 幻灯。决策点：周量如何涨、恢复周何时插、长跑多长。
> 上层原则见 [00-index §四](./00-index.md) 原则 3、5、7。

## ⭐ 增量节律（核心硬约束）

| 规则 | 值 | 源 verbatim | 溯源 |
|---|---|---|---|
| **连续增量上限** | **≤3 周** | "there should not be more than three consecutive weeks of increasing volume unless the training volume is low"（Principle #5, p.494-495；幻灯 5 重复）| ✅ |
| 恢复周间隔 | ≤3 周必插一次（默认 ~每 4 周）| "No more than three weeks should pass without being followed by a recovery week. The newer the runner, recovery weeks should occur more frequently"（p.539）| ✅ |
| 恢复周量降幅 | **30–50%** | "30–50 percent reduction from the previous week's volume"（p.539）| ✅ |
| 恢复周强度 | RPE ≤ 6 | "Intensity during recovery periods should be no higher than an RPE of 6"（p.539）| ✅ |

[09 模板](./09-program-templates.md) 实证：第 4/8/12/16 周量降 28–54%，每 4 周一次。

## ❌ 已废弃神话：「10% 增量规则」

- UESCA verbatim：**"The 10% volume increase rule is largely erroneous"**（p.553，主线已核实）。来源"lost in history… undocumented"（p.506）。引 Groningen 研究：10%/周组伤率 20.8% ≈ 50%/周组 20.3%，无显著差异。
- **正解** = 上面的「连续增量 ≤3 周」节律（原则 5）。
- 🚫 禁止在 prompt / 代码 / 文案中把「10%/周」作为规则。

## 长跑上限（马拉松）

| 规则 | 值 | 源 | 溯源 |
|---|---|---|---|
| 最大长跑 | ~20mi/32km（慢者 ~18mi/29km；快·低伤者 20–22mi/32–35km）| p.499 | ✅ |
| 时间替代上限 | 休闲跑者长跑 ≤ 2:30 | p.504（Daniels）| ✅ |
| 重点 | 累积量 > 单次长跑；UESCA 弱化单一长跑 | p.499 | ✅ |

## 每日分配
周量增量在一周各跑中**不均摊**，使单日增量保持小；UESCA 认为**单日量增量**比周量增量与伤病关系更强（p.508）。✅

## ⚠️ 代码中无源支撑项

| 代码声称 | file:line | 真相 | 溯源 |
|---|---|---|---|
| "最多连续增加 3 周训练量，然后恢复周" | `PromptTemplates.swift:65` | 与 p.494-495 一致 | ✅ |
| "每 4 周一个恢复周" | `PromptTemplates.swift:67` | 默认节律一致（真规则是 ≤3 周）| ✅ |
| **"每周至少 2 天完全休息"** | `PromptTemplates.swift:67` | **UESCA 3-16-26 无固定每周最少休息日**；休息按个体恢复率定，唯一硬规则是硬课后 ≥1 恢复日（见 [06](./06-quality-session-rules.md)）| ⚠️ |

> ⚠️ 行：代码把个性化的休息安排硬编码为「每周 ≥2 全休」，一手源未支持。改动属科学决策。详 [90 矩阵](./90-code-traceability-matrix.md)。
