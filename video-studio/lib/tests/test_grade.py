"""Unit tests for the color-grade palette and its threading into clip argv."""
from lib import grade, ffcmd, media_image


def test_series_look_locked_to_cool_morning():
    assert grade.SERIES_LOOK == "冷蓝晨光"
    assert grade.SERIES_LOOK in grade.LOOKS


def test_grade_filter_and_prefix_come_from_locked_look():
    assert grade.grade_filter() == grade.LOOKS["冷蓝晨光"]["grade"]
    assert grade.style_prefix() == grade.LOOKS["冷蓝晨光"]["style_prefix"]
    assert "cool morning" in grade.style_prefix()  # the prompt half of the look


def test_each_look_bundles_both_halves():
    for name, look in grade.LOOKS.items():
        assert look["grade"], name
        assert look["style_prefix"], name


def test_media_image_prefix_is_sourced_from_grade():
    # The two halves of the signature stay coupled (no drift between them).
    assert media_image.STYLE_PREFIX == grade.style_prefix()


def test_grade_threads_into_photographic_clips_only():
    g = grade.grade_filter()
    # still WITH grade: appended after the Ken Burns push-in
    s = ffcmd.still_clip_cmd("/x.png", 3.0, "/o.mp4", g)
    vf = s[s.index("-vf") + 1]
    assert "zoompan" in vf and vf.endswith(g)
    # still WITHOUT grade (the chart path): untouched
    s0 = ffcmd.still_clip_cmd("/x.png", 3.0, "/o.mp4")
    assert g not in s0[s0.index("-vf") + 1]
    # card: grade BEFORE the ass overlay so amber/white design stays pure
    c = ffcmd.still_card_cmd("/bg.png", 2.6, "/a.ass", "/o.mp4", g)
    cvf = c[c.index("-vf") + 1]
    assert g in cvf and cvf.index(g) < cvf.index("ass=/a.ass")
    # S2V
    v = ffcmd.video_clip_cmd("/s.mp4", 3.0, "/o.mp4", g)
    assert v[v.index("-vf") + 1].endswith(g)
