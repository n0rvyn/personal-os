---
name: video-make
description: "/video-make — 出一期跑步知识视频（Stage 1 出片）。读 config 取下一个 pending 期 → ffmpeg 合成 → 输出 episodes/{seq}-{slug}/{seq}-{slug}.mp4 + provenance（视频名 = 目录名）。触发词：出片、出视频、/video-make。"
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
# 从内容项目根目录执行（内含 .video-studio/config.yaml）
cd /path/to/your-content-project

python -m lib.runner
```

runner 会自动发现配置（向上找 .video-studio/config.yaml，再叠加
`~/.claude/video-studio/config.yaml` 机器级兜底）。

runner 内部：
1. `load_config` 自动发现+合并配置（fail-closed，缺字段即报错）
2. `check_ffmpeg_libass` 检测 libass（缺则 return 1 + 清晰中文错）
3. `select_next` 自动取 syllabus 下一个 pending 期（无需手传 seq/slug）
4. `prepare_render` → `run_pipeline` → ffmpeg 合成
5. 输出 `episodes/{seq}-{slug}/{seq}-{slug}.mp4` + `provenance` 路径（视频名与目录同名）

## ConfigNotFound — 建内容项目 or 迁移旧配置

若 runner 报 `ConfigNotFound`（无论哪层都没找到 config），询问用户：

> 找不到配置。请选择：
> A) 在当前目录建内容项目（scaffold）
> B) 把旧 `~/.video-studio/` 迁移到新方案

**选 A（新项目）**：

```bash
cd /path/to/new-content-project
python3 -c "
from pathlib import Path
from lib.content_project import scaffold_content_project
scaffold_content_project(Path('.'), book='<书系名>')
print('DONE')
"
```

脚手架建好后编辑 `.video-studio/config.yaml`（内容级）和
`~/.claude/video-studio/config.yaml`（机器级 ffmpeg/provider/voice），
再重跑 `python -m lib.runner`。

**选 B（迁移旧 ~/.video-studio）**：

```bash
python3 -c "
from pathlib import Path
from lib.content_project import migrate_legacy_config
result = migrate_legacy_config(
    legacy_dir=Path.home() / '.video-studio',
    machine_config_path=Path.home() / '.claude/video-studio/config.yaml',
    content_root=Path('/path/to/new-content-project'),
)
print('迁移完成:', result)
"
```

迁移把旧 config 拆成机器级（ffmpeg/provider/voice/render/audio/budget → `~/.claude/video-studio/config.yaml`）
和内容级（output/syllabus/character → 内容项目 `.video-studio/config.yaml`），
syllabus/cursor 文件复制到内容项目（旧文件原地保留，确认后自行删除）。

迁移完成后在内容项目目录重跑 `python -m lib.runner`。

## 用 `--config` 显式覆盖

自动发现已够用。若需指向特定 config（如多书系切换），显式传 `--config`：

```bash
python -m lib.runner --config /path/to/specific-config.yaml
```

## 执行后报告

runner 成功后打印：

```
video      : episodes/001-intro/001-intro.mp4
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
- **错误排查**：缺 libass → 先设 `FFMPEG_BIN`；config 报错 → `python -m lib.config --validate <cfg>`；ConfigNotFound → 见上方"建内容项目 or 迁移"流程
