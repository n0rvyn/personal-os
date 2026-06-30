# 训练科学权威层（Training Science Foundation）

> **定位**：Runetic 科学性的**唯一真相源**。所有训练计划生成、配速渲染、周期化、边界与教练叙述的科学依据以本层为准。
> **建立**：2026-06-22，从 5 个一手源直接抽取并对锚点核实。取代已废弃的 MiniMax 旧 KB（见 [CLEANUP-LOG](./CLEANUP-LOG.md)）。
> **维护**：源更新 / 代码常量变更 → 同步更新本层对应文档与 [90 审计矩阵](./90-code-traceability-matrix.md)。

---

## 一、怎么用本层

**AI / 开发检索**：按 App 决策点找文档（不是按书本章节）。要改配速逻辑 → 读 [01](./01-intensity-zones.md)/[02](./02-vdot-system.md)；要改周期化/量 → 读 [04](./04-periodization-phases.md)/[05](./05-volume-progression-recovery.md)；要核某个魔法数字有无科学依据 → 查 [90 矩阵](./90-code-traceability-matrix.md)。

**改代码科学常量前**：先在 [90 矩阵](./90-code-traceability-matrix.md)查该常量的源与溯源状态；标 ⚠️ 的常量改动属科学决策，需对源负责。

**每条规则四字段**：规则陈述 / 源引用（书·页｜模块·页｜幻灯｜xlsx）/ 代码锚点（file:line）/ 溯源状态。

## 二、溯源状态图例

| 标记 | 含义 |
|---|---|
| ✅ 已溯源 | 规则/数值可追到一手源页码，且代码与之一致 |
| ⚠️ 经验值未溯源 | 代码在用，但一手源**未支持**或treats为个性化（工程估计 / 待决策），不可冒充科学 |
| 🔧 产品上限 | 非科学，是产品/用户体验上限（如用户可调系数范围）|

## 三、源清单（5 个一手源）

| 源 | 范围 / 权威领域 |
|---|---|
| **Daniels' Running Formula (2021, 4th ed)** | 7 区强度、%VO2max/%HRmax、点数负荷体系、VDOT、6 秒规则 → [01](./01-intensity-zones.md)/[02](./02-vdot-system.md)/[03](./03-training-load-points.md) |
| **UESCA Running Coach Cert (3-16-26, 最新版 718pp)** | 周期化模型、量增量/恢复节律、质量训练规则、运动员采集、配速、环境 → [04](./04-periodization-phases.md)–[08](./08-pacing-and-environment.md) |
| **UESCA Program Design Slide Deck (37pp)** | 9 步计划设计流程、≤3 连续增量周、质量 ≤2/周 → [04](./04-periodization-phases.md)/[06](./06-quality-session-rules.md) |
| **Weekly-Running-Program.xlsx** | 单周 AM/PM + RPE/ZONE/距离/备注 粒度样例 → [09](./09-program-templates.md) |
| **Whole-Running-Program.xlsx** | 完整 19 周马拉松：块序列 + 量曲线 + 每 4 周恢复周 → [09](./09-program-templates.md) |

> 其它教练科学 PDF（过度训练/伤病/营养等）未纳入基石，清单见 [91](./91-source-pdf-inventory.md) 供自读。

> **页码基准**：所有 `p.NN` = 对应 PDF 的 `pdftotext -f NN` 物理页（可复现核对），非书内印刷页码。Daniels「reduced」PDF 含无 form-feed 的图片页，故物理页 ≠ 印刷页。

## 四、顶层开发原则（规范性硬约束）

源自上述科学，AI 与开发**必须遵守**；违反需显式授权。

1. **AI 只输出 TDL 公式，不输出具体配速** —— 配速由 iOS 端按用户 VDOT 渲染。详 [03](./03-training-load-points.md)/[02](./02-vdot-system.md)。
2. **七区强度用 Daniels 模型**（E/M/T/10K/I/H/R/FR），不引入第三套区命名。详 [01](./01-intensity-zones.md)。
3. **连续增量 ≤3 周**，之后插恢复周（量降 30–50%，RPE ≤6）。详 [05](./05-volume-progression-recovery.md)。
4. **质量训练 ≤2 次/周**，硬课后 ≥1 个恢复日。详 [06](./06-quality-session-rules.md)。
5. **「10% 增量规则」是被证伪的神话**（UESCA p.553「largely erroneous」），禁止作为规则；正解 = 原则 3。详 [05](./05-volume-progression-recovery.md)。
6. **「固定 80/20」不在 UESCA 课程中**，强度/量比例须个性化（p.503），禁止硬编码固定比。详 [06](./06-quality-session-rules.md)。
7. **马拉松长跑上限 ~20mi/32km**（慢者 ~18mi，快者 20–22mi），重累积量胜于单次长跑。详 [05](./05-volume-progression-recovery.md)。
8. **周期化是方向不是铁律** —— 结构基于个体，不是预设参数（UESCA p.466）。详 [04](./04-periodization-phases.md)。
9. **代码里标 ⚠️ 的常量（如「48h 硬课间隔」「每周 ≥2 全休」「VDOT 先验」）一手源未支持**，是工程估计，改动属科学决策。详 [90 矩阵](./90-code-traceability-matrix.md)。

## 五、目录

| 文档 | 内容 | 主要源 |
|---|---|---|
| [01-intensity-zones.md](./01-intensity-zones.md) | 七区：定义·%VO2max·%HRmax·配速推导 | Daniels |
| [02-vdot-system.md](./02-vdot-system.md) | VDOT 计算·赛时↔VDOT·配速表·先验 | Daniels |
| [03-training-load-points.md](./03-training-load-points.md) | 点数/min·1h-T=36·IF·rTSS | Daniels |
| [04-periodization-phases.md](./04-periodization-phases.md) | 周期模型·块序列·传统 vs 反向 | UESCA + 幻灯 |
| [05-volume-progression-recovery.md](./05-volume-progression-recovery.md) | 增量/恢复节律·长跑上限·10% 神话 | UESCA |
| [06-quality-session-rules.md](./06-quality-session-rules.md) | 质量上限·硬易交替·80/20 神话 | UESCA + 幻灯 |
| [07-athlete-intake.md](./07-athlete-intake.md) | 采集什么 + 为什么 | UESCA |
| [08-pacing-and-environment.md](./08-pacing-and-environment.md) | 配速策略·环境/海拔调整 | UESCA + Daniels |
| [09-program-templates.md](./09-program-templates.md) | 19 周马拉松权威样例 | xlsx |
| [10-grade-adjusted-pace.md](./10-grade-adjusted-pace.md) | GAP 坡度修正配速·Minetti 能耗曲线 | Minetti 2002 |
| [90-code-traceability-matrix.md](./90-code-traceability-matrix.md) | 科学常量审计：值/源/file:line/状态 | 全部 |
| [91-source-pdf-inventory.md](./91-source-pdf-inventory.md) | 其它教练科学 PDF 清单（供自读）| — |
| [CLEANUP-LOG.md](./CLEANUP-LOG.md) | 已删 MiniMax 旧文档 + 理由 | — |
