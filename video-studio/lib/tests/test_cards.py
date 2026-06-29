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
