# Decision Crystal: video-studio Phase 7 (产品化收尾)

Date: 2026-06-30

## Initial Idea
Phase 7 收尾 video-studio：用户经 skill 触发 Stage 0（大纲规划）/ Stage 1（出片），
补 full_e2e 端到端评测装置，按 personal-os lifecycle 把插件注册进 marketplace。
dev-guide `docs/06-plans/2026-06-29-video-studio-dev-guide.md` Phase 7 段。

## Discussion Points
四个架构决策在 self-pacing Step 2 上手前由用户当面拍板（plain-language 重问两轮）。
Stage 0 形态用户委托我判断（给了判据：自主更强用 skill，规则更强用小程序）。

## Rejected Alternatives
- 单个 `/video` skill 带 plan|make 子命令（对标 podcast）—— 用户选两个独立 skill。
- Stage 0 加 `python -m lib.planner` 生产 CLI 驱动 —— 判断活归 skill，机械（存盘/校验/游标）已在 lib/syllabus.py，无需多写 driver。
- spike 全留作回归基线 / 本阶段不碰 —— 用户选搬出函数后删脚本。

## Decisions (machine-readable)
- [D-001] skill 入口 = **两个独立 skill**：`/video-plan`（Stage 0 大纲规划）+ `/video-make`（Stage 1 出片），各一份 SKILL.md。不走 podcast 的单 skill 子命令形态。
- [D-002] `/video-make` = **thin shell 调 `python -m lib.runner --config ...`**，拓扑不写进 prose（拓扑=数据，在 lib/pipeline.py）。
- [D-003] `/video-plan` (Stage 0) = **skill（Agent dispatch planner persona）**：判断活（把训练科学拆成教学序列）归 AI 出大纲草稿→展示给用户→点头→调 lib/syllabus.py(save_syllabus) 存盘。机械的存盘/字段校验/游标已在 lib/syllabus.py。判据：Stage 0 自主性 > 规则性。
- [D-004] spike 处理 = **搬出 `measure_gap`（+ helpers `probe_duration_s`/`_volumedetect_mean`/`_ffprobe`/`probe_dimensions`）进 lib → 断开 production→spike 耦合 → 删 `spike_run.py`+`spike_acceptance.py` → 保留 `SPIKE_FINDINGS.md`（API 硬事实参考）。** 须先迁移再删（assemble 步依赖 measure_gap，先删会断片）。重定向 importer：`lib/wiring.py:525`、`evals/video_render_e2e.py:53`。
- [D-005] eval 缓存边界（dev-guide 已定，沿用不再问）：付费媒体/S2V 走缓存，script/忠实门/分镜/合成 **live**。video_full_e2e 镜像 `podcast-studio/evals/paperline_full_e2e.py`。
- [D-006] 自跑节奏 = self-pacing **phase 模式**（P7 是最后一阶段），到绿停在 seam 等用户 review，点头才 commit（用户工作节奏=硬约束）。

## Constraints
- ⛔ LLM 一律 `claude -p --model sonnet`，**永不提换直连 API**（破坏插件模型，用户极强烈）。
- 台词=忠实教学+温度，不编观点（DP-603）。
- 跑 driver/eval 必须 `export FFMPEG_BIN=~/.local/ffmpeg-libass/ffmpeg FFPROBE_BIN=~/.local/ffmpeg-libass/ffprobe`。
- 能自己验的自己验：claude 在 PATH，full_e2e live 自己跑，别甩用户。
- 插件目录约定 = **root 层**（`./video-studio`，对标 `./podcast-studio`），不在 `plugins/` 下；README 落 `video-studio/README.md`。

## Scope Boundaries
- IN: skills/（video-plan + video-make）、evals/video_full_e2e.py、marketplace.json + 两个 workflow + 根 README + video-studio/README.md 注册全连通、ffmpeg(libass) 安装文档 + 缺 libass 清晰报错、spike 迁移+退役。
- OUT: 新功能（运镜增强 nice-to-have、横移起点等 P5 NH 留着）；`/finalize` 跨阶段验收（seam 之后单独跑）。

## Source Context
- Design doc: docs/06-plans/2026-06-29-video-studio-dev-guide.md Phase 7 (line 240-272)
- Handoff: docs/06-plans/2026-06-30-video-studio-phase7-handoff.md
- Naming ref: podcast-studio/skills/podcast/SKILL.md; eval ref: podcast-studio/evals/paperline_full_e2e.py
