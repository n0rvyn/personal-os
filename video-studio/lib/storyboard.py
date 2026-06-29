"""Storyboard (分镜) step for video-studio.

Turns a list of narration beats into content-aware, shot-varied scene prompts
with continuity between adjacent shots — instead of a monotonous talking-head.
Backed by `claude -p --model sonnet` (the persona model, per project rule).

Output, aligned 1:1 to the input beats:
    [{"shots": [{"prompt": str, "shot_type": str, "with_character": bool}, ...]}, ...]

- `with_character=True`  -> 人物镜 (subject_reference locks the host into the frame)
- `with_character=False` -> 空镜 / detail (no person: track, pace chart, shoes, watch…)

chart / video beats still keep their own visual; their shots are advisory context
only (the model sees them so continuity flows around them).
"""
from __future__ import annotations

import json
import subprocess
import sys

# Per project rule: dispatched personas (claude -p) MUST be pinned to sonnet.
DEFAULT_MODEL = "sonnet"


def _build_prompt(beats: list[dict], character_desc: str, style: str) -> str:
    lines = []
    for i, b in enumerate(beats):
        vt = b.get("visual_type", "still")
        tag = {"chart": "[图表]", "video": "[实拍视频]"}.get(vt, "")
        lines.append(f"{i + 1}. {tag}{b['text']}")
    listing = "\n".join(lines)
    return f"""你是视频分镜师，为一条横屏(16:9)跑步知识科普短视频做分镜。
主讲人物固定为：{character_desc}
整体影像风格：{style}

下面是逐段旁白（按时间顺序）。为每一段设计镜头：

要求：
1. 理解这一句在讲什么，构建一个能体现这句【内容】的具体场景，绝不要千篇一律的"教练正脸对镜头讲话"。
2. 多变机位与景别：特写、中景、全景、过肩、手部特写、脚步特写、俯拍、跟拍、空镜（无人物的环境/细节）。
3. 相邻段落要有【连续性】：同一地点换机位、时间或动作自然推进、色调统一，让画面像跟着内容往前走，而不是随机拼贴。避免连续两段都是人物正脸。
4. 多用空镜打破"大B脸"：跑道、配速表意象、跑鞋、运动手表、心率、城市晨景、操场等。
5. 标 [图表] 的段落已用数据图表，标 [实拍视频] 的段落已有真人跑姿视频——这两类你给的镜头仅作前后连贯参考，照常输出但driver会忽略它们的图。
6. 每段 1~2 个镜头（信息多/时间长的段可 2 个，短段 1 个）。
7. with_character=true 表示画面里出现主讲人物；false 表示空镜/纯环境/细节（不出现人物）。

只输出一个 JSON 数组，长度必须等于旁白段数({len(beats)})。每个元素形如：
{{"shots":[{{"prompt":"具体画面描述(中文,简洁,视觉化,不含台词文字)","shot_type":"机位/景别","with_character":true}}]}}
严格 JSON 规则：所有 key 和字符串值用标准英文双引号；prompt 值内部【绝对不要】出现任何英文双引号，需要分隔就用顿号、或逗号；分隔符用半角逗号。
不要输出任何解释、不要 markdown 代码块，只要纯 JSON 数组。

旁白段落：
{listing}
"""


def _parse_storyboard(out: str, n_beats: int):
    """Return a validated list[dict] of length n_beats, or an error string
    (so the caller can retry — LLM JSON is not always well-formed)."""
    start, end = out.find("["), out.rfind("]")
    if start < 0 or end < 0 or end <= start:
        return f"no JSON array in output: {out[:200]}"
    try:
        data = json.loads(out[start:end + 1])
    except json.JSONDecodeError as e:
        return f"JSON parse failed: {e}"
    if not isinstance(data, list) or len(data) != n_beats:
        got = len(data) if isinstance(data, list) else type(data).__name__
        return f"expected {n_beats} entries, got {got}"
    return data


def build_storyboard(
    beats: list[dict],
    character_desc: str,
    style: str = "电影感，清晨冷光，写实，胶片颗粒",
    model: str = DEFAULT_MODEL,
    timeout: int = 240,
    retries: int = 3,
) -> list[dict]:
    """Generate per-beat shot lists via claude -p, retrying on a malformed
    response. Raises after `retries` failed attempts."""
    prompt = _build_prompt(beats, character_desc, style)
    last = "unknown"
    data = None
    for attempt in range(retries):
        proc = subprocess.run(
            ["claude", "-p", prompt, "--model", model],
            capture_output=True, text=True, timeout=timeout,
        )
        if proc.returncode != 0:
            last = f"claude exit {proc.returncode}: {proc.stderr[-300:]}"
        else:
            result = _parse_storyboard(proc.stdout.strip(), len(beats))
            if isinstance(result, list):
                data = result
                break
            last = result
        print(f"[storyboard] attempt {attempt + 1}/{retries} bad: {last}", file=sys.stderr)
    if data is None:
        raise RuntimeError(f"storyboard failed after {retries} attempts: {last}")

    # Normalize / validate each beat's shots.
    norm = []
    for i, entry in enumerate(data):
        shots = entry.get("shots") if isinstance(entry, dict) else None
        if not isinstance(shots, list) or not shots:
            raise RuntimeError(f"storyboard: beat {i} has no shots: {entry!r}")
        clean = []
        for s in shots:
            p = (s.get("prompt") or "").strip()
            if not p:
                raise RuntimeError(f"storyboard: beat {i} shot missing prompt: {s!r}")
            clean.append({
                "prompt": p,
                "shot_type": (s.get("shot_type") or "").strip(),
                "with_character": bool(s.get("with_character", True)),
            })
        norm.append({"shots": clean})
    return norm
