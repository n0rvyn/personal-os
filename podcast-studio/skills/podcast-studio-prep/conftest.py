"""pytest conftest for the vendored podcast-prep skill.

Puts the skill's own `scripts/` package and the plugin's `lib/` on
`sys.path` via Path(__file__)-resolved paths (NOT cwd-relative). This
makes the prep test suite green regardless of the invocation cwd:

  - `cd podcast-studio && python3 -m pytest skills/podcast-studio-prep/scripts/`
  - `cd podcast-studio/skills/podcast-studio-prep && python3 -m pytest scripts/`
  - `cd <repo-root> && python3 -m pytest podcast-studio/skills/podcast-studio-prep/scripts/`

Why each path is needed:
- `skills/podcast-studio-prep` (= HERE's parent) on sys.path so `from scripts.orchestrator
  import main` resolves — `scripts/` is a subdir of `skills/podcast-studio-prep/`.
- `podcast-studio/` (= HERE's grandparent) on sys.path so
  `from lib.config import load_config` resolves — `lib/` is a subdir of
  the plugin root.
"""
from __future__ import annotations

import sys
from pathlib import Path

# HERE = .../podcast-studio/skills/podcast-studio-prep
HERE = Path(__file__).resolve().parent
# prep package directory (so `from scripts.orchestrator import ...` works)
PREP_DIR = HERE
# plugin root (so `from lib.config import ...` works)
PLUGIN_ROOT = HERE.parent.parent

# Prepend (idempotent) so vendored modules win over any name collision
# on the host.
for path in (str(PLUGIN_ROOT), str(PREP_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)
