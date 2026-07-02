"""Tests for lib.module_setup.prepare_module (TDD fail-first contract).

Written before lib/module_setup.py exists; collection MUST fail at import
time with ModuleNotFoundError — that red is the Task 2-tests success signal.

Covers:
  - prepare_module: ask_fn → art_style, gen_fn → character_ref, save_fn called
  - Idempotency: module already has art_style+character_ref, force=False → skip gen
  - All ask/gen/save are injected mocks — zero real interaction, zero cost
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure plugin root (parent of lib/) is on sys.path
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

# TDD fail-first: this import raises ModuleNotFoundError until Task 2-impl lands
from lib.module_setup import prepare_module  # noqa: E402

from lib.syllabus import Module, Syllabus, Episode  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------

def _make_syllabus(character_ref: str = "assets/character/coach_ref.jpg") -> Syllabus:
    """Return a minimal Syllabus for use in tests."""
    ep = Episode(title="Ep1", doc_refs=[], source="", verify_status="", learning_goal="", status="pending")
    mod = Module(name="TestMod", style="educational", episodes=[ep])
    return Syllabus(book="test-book", character_ref=character_ref, modules=[mod])


def _make_module(art_style: str = "", character_ref: str = "") -> Module:
    ep = Episode(title="Ep1", doc_refs=[], source="", verify_status="", learning_goal="", status="pending")
    return Module(name="TestMod", style="educational", episodes=[ep],
                  art_style=art_style, character_ref=character_ref)


# ---------------------------------------------------------------------------
# Core behaviour
# ---------------------------------------------------------------------------

def test_prepare_module_sets_art_style_and_character_ref():
    """prepare_module writes ask_fn result → module.art_style and gen_fn result → module.character_ref."""
    syllabus = _make_syllabus()
    module = _make_module()

    ask_calls: list[Module] = []
    gen_calls: list[tuple[Module, str]] = []
    save_calls: list[Syllabus] = []

    def fake_ask(mod: Module) -> str:
        ask_calls.append(mod)
        return "简笔日漫"

    def fake_gen(mod: Module, art: str) -> str:
        gen_calls.append((mod, art))
        return "/tmp/fake_ref.jpg"

    def fake_save(syl: Syllabus) -> None:
        save_calls.append(syl)

    result = prepare_module(module, syllabus, fake_ask, fake_gen, fake_save)

    assert result.art_style == "简笔日漫"
    assert result.character_ref == "/tmp/fake_ref.jpg"
    assert len(ask_calls) == 1
    assert len(gen_calls) == 1
    assert gen_calls[0][1] == "简笔日漫"
    assert len(save_calls) == 1


def test_prepare_module_returns_updated_module():
    """prepare_module returns the same module object (mutated in-place)."""
    syllabus = _make_syllabus()
    module = _make_module()

    result = prepare_module(
        module, syllabus,
        ask_fn=lambda m: "水彩写实",
        gen_fn=lambda m, a: "/tmp/char.png",
        save_fn=lambda s: None,
    )

    assert result is module


def test_prepare_module_save_fn_receives_syllabus():
    """save_fn is called with the syllabus object (not the module)."""
    syllabus = _make_syllabus()
    module = _make_module()
    saved: list[Syllabus] = []

    prepare_module(
        module, syllabus,
        ask_fn=lambda m: "简笔日漫",
        gen_fn=lambda m, a: "/tmp/char.png",
        save_fn=lambda s: saved.append(s),
    )

    assert len(saved) == 1
    assert saved[0] is syllabus


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

def test_prepare_module_idempotent_when_already_complete():
    """If module already has art_style and character_ref and force=False, gen_fn is NOT called."""
    syllabus = _make_syllabus()
    module = _make_module(art_style="水彩写实", character_ref="/existing/ref.jpg")

    gen_called: list[bool] = []
    save_called: list[bool] = []

    def fake_gen(mod: Module, art: str) -> str:
        gen_called.append(True)
        return "/tmp/new_ref.jpg"

    def fake_save(syl: Syllabus) -> None:
        save_called.append(True)

    result = prepare_module(
        module, syllabus,
        ask_fn=lambda m: "新画风",
        gen_fn=fake_gen,
        save_fn=fake_save,
        force=False,
    )

    # gen_fn must NOT be called
    assert gen_called == [], "gen_fn must not be called when module is already complete"
    # save_fn may or may not be called — but art_style/character_ref must be unchanged
    assert result.art_style == "水彩写实"
    assert result.character_ref == "/existing/ref.jpg"


def test_prepare_module_force_true_regenerates():
    """force=True triggers gen even if module already has art_style+character_ref."""
    syllabus = _make_syllabus()
    module = _make_module(art_style="水彩写实", character_ref="/existing/ref.jpg")

    gen_calls: list[str] = []

    def fake_gen(mod: Module, art: str) -> str:
        gen_calls.append(art)
        return "/tmp/new_ref.jpg"

    result = prepare_module(
        module, syllabus,
        ask_fn=lambda m: "简笔日漫",
        gen_fn=fake_gen,
        save_fn=lambda s: None,
        force=True,
    )

    assert len(gen_calls) == 1
    assert result.art_style == "简笔日漫"
    assert result.character_ref == "/tmp/new_ref.jpg"


def test_prepare_module_missing_character_ref_still_runs_gen():
    """If art_style is set but character_ref is empty, gen is still triggered (module is not complete)."""
    syllabus = _make_syllabus()
    module = _make_module(art_style="水彩写实", character_ref="")

    gen_calls: list[str] = []

    def fake_gen(mod: Module, art: str) -> str:
        gen_calls.append(art)
        return "/tmp/ref.jpg"

    result = prepare_module(
        module, syllabus,
        ask_fn=lambda m: "水彩写实",
        gen_fn=fake_gen,
        save_fn=lambda s: None,
        force=False,
    )

    assert len(gen_calls) == 1
    assert result.character_ref == "/tmp/ref.jpg"
