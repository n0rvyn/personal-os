# tts-toolkit

Personal-OS marketplace plugin。把任意文本（≥ 5000 字也行）合成成一段 mp3 播客。支持双 provider：MiniMax + Volcengine（豆包语音）。

入口脚本：`skills/tts/scripts/synth.sh`；测试入口：`skills/tts/tests/run_e2e.sh`。

## 任务路由表（先读哪个文件）

| 干什么 | 看哪个文件 |
|---|---|
| 改 provider 行为 / 加新 provider | `skills/tts/references/provider-quirks.md` 必读 |
| 加新 voice / 查 voice 配对 | `skills/tts/references/voice-catalog.md` |
| 改入口、加 CLI 选项 | `skills/tts/scripts/synth.sh` |
| 改配额预检 | `skills/tts/scripts/quota_check.sh` |
| 改 E2E 跑哪条 provider | `skills/tts/tests/run_e2e.sh` |
| 调用方文档 / 安装说明 | `skills/tts/SKILL.md` |

## 命名雷区（先记住，不要再混）

- **"Vol 1.0" / "Volc 1.0" / `seed-tts-1.0`** = 本 toolkit 内的 Volcengine TTS 1.0 字符版（`X-Api-Resource-Id: seed-tts-1.0`，`volc.service_type.10029`）。这是 v0.1 ship 的产品形态。
- **"Volcengine 语音播客大模型"**（Adam issue #241）= Volcengine 上一个**独立**的双人对话播客产品，WebSocket 二进制协议，按 token 计费，跟 `synth.sh` 完全不同形态。是 v0.2 候选，**不**等同于 Vol 1.0。详见 `skills/tts/references/provider-quirks.md` 末尾 "Out-of-scope variants" 章节。
- Volcengine 一家有两套凭证：`VOLC_TTS_APPID` + `VOLC_TTS_TOKEN`（speech console，给 TTS 调用）vs `VOLC_ACCESS_KEY_ID` + `VOLC_SECRET_ACCESS_KEY`（IAM 控制台，给配额查询）。混用会静默失败。详见 provider-quirks.md "Credential separation"。

## Provider 路由：voice 前缀决定

`synth.sh:98-99` 按 voice 前缀自动分发：

- `volc-*` → `providers/volcengine.sh`
- `mm-*`   → `providers/minimax.sh`

无需在调用方传 `--provider` 参数。推荐对位 voice（podcast host 风格）：

| 风格 | MiniMax | Volcengine |
|---|---|---|
| 成熟男声 / 播客主播 | `mm-Chinese (Mandarin)_Radio_Host` | `volc-zh_male_M392_conversation_wvae_bigtts` |

完整清单见 `skills/tts/references/voice-catalog.md`。

## E2E 切 provider 怎么做

`run_e2e.sh:27` 用 `E2E_VOICE` 环境变量覆盖默认 voice。但 `run_e2e.sh:37` 把 `--vendor minimax` 写死了——切 Volcengine 跑 E2E 前要把这一行参数化（或加 `E2E_VENDOR` 环境变量，从 voice 前缀推断）。

```bash
# MiniMax 路径（脚本现状直接跑）
bash skills/tts/tests/run_e2e.sh

# Volcengine 路径（需先改 run_e2e.sh:37）
E2E_VOICE="volc-zh_male_M392_conversation_wvae_bigtts" \
  bash skills/tts/tests/run_e2e.sh
```

## 凭证速查

| Provider | 用途 | 环境变量 |
|---|---|---|
| MiniMax | TTS 调用 + 配额查询 | `MINIMAX_API_KEY` |
| Volcengine | TTS 调用 | `VOLC_TTS_APPID`, `VOLC_TTS_TOKEN`, `VOLC_TTS_RESOURCE_ID`（默认 `seed-tts-1.0`） |
| Volcengine | 配额查询 | `VOLC_ACCESS_KEY_ID`, `VOLC_SECRET_ACCESS_KEY`, `VOLC_TTS_DAILY_BUDGET` |

**安全硬规则**：永远不要在 `bash -x` / `set -x` / `set -v` 下跑 `volcsign.py` 或 `quota_check.sh`。bash 的 xtrace 会把环境变量赋值行打到 stderr（包括 `VOLC_SECRET_ACCESS_KEY`），泄密。详见 provider-quirks.md "Operator security rule"。

## 常用诊断命令

```bash
# 配额预检（不消耗 quota）
bash skills/tts/scripts/quota_check.sh check --vendor minimax   --required-chars 5471 --reserve-pct 30
bash skills/tts/scripts/quota_check.sh check --vendor volcengine --required-chars 5471 --reserve-pct 30

# 清理 staging（mp3 永远不会被删）
bash skills/tts/scripts/cleanup.sh --dry-run
bash skills/tts/scripts/cleanup.sh --apply

# 单元测试
bats skills/tts/tests/test_providers_minimax.bats
bats skills/tts/tests/test_quota_check.bats
bats skills/tts/tests/test_synth_sh.bats
bats skills/tts/tests/test_cleanup.bats
```

## v0.1 ship 范围

单 vendor 单 call、V1 同步接口：

- MiniMax `t2a_v2`（HEX 解码）
- Volcengine `openspeech.bytedance.com/api/v1/tts`（base64 解码、`Bearer;<token>` 分号 quirk、resource-id 配对）

不在范围（v0.2 候选）：Volcengine V3 SSE / WebSocket 单/双向、Volcengine 语音播客大模型（issue #241）、MiniMax 官方 podcast API、声音复刻 (seed-icl)。完整清单见 provider-quirks.md "Out-of-scope variants"。
