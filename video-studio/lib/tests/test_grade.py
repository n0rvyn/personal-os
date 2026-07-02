"""Unit tests for the two-dimensional visual API: art style (open vocab) + color grade (finite set).

New API contract:
  ART_STYLES     — dict[str, str] of known art style name → style_prefix
  COLOR_GRADES   — dict[str, str] of color grade name → ffmpeg filterchain
  resolve_art_style(name)  — open vocab: known → its prefix; unknown → name as prefix; empty → default
  resolve_color_grade(name) — finite set: known → its filter; unknown/empty → DEFAULT_COLOR_GRADE filter
  art_style_prefix          — alias for resolve_art_style
  color_grade_filter        — alias for resolve_color_grade

Old symbols that must be gone:
  resolve_look / grade_filter / style_prefix / LOOKS / SERIES_LOOK
"""
import lib.grade as g


# ---------------------------------------------------------------------------
# ART_STYLES dict exists and contains known entries
# ---------------------------------------------------------------------------

def test_art_styles_dict_exists():
    assert hasattr(g, "ART_STYLES"), "ART_STYLES dict must exist"
    assert isinstance(g.ART_STYLES, dict)
    assert len(g.ART_STYLES) >= 2, "ART_STYLES must have at least 2 known styles"


def test_art_styles_contains_anime_and_cinematic():
    assert "简笔日漫" in g.ART_STYLES, "简笔日漫 must be a known art style"
    assert "写实电影" in g.ART_STYLES, "写实电影 must be a known art style"


# ---------------------------------------------------------------------------
# COLOR_GRADES dict exists and contains known entries
# ---------------------------------------------------------------------------

def test_color_grades_dict_exists():
    assert hasattr(g, "COLOR_GRADES"), "COLOR_GRADES dict must exist"
    assert isinstance(g.COLOR_GRADES, dict)
    assert len(g.COLOR_GRADES) >= 3, "COLOR_GRADES must have at least 3 grades"


def test_color_grades_contains_expected_entries():
    assert "冷蓝晨光" in g.COLOR_GRADES
    assert "暖金" in g.COLOR_GRADES
    assert "中性" in g.COLOR_GRADES


def test_default_color_grade_is_cool_morning():
    assert hasattr(g, "DEFAULT_COLOR_GRADE")
    assert g.DEFAULT_COLOR_GRADE == "冷蓝晨光"


def test_default_art_style_exists():
    assert hasattr(g, "DEFAULT_ART_STYLE")
    assert g.DEFAULT_ART_STYLE in g.ART_STYLES


# ---------------------------------------------------------------------------
# resolve_art_style — known art style
# ---------------------------------------------------------------------------

def test_resolve_art_style_anime_contains_anime():
    prefix = g.resolve_art_style("简笔日漫")
    assert "anime" in prefix.lower(), f"Expected 'anime' in {prefix!r}"


def test_resolve_art_style_cinematic_contains_cinematic():
    prefix = g.resolve_art_style("写实电影")
    assert "cinematic" in prefix.lower() or "realistic" in prefix.lower(), (
        f"Expected cinematic/realistic in {prefix!r}"
    )


# ---------------------------------------------------------------------------
# resolve_art_style — open vocabulary (unknown name → name as prefix)
# ---------------------------------------------------------------------------

def test_resolve_art_style_unknown_returns_name_as_prefix():
    """Cyberpunk isn't in ART_STYLES; it must still produce a usable prefix."""
    prefix = g.resolve_art_style("赛博朋克")
    assert "赛博朋克" in prefix, (
        f"Open vocab: unknown art style name must appear in prefix, got {prefix!r}"
    )


def test_resolve_art_style_unknown_appends_no_text():
    """Open vocab prefix should include 'no text' to suppress text watermarks."""
    prefix = g.resolve_art_style("油画风格")
    assert "no text" in prefix.lower(), (
        f"Open vocab prefix should include 'no text', got {prefix!r}"
    )


# ---------------------------------------------------------------------------
# resolve_art_style — empty / None → default
# ---------------------------------------------------------------------------

def test_resolve_art_style_empty_returns_default():
    default_prefix = g.resolve_art_style("")
    expected = g.ART_STYLES[g.DEFAULT_ART_STYLE]
    assert default_prefix == expected, (
        f"Empty name must return default prefix {expected!r}, got {default_prefix!r}"
    )


def test_resolve_art_style_none_returns_default():
    default_prefix = g.resolve_art_style(None)
    expected = g.ART_STYLES[g.DEFAULT_ART_STYLE]
    assert default_prefix == expected


# ---------------------------------------------------------------------------
# resolve_color_grade — known grades
# ---------------------------------------------------------------------------

def test_resolve_color_grade_cool_morning_contains_colorbalance():
    filt = g.resolve_color_grade("冷蓝晨光")
    assert "colorbalance" in filt, f"Expected 'colorbalance' in 冷蓝晨光 filter, got {filt!r}"


def test_resolve_color_grade_warm_gold_contains_colorbalance():
    filt = g.resolve_color_grade("暖金")
    assert "colorbalance" in filt


def test_resolve_color_grade_neutral_is_near_neutral():
    """中性 grade must NOT use a colorbalance (it's near-neutral / eq only)."""
    filt = g.resolve_color_grade("中性")
    assert filt, "中性 filter must be non-empty"
    # Near-neutral = eq based, no heavy colorbalance shift
    assert "eq=" in filt, f"中性 grade expected eq=, got {filt!r}"


# ---------------------------------------------------------------------------
# resolve_color_grade — fallback on unknown/empty
# ---------------------------------------------------------------------------

def test_resolve_color_grade_unknown_falls_back_to_default():
    default_filt = g.resolve_color_grade("不存在的色调")
    expected = g.COLOR_GRADES[g.DEFAULT_COLOR_GRADE]
    assert default_filt == expected, (
        f"Unknown grade must fall back to default {expected!r}, got {default_filt!r}"
    )


def test_resolve_color_grade_empty_falls_back_to_default():
    default_filt = g.resolve_color_grade("")
    expected = g.COLOR_GRADES[g.DEFAULT_COLOR_GRADE]
    assert default_filt == expected


def test_resolve_color_grade_none_falls_back_to_default():
    default_filt = g.resolve_color_grade(None)
    expected = g.COLOR_GRADES[g.DEFAULT_COLOR_GRADE]
    assert default_filt == expected


# ---------------------------------------------------------------------------
# Two dimensions are independent (calling one doesn't affect the other)
# ---------------------------------------------------------------------------

def test_art_style_and_color_grade_are_independent():
    """Setting art style to anime must not change the color grade result."""
    art = g.resolve_art_style("简笔日漫")
    color_before = g.resolve_color_grade("冷蓝晨光")
    # Calling art style resolution must not mutate color grade state
    _ = g.resolve_art_style("写实电影")
    color_after = g.resolve_color_grade("冷蓝晨光")
    assert color_before == color_after, "resolve_art_style must not affect resolve_color_grade"
    assert "anime" in art.lower()


def test_anime_art_style_with_cool_grade_independent():
    """Anime art style + cool-morning color grade can coexist; each resolved separately."""
    art_prefix = g.resolve_art_style("简笔日漫")
    grade_filter = g.resolve_color_grade("冷蓝晨光")
    assert "anime" in art_prefix.lower()
    assert "colorbalance" in grade_filter
    # They are separate strings, not entangled
    assert art_prefix != grade_filter


# ---------------------------------------------------------------------------
# Convenience aliases exist
# ---------------------------------------------------------------------------

def test_art_style_prefix_alias_exists():
    assert hasattr(g, "art_style_prefix"), "art_style_prefix alias must exist"
    assert g.art_style_prefix("简笔日漫") == g.resolve_art_style("简笔日漫")


def test_color_grade_filter_alias_exists():
    assert hasattr(g, "color_grade_filter"), "color_grade_filter alias must exist"
    assert g.color_grade_filter("冷蓝晨光") == g.resolve_color_grade("冷蓝晨光")


# ---------------------------------------------------------------------------
# Old symbols are GONE (regression guard)
# ---------------------------------------------------------------------------

def test_resolve_look_removed():
    assert not hasattr(g, "resolve_look"), (
        "resolve_look must be removed — callers must use resolve_art_style/resolve_color_grade"
    )


def test_grade_filter_removed():
    assert not hasattr(g, "grade_filter"), (
        "grade_filter must be removed — callers must use color_grade_filter/resolve_color_grade"
    )


def test_style_prefix_removed():
    assert not hasattr(g, "style_prefix"), (
        "style_prefix must be removed — callers must use art_style_prefix/resolve_art_style"
    )


def test_looks_removed():
    assert not hasattr(g, "LOOKS"), (
        "LOOKS must be removed — replaced by ART_STYLES + COLOR_GRADES"
    )


def test_series_look_removed():
    assert not hasattr(g, "SERIES_LOOK"), (
        "SERIES_LOOK must be removed — replaced by DEFAULT_COLOR_GRADE"
    )


# ---------------------------------------------------------------------------
# Chart non-grade invariant: 中性 filter must not use heavy colorbalance
# ---------------------------------------------------------------------------

def test_neutral_grade_safe_for_chart_paths():
    """中性 should be usable for chart-adjacent flows without distorting colors."""
    filt = g.resolve_color_grade("中性")
    # The neutral grade is mild — eq based, no colorbalance that shifts channels
    assert "colorbalance" not in filt, (
        f"中性 grade must not use colorbalance (reserved for photographic looks), got {filt!r}"
    )
