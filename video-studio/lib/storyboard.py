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
import re
import subprocess
import sys

# Per project rule: dispatched personas (claude -p) MUST be pinned to sonnet.
DEFAULT_MODEL = "sonnet"

# POC6 shot-rhythm: each shot's `pace` maps to a relative dwell weight. Coarse
# buckets (not a free float) give more consistent output from the LLM and a
# clear authoring vocabulary: 金句 holds, 列举 quick-cuts, default normal.
PACE_WEIGHTS = {"hold": 2.0, "normal": 1.0, "quick": 0.6}
_DEFAULT_PACE = "normal"


def pace_weight(pace: str) -> float:
    """Relative dwell weight for a shot's pace (unknown -> normal)."""
    return PACE_WEIGHTS.get(pace, PACE_WEIGHTS[_DEFAULT_PACE])


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
3. 相邻段落要有【连续性】：同一地点换机位、时间或动作自然推进、色调统一，让画面像跟着内容往前走，而不是随机拼贴。
4. 多用空镜打破"大B脸"：跑道、配速表意象、跑鞋、运动手表、心率、城市晨景、操场等。
5. 标 [图表] 的段落已用数据图表，标 [实拍视频] 的段落已有真人跑姿视频——这两类你给的镜头仅作前后连贯参考，照常输出但driver会忽略它们的图。
6. 【镜头节奏】按内容动态决定每段镜头数与停留长短，每个镜头标 pace ∈ {{hold, normal, quick}}：
   - 金句 / 点题 / 强调句 → 给【1 个】镜头、慢留（pace="hold"），让观众停下来记住，不要切碎。
   - 列举 / 多个并列项 / 多动作 / 信息密集的长句 → 给【2~4 个】快切镜头（pace="quick"），每个镜头换【不同主体或角度】制造节奏，绝不是同一画面重复。
   - 普通陈述 / 过渡 → 1~2 个常速镜头（pace="normal"）。
   pace 表示停留时长：hold 最长、normal 居中、quick 最短。
7. 【避相邻雷同】（硬性）：相邻镜头——同段内相邻 + 跨段相接——的景别/机位必须明显不同；绝不连续两个人物正脸；列举的多个快切镜必须是不同主体或角度。
9. with_character=true 表示画面里出现主讲人物；false 表示空镜/纯环境/细节（不出现人物）。
10. keywords（字幕高亮）：在【整条片子】里只挑出真正的核心术语、关键数字、或金句词，放到对应段落的 keywords。**这是高度选择性的——大部分普通陈述句、过渡句、铺垫句都应该是空数组 []。** 预期相当一部分段落（往往一半以上）keywords 为空。绝不要每段都标、绝不硬凑；强调太多就等于没强调。每段最多 1~2 个，原样照抄台词中的词。判断标准：如果观众只能记住这条字幕里的一个词，是哪个？不值得记的整句就留空。

只输出一个 JSON 数组，长度必须等于旁白段数({len(beats)})。每个元素形如：
{{"shots":[{{"prompt":"具体画面描述(中文,简洁,视觉化,不含台词文字)","shot_type":"机位/景别","with_character":true,"pace":"normal"}}],"keywords":["乳酸阈"]}}
严格 JSON 规则：所有 key 和字符串值用标准英文双引号；prompt 值内部【绝对不要】出现任何英文双引号，需要分隔就用顿号、或逗号；分隔符用半角逗号。
不要输出任何解释、不要 markdown 代码块，只要纯 JSON 数组。

旁白段落：
{listing}
"""


def _extract_json_array(out: str) -> str | None:
    """Extract a JSON array from raw LLM output, tolerating the failure modes we
    actually observe: a dropped trailing ']' (the model forgets to close the
    array) and a trailing comma before a close. The scan is STRING-AWARE so
    brackets inside Chinese prompt values don't confuse the bracket counting —
    a naive rfind(']') grabs the wrong bracket when the array is unterminated.
    Returns the candidate JSON string, or None if there is no array at all."""
    start = out.find("[")
    if start < 0:
        return None
    stack: list[str] = []
    in_str = esc = False
    end = None
    for i in range(start, len(out)):
        ch = out[i]
        if esc:
            esc = False
            continue
        if in_str:
            if ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            stack.append(ch)
        elif ch in "]}":
            if stack:
                stack.pop()
            if not stack:
                end = i
                break
    if end is not None:
        cand = out[start:end + 1]
    else:
        # Unterminated: append the closers the still-open stack needs (most
        # often a single ']'). A mid-string truncation leaves in_str True and
        # won't parse — that correctly falls through to a retry, not a fake fix.
        closers = "".join("]" if c == "[" else "}" for c in reversed(stack))
        cand = out[start:].rstrip()
        if cand.endswith(","):
            cand = cand[:-1]
        cand += closers
    # Drop a dangling comma right before a close (…,] / …,}) — json is strict.
    return re.sub(r",(\s*[}\]])", r"\1", cand)


def _parse_storyboard(out: str, n_beats: int):
    """Return a validated list[dict] of length n_beats, or an error string
    (so the caller can retry — LLM JSON is not always well-formed)."""
    cand = _extract_json_array(out)
    if cand is None:
        return f"no JSON array in output: {out[:200]}"
    try:
        data = json.loads(cand)
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
    timeout: int = 360,
    retries: int = 3,
) -> list[dict]:
    """Generate per-beat shot lists via claude -p, retrying on a malformed
    response or a slow call. Raises after `retries` failed attempts."""
    prompt = _build_prompt(beats, character_desc, style)
    last = "unknown"
    data = None
    for attempt in range(retries):
        try:
            proc = subprocess.run(
                ["claude", "-p", prompt, "--model", model],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            # claude -p can run long; retry instead of crashing the whole run.
            last = f"claude timed out after {timeout}s"
            print(f"[storyboard] attempt {attempt + 1}/{retries} bad: {last}", file=sys.stderr)
            continue
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
            pace = (s.get("pace") or _DEFAULT_PACE).strip().lower()
            if pace not in PACE_WEIGHTS:
                pace = _DEFAULT_PACE
            clean.append({
                "prompt": p,
                "shot_type": (s.get("shot_type") or "").strip(),
                "with_character": bool(s.get("with_character", True)),
                "pace": pace,
            })
        raw_kw = entry.get("keywords") or []
        keywords = [k.strip() for k in raw_kw if isinstance(k, str) and k.strip()] \
            if isinstance(raw_kw, list) else []
        norm.append({"shots": clean, "keywords": keywords})
    return norm
