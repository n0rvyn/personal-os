# 07 · 运动员采集（Athlete Intake）

> 源：UESCA Cert (3-16-26) 采集/评估模块（20 题画像 + PARQ/HHQ）。决策点：onboarding 收什么、为什么，喂给计划生成。
> 上层原则见 [00-index](./00-index.md)。

## 采集字段 → 科学理由 → 代码映射

| 采集项 | 科学理由（UESCA）| 代码字段 file:line | 溯源 |
|---|---|---|---|
| 经验水平 | 决定起始量/强度引入速度、恢复周密度 | `GoalFormData.swift:17` (`experienceLevel`) | ✅ |
| 当前周量 | 增量基线（连续增量 ≤3 周相对此基线）| `GoalFormData.swift:20` (`currentWeeklyMileage`) | ✅ |
| 最长单次跑 | 长跑起点、长跑上限推进 | `GoalFormData.swift:23` (`longestRunDistance`) | ✅ |
| 近期赛绩/PB | VDOT 估算（见 [02](./02-vdot-system.md)）| `GoalFormData.swift`（recentRaceDistance/Time）| ✅ |
| 静息/最大心率 | Karvonen 心率区（见 [01](./01-intensity-zones.md)）| `GoalFormData.swift:27` (`restingHeartRate`) | ✅ |
| 可用时长（工作日/周末）| 单次课时长上限、课表排布 | `GoalFormData.swift:36`（weekdayMaxDuration 默认 60）| 🔧 用户输入 |
| 生活压力 | 总负荷/恢复需求（影响量·恢复周）| `GoalFormData.swift:50` (`stressLevel` 1-5) | ✅ |
| 健康限制/伤病 | 风险规避、禁忌课型 | `GoalFormData.swift:65` (`healthRestrictions`) | ✅ |
| 目标类型/赛事/目标时间 | 计划方向（least-to-most-specific，见 [04](./04-periodization-phases.md)）| `GoalFormData.swift`（goalType/raceDistance/targetTime）| ✅ |

> ⚠️ 代码用 `healthRestrictions`（自由文本）承载伤病/限制，无结构化 `injuryHistory` 字段；UESCA 采集建议含结构化伤病史，属可增强项（非本次 scope）。

## UESCA 采集原则
- 20 题运动员画像 + PARQ（体力活动准备问卷）+ HHQ（健康史）。
- 目的：训练结构**基于个体**而非预设参数（呼应 [04](./04-periodization-phases.md) 原则 8）。

## 代码锚点
- 表单：`Runetic/Models/GoalFormData.swift`（字段 + `toDTO()` L92-134）。
- Onboarding 状态：`Runetic/Views/Onboarding2/OnboardingData.swift`。
- 喂入计划：`PlanGenerationService.buildPlanUserData`（experience→runningYears 映射、restingHR 提取）。
