# 08 · 配速策略与环境调整

> 源：UESCA Cert (3-16-26) 配速/环境模块 + Daniels。决策点：比赛配速建议、环境下配速放松。
> 上层原则见 [00-index](./00-index.md)。

## 配速策略（UESCA）

| 规则 | 内容 | 源 | 溯源 |
|---|---|---|---|
| 起步保守 | "start conservative, finish strong" | UESCA 配速模块 | ✅ |
| 距离专项配速 | FTHR 基础区间：5K 95–110%、马拉松 70–80% 等 | UESCA | ✅ |
| 均速/负分段 | 后程不慢于前程为佳 | UESCA/Daniels | ✅ |

## 环境调整（⚠️ 多为工程经验值）

UESCA 环境处理以**适应协议为主，定性，无逐度公式**（agent 核实：3-16-26 无 per-degree 公式）。代码用了量化系数：

| 系数 | 值 | file:line | 溯源 |
|---|---|---|---|
| 温度阈值 | >25°C 起调 | `RenderedWorkout.swift:268-269` | ⚠️ 经验值（UESCA 无逐度公式）|
| 温度调整上限 | 最大 +15%（1%/度）| `RenderedWorkout.swift` (`EnvironmentAdjustment`) | ⚠️ 经验值 |
| 海拔阈值 | >1500m 起调 | `RenderedWorkout.swift` | ⚠️ 经验值 |
| 海拔调整上限 | 最大 +10%（1%/100m）| 同上 | ⚠️ 经验值 |
| 调整因数范围 | 0.8–1.2（用户/系统可调）| `RenderedWorkout.swift:244-260` | 🔧 产品上限 |
| 环境警告阈 | combinedFactor > 1.1 提示 | `WorkoutRenderer.swift:56-61` | 🔧 工程 |

> ⚠️ 行：温度/海拔的具体阈值与百分比是工程建模，UESCA/Daniels 未给等价逐度公式（Daniels 海拔章 p.~106+ 为定性 + 海拔表）。改这些值属科学/产品决策，须标注依据。详 [90 矩阵](./90-code-traceability-matrix.md)。

## 代码锚点
- 渲染入口（应用调整）：`Runetic/Services/WorkoutRenderer.swift:28-113`、`applyAdjustments` `:342-351`。
- 环境模型：`Runetic/Models/Training/RenderedWorkout.swift:267+`（`EnvironmentAdjustment`）、`PaceRange.withEnvironment` `:122-160`。
