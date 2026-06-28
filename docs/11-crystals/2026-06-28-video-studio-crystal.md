---
type: crystal
status: active
tags: [video-studio, text-to-video, consistency, faithfulness, ffmpeg]
refs: [docs/06-plans/2026-06-28-video-studio-design.md]
---

# Decision Crystal: video-studio 立项

Date: 2026-06-28

## Initial Idea
照着 podcast-studio 的样子，做一条流水线：把一段写好的文本转化成结构完整的视频，实现一键成片。之前用剪映一键成片(智能分镜=多为图片加动效 / 一镜到底=纯视频片段串联)。有 MiniMax coding plan(含视频制作)，本机装了剪映 App，问剪映是否有 MCP/API。后细化：主要讲跑步话题，从《丹尼尔斯跑步方程式》/UESCA 教材(都有 PDF)中抽取；发微信视频号，6-10 分钟一个主题；先做丹尼尔斯，先有个计划——多少模块、每模块几期，再推进。

## Discussion Points
1. **成片引擎**：原想用本机剪映一键成片。验证发现剪映 10.8.0 草稿 `draft_content.json` 是密文(Python 解析失败)、macOS 无自动导出 → 剪映做不了一键。决定用 ffmpeg 自己拼，直接出 mp4。
2. **画幅/时长**：AI 初判竖屏短视频。用户改为横屏 16:9、6-10 分钟长讲解、发微信视频号。
3. **输入边界**：三选一(读PDF出稿 / 只成片 / 两入口)，选"读 PDF 出稿"——指定主题+PDF章节范围，流水线抽取→成稿→成片。
4. **课程大纲规划**：用户要求出片前先有 Stage 0——拿教材出"多少模块、每模块几期"的大纲，半自动(机器提议+用户拍板锁 syllabus)。
5. **成片模式**：智能分镜(生图+KenBurns，省额度)与一镜到底(MiniMax 视频片段)混用，分镜判官逐 beat 分配，预算门管视频额度。
6. **时间轴**：AI 初版按文本猜镜头时长(视觉优先)。改为旁白驱动——逐 beat 配音拿 MiniMax `audio_length` 当屏上时长。
7. **一致性**：AI 一度把"人物一致性"说成后续再做。用户两次纠正——人物一致性是整条视频的基本盘 V1 必做；风格也要整体一致，区别仅在 V1 先固定一种风格(非"风格一致可选")。
8. **图表**：配速/VDOT 等数据用代码真实渲染，不用 AI 生图(AI 画数字必错)。
9. **BGM**：用户过去项目吃过亏——让调小音量做出来也偏大。要求写死 dB + sidechain ducking + ffmpeg volumedetect 实测验收。
10. **调度**：用户明确"调度跟 plugin 无关"——plugin 边界=被调用一次出下一期(游标前移)，何时触发由用户决定，不进 scope。
11. **plugin 边界**：三选一(新独立 plugin / 加进 podcast-studio 一条线 / 从零)，选新建独立 plugin `video-studio`，借鉴 podcast-studio 模式与可复用 lib。
12. **建造顺序**：风险前置——先 Spike 打通最险的一条链(1beat→静图+视频片段→TTS→ffmpeg KenBurns+硬字幕+混音+BGM ducking→可播 mp4)，过了再铺全量。

## Rejected Alternatives
- **剪映草稿引擎**: Rejected because — 本机 10.8.0 草稿加密、macOS 无自动导出，做不了一键。Rejection scope: V1 不做剪映；剪映草稿作为"后续可选导出"方向仍开放。
- **竖屏短视频**: Rejected because — 内容是 6-10 分钟教学长讲解，横屏看图表更顺。
- **只成片(用户自写稿) / 两入口并存**: Rejected because — 用户要"从教材抽取"这一价值，选读 PDF 出稿。
- **在 podcast-studio 加一条 video 线 / 从零造**: Rejected because — 视频产物与音频差异大，独立 plugin 边界更干净；从零造重复造轮子。
- **AI 生成图表**: Rejected because — AI 画数字/表必错，改代码真实渲染。

## Decisions (machine-readable)
- [D-001] 成片引擎用 ffmpeg 自己拼，直接出最终 mp4；V1 不做剪映草稿导出。
- [D-002] 产物为横屏 16:9 1080p、6-10 分钟、面向微信视频号。
- [D-003] 输入=读 PDF 出稿：指定主题+PDF 章节范围，流水线抽取→成稿→成片。
- [D-004] 系统分两层：Stage 0 半自动课程大纲规划器(机器提议模块/期数，用户拍板锁 syllabus)；Stage 1 单期生产线。
- [D-005] 成片模式=智能分镜+一镜到底混用，分镜判官逐 beat 分配视觉类型，预算门管 MiniMax 视频额度(可配上限，测试期保守)。
- [D-006] 时间轴旁白驱动：逐 beat 配音用 MiniMax `audio_length` 当该 beat 屏上时长。
- [D-007] 人物一致性 V1 必做，整条视频统一(系列级 character_ref，静图 subject_reference + 视频 S2V 锚定)。
- [D-008] 风格一致性同为基本要求(非可选)；V1 先固定一种风格，后续版本支持多风格(拆教材时按模块定)。(linked: D-007 — 一致性=人物+风格两者，都不可随镜头随机变)
- [D-009] 图表(配速/VDOT 等)用代码真实渲染，不经 AI 生图。
- [D-010] 客观数据走忠实/溯源门(移植 paperline verify_anchors 到跑步领域)，每条→PDF 页码。
- [D-011] BGM 严格低音量：写死 dB + sidechain ducking + ffmpeg volumedetect 实测验收。
- [D-012] 调度不在 plugin 范围；plugin 边界=被调用一次出下一期(游标前移)。
- [D-013] 新建独立 plugin video-studio，借鉴 podcast-studio 架构与可复用 lib。
- [D-014] 所有 pipeline persona pin `--model sonnet`。
- [D-015] 建造顺序风险前置：先 Spike 打通最险链路(含 BGM 响度实测、S2V 实测，S2V 不稳则视频镜头回退静图)，再铺全量。

## Constraints
- MiniMax 视频额度 21 条/周(当前用于测试；效果好再开高档位)——预算治理须可配上限。
- BGM 历史问题：调小也偏大 → 必须实测响度验收，不靠提示词。
- Stage 0 读 PDF 在隔离子进程用 pdftotext，主会话不读 PDF(省 token)。
- MiniMax 能力已主键验证：video 21/周；image-01 16:9 + subject_reference 跨场景复用人物；speech-02-hd 返回 mp3 + audio_length。

## Scope Boundaries
- IN: ffmpeg 出 16:9 1080p mp4；Stage 0 半自动大纲(先丹尼尔斯)；Stage 1 全链；人物一致+单一固定风格；混合分镜+预算治理；忠实/溯源门；MiniMax 配音+硬字幕；BGM 严格低音量；图表真实渲染；游标按序出片。
- OUT: 剪映草稿导出；多风格；竖屏；其它教材(UESCA 后续)；调度(与 plugin 无关)。

## Source Context
- Design doc: docs/06-plans/2026-06-28-video-studio-design.md
- Design analysis: none
- Key files discussed: podcast-studio/lib/pipeline.py, podcast-studio/lib/runner.py, podcast-studio/lib/dispatch.py, podcast-studio/lib/paperline/ledger.py
