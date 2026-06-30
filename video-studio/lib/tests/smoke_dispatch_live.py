"""Live smoke test for lib/dispatch.py — requires a real `claude -p` invocation.

Usage:
    RUN_LIVE=1 python lib/tests/smoke_dispatch_live.py

Guards:
  - Only runs when RUN_LIVE env var is set (skips otherwise).
  - Uses a temporary scratch + a temporary agent_dir with a stub persona.
  - Whitelist is overridden to {"_smoke"} so the stub agent passes.
  - Model is pinned to "sonnet" per helper-subagents-use-sonnet preference.

Acceptance (Phase 1 dev-guide, line 55):
  - dispatch_persona("_smoke", ...) → ok=True, out.txt exists non-empty.
  - Simulated missing-artifact path → ok=False (tested by injecting a fake
    runner that exits 0 but writes nothing).

Prints "dispatch live ok" and exits 0 on success.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Ensure the plugin root (parent of lib/) is on sys.path.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


def main() -> None:
    if not os.environ.get("RUN_LIVE"):
        print("skip (set RUN_LIVE=1 to run the live smoke test)")
        sys.exit(0)

    from lib.dispatch import dispatch_persona

    with tempfile.TemporaryDirectory(prefix="vs-smoke-") as tmp_str:
        tmp = Path(tmp_str)

        # Build a temporary agents dir with a stub persona.
        agent_dir_path = tmp / "agents"
        agent_dir_path.mkdir(parents=True, exist_ok=True)
        stub_md = agent_dir_path / "_smoke.md"
        stub_md.write_text(
            "You are a minimal smoke-test persona. "
            "When asked to write 'ok' to a file, do so immediately using the "
            "Write tool and nothing else.",
            encoding="utf-8",
        )

        scratch = tmp / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)

        print("Dispatching live claude -p smoke persona …")
        result = dispatch_persona(
            agent_name="_smoke",
            user_prompt=(
                "Write the single word 'ok' (no quotes, no newline) to the "
                "artifact file specified below. Use the Write tool."
            ),
            scratch_dir=scratch,
            expected_artifact="out.txt",
            plugin_root=tmp,
            agent_dir="agents",
            whitelist=frozenset({"_smoke"}),
            model="sonnet",
            timeout=120,
        )

        if not result.get("ok"):
            print(f"FAIL — live dispatch returned ok=False: {result}", file=sys.stderr)
            sys.exit(1)

        out_file = Path(result["artifact_path"])
        if not out_file.exists() or out_file.stat().st_size == 0:
            print(
                f"FAIL — artifact missing or empty at {out_file}", file=sys.stderr
            )
            sys.exit(1)

        print(f"  artifact contents: {out_file.read_text(encoding='utf-8')!r}")

        # ---------------------------------------------------------- ok=False path
        # Verify that a missing artifact returns ok=False (fake runner, no real claude).
        import subprocess as _sp

        class _FakeNoWrite:
            def __call__(self, argv, **kwargs):
                class _Proc:
                    returncode = 0
                    stdout = ""
                    stderr = ""
                return _Proc()

        scratch2 = tmp / "scratch2"
        scratch2.mkdir(parents=True, exist_ok=True)

        result2 = dispatch_persona(
            agent_name="_smoke",
            user_prompt="hello",
            scratch_dir=scratch2,
            expected_artifact="out.txt",
            plugin_root=tmp,
            agent_dir="agents",
            whitelist=frozenset({"_smoke"}),
            runner=_FakeNoWrite(),
        )

        if result2.get("ok") is not False:
            print(
                f"FAIL — missing-artifact path must return ok=False, got {result2}",
                file=sys.stderr,
            )
            sys.exit(1)

        print("dispatch live ok")


if __name__ == "__main__":
    main()
