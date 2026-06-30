# 01 · 强度区（Daniels 七区）

> 源：Daniels' Running Formula (2021, 4th ed)。决策点：配速/心率渲染、教练区命名、单日课表强度。
> 上层原则见 [00-index §四](./00-index.md) 原则 2。

## 七区定义表

| 区 | 全称 | %VO2max | %HRmax | 目的 | 源 | 溯源 |
|---|---|---|---|---|---|---|
| **E** | Easy | 59–74% | 65–79% | 有氧基础、毛细血管化、抗伤、恢复 | Daniels p.83 | ✅ |
| **M** | Marathon | 75–84% | 80–89% | 马拉松专项耐力、配速感 | Daniels p.89 | ✅ |
| **T** | Threshold | 83–88% | 88–92% | 乳酸阈、清除能力（"comfortably hard"）| Daniels p.91 | ✅ |
| **10K** | (T–I 间) | 88–92% | 91–94% | 阈—VO2max 过渡 | Daniels p.109（.8 点段）| ✅ |
| **I** | Interval | 98–100% | 98–100% | VO2max 上限 | Daniels p.111 | ✅ |
| **H** | Hard | ≈I | 98–100% | 基于时长的 VO2max 努力（I 的时间表述）| 代码 `DanielsPaceZones` | ⚠️ |
| **R** | Repetition | 105–110% | —（段太短不用 HR）| 速度、跑步经济性 | Daniels p.111 | ✅ |
| **FR** | Fast Rep | 115–120% | — | 无氧功率、神经肌募集 | Daniels p.111 | ✅ |

verbatim 锚点：E = "59 to 74 percent of O2max or about 65 to 79 percent of maximum heart rate"(p.83)；M = "84 percent of O2max or about 80 to 89 percent"(p.89)；R/FR = "105 percent to 110 percent"／"115 percent to 120 percent"(p.111)。

## 关键规则

- **6 秒规则**：相邻强度区配速差 ≈ 6 s/400m（R→I→T 梯度）。源 = Daniels 经验法则。溯源 ✅（法则）。
- **R/FR 不用心率**：段太短，心率滞后，按配速控制。
- **E 区双重边界**：%VO2max 与 %HRmax 区间并存，渲染取交集；低 VDOT(<34) 时 I/R/FR 配速不渲染（太快不安全）。

## 代码锚点

| 概念 | 文件:行 |
|---|---|
| 区枚举 E/M/T/10K/I/H/R/FR | `Runetic/Models/Training/DanielsPaceZones.swift:157` |
| 各区 %VO2max 字符串 | `DanielsPaceZones.swift:195` (`vo2maxRange`) |
| 区解析（配速→区）| `DanielsPaceZones.swift:229-268` |
| 心率区 %（E 0.65–0.79 等，Karvonen）| `Runetic/Models/Training/DanielsHeartRateZones.swift:35-67` |
| VDOT→各区配速 | `Runetic/Services/PaceZoneCalculator.swift:26-33` |
| 6 秒规则校验 | `PaceZoneCalculator.swift:43-62` |

## 溯源备注（⚠️）
- **H 区**：代码把 H 当 I 的「按时长」变体（同 %），Daniels 原书未单列 H 为独立 %VO2max 区。属实现命名，矩阵标 ⚠️。
- 心率 Karvonen 实现细节（restingHR 缺省回退 %HRmax）见 [90 矩阵](./90-code-traceability-matrix.md)。
