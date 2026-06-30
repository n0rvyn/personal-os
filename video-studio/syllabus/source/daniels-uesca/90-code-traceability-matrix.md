# 90 · 代码科学常量审计矩阵

> 每个硬编码科学常量：值 / 源 / 代码 file:line / 溯源状态。所有 file:line 已 grep 复核存在且值一致（2026-06-22）。
> 状态：✅ 已溯源（源页 + 代码一致）｜ ⚠️ 经验值未溯源（代码在用，一手源未支持/视为个性化）｜ 🔧 产品上限（非科学）。

## A. Daniels 点数与区（全 ✅）

| 常量 | 值 | 源 | file:line | 状态 |
|---|---|---|---|---|
| 点数 E/M/T/10K/I/R/FR | 0.2/0.4/0.6/0.8/1.0/1.5/2.0 | Daniels p.108-111 | `RTSSCalculator.swift:22-31` | ✅ |
| 点数镜像 | 同上 | 同上 | `DanielsPaceZones.swift:168-178` | ✅ |
| 1h T = 36 点 | 36 | 0.6×60 派生 | `RTSSCalculator.swift:10,129-132` | ✅ |
| %VO2max E/M/T/I/R/FR | 59-74/75-84/83-88/98-100/105-110/115-120 | Daniels p.83,89,111 | `DanielsPaceZones.swift:195-205` | ✅ |
| %HRmax E/M/T/10K/I | 65-79/80-89/88-92/91-94/98-100 | Daniels p.83,89,91 | `DanielsHeartRateZones.swift:38-46` | ✅ |
| VDOT 区间 | 30–85 | Daniels 表限 | `VDOTCalculator.swift:57` | ✅ |
| 6 秒规则 | ±2s/400m | Daniels 法则 | `PaceZoneCalculator.swift:43-62` | ✅ |
| H 区 = I（同 %）| 1.0/min, 98-100% | — | `DanielsPaceZones.swift:168-205` | ⚠️ 实现命名，Daniels 未单列 H |

## B. UESCA 约束（混合）

| 常量 | 值 | 源 | file:line | 状态 |
|---|---|---|---|---|
| 连续增量上限 | ≤3 周 | UESCA p.494-495 | `PromptTemplates.swift:65` | ✅ |
| 恢复周间隔 | 每 4 周 | UESCA p.539（≤3 周必插）| `PromptTemplates.swift:67` | ✅ |
| 质量训练上限 | ≤2/周 | 幻灯 Step 8 | `PromptTemplates.swift:69` | ✅ |
| **每周 ≥2 全休** | 2 天 | **UESCA 3-16-26 无此固定下限** | `PromptTemplates.swift:67` | ⚠️ |
| **硬课间隔 48h** | 48 小时 | **UESCA 仅「硬课后 ≥1 恢复日」(p.495)，无固定 48h** | `PromptTemplates.swift:69` | ⚠️ |
| 传统/反向周期 | traditional/reverse | UESCA p.472,480 | `PromptTemplates.swift:711-713` | ✅ |

## C. VDOT 先验（全 ⚠️ — Daniels 原书无此公式）

| 常量 | 值 | file:line | 状态 |
|---|---|---|---|
| 男基线 | 35 | `VDOTCalculator.swift:40` | ⚠️ 经验 |
| 女基线 | 32 | `VDOTCalculator.swift:41` | ⚠️ 经验 |
| 年龄调整 | −2/10y | `VDOTCalculator.swift:106-127` | ⚠️ 经验 |
| 经验加成 | +0.5/y, cap +5 | 同上 | ⚠️ 经验 |
| 置信度 | 赛绩0.9/HK0.6 | `VDOTCalculator.swift:52-103` | 🔧 工程 |

## D. 负荷/基线建模（工程标准，非 Daniels/UESCA）

| 常量 | 值 | 源 | file:line | 状态 |
|---|---|---|---|---|
| CTL 时间常数 | 42 天 | TrainingPeaks 标准 | `BaselineCalculator.swift:98` | ⚠️（非 Daniels）|
| ATL 时间常数 | 7 天 | TrainingPeaks 标准 | `BaselineCalculator.swift:99` | ⚠️（非 Daniels）|
| IF 公式 | sqrt(pts/(0.6×min)) | 工程（T 基线）| `RTSSCalculator.swift:143-159` | ⚠️ 工程 |
| **追踪层 rTSS (NGP)** | `IF²×h×100`，1h-T=100 | TrainingPeaks rTSS（Coggan TSS←Banister TRIMP；McGregor NGP）| `RunningTSSCalculator.swift`（`rtss`）| ⚠️ 工程 + NGP **公开版近似**（非逐位等同 TrainingPeaks）|
| **追踪层 hrTSS fallback** | 1h@LTHR=100 | 心率版 Banister TRIMP；LTHR=HRmax×~0.89 / T 区 | `RunningTSSCalculator.swift`（`hrTSS`）| ⚠️ 工程（LTHR 估计，非实测）|

> 追踪层 rTSS/hrTSS 为 completed-run 负荷货币（TSS 尺 1h=100），与处方层 Daniels 点并存；双层模型见 [03](./03-training-load-points.md) §负荷追踪 vs 处方。

## E. 环境/调整（⚠️ 工程经验 + 🔧 产品上限）

| 常量 | 值 | file:line | 状态 |
|---|---|---|---|
| 温度阈值 | >25°C | `RenderedWorkout.swift:268` | ⚠️ 经验（UESCA 无逐度公式）|
| 温度上限 | +15% | `RenderedWorkout.swift` | ⚠️ 经验 |
| 海拔阈值 | >1500m | `RenderedWorkout.swift` | ⚠️ 经验 |
| 海拔上限 | +10% | 同上 | ⚠️ 经验 |
| 调整因数范围 | 0.8–1.2 | `RenderedWorkout.swift:251,260` | 🔧 产品上限 |
| maxHR 校验 | 100–250 bpm | `DanielsHeartRateZones.swift:79` | ⚠️ 经验（合理范围）|
| 工作日/周末时长 | 60/90 min | `GoalFormData.swift:36-37` | 🔧 用户默认 |

## 汇总
- ✅ 已溯源：Daniels 点数/区/VDOT 限 + UESCA 连续增量/恢复/质量上限 + 周期枚举。
- ⚠️ 须警惕（一手源未支持，改动属科学决策）：**48h 硬课间隔、每周 ≥2 全休、VDOT 全部先验、CTL/ATL 常数、IF 公式、温度/海拔系数、H 区命名、追踪层 rTSS NGP 公开版近似、hrTSS LTHR 工程估计**。
- 🔧 产品/用户上限（非科学）：调整因数 0.8–1.2、置信度、时长默认。

> 关联交叉链接：⚠️ UESCA 项详 [05](./05-volume-progression-recovery.md)/[06](./06-quality-session-rules.md)；⚠️ VDOT 先验详 [02](./02-vdot-system.md)；⚠️ 环境详 [08](./08-pacing-and-environment.md)。
> ⚠️ 若 `docs/04-implementation/training-formula-architecture.md`（保留的架构文档）含与本矩阵冲突的数字，以本矩阵 + 源为准；不编辑该架构文档原文，在此标注差异即可（当前未发现冲突）。
