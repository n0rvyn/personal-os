# 03 · 训练负荷：Daniels 点数体系

> 源：Daniels' Running Formula (2021) Ch5「Points for Various Workouts」(p.108–112)。决策点：量化单次/周训练负荷、IF、rTSS。
> 上层原则见 [00-index §四](./00-index.md) 原则 1。

## 负荷追踪 vs 处方（两层两货币）

> 决策：计划 `2026-06-28-training-load-true-rtss` [Chosen-1]。负荷分两层，各用各的货币，**并存不替换**（本节为新增；下文 Daniels 点表/派生量全部保留）。

| 层 | 输入 | 货币 | 算法 / 锚点 | 溯源 |
|---|---|---|---|---|
| **追踪层**（completed run → CTL/ATL/TSB）| 实际完成跑步的 GPS / 心率序列 | **真 rTSS**（TSS 尺 **1h-T=100**）| NGP 法，`RunningTSSCalculator` | ⚠️ 工程 + 公开版近似 |
| **处方层**（TDL 公式 / 周量预算）| zone × 时长（无 GPS 序列）| **Daniels 点** | zone 点/min × min，`RTSSCalculator` | ✅ Daniels p.108-111 |

### 追踪层 = 真 rTSS（NGP 法）
- **算法**：瞬时速度（ΔcumDist/Δt）→ Minetti 坡度修正得等效平地速度 → **30s 滚动均值** → **4 次方归一化**得 NGP → `IF = NGP / thresholdPace` → `rTSS = IF² × hours × 100`。
- **尺度锚点**：1 小时 @ 阈值（T）配速 = **100**（TSS-100，1h-T=100）。
- **谱系**：TrainingPeaks rTSS = **Coggan** TSS ← **Banister** TRIMP；图形修正 = **McGregor** NGP。
- ⚠️ **公开版近似**：McGregor NGP 受专利保护；本实现是公开版近似（同 GoldenCheetah / intervals.icu 的独立 app 天花板），**非逐位等同 TrainingPeaks**。
- ⚠️ **工程估计**：GPS 海拔噪声经 Minetti 凸曲线整流偏快，坡度复用 P5 海拔低通（见 `RunDetailMapper`）。
- 代码：`Runetic/Services/RunningTSSCalculator.swift`（`rtss(samples:thresholdPaceSecPerKm:)`）。

### hrTSS fallback（室内 / 无 route）
- 室内跑无 GPS route → **心率版 hrTSS**，锚 **1h @ LTHR = 100**，同 100 尺。
- LTHR 由 **HRmax × ~0.89** / `DanielsHeartRateZones` T 区估算。
- ⚠️ **工程估计**：LTHR 由 HRmax/T 区推得，**非心率实验室实测值**。
- 无 route + 无 HR → honest-degrade（不计入 CTL，留标识区分"算不出"vs"真休息 0"，详见计划 [Chosen-4]）。
- 代码：`RunningTSSCalculator.hrTSS(hrZonePct:avgHR:durationMin:lthr:)`。

### 处方层 = Daniels 点（保留）
- 处方（TDL 公式、周量预算）**无 GPS 序列**，真 rTSS 算不出 → zone × 时长 = Daniels 点为本职，保留不动。
- 货币、点表、1h-T=36、IF 公式见下文，全部 ✅ 已溯源（Daniels p.108-111），本次**不改**。

### 提取层货币锚点（WorkoutFeatureExtractor）
- `WorkoutFeatureExtractor`（完成跑步 → `CachedWorkoutFeatures.estimatedTSS`）现产**真 rTSS(NGP) / 室内 hrTSS**（100 尺），**不再产 Daniels 点估计**。
- 下游（CTL/ATL/TSB、sparkline、RunDetail、similarity、今日 rtss）读同一 100 尺货币。

## 点数/分钟表 ✅ 代码逐值与原书一致

| 区 | 点/min | Daniels verbatim | 溯源 |
|---|---|---|---|
| E | 0.2 | "worth .2 points per minute" (p.108) | ✅ |
| M | 0.4 | "assign .4 points per minute" (p.108) | ✅ |
| T | 0.6 | ".6 points for each minute … in the T zone" (p.109) | ✅ |
| 10K | 0.8 | "for each minute in this zone, .8 points" (p.109) | ✅ |
| I | 1.0 | "each minute at I intensity to be worth 1 point" (p.111) | ✅ |
| H | 1.0 | （= I，按时长）| ⚠️（实现，见 [01](./01-intensity-zones.md)）|
| R | 1.5 | "each minute at R intensity to be worth 1.5 points" (p.111) | ✅ |
| FR | 2.0 | "each minute at FR intensity to be worth 2 points" (p.111) | ✅ |

代码：`Runetic/Services/RTSSCalculator.swift:22-31`（`zonePoints`）；镜像于 `DanielsPaceZones.swift:168-178`（`pointsPerMinute`）。

## 派生量

| 量 | 公式 / 值 | 源 | 溯源 |
|---|---|---|---|
| **1h T = 36 点** | 0.6 × 60 | Daniels 点/min 派生；代码注释亦载 (`RTSSCalculator.swift:10`) | ✅（派生）|
| Intensity Factor (IF) | `IF = sqrt(points / (0.6 × minutes))`，基线 0.6 | 工程公式（基于 T 基线）| ⚠️ 工程 |
| ReferenceWorkout | easy60=12 / threshold60=36 / marathon60=24 / 5×1K=28 / longRun90=18 / race10K=32 | `RTSSCalculator.swift:166-185` | ✅ 由点表派生 |

> IF 的 sqrt 形式是工程建模（类 TrainingPeaks IF），非 Daniels 原书；基线 0.6 = T 点率。矩阵标 ⚠️。

## 周点数参考（Daniels 原书示例）
高中起步 50/周 → 大学 150/周 → 毕业后 200+/周（p.111-112）。用作量级直觉，非硬目标。

## TDL 公式（描述层）
- AI 输出 TDL 公式串（如 `E2K + [T1K/1min]×4 + E1K`），iOS 解析 + 按 VDOT 渲染配速（原则 1）。
- 解析：`Runetic/Services/Parsers/TDLParser.swift`；结构：`Runetic/Models/Training/TDLFormula.swift`；元数据（含 rTSS）：`TDLFormula.swift:178-231` 调 `RTSSCalculator.calculate(zoneDurations:)`。

## 代码锚点

| 概念 | 文件:行 |
|---|---|
| 点数表 | `RTSSCalculator.swift:22-31` |
| 1h-T=36 基线注释/校验 | `RTSSCalculator.swift:10`, `:129-132` |
| IF 公式 | `RTSSCalculator.swift:143-159` |
| 间歇点数（work+rec+wu+cd）| `RTSSCalculator.swift:109-124` |
| TDL 解析 | `Runetic/Services/Parsers/TDLParser.swift:55-193` |
