# 02 · VDOT 体系

> 源：Daniels' Running Formula (2021)。决策点：从赛绩/PB 估当前能力 → 推导各区配速。
> 上层原则见 [00-index §四](./00-index.md) 原则 1。

## 概念

VDOT = Daniels 的「有效 VO2max」单值，整合跑步经济性，用单个数代表当前能力（区间 30–85）。流程：赛时 → VDOT → 七区配速表。

## 核心规则

| 规则 | 值 | 源 | 溯源 |
|---|---|---|---|
| VDOT 表区间 | 30–85（超出 clamp）| Daniels 表上下限 | ✅ |
| 锚点校验 | VDOT 60 = 17:03 5K 或更快 | Daniels p.126 verbatim | ✅ |
| 赛绩→VDOT | 取最快单项 PR 对应 VDOT | Daniels 方法 | ✅ |

## ⚠️ 配速表真相源 = 代码 JSON，非 PDF 抽表

- 真相源：`Runetic/Resources/vdot_pace_data.json` **v1.2**，2026-06 按 **Daniels-Gilbert VO2 模型**重算，四源交叉校验（vdoto2.com / kalamazooarearunners.org / Daniels 锚点 / 其它）。
- **教训**：旧 v1.0 从 PDF→text 抽表错误，VDOT 60 处约为正确值 83%，导致用户 10K 42:17 显示 VDOT 43.5（应 ~48）。记录于 `docs/09-lessons-learned/vdot-data-correction-2025-12.md`。
- **原则**：VDOT 配速/赛时数字以 JSON v1.2 为准；任何「从 PDF 表重抽」必须对锚点（VDOT 50≈5K 破 20:00、VDOT 60=17:03）复核，PDF 文本层表格不可直接信。

## ⚠️ Population prior（冷启动估计）— 非 Daniels 原书

代码在无赛绩时用人口先验估 VDOT：

| 参数 | 值 | file:line | 溯源 |
|---|---|---|---|
| 男基线 | 35 | `VDOTCalculator.swift:40` | ⚠️ 经验值 |
| 女基线 | 32 | `VDOTCalculator.swift:41` | ⚠️ 经验值 |
| 年龄调整 | −2 VDOT / 10 年（基准 30 岁）| `VDOTCalculator.swift:106-127` | ⚠️ 经验值 |
| 经验加成 | +0.5 VDOT/年，封顶 +5 | 同上 | ⚠️ 经验值 |
| 置信度 | 赛绩 0.9 / HK PR 0.6 / 先验更低 | `VDOTCalculator.swift:52-103` | 🔧 工程 |

> Daniels 原书未给此先验公式，属工程冷启动估计。改动属科学决策，须对源/数据负责。

## 代码锚点

| 概念 | 文件:行 |
|---|---|
| 赛绩→VDOT（clamp 30–85）| `Runetic/Services/VDOTCalculator.swift:52-64` |
| HK PR 推断 | `VDOTCalculator.swift:67-103` |
| 人口先验 | `VDOTCalculator.swift:106-127` |
| VDOT 表查询/插值 | `Runetic/Utils/VDOTTable.swift:67-158` |
| 配速数据 | `Runetic/Resources/vdot_pace_data.json` (v1.2) |
