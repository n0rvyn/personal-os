"""记分卡组装 (lib.scorecard) — 硬门 + 判官维度 → 达标尺 verdict.

Phase 3 工艺门的产品层组装者. 把确定性硬门 (`structlint` + `dedup`) 与
判官维度(复用 `qianzhongshu` 的 `total`、`factcheck` 的 `ok`、新增
`scorecard` 判官的 3 维)合并成一张记分卡,产出 `{passed, hard_gates,
judge_dims, reason}`. advisory by default — `passed=False` 仍然渲染完整
记分卡;生产期 halt 由 `--enforce-scorecard` flag 在 runner 里触发.

Why this lives in its own module: 让 06-14→不达标 这个 acceptance #1 完全
deterministic. 硬门是 code,judge 维度是 LLM. 即便判官派发失败/产物缺/
非法 JSON,硬门照常判,advisory 不崩. The orchestrator (opus) reviews 整
张记分卡,看硬门红点 = 修生成侧,看判官维度低分 = 反思 prompt 紧密度.

温度原则 invariant: 判官 3 维 (`有观点` / `有温度` / `不同质化`) 是**奖
励主观判断**,不惩罚. 重复主观立场 + 织入可证伪判断的稿子在硬门里不
被误伤;判官 3 维低分时记分卡 reason 会标"主观偏弱"以供观察,硬门
仍 ok 时 passed=True(温度盾 acceptance #5).

冻结常量 (`QZS_TOTAL_FLOOR`, `JUDGE_DIM_FLOOR`) 在 Task 7 fixture 标定
后不再动. live run 不过门一律修生成侧,不擅自放宽阈值凑绿.
"""
from __future__ import annotations

from typing import Any, Optional

from lib import dedup, structlint


# ---------------------------------------------------------------------------
# 冻结常量 (named, frozen — Task 7 fixture 标定后不再改)
# ---------------------------------------------------------------------------

# 钱钟书 total floor. qianzhongshu 4 维满分为 20 (`洞察+命名+跨域+思考问
# 句`);14 留给「有强洞察 + 一两个维度偏弱」的空间,不要求 4 维全顶. 与
# `lib.episode.select_draft` 的选稿 floor 对齐(同源),但 scorecard 是
# 读已有 verdict 的 total,不是重判.
QZS_TOTAL_FLOOR: int = 14

# 判官 3 维 floor (1..5 量表). 3 = "明显有但不顶格" — 不要求主播把每个
# 维度都打到 5,留出"有温度"和"有观点"间可权衡的余量. 低于 3 标红进
# reason 提示,生产期可纳入 halt.
JUDGE_DIM_FLOOR: int = 3

# 判官 3 维的有效量表范围. 1..5 是 LLM 1-5 量表约定;0 / 6+ / 非 int →
# `unscored` (`safe_parse_scorecard` 兜底).
_JUDGE_VALID_MIN: int = 1
_JUDGE_VALID_MAX: int = 5

# 判官 3 维 (有顺序: 输出顺序 = 此顺序, 渲染时字段名一致).
JUDGE_DIMS: tuple[str, ...] = ("有观点", "有温度", "不同质化")


# ---------------------------------------------------------------------------
# 硬门
# ---------------------------------------------------------------------------

def _hard_gate_sections(body: str, show: str) -> dict[str, Any]:
    """段数硬门 — 委托 `structlint.check_sections`."""
    r = structlint.check_sections(body, show)
    return {
        "name": "sections",
        "ok": bool(r["ok"]),
        "detail": r.get("reason", ""),
        "hits": list(r.get("hits", [])),
    }


def _hard_gate_draft_marker(body: str) -> dict[str, Any]:
    """无草稿头硬门 — 委托 `structlint.check_no_draft_marker`."""
    r = structlint.check_no_draft_marker(body)
    return {
        "name": "draft_marker",
        "ok": bool(r["ok"]),
        "detail": r.get("reason", ""),
        "hits": list(r.get("hits", [])),
    }


def _hard_gate_betting_section(body: str) -> dict[str, Any]:
    """无独立下注段硬门 — 委托 `structlint.check_no_betting_section`."""
    r = structlint.check_no_betting_section(body)
    return {
        "name": "betting_section",
        "ok": bool(r["ok"]),
        "detail": r.get("reason", ""),
        "hits": list(r.get("hits", [])),
    }


def _hard_gate_duration(script_text: str) -> dict[str, Any]:
    """念稿时长硬门 — 委托 `structlint.check_duration`."""
    r = structlint.check_duration(script_text)
    return {
        "name": "duration",
        "ok": bool(r["ok"]),
        "detail": r.get("reason", ""),
        "hits": list(r.get("hits", [])),
    }


def _hard_gate_intra_dup(body: str) -> dict[str, Any]:
    """站内重复硬门 — 委托 `dedup.check_intra_dup`.

    06-14 root cause 之一 = 逐字重复段漏网;这个硬门是 acceptance #1 的
    「确定性独扛」的一部分. 不依赖嵌入,主信号是 n-gram (Jaccard /
    13-gram verbatim),所以 fail-soft 的嵌入 helper 不会拖累它.
    """
    r = dedup.check_intra_dup(body)
    return {
        "name": "intra_dup",
        "ok": bool(r["ok"]),
        "detail": r.get("reason", ""),
        "hits": list(r.get("hits", [])),
    }


def _hard_gate_cross_dup(script_text: str, store: Any, today: str) -> dict[str, Any]:
    """跨期过热锚硬门 — 委托 `dedup.check_cross_dup`.

    13a 读的是 step-19 之前的 pre-update store(step-19 才会更 store).
    与 davinci 写稿时 avoid_memo 同源,直接量「davinci 是否无视了自己
    的 avoid_memo」.
    """
    r = dedup.check_cross_dup(script_text, store if isinstance(store, dict) else {}, today)
    return {
        "name": "cross_dup",
        "ok": bool(r["ok"]),
        "detail": r.get("reason", ""),
        "hits": list(r.get("hits", [])),
    }


# ---------------------------------------------------------------------------
# 判官维度 helpers
# ---------------------------------------------------------------------------

def _axis_qianzhongshu_total(score_verdict: Any) -> dict[str, Any]:
    """读 score-verdict 的 `total`,不重判. 选候选 = max `total`(`
    `lib.episode.select_draft`` 同源).

    `score_verdict=None` / wrong shape → `score=0, ok=False` (fail-closed
    for 钱钟书: 没 verdict 视同未达 floor,记分卡 reason 标「无
    score-verdict」).
    """
    total: Any = 0
    if isinstance(score_verdict, dict):
        cands = score_verdict.get("candidates")
        if isinstance(cands, list) and cands:
            try:
                total = max(
                    (
                        int(
                            (c.get("scores") or {}).get("total", 0)
                            if isinstance(c, dict)
                            else 0
                        )
                        for c in cands
                    ),
                    default=0,
                )
            except (TypeError, ValueError):
                total = 0

    try:
        total_int = int(total)
    except (TypeError, ValueError):
        total_int = 0

    ok = total_int >= QZS_TOTAL_FLOOR
    detail = (
        f"qianzhongshu total={total_int} (floor={QZS_TOTAL_FLOOR})"
        if isinstance(score_verdict, dict)
        else "无 score-verdict"
    )
    return {
        "name": "qianzhongshu_total",
        "ok": ok,
        "score": total_int,
        "detail": detail,
    }


def _axis_factcheck(factcheck_verdict: Any) -> dict[str, Any]:
    """读 factcheck verdict.ok, 不重跑. ok=False → 红.

    信息准确轴: factcheck 是该轴的权威源(信息可追溯 = 该轴绿). 缺
    verdict → 红(fail-closed,温度原则不替事实让路).

    `score` 在 1..5 量表上二值化: 5 = ok (事实全过, 信息准确), 1 =
    fail (任一 flag 命中). 这让记分卡的判官维度表保持统一量表,与
    判官 3 维 1..5 直角可读;也满足 scorecard 测试 `score >= 3` 的
    数值断言. 缺 verdict → 1 (fail-closed,绝不静默绿).
    """
    SCORE_OK = 5
    SCORE_FAIL = 1
    ok_flag = False
    detail = "无 factcheck-verdict"
    if isinstance(factcheck_verdict, dict):
        if "ok" in factcheck_verdict:
            ok_flag = bool(factcheck_verdict.get("ok"))
            reason = factcheck_verdict.get("reason", "")
            detail = (
                f"factcheck ok={ok_flag}"
                + (f" — {reason}" if reason else "")
            )
        else:
            detail = "factcheck verdict 缺 'ok' 字段"

    return {
        "name": "factcheck",
        "ok": ok_flag,
        "score": SCORE_OK if ok_flag else SCORE_FAIL,
        "detail": detail,
    }


def _axis_judge_dim(name: str, value: Any) -> dict[str, Any]:
    """把 `safe_parse_scorecard` 的单维值转成 axis dict.

    `value == "unscored"` (判官派发失败/非法) → ok=False, score="unscored"
    (advisory: 不静默绿,也不因判官死掉而崩,跑得动其余轴).
    """
    if value == "unscored" or value is None:
        return {
            "name": name,
            "ok": False,
            "score": "unscored",
            "detail": f"{name}: 判官未评 (派发失败 / 非法值)",
        }
    try:
        v = int(value)
    except (TypeError, ValueError):
        return {
            "name": name,
            "ok": False,
            "score": "unscored",
            "detail": f"{name}: 非 int 值 {value!r}",
        }
    if v < _JUDGE_VALID_MIN or v > _JUDGE_VALID_MAX:
        return {
            "name": name,
            "ok": False,
            "score": "unscored",
            "detail": f"{name}: 越界 {v} (有效 {_JUDGE_VALID_MIN}..{_JUDGE_VALID_MAX})",
        }
    ok = v >= JUDGE_DIM_FLOOR
    return {
        "name": name,
        "ok": ok,
        "score": v,
        "detail": f"{name}={v} (floor={JUDGE_DIM_FLOOR})",
    }


# ---------------------------------------------------------------------------
# public: 组装记分卡
# ---------------------------------------------------------------------------

def build_scorecard(
    body: str,
    script_text: str,
    show: str,
    *,
    score_verdict: Any,
    factcheck_verdict: Any,
    store: Any,
    today: str,
    judge_verdict: Any,
) -> dict[str, Any]:
    """组装记分卡 = 硬门(structlint + dedup) + 判官维度(钱钟书 total 复用 +
    factcheck ok 复用 + 判官 3 维).

    Args:
      body: reader `.md` body (段数/草稿头/下注段/站内重复 入参).
      script_text: broadcast `.txt` 念稿 (跨期 + 时长 入参).
      show: "morning" / "evening" (段数期望值来源).
      score_verdict: 上游 qianzhongshu 步骤的 verdict dict(只读,不重判).
      factcheck_verdict: 上游 factcheck 步骤的 verdict dict(只读,不重跑).
      store: covered-ground store dict(只读在场检查;不重抽取).
      today: ISO 日期 `YYYY-MM-DD`(给 is_stale + 渲染).
      judge_verdict: 判官 `agents/scorecard.md` 的产物 dict;None / 非法
        → 该维 `unscored`,硬门照判.

    Returns:
      `{passed, hard_gates, judge_dims, reason, today, show}`.

      - `passed` = 所有硬门都 ok. 判官维度**不**参与 `passed`(硬门是
        达标尺底线,判官维度低分进 reason 提示,生产期可由 orchestrator
        决定是否纳入 halt). 温度原则的 5 号 acceptance 即由此而来.
      - `hard_gates` = `[{name, ok, detail, hits}, ...]`,**稳定顺序**:
        sections → draft_marker → betting_section → duration → intra_dup
        → cross_dup. 顺序固定,记分卡渲染行序可预测.
      - `judge_dims` = `[{name, ok, score, detail}, ...]`,**稳定顺序**:
        qianzhongshu_total → factcheck → 有观点 → 有温度 → 不同质化.
      - `reason` = 首个红硬门的 `detail`(给操作员一行定位),或
        判官维度红点的简短描述(advisory 观测用). `passed=True` 且无
        判官红点时 = `""`.
    """
    # --- 硬门(确定性,六个) ---------------------------------------------
    hard_gates: list[dict[str, Any]] = [
        _hard_gate_sections(body, show),
        _hard_gate_draft_marker(body),
        _hard_gate_betting_section(body),
        _hard_gate_duration(script_text),
        _hard_gate_intra_dup(body),
        _hard_gate_cross_dup(script_text, store, today),
    ]
    passed = all(g["ok"] for g in hard_gates)

    # --- 判官维度(读已有 verdict / 解析判官产物) -----------------------
    judge_dims: list[dict[str, Any]] = [
        _axis_qianzhongshu_total(score_verdict),
        _axis_factcheck(factcheck_verdict),
    ]
    parsed_judge = safe_parse_scorecard(judge_verdict)
    for dim_name in JUDGE_DIMS:
        judge_dims.append(_axis_judge_dim(dim_name, parsed_judge.get(dim_name, "unscored")))

    # --- reason(给操作员一行定位) ------------------------------------
    reason_parts: list[str] = []
    first_red = next((g for g in hard_gates if not g["ok"]), None)
    if first_red is not None:
        reason_parts.append(f"硬门红: {first_red['name']} — {first_red['detail']}")
    red_dims = [d for d in judge_dims if not d["ok"]]
    if red_dims:
        dim_summary = ", ".join(
            f"{d['name']}={d.get('score', '?')}" for d in red_dims
        )
        reason_parts.append(f"判官维度红: {dim_summary}")

    reason = " | ".join(reason_parts) if reason_parts else ""

    return {
        "passed": passed,
        "hard_gates": hard_gates,
        "judge_dims": judge_dims,
        "reason": reason,
        "today": today,
        "show": show,
    }


# ---------------------------------------------------------------------------
# public: 判官产物 fail-soft 解析
# ---------------------------------------------------------------------------

def safe_parse_scorecard(raw: Any) -> dict[str, Any]:
    """把判官 LLM 的产物 `raw`(dict / JSON 串 / None / 其它)安全解析成
    `{有观点: int|"unscored", 有温度: int|"unscored", 不同质化: int|"unscored"}`.

    Rules:
      - 必须是 dict(str-keyed 期望,但**任何非 dict** 也安全,绝 raise).
      - 每个 dim 值必须是 1..5 的 int;`0` / `6+` / 字符串 / `None` /
        浮点 / 越界 → `unscored`.
      - 全包 try/except;判官派发失败时,runner 的 `safe_parse_scorecard`
        收到 `{}` / `None` 时,本函数返回所有 dim = `"unscored"`.
    """
    out: dict[str, Any] = {dim: "unscored" for dim in JUDGE_DIMS}

    # 类型卫: 判官可能因 persona prompt 让模型把 JSON 嵌在文本里;Runner
    # 在调 `safe_parse_scorecard` 前应已 `json.loads` 过一遍,但这里是
    # 兜底,再 try 一次.
    parsed: Any = raw
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            import json
            parsed = json.loads(raw)
        except Exception:
            parsed = None

    if not isinstance(parsed, dict):
        return out

    for dim in JUDGE_DIMS:
        try:
            v = parsed.get(dim, "unscored")
        except Exception:
            continue
        if v == "unscored":
            out[dim] = "unscored"
            continue
        # bool 是 int 的子类 — 拒绝 bool 混入(LLM 偶尔返回 True 标 5)
        if isinstance(v, bool):
            out[dim] = "unscored"
            continue
        if isinstance(v, int) and not isinstance(v, bool):
            if _JUDGE_VALID_MIN <= v <= _JUDGE_VALID_MAX:
                out[dim] = v
            else:
                out[dim] = "unscored"
        else:
            out[dim] = "unscored"

    return out


# ---------------------------------------------------------------------------
# public: 渲染人读 markdown
# ---------------------------------------------------------------------------

def render_scorecard_md(result: dict[str, Any]) -> str:
    """把人读记分卡渲染成 markdown.

    形态: 总判(passed / 不达标)→ 硬门表(段数/草稿头/下注段/时长/站内
    重复/跨期过热锚)→ 判官维度表(钱钟书/factcheck/有观点/有温度/不同
    质化)→ 命中详情(hard_gates 的 hits + 红判官 dim 的 detail).
    """
    lines: list[str] = []

    show = result.get("show", "?")
    today = result.get("today", "?")
    passed = bool(result.get("passed"))
    reason = result.get("reason", "")

    # --- Header ---
    title = f"# 记分卡 — {today} {show}"
    lines.append(title)
    lines.append("")

    # --- 总判 ---
    verdict_line = "**总判: 通过 ✓**" if passed else "**总判: 不达标 ✗**"
    lines.append(verdict_line)
    if reason:
        lines.append("")
        lines.append(f"> {reason}")
    lines.append("")

    # --- 硬门表 ---
    lines.append("## 硬门(确定性)")
    lines.append("")
    lines.append("| 硬门 | 状态 | 详情 |")
    lines.append("| --- | --- | --- |")
    for g in result.get("hard_gates", []):
        name = g.get("name", "?")
        ok = g.get("ok")
        status = "✓" if ok else "✗"
        detail = g.get("detail", "") or ""
        lines.append(f"| {name} | {status} | {detail} |")
    lines.append("")

    # --- 判官维度表 ---
    lines.append("## 判官维度")
    lines.append("")
    lines.append("| 维度 | 状态 | 分 | 详情 |")
    lines.append("| --- | --- | --- | --- |")
    for d in result.get("judge_dims", []):
        name = d.get("name", "?")
        ok = d.get("ok")
        status = "✓" if ok else "✗"
        score = d.get("score", "")
        detail = d.get("detail", "") or ""
        lines.append(f"| {name} | {status} | {score} | {detail} |")
    lines.append("")

    # --- 命中详情 ---
    hits_lines: list[str] = []
    for g in result.get("hard_gates", []):
        if g.get("hits"):
            for h in g["hits"]:
                hits_lines.append(f"- 硬门 {g.get('name', '?')}: {h}")
    for d in result.get("judge_dims", []):
        if not d.get("ok") and d.get("score") == "unscored":
            hits_lines.append(f"- 判官 {d.get('name', '?')}: 未评 (判官派发失败)")
    if hits_lines:
        lines.append("## 命中详情")
        lines.append("")
        lines.extend(hits_lines)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
