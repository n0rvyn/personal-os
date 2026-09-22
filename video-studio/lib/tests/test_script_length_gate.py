"""Length gate + retry feedback (make_script_dispatch) and --stop-after truncation.

Why these exist: the writer persona asked for "2200–3000 字" and the two live
episodes came in at 2204 and 2298 — it settles on whatever floor it is told,
because nothing checked. A target that is merely requested is not a target. The
gate below is what makes `script.target_chars` mean something, and the retry
brief is what lets the writer fix the specific miss instead of re-rolling blind.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.config import ScriptConfig
from lib.wiring import make_script_dispatch, script_chars

# Source text the anchors below must ground against (忠实门 checks anchors only).
FT = "Easy pace 5:20 per km. The threshold run is comfortably hard."
ANCHOR = "Easy pace 5:20 per km"


class Cfg:
    """Minimal stand-in carrying only what make_script_dispatch reads."""

    def __init__(self, script=None):
        self.script = script
        self.host = None


def _step():
    return {"name": "script", "kind": "agent", "agent": "digest-writer",
            "artifact": "script_ledger"}


def _persona_writing(text: str):
    """A digest-writer that always emits one beat with `text` and a real anchor."""
    def persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text(
            json.dumps({"beats": [{"beat_id": "b0", "text": text, "anchor": ANCHOR}]}),
            encoding="utf-8",
        )
        persona.prompts.append(prompt)
        return {"ok": True, "artifact_path": str(out)}
    persona.prompts = []
    return persona


def _ctx(tmp_path):
    return {
        "fulltext": FT,
        "ep": {"script": tmp_path / "script.md", "beats": tmp_path / "beats.json"},
    }


# ---------------------------------------------------------------- script_chars

def test_script_chars_counts_narration_not_anchors():
    """The count must track what TTS speaks. Anchors are source citations that
    never reach the audio — counting them would let a script hit the floor on
    quotation alone."""
    beats = [
        {"text": "一二三", "anchor": "x" * 500},
        {"text": "四五", "anchor": "y" * 500},
    ]
    assert script_chars(beats) == 5


def test_script_chars_survives_missing_and_malformed_beats():
    assert script_chars([{"anchor": "a"}, None, {"text": None}, {"text": "ab"}]) == 2


# ------------------------------------------------------------------ the gate

def test_under_length_writes_no_artifact(tmp_path):
    """A faithful but short draft must NOT pass — that is the whole point."""
    cfg = Cfg(ScriptConfig(target_chars=3000, min_chars=2800, max_chars=3400))
    d = make_script_dispatch(cfg, dispatch_persona_fn=_persona_writing("短" * 100))
    ctx = _ctx(tmp_path)
    d("digest-writer", _step(), ctx, tmp_path)

    assert "script_ledger" not in ctx           # gate miss → runner retries
    assert ctx["_length_short"] == (100, 2800)
    assert not ctx["ep"]["script"].exists()     # no half-baked deliverable


def test_at_length_and_grounded_passes(tmp_path):
    cfg = Cfg(ScriptConfig(target_chars=3000, min_chars=2800, max_chars=3400))
    d = make_script_dispatch(cfg, dispatch_persona_fn=_persona_writing("字" * 2900))
    ctx = _ctx(tmp_path)
    d("digest-writer", _step(), ctx, tmp_path)

    assert Path(ctx["script_ledger"]).exists()
    assert "_length_short" not in ctx
    assert ctx["ep"]["script"].exists()


def test_over_max_is_not_rejected(tmp_path):
    """max_chars steers the prompt but must not gate: rejecting long drafts
    teaches the writer to pad to the floor and then cut, which is the failure
    mode the floor already risks."""
    cfg = Cfg(ScriptConfig(target_chars=3000, min_chars=2800, max_chars=3400))
    d = make_script_dispatch(cfg, dispatch_persona_fn=_persona_writing("字" * 5000))
    ctx = _ctx(tmp_path)
    d("digest-writer", _step(), ctx, tmp_path)
    assert Path(ctx["script_ledger"]).exists()


def test_no_script_config_means_no_gate(tmp_path):
    """A cfg carrying no ScriptConfig leaves the gate inert (pre-existing
    behaviour), so a 5-char fixture beat still passes."""
    d = make_script_dispatch(Cfg(None), dispatch_persona_fn=_persona_writing("五个字啊"))
    ctx = _ctx(tmp_path)
    d("digest-writer", _step(), ctx, tmp_path)
    assert Path(ctx["script_ledger"]).exists()


def test_length_gate_does_not_mask_faithfulness(tmp_path):
    """A long draft with a fabricated anchor must still be flagged: length can
    never buy its way past the 忠实门."""
    def persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps({"beats": [
            {"beat_id": "b0", "text": "字" * 3000, "anchor": "Easy pace 9:99 per km"}
        ]}), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = Cfg(ScriptConfig(target_chars=3000, min_chars=2800, max_chars=3400))
    d = make_script_dispatch(cfg, dispatch_persona_fn=persona)
    ctx = _ctx(tmp_path)
    d("digest-writer", _step(), ctx, tmp_path)

    assert "script_ledger" not in ctx
    assert ctx["_faithfulness_flagged"][0]["beat_id"] == "b0"


# --------------------------------------------------------- retry feedback loop

def test_retry_prompt_names_the_shortfall(tmp_path):
    """Attempt 2 must be told how many characters it was short by. Without this
    the writer re-rolls blind and lands on the same floor again."""
    cfg = Cfg(ScriptConfig(target_chars=3000, min_chars=2800, max_chars=3400))
    persona = _persona_writing("短" * 100)
    d = make_script_dispatch(cfg, dispatch_persona_fn=persona)
    ctx = _ctx(tmp_path)

    d("digest-writer", _step(), ctx, tmp_path)   # attempt 1 → short
    d("digest-writer", _step(), ctx, tmp_path)   # attempt 2 → sees the brief

    assert "字数不够" not in persona.prompts[0]     # nothing to report yet
    assert "还差 2700 字" in persona.prompts[1]
    assert "加实料" in persona.prompts[1]


def test_retry_prompt_names_the_flagged_anchor(tmp_path):
    """The faithfulness verdict was already stashed in ctx and then dropped — the
    writer never saw which beat was rejected. It must now."""
    def persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps({"beats": [
            {"beat_id": "b7", "text": "编造", "anchor": "Easy pace 9:99 per km"}
        ]}), encoding="utf-8")
        persona.prompts.append(prompt)
        return {"ok": True, "artifact_path": str(out)}
    persona.prompts = []

    d = make_script_dispatch(Cfg(None), dispatch_persona_fn=persona)
    ctx = _ctx(tmp_path)
    d("digest-writer", _step(), ctx, tmp_path)
    d("digest-writer", _step(), ctx, tmp_path)

    assert "忠实门" in persona.prompts[1]
    assert "b7" in persona.prompts[1]
    assert "9:99" in persona.prompts[1]


def test_stale_findings_do_not_replay_into_the_next_retry(tmp_path):
    """Attempt 2 fixes the faithfulness miss but is still short. Attempt 3 must
    be told ONLY about the length — replaying the now-fixed anchor would send the
    writer chasing a problem it already solved."""
    state = {"n": 0}

    def persona(agent, prompt, scratch_dir, expected, **kw):
        state["n"] += 1
        anchor = "Easy pace 9:99 per km" if state["n"] == 1 else ANCHOR
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps({"beats": [
            {"beat_id": "b1", "text": "短" * 100, "anchor": anchor}
        ]}), encoding="utf-8")
        persona.prompts.append(prompt)
        return {"ok": True, "artifact_path": str(out)}
    persona.prompts = []

    cfg = Cfg(ScriptConfig(target_chars=3000, min_chars=2800, max_chars=3400))
    d = make_script_dispatch(cfg, dispatch_persona_fn=persona)
    ctx = _ctx(tmp_path)
    for _ in range(3):
        d("digest-writer", _step(), ctx, tmp_path)

    assert "忠实门" in persona.prompts[1]        # attempt 2 hears about the anchor
    assert "忠实门" not in persona.prompts[2]    # attempt 3 does NOT — it was fixed
    assert "字数不够" in persona.prompts[2]
    assert "_faithfulness_flagged" not in ctx


def test_prompt_carries_the_length_target_on_first_attempt(tmp_path):
    cfg = Cfg(ScriptConfig(target_chars=3000, min_chars=2800, max_chars=3400))
    persona = _persona_writing("字" * 2900)
    d = make_script_dispatch(cfg, dispatch_persona_fn=persona)
    d("digest-writer", _step(), _ctx(tmp_path), tmp_path)
    p = persona.prompts[0]
    assert "目标 3000 字" in p and "硬下限 2800 字" in p and "绝不注水" in p


# ------------------------------------------------------------ the schema miss

def test_schema_miss_is_reported_to_the_retry(tmp_path):
    """A beat with no anchor used to retry blind: the writer was told nothing, so
    it re-rolled the whole episode instead of adding the one missing field. (Live:
    "beat 2 missing or empty 'anchor'".)"""
    state = {"n": 0}

    def persona(agent, prompt, scratch_dir, expected, **kw):
        state["n"] += 1
        beats = (
            [{"beat_id": "b0", "text": "字" * 2900, "anchor": ANCHOR},
             {"beat_id": "b1", "text": "没写依据"}]              # attempt 1: no anchor
            if state["n"] == 1 else
            [{"beat_id": "b0", "text": "字" * 2900, "anchor": ANCHOR}]
        )
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps({"beats": beats}, ensure_ascii=False), encoding="utf-8")
        persona.prompts.append(prompt)
        return {"ok": True, "artifact_path": str(out)}
    persona.prompts = []

    cfg = Cfg(ScriptConfig())
    d = make_script_dispatch(cfg, dispatch_persona_fn=persona)
    ctx = _ctx(tmp_path)

    d("digest-writer", _step(), ctx, tmp_path)
    assert "script_ledger" not in ctx
    assert "anchor" in ctx["_schema_error"]

    d("digest-writer", _step(), ctx, tmp_path)
    assert "JSON 不合格" in persona.prompts[1]
    assert "anchor" in persona.prompts[1]
    assert Path(ctx["script_ledger"]).exists()      # attempt 2 passes
    assert "_schema_error" not in ctx               # and the finding is cleared


# ------------------------------------------------------------- the style gate
#
# These exist because instructions did not hold. The persona forbade the greeting
# opening, the user prompt forbade it in bold, and the live writer opened with
# 「大家好，我是卞旸，欢迎来到跑者学堂」 on both pilot runs. Recompute, never trust.

def test_style_gate_rejects_the_greeting_opening():
    from lib.ledger import verify_style

    # The exact string the live writer produced, twice.
    v = verify_style({"beats": [
        {"beat_id": "b00", "text": "大家好，我是卞旸，欢迎来到跑者学堂。今天看一组数据："},
        {"beat_id": "b01", "text": "正文"},
    ]})
    assert not v["ok"]
    assert v["violations"][0]["rule"] == "冷开场"
    assert v["violations"][0]["found"] == "大家好"


def test_style_gate_allows_the_intro_after_the_hook():
    """The self-intro is not banned — it is banned in beat 0. Landing it in beat 1
    is exactly what the persona asks for, and must pass."""
    from lib.ledger import verify_style

    v = verify_style({"beats": [
        {"beat_id": "b00", "text": "三个女人，站在同一间实验室里。"},
        {"beat_id": "b01", "text": "我是卞旸，跑者学堂。回到那三个女跑者。"},
    ]})
    assert v["ok"], v["violations"]


def test_style_gate_rejects_cross_episode_references_anywhere():
    from lib.ledger import verify_style

    v = verify_style({"beats": [
        {"beat_id": "b00", "text": "三个女人，站在同一间实验室里。"},
        {"beat_id": "b09", "text": "这期就聊到这儿，我们下期见。"},
    ]})
    assert not v["ok"]
    rules = {x["rule"] for x in v["violations"]}
    assert rules == {"独立成篇"}
    assert {x["found"] for x in v["violations"]} & {"下期", "我们下期见"}


def test_style_gate_allows_citation_framing_within_budget():
    """The persona explicitly permits citing authority once or twice per episode
    ("控制在两三句以内") — three hits must still pass."""
    from lib.ledger import verify_style

    v = verify_style({"beats": [
        {"beat_id": "b00", "text": "三个女人，站在同一间实验室里。"},
        {"beat_id": "b01", "text": "教材原话：冰敷会减缓愈合过程。"},
        {"beat_id": "b02", "text": "教材提到一个关键词：卫星细胞。"},
        {"beat_id": "b03", "text": "教材说得很明确：过度拉伸本身就会致伤。"},
    ]})
    assert v["ok"], v["violations"]


def test_style_gate_rejects_citation_framing_past_budget():
    """Past the budget, every hit (not just the excess) is reported so the writer
    knows which beats to rewrite."""
    from lib.ledger import verify_style

    v = verify_style({"beats": [
        {"beat_id": "b00", "text": "三个女人，站在同一间实验室里。"},
        {"beat_id": "b01", "text": "教材原话：冰敷会减缓愈合过程。"},
        {"beat_id": "b02", "text": "教材提到一个关键词：卫星细胞。"},
        {"beat_id": "b03", "text": "教材说得很明确：过度拉伸本身就会致伤。"},
        {"beat_id": "b04", "text": "教材给了一个具体数字：超过40秒。"},
    ]})
    assert not v["ok"]
    rules = {x["rule"] for x in v["violations"]}
    assert rules == {"引用腔"}
    assert len(v["violations"]) == 4


def test_style_gate_blocks_the_artifact_and_briefs_the_retry(tmp_path):
    """End to end: a greeting opening writes no artifact, and attempt 2 is told
    exactly which beat and which phrase to fix."""
    def persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps({"beats": [
            {"beat_id": "b00",
             "text": "大家好，我是卞旸，欢迎来到跑者学堂。" + "字" * 2900,
             "anchor": ANCHOR},
        ]}, ensure_ascii=False), encoding="utf-8")
        persona.prompts.append(prompt)
        return {"ok": True, "artifact_path": str(out)}
    persona.prompts = []

    cfg = Cfg(ScriptConfig())
    d = make_script_dispatch(cfg, dispatch_persona_fn=persona)
    ctx = _ctx(tmp_path)

    d("digest-writer", _step(), ctx, tmp_path)
    assert "script_ledger" not in ctx          # long AND grounded, but still rejected
    assert ctx["_style_violations"][0]["found"] == "大家好"

    d("digest-writer", _step(), ctx, tmp_path)
    assert "第一个 beat 出现了「大家好」" in persona.prompts[1]
    assert "冷钩子" in persona.prompts[1]


def test_citation_framing_over_budget_briefs_the_retry(tmp_path):
    """End to end: an episode that leans on "教材说/教材原话" past the persona's
    own budget writes no artifact, and attempt 2 is told to internalize the
    knowledge instead of framing every claim as a citation."""
    def persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        beats = [{"beat_id": "b00", "text": "三个女人，站在同一间实验室里。" + "字" * 700,
                  "anchor": ANCHOR}]
        for i, phrase in enumerate(
            ["教材原话：冰敷会减缓愈合过程。", "教材提到一个关键词：卫星细胞。",
             "教材说得很明确：过度拉伸会致伤。", "教材给了一个具体数字：超过40秒。"]
        ):
            beats.append({"beat_id": f"b0{i + 1}", "text": phrase + "字" * 550,
                           "anchor": ANCHOR})
        out.write_text(json.dumps({"beats": beats}, ensure_ascii=False), encoding="utf-8")
        persona.prompts.append(prompt)
        return {"ok": True, "artifact_path": str(out)}
    persona.prompts = []

    cfg = Cfg(ScriptConfig())
    d = make_script_dispatch(cfg, dispatch_persona_fn=persona)
    ctx = _ctx(tmp_path)

    d("digest-writer", _step(), ctx, tmp_path)
    assert "script_ledger" not in ctx
    assert {v["rule"] for v in ctx["_style_violations"]} == {"引用腔"}

    d("digest-writer", _step(), ctx, tmp_path)
    assert "引用腔" in persona.prompts[1]
    assert "内化成你自己要讲的话" in persona.prompts[1]


# ------------------------------------------------------ host line vs the hook

class _Host:
    name, channel, tone, gender = "卞旸", "跑者学堂", "亲切", "女"


def test_host_line_does_not_order_a_greeting_opening(tmp_path):
    """Regression, caught by the pilot: the host line used to *order* the writer to
    open beat 1 with 「大家好，我是X，欢迎来到Y」. It sits in the user prompt, so it
    beat the persona — the draft opened with the greeting even though the persona
    forbids it. The channel's own data (viewers average 55.5s) says the opening
    seconds cannot be spent on a greeting."""
    cfg = Cfg(ScriptConfig())
    cfg.host = _Host()
    persona = _persona_writing("字" * 2900)
    make_script_dispatch(cfg, dispatch_persona_fn=persona)(
        "digest-writer", _step(), _ctx(tmp_path), tmp_path)

    p = persona.prompts[0]
    assert "欢迎来到跑者学堂」式问候" not in p
    assert "开场第一个 beat 用「大家好" not in p
    # and it must positively forbid the greeting + place the intro after the hook
    assert "第一个 beat 是冷钩子" in p
    assert "禁止" in p and "大家好" in p
    assert "我是卞旸，跑者学堂" in p


# ------------------------------------------- authored deliverables (title/cards)

def test_publish_title_is_persisted_not_dropped(tmp_path):
    """The 视频号 title is the one line a viewer sees before deciding to tap. The
    persona authors it; if the pipeline does not read it, it is silently lost."""
    def persona(agent, prompt, scratch_dir, expected, **kw):
        out = Path(scratch_dir) / expected
        out.write_text(json.dumps({
            "title": "最大摄氧量最低的那个，拿了全美冠军",
            "title_subtitle": "你追的那个数字可能没用",
            "end_card_bullets": ["用比赛成绩查 VDOT", "别信手表的摄氧量"],
            "beats": [{"beat_id": "b0", "text": "字" * 2900, "anchor": ANCHOR}],
        }, ensure_ascii=False), encoding="utf-8")
        return {"ok": True, "artifact_path": str(out)}

    cfg = Cfg(ScriptConfig(target_chars=3000, min_chars=2800, max_chars=3400))
    ctx = _ctx(tmp_path)
    make_script_dispatch(cfg, dispatch_persona_fn=persona)(
        "digest-writer", _step(), ctx, tmp_path)

    assert ctx["title"] == "最大摄氧量最低的那个，拿了全美冠军"

    persisted = json.loads(Path(ctx["ep"]["beats"]).read_text(encoding="utf-8"))
    assert persisted["title"] == "最大摄氧量最低的那个，拿了全美冠军"

    md = ctx["ep"]["script"].read_text(encoding="utf-8")
    assert "视频号标题" in md and "最大摄氧量最低的那个" in md
    assert "尾卡文案" in md and "用比赛成绩查 VDOT" in md
    assert "2900 字" in md


# ------------------------------------------------------- config validation

def test_config_rejects_incoherent_lengths(tmp_path):
    """min > target would make the gate unsatisfiable by construction."""
    from lib.config import ConfigError, load_config

    base = (tmp_path / "out")
    base.mkdir()
    (tmp_path / "config.yaml").write_text(
        "render: {width: 1920, height: 1080, fps: 30, subtitle_font: Arial}\n"
        "audio: {voice_id: v1, bgm_duck_db: 18.0}\n"
        "media: {provider: doubao}\n"
        "budget: {video_shots_per_episode: 6, video_floor: 4}\n"
        "ffmpeg: {ffmpeg_bin: ffmpeg, ffprobe_bin: ffprobe}\n"
        f"output_dir: {base}\n"
        "script: {target_chars: 1000, min_chars: 2800}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="min_chars"):
        load_config(str(tmp_path / "config.yaml"))


# --------------------------------------------------------- --stop-after

def test_stop_after_truncates_the_pipeline_and_skips_media():
    """--stop-after covered-update must leave the script stations and drop every
    media station — that is what makes a script-only run cost nothing to render."""
    from lib.pipeline import load_pipeline

    names = [s["name"] for s in load_pipeline()]
    cut = names.index("covered-update") + 1
    remaining = set(names[:cut])

    assert {"extract", "avoid-memo", "script", "distill", "covered-update"} <= remaining
    assert not remaining & {
        "tts", "gen-still", "gen-video", "chart", "subtitle", "assemble",
    }


def test_script_step_has_two_retries():
    """Two gates (忠实 + 字数) share the retry budget; one retry would halt an
    episode that is a single draft away from passing both."""
    from lib.pipeline import load_pipeline

    step = next(s for s in load_pipeline() if s["name"] == "script")
    assert step["retry"] == 3


# ------------------------------------------------- citation-voice pattern gap
#
# EP012 pre-polish audit (2026-07-28): the fixed 7-phrase table caught only 2
# of these 15 real variants. `_CITATION_PATTERN` (Task 1-impl) must catch most
# of them. `ground_text` (also Task 1-impl) is a deliberately weak signal —
# see its docstring — these tests assert its real (weak) behavior, not a
# faithfulness guarantee it cannot provide.

_EP012_PRE_POLISH_SNIPPETS = [
    "一分跑，两分走，重复五轮。这是教材里写出的跑走结合起步方案。",
    "但这本教材从头到尾在说同一句话：复跑这件事，不靠感觉，靠结构。",
    "但教材写得明明白白：跑走结合的目的，根本不是练心肺。",
    "如果你连这十五分钟都撑不住——其实更稳的入口，教材也写了三个。",
    "反过来——什么时候不能跑？教材列了三条。",
    "教材原话是：你说紧，是哪一层的紧？",
    "所以教材给教练的指令是：拿不准，宁可保守，别硬上这一次。",
    "好，身体这条线讲完了。但教材里另一章说了一件大多数跑者根本没意识到的事",
    "教材里给了一个真实的例子。",
    "书里特意强调：这一步不是运动员一个人打分就完事的。",
    "教材把伤后的心理变化分成了三个阶段。",
    "这才是最考验人的一段——因为教材特意写到，这一阶段还容易出现另一种情况",
    "教材给了一条很硬的规则——焦虑没有降下来之前，不要上到下一步。",
    "教材同一章里讲了一个叫心理生物学模型的框架",
    "教材里另一段讲挑战评估和威胁评估的关系",
]


def test_citation_pattern_catches_ep012_pre_polish_variants():
    from lib.ledger import verify_style

    beats = [{"beat_id": f"b{i:02d}", "text": t} for i, t in enumerate(_EP012_PRE_POLISH_SNIPPETS)]
    result = verify_style({"beats": beats})
    citation_hits = [v for v in result["violations"] if v["rule"] == "引用腔"]
    assert len(citation_hits) > 10, f"pattern should catch most of 15 known variants, only caught {len(citation_hits)}"


def test_ground_text_finds_a_number_anywhere_in_fulltext():
    from lib.ledger import ground_text

    fulltext = "DOMS usually occurs within 24-48 hours following strenuous activity."
    # ground_text 只回答"这个数字字符串在原文某处出现过吗"——不是"这句话没有编造"。
    assert ground_text("运动后 24 到 48 小时的延迟性酸痛", fulltext) is True
    # 纯中文候选（无数字/无 ASCII 词）恒返回 True——这是已知局限，不是 bug，
    # 测试把它明写出来，不能让人以为 ground_text 能拦住纯文字编造。
    assert ground_text("教材说月球是奶酪做的，跑者应该每天吃三块", fulltext) is True
