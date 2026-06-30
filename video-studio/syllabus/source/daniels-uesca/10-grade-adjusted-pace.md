# 10 · 坡度修正配速（GAP, Grade-Adjusted Pace）

> 权威层补全（2026-06-26）。RunDetail Phase 5 引入。
> ⚠️ **与 `08-pacing-and-environment.md` 的海拔调整是两回事**：08 讲**海拔高度**（thin-air, >1500m，稀薄空气）对配速的影响；本文讲**坡度**（grade，上下坡地形）对能耗的影响。UESCA/Daniels 未给逐度坡度公式，故引入下列外部权威源。

## 权威源

Minetti AE, Moia C, Roi GS, Susta D, Ferretti G. **"Energy cost of walking and running at extreme uphill and downhill slopes."** *Journal of Applied Physiology* 93:1039–1046, 2002. (PubMed 12183501)

10 名跑者跑步机测 −0.45..0.45 坡度的能耗，5 阶多项式拟合。是当代 Strava/Garmin GAP 算法的共同基础。

## 跑步能耗多项式 Cr(i)

`i` = 坡度小数（rise/run，如 10% 上坡 = 0.10），`Cr` 单位 J·kg⁻¹·m⁻¹：

```
Cr(i) = 155.4·i⁵ − 30.4·i⁴ − 43.3·i³ + 46.3·i² + 19.5·i + 3.6
```

- 平地代价 **Cr(0) = 3.6**。
- 有效域 **−0.45 ≤ i ≤ 0.45**（论文测量范围；域外不外推，clamp）。
- 曲线 **U 形（凸）**：最低能耗在**中度下坡** i≈−0.20（刹车前），之后回升；i=−0.45 时 Cr=4.03 > 平地（陡降刹车代价）。

| i | Cr | f = Cr/3.6 |
|---|------|------|
| 0.00 | 3.600 | 1.000 |
| 0.05 | 4.685 | 1.301 |
| 0.10 | 5.968 | 1.658 |
| −0.10 | 2.152 | 0.598 |
| −0.20 | ~1.73（经验最低）| ~0.48 |
| 0.45 | 19.426 | 5.396 |
| −0.45 | 4.032 | 1.120 |

## GAP 定义

GAP = **等代价的平地配速**（把爬坡/下坡折算成「相同能耗下平地会跑出的配速」）。

- 因子 `f(i) = Cr(i) / Cr(0)`。
- `GAP = 实际配速 / f`。
- **上坡** f>1 → GAP **更快**（更小秒/km）：爬坡很累，等效平地会快很多。
- **中度下坡** f<1 → GAP **更慢**：下坡省力，等效平地会慢。
- **极陡下坡**（i<−0.20 后）f 回升 >1 → GAP 反而更快（刹车代价）。

## 实现要点（防误用）

- **GPS 海拔噪声**：跑步 GPS 点 ~3m 间隔、海拔噪声 ±3–5m → 原始逐点坡度常 ±100% 撞 clamp。Minetti 曲线凸 → **零均值噪声不抵消，系统性偏快**。**必须先平滑海拔**（移动平均 / 最小基线距离 ~15m）再算梯度，否则平地能读出 2–3× 假快。
- **honest-empty**：`CLLocation.altitude` 非 optional、无效时返 **0.0**，仅 `verticalAccuracy <= 0` 标无效。无气压计/室内跑无海拔 → 必须 guard `verticalAccuracy`，否则伪造平地 GAP。缺海拔段 → GAP `nil`（隐），**不退化为 == 实际配速**。

## 代码落点

- `Runetic/Services/GradeAdjustedPace.swift` — `minettiCost(gradient:)` / `gapFactor` / `adjustedPaceSec`。
- `Runetic/Models/Mappers/RunDetailMapper.swift` — `perKmGradeAdjustedPaceSecs`（段内逐点平滑→Cr 加权平均→f→修正配速）。
- 见 `90-code-traceability-matrix.md` 对应条目。
