# 09 · 计划模板（权威样例）

> 源：`Whole-Running-Program.xlsx`（19 周马拉松）+ `Weekly-Running-Program.xlsx`（单周）。这是「原则→具体计划」的桥：UESCA 抽象规则在真实计划里的样子。
> 关联：[04 周期化](./04-periodization-phases.md)、[05 量·恢复](./05-volume-progression-recovery.md)。

## A. 19 周马拉松量曲线 + 块序列

| 周 | 起始 | 块 | 周量(mi/km) | 标记 |
|---|---|---|---|---|
| 1 | Jun 1 | General Conditioning | 15 / 24.1 | |
| 2 | Jun 8 | General Conditioning | 15 / 24.1 | |
| 3 | Jun 15 | General Conditioning | 16.6 / 26.7 | |
| 4 | Jun 22 | General Conditioning | 12 / 19.3 | ⬇ 恢复 (−28%) |
| 5 | Jun 29 | General Conditioning | 22.5 / 36.2 | |
| 6 | Jul 6 | Base Fitness | 22.5 / 36.2 | |
| 7 | Jul 13 | Base Fitness | 22 / 35.4 | 含 10K 测试 |
| 8 | Jul 20 | Base Fitness | 14 / 22.5 | ⬇ 恢复 |
| 9 | Jul 27 | Base Fitness | 26 / 41.8 | |
| 10 | Aug 3 | Base Fitness | 22 / 35.4 | |
| 11 | Aug 10 | Build Fitness | 24 / 38.6 | |
| 12 | Aug 17 | Build Fitness | 16 / 25.7 | ⬇ 恢复 |
| 13 | Aug 24 | Build Fitness | 29 / 46.7 | |
| 14 | Aug 31 | Build Fitness | 33 / 53 | |
| 15 | Sep 7 | Build Fitness | 39 / 62.8 | |
| 16 | Sep 14 | Build Fitness | 18 / 29 | ⬇ 恢复 (−54%) |
| 17 | Sep 21 | Peak Fitness | 43 / 69.2 | 峰值 + 17mi 长跑 |
| 18 | Sep 28 | Peak Fitness | 39 / 62.8 | 20mi 长跑 |
| 19 | Oct 5 | Taper | 18 / 29 | |
| 20 | Oct 12 | Taper | 11 + 26.2 race | GOAL RACE |

**结构事实（佐证 UESCA）：**
- 块序列 = 经典 7 区段（[04](./04-periodization-phases.md)）：General Conditioning→Base→Build→Peak→Taper→Race。
- 恢复周 = 第 4/8/12/16 周 = **每 4 周**，量降 28–54%（[05](./05-volume-progression-recovery.md) 的 30–50% 规则）。
- 量峰 43mi（Sep21），长跑峰 20mi（Sep28）= 马拉松长跑 ~20mi 上限（[05](./05-volume-progression-recovery.md)）。
- 周一恒 OFF；质量日（tempo/间歇）被 easy 日隔开（[06](./06-quality-session-rules.md) 硬易交替）。

## B. 单周细粒度样例（AM/PM）

每日 = Workout + Training Block + **RPE** + **ZONE** + Total Distance + **Notes**：

| 日 | 课 | RPE | ZONE | 距离 | 备注 |
|---|---|---|---|---|---|
| 二 AM | 4×400M ×2（组间 400m 慢跑）| 8 | High end of LT | 3.5mi | 热身 + 组间 10min 走 |
| 二 PM | 3×800M ×2 | 9 | VO2 Max | 4mi | 15min 热身 + 4 strides |
| 三 | Easy Run | 2 | Recovery | 2mi | 选平路 |
| 四 | 5mi 含 9 段 fartlek | 6 | Steady State | 5mi | 每段 ~20s，前 2mi 热身 |
| 日 | Endurance Run | 5 | Recovery | 7mi | 心率 110–125 bpm |

> RPE + ZONE + Notes 三元组 = 人类教练的单日标注粒度，App 单日卡片可对标。

## C. App 计划生成对照点
- 块序列 → `PromptTemplates.swift:39-58` periodization guidance + `PhaseData`。
- 周量曲线 + 恢复周 → `PlanGenerationService.generateCompletePlan` framework（`WeekFrameworkData.isRecoveryWeek`）。
- 单周 AM/PM + RPE/ZONE → detailed weeks → `WorkoutRenderer` + TDL（[03](./03-training-load-points.md)）。
