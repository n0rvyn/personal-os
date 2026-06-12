"""Shared Personal-OS config loader. Duplicated across plugins — no cross-plugin import.

This copy (pkos/scripts/personal_os_config.py) is the Phase 2 canonical source.
Phase 4 must propagate _resolve_config_path() to the 5 sibling copies:
  domain-intel/scripts/personal_os_config.py
  youtube-scout/scripts/personal_os_config.py
  health-insights/scripts/personal_os_config.py
  portfolio-lens/scripts/personal_os_config.py
  session-reflect/scripts/personal_os_config.py
and reconcile the 2 currently-drifted copies (md5 mismatch) to match this one.

Returned paths are always expanded (~ resolved, absolute). Callers may use
Path() directly on returned values without extra expanduser().

CLI: `python3 personal_os_config.py --get <key>` prints a single resolved value,
     used by shell wrappers.
     Supports flat keys (exchange_dir, scratch_dir) and dotted namespace keys
     (pkos.notion_databases.inbox, etc.).
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

def _get_dotted(cfg: dict, key: str) -> str:
    """Resolve a dotted namespace key (e.g. 'pkos.notion_databases.inbox').
    Returns empty string if any segment in the path is missing."""
    segments = key.split(".")
    value = cfg
    for seg in segments:
        if isinstance(value, dict) and seg in value:
            value = value[seg]
        else:
            return ""
    return str(value) if value is not None else ""


def load_config() -> dict:
    """Load ~/.claude/personal-os.yaml. Prompt on first run if interactive, else defaults.

    Returns a dict with expanded absolute paths for flat keys; namespace sub-dicts
    are returned as-is (not expanded) to preserve their structure.
    """
    config_path = _resolve_config_path()
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text()) or {}
        # Merge defaults at the top level only; namespace keys come from the file
        merged = {**DEFAULTS, **data}
        # Expand only flat (non-namespaced) top-level keys
        expanded = {}
        for k, v in merged.items():
            if k in DEFAULTS and isinstance(v, str):
                expanded[k] = str(Path(os.path.expanduser(v)).resolve())
            else:
                expanded[k] = v
        return expanded
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
    resolved = {k: str(Path(os.path.expanduser(v)).resolve()) for k, v in cfg.items()}
    for d in resolved.values():
        Path(d).mkdir(parents=True, exist_ok=True)
    return resolved

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--get", required=True,
                   help="Key to retrieve. Supports flat keys (exchange_dir, scratch_dir) "
                        "and dotted namespace keys (pkos.notion_databases.inbox, etc.).")
    args = p.parse_args()
    cfg = load_config()
    key = args.get
    if "." in key:
        result = _get_dotted(cfg, key)
    else:
        result = cfg.get(key, "")
    print(result)
