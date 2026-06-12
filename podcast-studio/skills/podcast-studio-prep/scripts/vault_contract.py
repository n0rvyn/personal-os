"""Resolve recall directories from the vault's own directory contract.

The authoritative contract lives in the user's vault at
`<vault_root>/99-System/10-Directory-Contract.md` (NOT in this plugin). It states
which directories podcast recall reads. Reading it here keeps podcast-studio
self-contained: it consults the vault's data, never another plugin.

`load_recall_dirs(vault_root)` parses the contract's `## Recall contract` section
and returns `{"self_past": (...), "cross_domain": (...)}`. If the file is missing,
the section is absent, or parsing yields nothing, it returns the PKOS default
layout. It NEVER raises — recall must not crash on a malformed vault doc.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# PKOS default layout — the fallback when the vault has no parseable contract.
# Kept identical to cross_domain's historical hardcoded constants so behavior is
# byte-unchanged when no contract file is present (e.g. in tests).
DEFAULT_SELF_PAST_DIRS: tuple[str, ...] = ("20-Ideas/观点心得/", "90-Productions/Podcasts/")
DEFAULT_CROSS_DOMAIN_DIRS: tuple[str, ...] = ("10-Knowledge/", "20-Ideas/")

CONTRACT_RELPATH = "99-System/10-Directory-Contract.md"

# A directory token in the contract: backtick-wrapped, starts `NN-`, contains a slash.
_DIR_RE = re.compile(r"`(\d{2}-[^`]*/[^`]*)`")
# Exclusion markers — dir tokens appearing after one of these on a bullet are dropped.
_EXCLUSION_MARKERS = (" NOT ", " never ", "—NOT", " NOT`", "不读", "排除", "—not")


def _recall_section(text: str) -> str:
    """Return the body of the '## Recall contract' section (up to the next '## ')."""
    out: list[str] = []
    in_section = False
    for line in text.splitlines():
        if line.strip().lower().startswith("## recall contract"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if in_section:
            out.append(line)
    return "\n".join(out)


def _bullets(section_text: str) -> list[str]:
    """Group section lines into bullets, joining wrapped continuation lines.

    A bullet starts with `- `; subsequent non-blank, non-bullet lines are folded
    into it (the live contract wraps the self_past bullet across two lines)."""
    bullets: list[str] = []
    cur: str | None = None
    for line in section_text.splitlines():
        if re.match(r"^\s*-\s", line):
            if cur is not None:
                bullets.append(cur)
            cur = line.strip()
        elif cur is not None and line.strip():
            cur += " " + line.strip()
    if cur is not None:
        bullets.append(cur)
    return bullets


def _extract_dirs(bullet: str) -> tuple[str, ...]:
    """Pull backtick dir paths from a bullet, dropping anything after an exclusion
    marker (so `... ← A + B. NOT C` yields (A, B), never C)."""
    cut = bullet
    for marker in _EXCLUSION_MARKERS:
        idx = cut.find(marker)
        if idx != -1:
            cut = cut[:idx]
    dirs: list[str] = []
    for m in _DIR_RE.finditer(cut):
        d = m.group(1).strip()
        if not d.endswith("/"):
            d += "/"
        if d not in dirs:
            dirs.append(d)
    return tuple(dirs)


def load_recall_dirs(vault_root: str | os.PathLike | None) -> dict[str, tuple[str, ...]]:
    """Resolve recall dirs from the vault contract, falling back to PKOS defaults.

    Returns {"self_past": (...), "cross_domain": (...)}. Never raises.
    """
    default = {
        "self_past": DEFAULT_SELF_PAST_DIRS,
        "cross_domain": DEFAULT_CROSS_DOMAIN_DIRS,
    }
    try:
        if not vault_root:
            return default
        contract = Path(os.path.expanduser(str(vault_root))) / CONTRACT_RELPATH
        if not contract.is_file():
            return default
        text = contract.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return default

    section = _recall_section(text)
    if not section.strip():
        return default

    self_past: tuple[str, ...] = ()
    cross_domain: tuple[str, ...] = ()
    for bullet in _bullets(section):
        low = bullet.lower()
        if "self_past" in low and not self_past:
            self_past = _extract_dirs(bullet)
        elif "cross_domain" in low and not cross_domain:
            cross_domain = _extract_dirs(bullet)

    if not self_past and not cross_domain:
        return default
    return {
        "self_past": self_past or DEFAULT_SELF_PAST_DIRS,
        "cross_domain": cross_domain or DEFAULT_CROSS_DOMAIN_DIRS,
    }
