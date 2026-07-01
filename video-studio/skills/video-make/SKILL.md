---
name: video-make
description: "/video-make — 出一期跑步知识视频（Stage 1 出片）。读 config 取下一个 pending 期 → ffmpeg 合成 → 输出 episodes/{seq}-{slug}/final.mp4 + provenance。触发词：出片、出视频、/video-make。"
allowed-tools:
  - Bash
---

# /video-make — Stage 1 出片（thin shell）

`/video-make` 是 Stage 1 出片入口的 thin wrapper，调 `python -m lib.runner`。

pipeline 拓扑（脚本生成、忠实门、分镜判官、运镜、ffmpeg 合成）声明为数据，见
`lib/pipeline.py`（step 表是权威台账）；执行逻辑在 `lib/runner.py`，skill
不重述拓扑。

## 前置检查

运行前确认以下环境变量已设（缺 libass 的 ffmpeg 无法烧字幕，runner 在渲染前
会检测并清晰报错，但不如提前导出）：

```bash
export FFMPEG_BIN=~/.local/ffmpeg-libass/ffmpeg
export FFPROBE_BIN=~/.local/ffmpeg-libass/ffprobe
```

> 系统自带 ffmpeg 通常不带 libass。若未设 `FFMPEG_BIN`，runner 自动读
> `config.ffmpeg.ffmpeg_bin`；缺 libass 时立即打印清晰中文错误并退出，**不进
> 入渲染**。

## 调用方式

```bash
# 从 video-studio 插件根目录执行（CLAUDE_PLUGIN_ROOT 即此目录）
cd "${CLAUDE_PLUGIN_ROOT}"

python -m lib.runner --config ~/.video-studio/config.yaml
```

runner 内部：
1. `load_config` 读配置（fail-closed，缺字段即报错）
2. `check_ffmpeg_libass` 检测 libass（缺则 return 1 + 清晰中文错）
3. `select_next` 自动取 syllabus 下一个 pending 期（无需手传 seq/slug）
4. `prepare_render` → `run_pipeline` → ffmpeg 合成
5. 输出 `episodes/{seq}-{slug}/final.mp4` + `provenance` 路径

## 用 `--config` 指向配置

config 默认路径 `~/.video-studio/config.yaml`（与 runner `--config` 参数一致）。

若有多个 config（不同书系），显式传 `--config`：

```bash
python -m lib.runner --config ~/my-project/video-studio-config.yaml
```

## 执行后报告

runner 成功后打印：

```
final.mp4  : episodes/001-intro/final.mp4
provenance : episodes/001-intro/provenance.json
```

把这两个路径告诉用户，并说明下一个 pending 期（从 syllabus 游标读取）。
若 runner 返回非零，把 stderr 完整展示给用户，不隐藏错误。

## LLM dispatch 说明

视频脚本生成、忠实门、分镜判官等需要 LLM 的步骤，runner 内部通过
`dispatch_persona` 走 `claude -p --model sonnet`（personal-os 标准路由）。

**永不换直连 API。** 所有 LLM 调用均经 `claude -p`，不走
`ANTHROPIC_API_KEY` 直连。慢或报错在 `claude -p` 内修（prompt 或 agent 行为调整），
不切换调用方式。

## 相关资源

- **config 格式**：见 `lib/config.py` + `video-studio/README.md`
- **pipeline 步骤**：`lib/pipeline.py`（step 表）+ `lib/wiring.py`（executor）
- **syllabus 管理**：`/video-plan`（Stage 0 大纲规划 skill，生成 syllabus 供本 skill 消费）
- **错误排查**：缺 libass → 先设 `FFMPEG_BIN`；config 报错 → `python -m lib.config --validate <cfg>`
