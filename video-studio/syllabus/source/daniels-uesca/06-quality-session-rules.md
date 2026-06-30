# 06 · 质量训练规则与硬易交替

> 源：UESCA Cert (3-16-26) + Program Design 幻灯。决策点：每周几次质量训练、硬课如何隔、强度/量比例。
> 上层原则见 [00-index §四](./00-index.md) 原则 4、6。

## ⭐ 质量训练硬约束

| 规则 | 值 | 源 | 溯源 |
|---|---|---|---|
| **质量训练上限** | **≤2 次/周**（常见 1 硬 + 1 阈）| 幻灯 Step 8 | ✅ |
| **硬课后恢复** | 每个硬课日后 **≥1 恢复日** | Program Design Principle #3, p.495 | ✅ |
| 恢复周强度 | RPE ≤6（见 [05](./05-volume-progression-recovery.md)）| p.539 | ✅ |

## ❌ 已废弃神话：「固定 80/20」强度分布

- 全文扫描 UESCA 3-16-26 对 "80/20 / polarized / intensity distribution" **零命中**。
- UESCA 明确立场：**"the ratio between [intensity and volume] must be configured individually"**（p.503）；并批评机械的 Zone-2 教条（p.499-501）。
- **正解**：强度/量比例**个性化**，基于个体反馈，不设固定比。
- 🚫 禁止硬编码固定 80/20 或任何全局比例。

## 硬易交替原则
质量日之间夹 easy/恢复日；新手恢复需求更高（恢复日更密）。[09 模板](./09-program-templates.md) 实证：周质量日（tempo/间歇）被 easy 日隔开，周一恒 OFF。

## ⚠️ 代码中无源支撑项

| 代码声称 | file:line | 真相 | 溯源 |
|---|---|---|---|
| "质量训练：每周最多 2 次" | `PromptTemplates.swift:69` | 与幻灯 Step 8 一致 | ✅ |
| **"间隔 48 小时"** | `PromptTemplates.swift:69` | **UESCA 3-16-26 无固定 48h 硬课间隔规则**；只有「硬课后 ≥1 恢复日」的定性规则（Principle #3）。48h 是常见经验近似但非 UESCA 明文 | ⚠️ |

> ⚠️ 行：代码把「硬课后 ≥1 恢复日」量化为固定「48 小时」，一手源未明文支持该具体数值。属合理工程近似，但标 ⚠️ 以示非源直出。详 [90 矩阵](./90-code-traceability-matrix.md)。

## 代码锚点
- 质量/恢复约束 prompt：`Runetic/Utils/PromptTemplates.swift:69`（及 L65-69 整段约束）。
- 教练语气/频率（影响叙述非科学规则）：`PromptTemplates.swift` `CoachToneParams`/`CoachFrequency`。
