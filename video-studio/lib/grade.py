"""Color-grade palette (POC5) — the series-level visual signature.

A "look" bundles the two halves of how a whole video reads on screen:
  - ``grade``: an ffmpeg filterchain applied to every photographic shot, so the
    AI-generated stills / S2V / card backgrounds share one palette instead of
    drifting warm↔cool shot to shot.
  - ``style_prefix``: the image-gen prompt prefix, so the pictures are GENERATED
    toward the same look the grade then locks in.

These two must agree (a cool grade over warm-prompted images fights itself), so
they live together here and are chosen ONCE at the column/series level — NOT
re-rolled per episode. A consistent look is what makes a column recognisable;
an LLM may later *recommend* a look at column setup, but it stays fixed for the
column. ``SERIES_LOOK`` is the lock.

Scope: the grade is applied to photographic shots only. Charts (matplotlib
designed data) are excluded to keep numbers readable; card design overlays
(amber bars, white text) are burned AFTER the grade so they stay pure.
"""
from __future__ import annotations

# Each look: grade = ffmpeg -vf filterchain (subtle — preserve identity/skin,
# just unify palette + add a film signature). style_prefix feeds media_image.
LOOKS: dict[str, dict[str, str]] = {
    # Locked signature for this column: cool morning, calm, cinematic.
    "冷蓝晨光": {
        "grade": ("colorbalance=rs=-0.05:bs=0.07:bm=0.02,"
                  "curves=master='0/0.02 0.5/0.5 1/0.98',"
                  "eq=contrast=1.06:saturation=0.97"),
        "style_prefix": "cinematic, cool morning light, film grain, realistic, no text",
    },
    "暖金": {
        "grade": ("colorbalance=rs=0.06:rh=0.05:bs=-0.05,"
                  "eq=contrast=1.05:saturation=1.06"),
        "style_prefix": "cinematic, warm golden hour light, film grain, realistic, no text",
    },
    "橙青": {
        "grade": ("colorbalance=rs=-0.06:bs=0.10:rh=0.07:bh=-0.05,"
                  "eq=contrast=1.10:saturation=1.02"),
        "style_prefix": "cinematic teal-and-orange, film grain, realistic, no text",
    },
    "简笔日漫": {
        # 平涂动漫不吃胶片颗粒/写实调色:grade 用近中性(只轻微统一对比/饱和),
        # 保住线稿干净、不加噪。
        "grade": "eq=contrast=1.02:saturation=1.05",
        "style_prefix": (
            "anime illustration, simple clean line art, flat cel shading, "
            "soft flat colors, no photorealism, no text"
        ),
    },
}

# The locked look for this column. Stage 1: column-level config / LLM recommend.
SERIES_LOOK = "冷蓝晨光"

# config/syllabus 里的 style 值 → 规范 look key。别名容错 + 未知/空回落默认。
_LOOK_ALIASES = {"cinematic": "冷蓝晨光", "anime": "简笔日漫", "日漫": "简笔日漫"}


def resolve_look(name: str | None) -> str:
    """Map a config style value to a canonical LOOKS key; fall back to SERIES_LOOK."""
    if not name:
        return SERIES_LOOK
    n = name.strip()
    if n in LOOKS:
        return n
    return _LOOK_ALIASES.get(n.lower(), _LOOK_ALIASES.get(n, SERIES_LOOK))


def grade_filter(look: str = SERIES_LOOK) -> str:
    """The ffmpeg grade filterchain for `look` (applied to photographic shots)."""
    return LOOKS[look]["grade"]


def style_prefix(look: str = SERIES_LOOK) -> str:
    """The image-gen prompt prefix for `look` (the other half of the signature)."""
    return LOOKS[look]["style_prefix"]
