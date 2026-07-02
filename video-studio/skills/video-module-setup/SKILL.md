---
name: video-module-setup
description: "/video-module-setup — 做某个模块之前先锁画风 + 生成该模块的主讲人物图（模块级）。读 syllabus 取当前/指定模块 → 问用户选画风（开放词表）→ 按画风生成该模块人物参考图 → 写回 module.art_style + module.character_ref。触发词：锁画风、做模块前、模块起始、生成人物图、/video-module-setup。⚠️ 含付费出图，需用户在场。"
allowed-tools:
  - Bash
  - AskUserQuestion
---

# /video-module-setup — 模块级画风锁定 + 每模块人物图

在开始产出一个模块的各期视频**之前**，先给这个模块定一个画风、并据此生成该模块的主讲人物参考图。画风是**模块级**的（不同模块可不同）；色调是整部剧默认或由导演每期定（见 grade.COLOR_GRADES），不在这一步。

核心逻辑是纯函数 `lib.module_setup.prepare_module(module, syllabus, ask_fn, gen_fn, save_fn, force=False)`（已单测）。本 skill 是它的 **live 包装**：`ask_fn` = AskUserQuestion，`gen_fn` = 真出图（Doubao/MiniMax，**付费**），`save_fn` = `save_syllabus`。

## ⚠️ 付费 + 需用户在场
这一步会**真出图**（provider 付费）。self-pacing 等自动流程不得 AFK 自跑此 skill。

## 流程

1. **定位模块**：读 config（走配置发现）→ `load_syllabus`。默认取"下一个 pending 期所属的模块"；用户可指定模块序号。若该模块 `art_style` 且 `character_ref` 都已设，提示"已锁定，是否重做（force）"，否则结束。

2. **选画风（AskUserQuestion）**：列已知画风作候选，并说明**开放词表**——用户可自己输入任意画风词（如 油画/像素/古风/胶片/国漫/朋克…），不限于候选。
   - 已知候选来自 `grade.ART_STYLES`（当前：简笔日漫、写实电影）。
   - 用户选的值就是 `module.art_style`。

3. **生成该模块人物图（付费）**：按所选画风生成主讲人物参考图。画风前缀走 `grade.resolve_art_style(art_style)`（开放词表：未知画风名也能成前缀），拼上人物描述，调当前 provider 的人物图生成（`lib.media_image` / `lib.media_doubao`，与主 pipeline 同一套，**永不换直连 API**）。落盘到内容项目的 assets 目录，路径写入 `module.character_ref`。

4. **写回**：调 `prepare_module(...)`（ask_fn 返回步骤2的画风、gen_fn 返回步骤3的图路径、save_fn=save_syllabus）→ 持久化 syllabus。`force=True` 时即使已设也重做。

5. **报告**：打印锁定的画风 + 生成的人物图路径，供用户目检人物是否符合所选画风。

## 与其它 skill 的关系
- `/video-plan`（Stage 0）：规划 syllabus（模块/期）。本 skill 在其后、`/video-make` 出片之前，给模块补画风+人物。
- `/video-make`（Stage 1）：出片时读 `module.art_style`（画风）+ `module.character_ref`（人物），本 skill 就是给这两个字段赋值的地方。
