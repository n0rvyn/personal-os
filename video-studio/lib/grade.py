"""Visual signature: two orthogonal dimensions — art style and color grade.

Art style (open vocabulary)
  Controls the *generation* prompt prefix fed to the image model.
  Known names map to curated prefixes; any unknown name is used directly as a
  prefix (open vocab), so "赛博朋克 / 油画 / 像素" all produce usable output
  without requiring an explicit entry here.  Dimension is module-scoped.

Color grade (finite set)
  Controls the *ffmpeg filterchain* applied to every photographic shot so stills
  and S2V clips share one palette instead of drifting warm↔cool.  Known names
  map to tuned filters; unknown/empty names fall back to DEFAULT_COLOR_GRADE.
  Dimension is series-scoped (default) with optional per-episode director
  override.

These two dimensions are independent.  A module can be 简笔日漫 (anime) with
冷蓝晨光 (cool-morning) color grade, or 写实电影 with 暖金 — every combination
is valid.  Charts (matplotlib data) are excluded from color grading to keep
numbers readable; card overlays are burned AFTER grading.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# COLOR_GRADES — finite set, series-level lock
# ---------------------------------------------------------------------------

COLOR_GRADES: dict[str, str] = {
    # Locked signature: cool morning, calm, cinematic.
    "冷蓝晨光": (
        "colorbalance=rs=-0.05:bs=0.07:bm=0.02,"
        "curves=master='0/0.02 0.5/0.5 1/0.98',"
        "eq=contrast=1.06:saturation=0.97"
    ),
    "暖金": (
        "colorbalance=rs=0.06:rh=0.05:bs=-0.05,"
        "eq=contrast=1.05:saturation=1.06"
    ),
    "橙青": (
        "colorbalance=rs=-0.06:bs=0.10:rh=0.07:bh=-0.05,"
        "eq=contrast=1.10:saturation=1.02"
    ),
    # Near-neutral: mild eq only, no colorbalance channel shift.
    # Use for anime/illustration where a cool/warm shift would fight the flat art.
    "中性": "eq=contrast=1.02:saturation=1.03",
}

DEFAULT_COLOR_GRADE = "冷蓝晨光"

# ---------------------------------------------------------------------------
# ART_STYLES — open vocabulary; unknown names fall through to raw prefix
# ---------------------------------------------------------------------------

ART_STYLES: dict[str, str] = {
    "简笔日漫": (
        "anime illustration, simple clean line art, flat cel shading, "
        "soft flat colors, no photorealism, no text"
    ),
    # Cinematic realism: color-temperature words removed — the color grade
    # controls that; style prefix stays palette-agnostic.
    "写实电影": "cinematic, film grain, realistic, no text",
}

DEFAULT_ART_STYLE = "写实电影"

# ---------------------------------------------------------------------------
# resolve_color_grade
# ---------------------------------------------------------------------------


def resolve_color_grade(name: str | None = None) -> str:
    """Return the ffmpeg filterchain for *name*.

    - Known name → its filter.
    - Empty / None / unknown → ``COLOR_GRADES[DEFAULT_COLOR_GRADE]``.
    """
    if not name:
        return COLOR_GRADES[DEFAULT_COLOR_GRADE]
    n = name.strip()
    return COLOR_GRADES.get(n, COLOR_GRADES[DEFAULT_COLOR_GRADE])


# ---------------------------------------------------------------------------
# resolve_art_style — open vocabulary
# ---------------------------------------------------------------------------


def resolve_art_style(name: str | None = None) -> str:
    """Return the image-gen prompt prefix for *name*.

    - Known name → its curated prefix.
    - Empty / None → ``ART_STYLES[DEFAULT_ART_STYLE]``.
    - Unknown name → ``"{name.strip()}, no text"`` (open vocab — any style works).
    """
    if not name:
        return ART_STYLES[DEFAULT_ART_STYLE]
    n = name.strip()
    if n in ART_STYLES:
        return ART_STYLES[n]
    # Open vocabulary: unknown name becomes the prompt prefix directly.
    return f"{n}, no text"


# ---------------------------------------------------------------------------
# Convenience aliases
# ---------------------------------------------------------------------------

art_style_prefix = resolve_art_style
color_grade_filter = resolve_color_grade
