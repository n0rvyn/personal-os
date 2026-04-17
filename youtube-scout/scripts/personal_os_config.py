"""Shared Personal-OS config loader. Duplicated across plugins — no cross-plugin import.

Returned paths are always expanded (~ resolved, absolute). Callers may use
Path() directly on returned values without extra expanduser().

CLI: `python3 personal_os_config.py --get <key>` prints a single resolved path,
     used by shell wrappers.
"""
import os, sys, yaml
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude" / "personal-os.yaml"
DEFAULTS = {
    "exchange_dir": "~/Obsidian/PKOS/.exchange",
    "scratch_dir":  "~/.personal-os/scratch",
}

def _expand(cfg: dict) -> dict:
    return {k: str(Path(os.path.expanduser(v)).resolve()) if isinstance(v, str) else v
            for k, v in cfg.items()}

def load_config() -> dict:
    """Load ~/.claude/personal-os.yaml. Prompt on first run if interactive, else defaults.

    Returns a dict with expanded absolute paths.
    """
    if CONFIG_PATH.exists():
        data = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        merged = {**DEFAULTS, **data}
        return _expand(merged)
    # first-run init
    if sys.stdin.isatty() and sys.stdout.isatty():
        print("Personal-OS first-run setup. Press Enter to accept defaults.", file=sys.stderr)
        ex = input(f"IEF exchange dir [{DEFAULTS['exchange_dir']}]: ").strip() or DEFAULTS["exchange_dir"]
        sc = input(f"Scratch dir [{DEFAULTS['scratch_dir']}]: ").strip() or DEFAULTS["scratch_dir"]
        cfg = {"exchange_dir": ex, "scratch_dir": sc}
    else:
        cfg = dict(DEFAULTS)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))
    resolved = _expand(cfg)
    for d in resolved.values():
        Path(d).mkdir(parents=True, exist_ok=True)
    return resolved

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--get", required=True, choices=["exchange_dir", "scratch_dir"])
    args = p.parse_args()
    print(load_config()[args.get])
