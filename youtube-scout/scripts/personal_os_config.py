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

# Sentinel keys — a cwd-walk candidate `personal-os.yaml` must parse to a dict
# containing at least one of these to be accepted. Prevents an unrelated
# same-named file on the cwd chain from hijacking resolution.
_SENTINEL_KEYS = ("exchange_dir", "scratch_dir", "vault", "tts")


def _resolve_config_path() -> Path:
    """Resolve which personal-os.yaml to load.

    Order (DP-003=C):
      1. PERSONAL_OS_ROOT env (trusted — no sentinel check)
      2. bounded cwd-walk for `personal-os.yaml` with sentinel check
      3. home fallback: ~/.claude/personal-os.yaml

    Sentinel parse failure (read error, YAML error, non-dict root, missing
    all sentinel keys) is fail-soft: skip that candidate, continue walking.
    Env-specified path is never sentinel-checked (explicit override = trust).
    """
    env_root = os.environ.get("PERSONAL_OS_ROOT")
    if env_root:
        return Path(env_root).expanduser() / "personal-os.yaml"
    for d in [Path.cwd(), *Path.cwd().parents]:
        candidate = d / "personal-os.yaml"
        if not candidate.is_file():
            continue
        try:
            data = yaml.safe_load(candidate.read_text())
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if not any(k in data for k in _SENTINEL_KEYS):
            continue
        return candidate
    return Path.home() / ".claude" / "personal-os.yaml"


def _expand(cfg: dict) -> dict:
    return {k: str(Path(os.path.expanduser(v)).resolve()) if isinstance(v, str) else v
            for k, v in cfg.items()}

def load_config() -> dict:
    """Load ~/.claude/personal-os.yaml. Prompt on first run if interactive, else defaults.

    Returns a dict with expanded absolute paths.
    """
    config_path = _resolve_config_path()
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text()) or {}
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
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump(cfg, sort_keys=False))
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
