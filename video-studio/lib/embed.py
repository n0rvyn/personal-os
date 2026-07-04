"""Embedding interface — Swift `NLContextualEmbedding` shell + cosine + n-gram fallback.

This module is the SOLE place in the codebase that shells out to
`tools/embed.swift`. It encapsulates:

  1. macOS-only helper detection (`_helper_available`)
  2. Subprocess invocation as a LIST argv (no `shell=True`, no text in argv —
     text travels via stdin — threat model)
  3. JSON parse of `{"vector": [Double, ...]}` from the Swift helper's stdout
  4. Pure-math `cosine(a, b)` (zero-vector safe → 0.0)
  5. Pure-math `ngram_similarity(a, b)` — 2-gram set Jaccard, fallback path
     when the helper is unavailable or fails
  6. `similarity(a, b)` — vector cosine when both texts embed cleanly,
     n-gram Jaccard otherwise. NEVER raises on helper failure (fail-soft).

Threat model (per phase2-covered-ground-plan §Threat Model):
  - Text travels via stdin (`input=` kwarg), never inside argv.
  - LIST argv + no `shell=True` is non-negotiable.
  - Helper missing / non-macOS / timeout / non-zero exit / malformed JSON
    → return None (caller falls back to n-gram). The pipeline never halts
    because the embedder failed.

This module is DATA-FREE — it doesn't know about episodes, stores, or
recurrence. Phase 2 callers (`lib.coveredground.update_store`,
`lib.runner._run_code_step`) compose similarity calls; this module just
computes scores.
"""
from __future__ import annotations

import json
import math
import os
import platform
import shutil
import subprocess  # noqa: F401  (referenced via exception below)
import warnings
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Union


# The default subprocess runner. Used to detect "test injected a fake"
# so we skip the on-disk helper check in that case.
_DEFAULT_RUNNER = subprocess.run


# One-time fallback-warning guard. When a helper binary/source RESOLVES on disk
# but fails to produce a vector at exec time — the classic case being the
# committed x86_64 `tools/embed` binary on an arm64 host without Rosetta, which
# passes `os.access(X_OK)` but raises OSError "Bad CPU type in executable" —
# `embed_text` returns None and `similarity` silently degrades to n-gram
# Jaccard. The covered-ground store then records weaker (literal-bigram)
# signals indefinitely with no operator signal. Warn ONCE per process so the
# degradation is visible without spamming (similarity calls embed_text twice
# per pair, and the store calls similarity many times per run).
_FALLBACK_WARNED = False


def _warn_embed_fallback(helper: str) -> None:
    """Emit a one-time RuntimeWarning when a RESOLVED helper fails to run.

    Only fires on the production path when a helper was found on disk but
    `_invoke_runner` returned None (arch mismatch / missing swift / timeout /
    non-zero exit / malformed JSON). The "no helper at all" path returns
    earlier WITHOUT warning — that absence is expected (non-macOS / unbuilt),
    not a silent surprise.
    """
    global _FALLBACK_WARNED
    if _FALLBACK_WARNED:
        return
    _FALLBACK_WARNED = True
    warnings.warn(
        f"embed: helper resolved ({helper}) but produced no vector — falling "
        f"back to n-gram Jaccard for ALL similarity this run. Likely an "
        f"architecture mismatch (e.g. the committed x86_64 tools/embed on an "
        f"arm64 host without Rosetta). Rebuild for this host with "
        f"`swiftc tools/embed.swift -o tools/embed` to restore the "
        f"NLContextualEmbedding path.",
        RuntimeWarning,
        stacklevel=3,
    )


# ---------------------------------------------------------------------------
# N-gram fallback knobs
# ---------------------------------------------------------------------------

# N-gram order for the v1 fallback (Chinese bigrams approximate word-level
# overlap without a tokenizer). 3-grams over-segment short phrases.
_NGRAM_N = 2

# Reskin detection threshold lives in ONE place: `lib.coveredground._RESKIN_THRESHOLD`.
# This module intentionally does NOT define its own copy — an earlier dead
# `_RESKIN_THRESHOLD = 0.93` here was unused (no reader inside embed.py, no
# importer elsewhere) and had drifted from BOTH the plan's 0.82 and the
# operative coveredground value; removed to keep a single source of truth.
#
# Rationale for the operative value (Phase-2 e2e measurement, 2026-06-14):
# 0.82 FALSE-MERGED distinct same-family historical anchors — cos(1956苏伊士
# 运河危机, 1973石油危机)=0.891 wrongly collapsed two anchors, deleting 石油 as a
# trackable anchor. Critically, a genuine reskin (印刷术/活字印刷术=0.884) scores
# LOWER than that false-merge — so NO single threshold separates "reskin of same
# anchor" from "different anchor, same family" at phrase level. Resolution: merge
# only near-identical (exact match = 1.0 still merges); the distiller's consistent
# anchor naming + count-based staleness carry the dedup. Phrase-level embedding
# reskin is a known-weak refinement.

# Default subprocess timeout for the Swift helper. A short Chinese text
# embeds in well under a second; 30s covers cold asset load on first run.
_EMBED_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Vector path: shell the Swift helper, parse JSON
# ---------------------------------------------------------------------------

def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity in [-1, 1] (clipped to [0, 1] for callers that
    only ever feed non-negative NLContextualEmbedding vectors).

    Returns 0.0 when either vector is zero (no divide-by-zero, no
    `math.nan`). Treats vectors of unequal length as overlapping on the
    shorter prefix (defensive — the Swift helper emits fixed dim).
    """
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for i in range(n):
        ai = float(a[i])
        bi = float(b[i])
        dot += ai * bi
        na += ai * ai
        nb += bi * bi
    if na == 0.0 or nb == 0.0:
        return 0.0
    sim = dot / (math.sqrt(na) * math.sqrt(nb))
    # Clip into [0, 1] — callers treat this as a "similarity score", not a
    # signed dot product. NLContextualEmbedding vectors are non-negative
    # in practice; this is a safety belt, not a normalization.
    if sim < 0.0:
        return 0.0
    if sim > 1.0:
        return 1.0
    return sim


def ngram_similarity(a: str, b: str) -> float:
    """2-gram set Jaccard — the v1 fallback for cross-anchor comparison.

    Both inputs are lowercased and split into `_NGRAM_N`-character sliding
    windows; similarity is |A ∩ B| / |A ∪ B|. Empty input on either side
    → 0.0 (no division by zero, no index errors).

    Cheap to compute, language-agnostic, and a strong-enough proxy for the
    "is this the same apparatus under a thin reskin?" question that the
    covered-ground store relies on.
    """
    sa = _ngram_set(a)
    sb = _ngram_set(b)
    if not sa and not sb:
        return 0.0
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    if union == 0:
        return 0.0
    return inter / union


def _ngram_set(text: str) -> set[str]:
    """Lowercase text → set of character `_NGRAM_N`-grams.

    Whitespace is preserved (don't strip it — Chinese text often has no
    spaces, but mixed-language anchors may). Empty input → empty set.
    """
    if not text:
        return set()
    s = text.lower()
    n = _NGRAM_N
    if len(s) < n:
        # A single-char "anchor" yields one n-gram of length 1 — still
        # valid for Jaccard. Don't drop it.
        return {s}
    return {s[i:i + n] for i in range(len(s) - n + 1)}


# ---------------------------------------------------------------------------
# Swift helper detection + shell
# ---------------------------------------------------------------------------

def _resolve_swift_bin(
    plugin_root: Optional[Union[str, os.PathLike]],
    swift_bin: Optional[str],
) -> Optional[str]:
    """Pick the helper to invoke: explicit `swift_bin` > compiled bin next
    to `embed.swift` > the `.swift` source itself (requires `swift` on PATH
    at call time). Returns None if no candidate exists.
    """
    if swift_bin:
        return swift_bin
    if not plugin_root:
        return None
    root = Path(str(plugin_root))
    # Pre-compiled bin takes priority over recompiling the source each call.
    candidates = [
        root / "tools" / "embed",
        root / "tools" / "tools" / "embed",  # defensive: tools/ nested once
    ]
    for c in candidates:
        if c.is_file() and os.access(str(c), os.X_OK):
            return str(c)
    src = root / "tools" / "embed.swift"
    if src.is_file():
        return str(src)
    return None


def _helper_available(
    plugin_root: Optional[Union[str, os.PathLike]] = None,
    swift_bin: Optional[str] = None,
) -> bool:
    """True only when (a) we're on macOS and (b) the helper source or
    pre-compiled binary exists on disk.

    Does NOT verify that `swiftc` is on PATH — that's a runtime concern of
    `embed_text` (a source-mode invocation will return None on missing
    swiftc and the caller will fall back to n-gram).
    """
    if platform.system() != "Darwin":
        return False
    return _resolve_swift_bin(plugin_root, swift_bin) is not None


def embed_text(
    text: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
    swift_bin: Optional[str] = None,
    plugin_root: Optional[Union[str, os.PathLike]] = None,
    timeout: int = _EMBED_TIMEOUT_S,
) -> Optional[list[float]]:
    """Run `tools/embed.swift` against `text` and return the embedding vector.

    Text travels via stdin (never argv). Returns None on any failure —
    non-macOS, missing helper, swiftc missing, subprocess timeout,
    non-zero exit, malformed JSON, or empty vector. Callers fall back to
    `ngram_similarity` when this returns None.

    The `runner` kwarg is for tests (fake `subprocess.run`); production
    callers should omit it. When a non-default `runner` is injected, the
    on-disk helper check is bypassed — the test controls the runner and
    owns the argv it returns. Production callers (`subprocess.run`) still
    require the helper to be resolvable.
    """
    if text is None or text == "":
        return None

    # Test path: the injected runner owns reality. Skip helper detection
    # so a test asserting `len(call_log) == 2` actually sees the calls.
    if runner is not _DEFAULT_RUNNER:
        argv: list[str] = ["<fake-helper>"]
        return _invoke_runner(runner, argv, text, timeout)

    helper = _resolve_swift_bin(plugin_root, swift_bin)
    if helper is None:
        return None

    # Source-mode invocation goes through `swift` (the interpreter driver);
    # pre-compiled bin is invoked directly. `swift` is on PATH on a normal
    # macOS dev box — if not, the subprocess returns non-zero and we fall
    # back.
    if helper.endswith(".swift"):
        argv = ["swift", helper]
    else:
        argv = [helper]

    result = _invoke_runner(runner, argv, text, timeout)
    if result is None:
        # A helper RESOLVED on disk but produced no vector — surface the
        # silent degradation once (the "helper is None" branch above already
        # returned for the expected no-helper case, without warning).
        _warn_embed_fallback(helper)
    return result


def _invoke_runner(
    runner: Callable[..., Any],
    argv: list[str],
    text: str,
    timeout: int,
) -> Optional[list[float]]:
    """Shared subprocess invocation + JSON parse. Returns None on any
    failure mode (timeout, missing binary, non-zero exit, malformed
    JSON, empty vector). The public API funnels through here so the
    production and test paths stay in lock-step.
    """
    try:
        completed = runner(
            argv,
            input=text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    except FileNotFoundError:
        # `swift` not on PATH, or helper binary missing despite existence
        # check (race).
        return None
    except OSError:
        return None
    except Exception:
        # Test path: an injected fake runner may raise arbitrary errors
        # to simulate helper failure. Production subprocess.run only
        # raises the three caught above; anything else here means a
        # fake is asserting a failure mode — fall back to n-gram.
        return None

    returncode = getattr(completed, "returncode", None)
    if returncode != 0:
        return None

    stdout = getattr(completed, "stdout", "") or ""
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    vec = payload.get("vector")
    if not isinstance(vec, list) or not vec:
        return None
    # Defensive: every element should be a number. Anything else → None.
    try:
        return [float(x) for x in vec]
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Public entry point used by Phase 2 callers
# ---------------------------------------------------------------------------

def similarity(
    a: str,
    b: str,
    *,
    runner: Callable[..., Any] = subprocess.run,
    swift_bin: Optional[str] = None,
    plugin_root: Optional[Union[str, os.PathLike]] = None,
    timeout: int = _EMBED_TIMEOUT_S,
) -> float:
    """Similarity in [0, 1] between two Chinese anchors.

    Tries the vector path first (both texts via `embed_text`); falls back
    to `ngram_similarity` whenever either call returns None OR the vector
    path is unavailable (non-macOS, missing helper, etc). Never raises on
    helper failure — that's the contract callers depend on (covered-ground
    updates, runner post-publish).
    """
    va = embed_text(
        a,
        runner=runner,
        swift_bin=swift_bin,
        plugin_root=plugin_root,
        timeout=timeout,
    )
    vb = embed_text(
        b,
        runner=runner,
        swift_bin=swift_bin,
        plugin_root=plugin_root,
        timeout=timeout,
    )
    if va is not None and vb is not None:
        return cosine(va, vb)
    return ngram_similarity(a, b)


# ---------------------------------------------------------------------------
# Tiny CLI for ad-hoc verification (echo "text" | python -m lib.embed).
# Not a "public API" — for debugging only. The pipeline never invokes
# `python -m lib.embed`; it imports `similarity` / `embed_text`.
# ---------------------------------------------------------------------------

def _main() -> int:
    """Read stdin, print `similarity(reference, stdin)` to stdout.

    Optional first arg: a reference text. If absent, print the embedding
    vector length and the n-gram-self score (1.0 for non-empty input).
    """
    import sys

    ref = sys.argv[1] if len(sys.argv) > 1 else None
    raw = sys.stdin.read()
    if ref is None:
        vec = embed_text(raw)
        if vec is None:
            print(f"vector=<unavailable> ngram(self)={ngram_similarity(raw, raw)}")
            return 0
        print(f"vector_dim={len(vec)}")
        return 0
    print(similarity(ref, raw))
    return 0


if __name__ == "__main__":
    import sys  # noqa: F401  (kept local for the __main__ guard)

    raise SystemExit(_main())