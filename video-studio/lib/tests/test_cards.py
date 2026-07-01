"""Unit tests for the title/end card builders and their ffcmd argv."""
from lib import cards, ffcmd


def test_title_ass_has_styles_and_copy():
    ass = cards.build_title_ass("跑步知识 · EP.01", "乳酸阈配速", "副标题占位")
    assert "[V4+ Styles]" in ass
    assert "PlayResX: 1920" in ass and "PlayResY: 1080" in ass
    # parameterized copy lands in the events
    assert "跑步知识 · EP.01" in ass
    assert "乳酸阈配速" in ass
    assert "副标题占位" in ass
    # duration default -> end timestamp
    assert f"0:00:{cards.TITLE_DUR:05.2f}" in ass


def test_end_ass_renders_each_bullet_with_dividers():
    bullets = [["01", "甲"], ["02", "乙"], ["03", "丙"]]
    ass = cards.build_end_ass("本期 3 个要点", bullets, "关注 · X    下期 · Y")
    for num, line in bullets:
        assert num in ass
        assert line in ass
    assert "本期 3 个要点" in ass
    assert "关注 · X    下期 · Y" in ass
    # N bullets -> N-1 hairline dividers (the grey rect colour)
    assert ass.count(cards._GREY) == len(bullets) - 1


def test_end_ass_single_bullet_has_no_divider():
    ass = cards.build_end_ass("要点", [["01", "只有一条"]], "footer")
    assert cards._GREY not in ass


# ---- doc-mode card copy (built faithfully from the episode) ----

def test_build_title_card_from_episode_metadata():
    tcfg = cards.build_title_card("VDOT 是什么", "学会反推训练配速", "003")
    assert tcfg["title"] == "VDOT 是什么"          # episode title, not a default
    assert tcfg["subtitle"] == "学会反推训练配速"     # learning goal
    assert tcfg["tag"] == "跑步知识 · EP.03"          # episode number
    assert tcfg["bg_prompt"] == cards.DEFAULT_TITLE["bg_prompt"]   # column-locked


def test_build_title_card_falls_back_when_metadata_missing():
    tcfg = cards.build_title_card("", "", "")
    assert tcfg["title"] == cards.DEFAULT_TITLE["title"]
    assert tcfg["tag"] == cards.DEFAULT_TITLE["tag"]


def test_parse_takeaways_extracts_three_bullets():
    text = ("三件事带走。\n\n第一，你的比赛成绩是有用的数据。一场 5K。"
            "第二，别用别人的配速。第三，每 6 到 8 周重测一次。")
    assert cards.parse_takeaways(text) == [
        "你的比赛成绩是有用的数据", "别用别人的配速", "每 6 到 8 周重测一次"]


def test_parse_takeaways_handles_colon_and_dun_separators():
    # 第一：…、第二：…、第三：… — trailing enumerator punctuation stripped
    text = "三条带走。第一：测出你的400米、第二：算出差值、第三：对照表看短板。"
    assert cards.parse_takeaways(text) == ["测出你的400米", "算出差值", "对照表看短板"]


def test_parse_takeaways_returns_empty_without_convention():
    assert cards.parse_takeaways("这段没有任何结尾要点枚举。") == []
    assert cards.parse_takeaways("") == []
    # "第一次" must not false-match (no separator after 一)
    assert cards.parse_takeaways("这是第一次尝试，没有要点。") == []


def test_build_end_card_uses_takeaways_beat():
    text = "三件事带走。第一，查表。第二，分区练。第三，别硬跟别人。"
    ecfg = cards.build_end_card(text, "学会反推训练配速")
    assert ecfg["tag"] == "本期 3 个要点"
    assert ecfg["bullets"] == [["01", "查表"], ["02", "分区练"], ["03", "别硬跟别人"]]


def test_build_end_card_neutral_fallback_never_fabricates():
    # no takeaways convention → neutral card, learning goal only, no fake bullets
    ecfg = cards.build_end_card("没有枚举结构的一段话。", "学会反推训练配速")
    assert ecfg["tag"] == "本期小结"
    assert ecfg["bullets"] == [["", "学会反推训练配速"]]
    # the wrong-episode DEFAULT_END bullets never leak in
    assert not any("乳酸阈" in line for _, line in ecfg["bullets"])


def test_build_end_card_empty_when_no_source():
    ecfg = cards.build_end_card("", "")
    assert ecfg["bullets"] == []
    # build_end_ass must render an empty-bullet card without error
    ass = cards.build_end_ass(ecfg["tag"], ecfg["bullets"], ecfg["footer"])
    assert "本期小结" in ass


# ---- Task 4-tests: authored end_card_bullets + CJK grounding ----

def test_bullet_grounded_cjk_overlap():
    # paraphrase bullet whose bigrams overlap the script → grounded (True)
    script = "间歇冲刺训练时要盯配速表，不要一直盯着心率表，否则容易跑慢"
    assert cards._bullet_grounded("间歇盯配速别看心率", script) is True
    # completely off-topic bullet against a VDOT/zone script → not grounded (False)
    vdot_script = "VDOT是衡量跑步能力的综合指标，七区强度边界由VO2max百分比定义"
    assert cards._bullet_grounded("多喝水补充电解质", vdot_script) is False


def test_build_end_card_prefers_authored_bullets():
    script = "轻松跑要维持E档心率，阈值跑是舒服地难受的节奏，间歇跑盯配速别看心率表"
    ecfg = cards.build_end_card(
        last_beat_text="三件事带走。",
        learning_goal="学会七区强度分法",
        authored_bullets=["轻松跑按E档心率", "阈值跑舒服地难受", "间歇盯配速别看心率"],
        script_text=script,
    )
    assert ecfg["tag"] == "本期 3 个要点"
    bullet_lines = [line for _, line in ecfg["bullets"]]
    assert "轻松跑按E档心率" in bullet_lines
    assert "间歇盯配速别看心率" in bullet_lines


def test_build_end_card_drops_ungrounded_authored_bullet():
    # script is about VDOT zones — "多喝水" is completely off-topic
    script = "VDOT强度分区从E区到R区，每个区间有对应的VO2max百分比和心率区间"
    ecfg = cards.build_end_card(
        last_beat_text="第一，E区是有氧基础。第二，M区是马拉松配速。第三，T区是阈值。",
        learning_goal="理解VDOT七区",
        authored_bullets=["多喝水补充电解质"],  # off-topic → dropped
        script_text=script,
    )
    # all authored bullets dropped → falls back to parse_takeaways or neutral
    bullet_lines = [line for _, line in ecfg["bullets"]]
    assert "多喝水补充电解质" not in bullet_lines


def test_build_end_card_falls_back_when_no_authored():
    # authored_bullets=None → existing parse_takeaways path unchanged
    text = "三件事带走。第一，查表。第二，分区练。第三，别硬跟别人。"
    ecfg = cards.build_end_card(text, "学会反推训练配速", authored_bullets=None)
    assert ecfg["tag"] == "本期 3 个要点"
    assert ecfg["bullets"] == [["01", "查表"], ["02", "分区练"], ["03", "别硬跟别人"]]


# ---- Task 5-tests: title_subtitle authored + fallback ----

def test_build_title_card_prefers_authored_subtitle():
    # When subtitle is supplied, use it verbatim (not learning_goal).
    tcfg = cards.build_title_card(
        "七区强度",
        learning_goal="学会七种强度分区的概念及训练配速应用方法",
        seq="001",
        subtitle="七个强度区，一次搞懂",
    )
    assert tcfg["subtitle"] == "七个强度区，一次搞懂"


def test_build_title_card_truncates_learning_goal_fallback():
    # No subtitle → learning_goal truncated to ≤22 chars (or first punctuation).
    long_goal = "学会七种强度分区的概念及训练配速应用方法，理解VO2max百分比如何对应日常跑步节奏"
    tcfg = cards.build_title_card("七区强度", learning_goal=long_goal, seq="002")
    assert len(tcfg["subtitle"]) <= 22


def test_build_title_card_no_annotation_prefix():
    # An annotation-prefixed learning_goal must NOT leak onto the card when
    # an authored subtitle is provided (NH-5).
    annotated_goal = "工程估计，非源直出：学会七区强度分法"
    tcfg = cards.build_title_card(
        "七区强度",
        learning_goal=annotated_goal,
        seq="003",
        subtitle="七个强度区，一次搞懂",
    )
    assert "工程估计" not in tcfg["subtitle"]
    assert tcfg["subtitle"] == "七个强度区，一次搞懂"


# ---- length-adaptive title layout (≤2 lines, no collision) ----

def test_layout_title_short_stays_one_big_line():
    lines, fs = cards._layout_title("乳酸阈配速")
    assert lines == ["乳酸阈配速"]
    assert fs == cards._TITLE_FS_MAX


def test_layout_title_long_breaks_at_punctuation_and_shrinks():
    lines, fs = cards._layout_title("七区强度总览：E 到 FR，你的每一次跑步该多快")
    assert len(lines) == 2                       # exactly two lines
    assert lines[0] == "七区强度总览：E 到 FR"       # split at the comma, not mid-phrase
    assert lines[1] == "你的每一次跑步该多快"
    assert fs < cards._TITLE_FS_MAX               # shrunk to fit
    assert fs >= cards._TITLE_FS_MIN
    # longest line fits the column at the chosen font
    assert max(len(l) for l in lines) * fs <= cards._TITLE_COL_W


def test_layout_title_no_punctuation_hard_splits():
    lines, fs = cards._layout_title("这是一个没有标点符号的超长标题需要硬拆成两行显示")
    assert len(lines) == 2
    assert "".join(lines) == "这是一个没有标点符号的超长标题需要硬拆成两行显示"


def test_build_title_ass_two_line_title_no_tag_collision():
    ass = cards.build_title_ass(
        "跑步知识 · EP.01", "七区强度总览：E 到 FR，你的每一次跑步该多快", "副标")
    # both title lines present, top-anchored below the tag (y ≥ 452, tag bar 392–432)
    assert "七区强度总览：E 到 FR" in ass and "你的每一次跑步该多快" in ass
    assert r"\an7\pos(140,452)" in ass           # first title line starts below the tag
    # a shrunk font-size override is applied (not the full 128)
    assert r"\fs" in ass


def test_still_card_cmd_is_video_only_with_ass_and_kenburns():
    argv = ffcmd.still_card_cmd("/bg.png", 2.6, "/x.ass", "/out.mp4")
    assert "-an" in argv                      # video-only
    assert argv[-1] == "/out.mp4"
    vf = argv[argv.index("-vf") + 1]
    assert "zoompan" in vf                     # Ken Burns push-in
    assert "ass=/x.ass" in vf                  # design overlay burned in
    assert "-i" in argv and "/bg.png" in argv


def test_silence_mp3_cmd_matches_tts_params():
    argv = ffcmd.silence_mp3_cmd(2.6, "/sil.mp3")
    assert "anullsrc=channel_layout=mono:sample_rate=32000" in argv
    assert argv[argv.index("-ar") + 1] == "32000"
    assert argv[argv.index("-ac") + 1] == "1"
    assert argv[argv.index("-t") + 1] == "2.6"
    assert "libmp3lame" in argv
    assert argv[-1] == "/sil.mp3"
