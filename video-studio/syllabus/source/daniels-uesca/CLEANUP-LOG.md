# CLEANUP-LOG · 已删 MiniMax 旧文档

> 2026-06-22。用户裁定：旧训练科学文档为 MiniMax 所写、错误百出，授权清理「不合理/不合适/胡说/片面」者。
> 判断式清理：删除**纯源摘要类**（从旧版 PDF 抽取、已被新权威层取代、且有记录在案的错误）；**保留**设计/架构/prompt 类文档（人类设计思路，非源摘要）。
> 本文件的「实际删除集」是 [Task 12 入站引用修正] 的输入。

## 实际删除集（39 文件 + 2 目录）

| 路径 | 类型 | 删除理由 | 新层替代 |
|---|---|---|---|
| `docs/06-discussions/daniels-running-formula/`（14 文件）| Daniels 章节摘要 | MiniMax 写、基于旧版、已知 VDOT 表错误（见 `docs/09-lessons-learned/vdot-data-correction-2025-12.md`），被新层逐条溯源取代 | [01](./01-intensity-zones.md)/[02](./02-vdot-system.md)/[03](./03-training-load-points.md) |
| `docs/06-discussions/uesca-running-coach/`（24 文件）| UESCA 模块摘要 | MiniMax 写、基于旧版课程、含 10% 规则等被证伪内容，被新层取代 | [04](./04-periodization-phases.md)–[08](./08-pacing-and-environment.md) |
| `docs/99-references/uesca/README.md` + 空目录 | UESCA 教材学习笔记 | 源摘要索引，基于旧版，被新层 [00-index](./00-index.md) 取代 | [00-index](./00-index.md) |

## 评估后保留（非源摘要，repoint 引用即可）

| 路径 | 保留理由 |
|---|---|
| `docs/05-prompts/uesca-principles.md` | 已核实 = 修正版 v2.0，已自行废除 10% 规则，与真源一致；24+ 入站引用，prompt 权威资产，保留 |
| `docs/05-prompts/training-philosophies.md` | prompt 资产（v1.1 已修正强度分配）；作为「科学真相」已被 [06](./06-quality-session-rules.md) 取代，保留作 prompt 用，建议日后与新层对账 |
| `docs/06-discussions/ai-coach-philosophy.md` | 设计愿景文档（个人训练模型思路），非源摘要，不含错误科学断言 |
| `docs/06-discussions/plan-generation-system-design.md` | 设计方案文档；引用了被删 KB → Task 12 改指新层 |
| `docs/06-discussions/baseline-vs-capability-model.md` | 架构分析文档（服务职责划分），非源摘要 |
| `docs/04-implementation/training-formula-architecture.md` | 架构真相（描述层/渲染层分离），与代码一致，保留 |

## 回溯
所有删除经 git 跟踪，可 `git checkout` 恢复。删除集计 39 文件。
