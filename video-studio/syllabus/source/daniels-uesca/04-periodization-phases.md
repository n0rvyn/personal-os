# 04 · 周期化与训练相

> 源：UESCA Cert (3-16-26) Module 15 + Program Design 幻灯。决策点：计划框架的相/块序列、传统 vs 反向。
> 上层原则见 [00-index §四](./00-index.md) 原则 8。

## 核心立场（⭐ 个性化优先）

UESCA：周期化是**方向不是铁律** —— "The correct training structure must be based on an individual, not a predefined set of parameters"；相"should not be rigid… provide general direction"。(p.466) 溯源 ✅。

> 这是原则 8 的来源：计划生成可用相/块作骨架，但不得把固定相参数当硬规则套到所有用户。

## 经典（线性）周期化 — 7 区段构建序列

源 p.472，✅。也正是 [09 模板](./09-program-templates.md) 19 周马拉松的实际块序列：

1. **General Conditioning**（一般适应）
2. **Base Fitness**（专项低强度，量增）
3. **Build Fitness**（引入更高强度，量增）
4. **Peak Fitness**（强度续升；量持平/降，但含最高量长跑日）
5. **Taper**（量骤降，保留短强度）
6. **Goal Event**
7. **Recover / Transition**（赛季外）

总趋势："volume steadily decreases… while intensity steadily increases"（经典型，p.472）。

层级：Microcycle(~1 周) ⊂ Mesocycle(2–6 微周) ⊂ Macrocycle(年度)（p.471）。

## 其它模型（p.473–480）

| 模型 | 要点 | 源 |
|---|---|---|
| Lydiard 金字塔 | 单峰线性，单一适应聚焦 | p.473-474 |
| Block 周期化 | 序列块，每块单一聚焦（accumulation 2–6 周 / transmutation / realization=taper）| p.474-477 |
| Hybrid（中级）| 4 周中周期：1–3 周升量/强度，第 4 周 unloading 恢复周 | p.477-478 |
| **Reverse 反向** | 强度早期高、量后期高；适合马拉松/超马（最专项的质是量）| p.480 |

**UESCA 自身立场**：反对「一次只练一个 fitness area」，主张多area同时练以保全面、防单一过度（p.478）。

**"Least to Most Specific"** 构建原则：最不专项的离赛远、最专项的离赛近（5K→耐力早/强度晚；超马→强度早/量晚）(p.480)。✅

## 代码映射

| 概念 | file:line | 溯源 |
|---|---|---|
| 传统/反向枚举 | `Runetic/Utils/PromptTemplates.swift:711-713`（traditional=基础→强化→速度→减量；reverse=速度→强化→基础→减量）| ✅ |
| 相结构 prompt 指引 | `PromptTemplates.swift:39-58` | ✅ |
| Phase 模型 | `Runetic/Models/TrainingPhase.swift:11`；`PhaseData` `PromptTemplates.swift:766` | ✅ |
| 周框架（isRecoveryWeek）| `WeekFrameworkData`（`PromptTemplates.swift` framework DTO）| ✅ |

> 注：代码 reverse 注释为「速度→强化→基础→减量」，与 UESCA「反向 = 强度早/量晚」一致。
