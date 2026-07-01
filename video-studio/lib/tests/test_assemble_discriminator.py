"""Unit tests for `_card_configs_for(ctx)` — the NH-2 + S1-A5 card-config
discriminator.

The helper is a pure module-level function extracted from the inline card-cfg
block inside ``step_assemble`` (wiring.py:542-556 in the pre-extraction state).
It returns ``(tcfg, ecfg)`` by branching on **fixture presence** — NOT on the
presence of ``ctx.get("topic")``, so:

  - A cards-less fixture keeps DEFAULT_* cards   (NH-2)
  - A PDF render (no fixture, no topic) gets episode-derived cards, never
    DEFAULT_END's foreign 乳酸阈/VDOT bullets  (S1-A5)

These tests import via attribute access (``wiring._card_configs_for``) so the
FAIL-first signal is an isolated ``AttributeError`` at call time, not a
whole-file collection error that masks other tests.
"""
import lib.wiring as wiring
from lib import cards


# ---- helper ----------------------------------------------------------------

def _call(ctx):
    """Call the not-yet-extracted helper via attribute access."""
    return wiring._card_configs_for(ctx)  # noqa: SLF001


# ---- test cases ------------------------------------------------------------

def test_card_configs_fixture_mode_cards_less():
    """A fixture without a 'cards' key → DEFAULT_TITLE / DEFAULT_END (NH-2).

    A cards-less fixture must never receive doc-derived or empty cards just
    because it has no 'topic' set.
    """
    ctx = {
        "fixture": {"description": "a cards-less fixture"},
        # No 'topic', no 'learning_goal' — exactly the shape a fixture render uses.
    }
    tcfg, ecfg = _call(ctx)

    # Title card stays at the column default (not empty, not doc-derived).
    assert tcfg["title"] == cards.DEFAULT_TITLE["title"]
    assert tcfg["tag"] == cards.DEFAULT_TITLE["tag"]

    # End card stays at the column default — never doc-derived from absent metadata.
    assert ecfg["tag"] == cards.DEFAULT_END["tag"]
    # DEFAULT_END's own bullets stay intact (they are the fixture's fallback).
    assert ecfg["bullets"] == cards.DEFAULT_END["bullets"]


def test_card_configs_fixture_mode_with_cards():
    """A fixture that explicitly provides 'cards' → merges over DEFAULT_*."""
    ctx = {
        "fixture": {
            "cards": {
                "title": {"title": "阈值跑实战", "tag": "跑步知识 · EP.02"},
                "end": {"tag": "本期 2 个要点",
                        "bullets": [["01", "先测后跑"], ["02", "不要靠感觉"]]},
            }
        },
    }
    tcfg, ecfg = _call(ctx)

    # Hand-authored values override the defaults.
    assert tcfg["title"] == "阈值跑实战"
    assert tcfg["tag"] == "跑步知识 · EP.02"
    # bg_prompt is NOT in the hand-authored cards → falls through to DEFAULT_TITLE.
    assert tcfg["bg_prompt"] == cards.DEFAULT_TITLE["bg_prompt"]

    assert ecfg["tag"] == "本期 2 个要点"
    assert ecfg["bullets"] == [["01", "先测后跑"], ["02", "不要靠感觉"]]
    # Footer not in hand-authored → stays DEFAULT_END.
    assert ecfg["footer"] == cards.DEFAULT_END["footer"]


def test_card_configs_doc_mode():
    """ctx with 'topic' set and no 'fixture' → episode-derived (build_title_card /
    build_end_card).
    """
    ctx = {
        "topic": "VDOT 是什么",
        "learning_goal": "学会反推训练配速",
        "seq": "003",
        "beats": [{"text": "三件事带走。第一，查表。第二，分区练。第三，别硬跟别人。"}],
    }
    tcfg, ecfg = _call(ctx)

    # Title should reflect the episode topic, not the DEFAULT.
    assert tcfg["title"] == "VDOT 是什么"
    assert "03" in tcfg["tag"]    # EP.03 tag

    # End card should reflect the episode's own takeaways.
    assert ecfg["tag"] == "本期 3 个要点"
    bullet_lines = [line for _, line in ecfg["bullets"]]
    assert "查表" in bullet_lines

    # The wrong-episode DEFAULT_END bullets must NEVER appear.
    for _, line in ecfg["bullets"]:
        assert "乳酸阈" not in line


def test_card_configs_pdf_mode_not_fixture():
    """ctx with no 'fixture' AND no 'topic' (PDF-shape render) → episode-derived
    branch, NOT DEFAULT_END's foreign 乳酸阈/VDOT bullets (S1-A5).
    """
    ctx = {
        # Neither 'fixture' nor 'topic' — the shape used by PDF-based renders.
        "learning_goal": "了解强度分区理论",
        "seq": "007",
        "beats": [{"text": "第一，E区是有氧基础。第二，T区是阈值配速。第三，I区最高强度。"}],
    }
    tcfg, ecfg = _call(ctx)

    # Must NOT produce DEFAULT_END's hardcoded foreign bullets.
    for _, line in ecfg["bullets"]:
        assert "乳酸阈 = 你还能稳定坚持的最快配速" != line, (
            "DEFAULT_END foreign bullet leaked into a non-fixture render"
        )

    # Episode-derived end card: parse_takeaways from last beat text.
    bullet_lines = [line for _, line in ecfg["bullets"]]
    assert "E区是有氧基础" in bullet_lines
