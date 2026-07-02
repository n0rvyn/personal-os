"""module_setup.py — modular art-style lock + per-module character reference.

Provides a pure-logic prepare_module() function that:
  1. Asks the user for an art style via ask_fn (injected).
  2. Generates a character reference image via gen_fn (injected).
  3. Writes both back onto the module and persists via save_fn (injected).

All side-effects (user interaction, image generation, file I/O) are injected,
making this testable without real interaction or API calls.

Live wrappers (AskUserQuestion + Doubao image gen) live in the skill layer
(video-module-setup/SKILL.md) and are user-gated.
"""
from __future__ import annotations

from typing import Callable

from lib.syllabus import Module, Syllabus


def prepare_module(
    module: Module,
    syllabus: Syllabus,
    ask_fn: Callable[[Module], str],
    gen_fn: Callable[[Module, str], str],
    save_fn: Callable[[Syllabus], None],
    force: bool = False,
) -> Module:
    """Set up art style and character reference for a module.

    Parameters
    ----------
    module:   The Module to configure (mutated in-place).
    syllabus: The Syllabus that owns this module (passed to save_fn).
    ask_fn:   Called with the module; returns the chosen art style string.
    gen_fn:   Called with (module, art_style); returns path/URL of generated image.
    save_fn:  Called with the syllabus after writing fields; persists the state.
    force:    When True, run even if module already has art_style + character_ref.

    Returns
    -------
    The module object (same reference, updated in-place).
    """
    # Idempotency guard: skip if already complete and not forced.
    if not force and module.art_style and module.character_ref:
        return module

    art = ask_fn(module)
    module.art_style = art

    cref = gen_fn(module, art)
    module.character_ref = cref

    save_fn(syllabus)

    return module
