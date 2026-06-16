"""podcast-studio config resolver.

Single source of truth for `~/.podcast-studio/config.yaml`. Used by:
- prep scripts (Python) — `from lib.config import load_config`
- tts env shim (bash) — `lib/podcast-env.sh` shells out to this module

Fails-closed: missing file / missing required key / nonexistent vault dir
all raise ConfigError naming the offending key.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ConfigError(Exception):
    """Raised when the config is missing, malformed, or fails validation."""


REQUIRED_VAULT_KEYS = ("subjective_dir", "news_dir", "output_dir")
REQUIRED_TTS_KEYS = ("provider", "host_voice")

# Sentinel keys for project-anchor (`personal-os.yaml`) candidate adoption.
# A candidate must contain `vault` or `tts` (podcast's data slice) to be
# accepted — a fleet-only file with just `exchange_dir`/`scratch_dir` does
# not hijack a standalone podcast user's resolve.
_SENTINEL_KEYS = ("vault", "tts")


@dataclass(frozen=True)
class VaultConfig:
    subjective_dir: str
    news_dir: str
    output_dir: str
    # Phase 4: derived subdirs of output_dir (NOT YAML keys). episodes/ holds
    # listener artifacts (.md/.mp3/.stance.yaml), state/ holds continuity
    # (covered-ground.yaml/character-bible.md/throughline), reports/ holds
    # scorecards. Computed + auto-created in _validate_vault_paths AFTER the
    # output_dir fail-closed existence check. Non-default — must precede `root`.
    episodes_dir: str
    state_dir: str
    reports_dir: str
    # Optional Obsidian/PKOS vault root — the base for recall + the directory
    # contract (`<root>/99-System/10-Directory-Contract.md`). Distinct from
    # subjective_dir. When unset, recall falls back to subjective_dir.
    root: str | None = None


@dataclass(frozen=True)
class TtsConfig:
    provider: str
    host_voice: str


@dataclass(frozen=True)
class PodcastTeamConfig:
    vault: VaultConfig
    tts: TtsConfig
    # Phase 3: project-anchor `personal-os.yaml` top-level `exchange_dir`,
    # exposed for the IEF producer/consumer path (DP-002=A). Fail-soft
    # when absent: legacy `~/.podcast-studio/config.yaml` has no key →
    # `None`. Existence/read of this directory is Phase 5's concern.
    exchange_dir: str | None = None


def _default_config_path() -> Path:
    return Path(os.path.expanduser("~/.podcast-studio/config.yaml"))


def _resolve_personal_os_yaml() -> Path | None:
    """Locate a project-root `personal-os.yaml` for the podcast config.

    Resolution order:
    1. `PERSONAL_OS_ROOT` env var — if set, trust it and return
       `<env>/personal-os.yaml` without sentinel validation.
    2. cwd-walk: starting at `Path.cwd()`, climb `Path.cwd().parents` and
       return the first `personal-os.yaml` that is a file, parses as a
       mapping, and contains `vault` or `tts` (podcast's data slice).

    All candidate-parse failures (missing file, unimportable yaml, bad
    YAML, non-dict, missing sentinel) are fail-soft — the candidate is
    skipped and the walk continues. If nothing matches, return `None`
    and let the caller fall back to the podcast-private home default.
    """
    env = os.environ.get("PERSONAL_OS_ROOT")
    if env:
        return Path(env).expanduser() / "personal-os.yaml"

    for d in [Path.cwd(), *Path.cwd().parents]:
        candidate = d / "personal-os.yaml"
        try:
            if not candidate.is_file():
                continue
            import yaml  # type: ignore[import-untyped]

            loaded = yaml.safe_load(candidate.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                continue
            if not any(k in loaded for k in _SENTINEL_KEYS):
                continue
            return candidate
        except Exception:
            # Any failure (ImportError, YAMLError, OSError, type/key
            # mismatches) → fail-soft, keep walking up.
            continue

    return None


def _resolve_config_path(path: str | os.PathLike | None) -> Path:
    if path is not None:
        return Path(path).expanduser()
    env = os.environ.get("PODCAST_STUDIO_CONFIG")
    if env:
        return Path(env).expanduser()
    anchored = _resolve_personal_os_yaml()
    if anchored is not None:
        return anchored
    return _default_config_path()


def _read_yaml(path: Path) -> dict[str, Any]:
    """Parse a fixed 2-level YAML schema.

    Prefers PyYAML when importable (zero-cost when present, full-fidelity
    nested parse). Falls back to a minimal nesting-aware reader for the
    specific shape `vault:` / `tts:` with 2-space-indented `key: value`
    string children. The minimal reader is intentionally narrow — it
    does NOT try to be a general YAML parser.
    """
    text = path.read_text(encoding="utf-8")

    try:
        import yaml  # type: ignore[import-untyped]

        loaded = yaml.safe_load(text)
        if not isinstance(loaded, dict):
            raise ConfigError(f"config {path} root must be a mapping")
        return loaded
    except ImportError:
        return _minimal_nested_yaml(text)


def _minimal_nested_yaml(text: str) -> dict[str, Any]:
    """Minimal nested-section reader for the podcast-studio config shape.

    Parses lines of the form:
        top_key:
          child_key: value
          child_key: value
        other_top:
          child: value

    Values are treated as strings. Tilde-prefixed values are NOT expanded
    here — that's the caller's job (we want to validate raw values, then
    expand before resolving on disk).
    """
    out: dict[str, dict[str, str]] = {}
    current_section: str | None = None
    line_re = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")

    for raw_line in text.splitlines():
        # Strip comments and trailing whitespace; skip blank lines.
        line = raw_line.split("#", 1)[0].rstrip()
        if not line.strip():
            continue

        m = line_re.match(line)
        if not m:
            raise ConfigError(f"unparseable config line: {raw_line!r}")

        indent, key, value = m.group(1), m.group(2), m.group(3).strip()

        if indent == "":
            # Top-level section header.
            if value:
                raise ConfigError(
                    f"top-level key {key!r} must be a section (no inline value)"
                )
            current_section = key
            out.setdefault(current_section, {})
        else:
            if current_section is None:
                raise ConfigError(
                    f"nested key {key!r} appears before any top-level section"
                )
            if not value:
                raise ConfigError(
                    f"section {current_section!r} key {key!r} has empty value"
                )
            out[current_section][key] = value

    if not out:
        raise ConfigError("config is empty")

    return out


def _validate_vault_paths(vault_raw: dict[str, str]) -> VaultConfig:
    for key in REQUIRED_VAULT_KEYS:
        if key not in vault_raw:
            raise ConfigError(f"missing required key: vault.{key}")
        val = vault_raw[key]
        if not isinstance(val, str) or not val.strip():
            raise ConfigError(f"vault.{key} must be a non-empty string")

    resolved: dict[str, str] = {}
    for key in REQUIRED_VAULT_KEYS:
        path = Path(os.path.expanduser(vault_raw[key]))
        if not path.exists():
            raise ConfigError(
                f"vault.{key} does not exist: {path} "
                f"(set a real path in ~/.podcast-studio/config.yaml)"
            )
        if not path.is_dir():
            raise ConfigError(f"vault.{key} is not a directory: {path}")
        resolved[key] = str(path)

    # Phase 4: derive episodes/state/reports subdirs from the now-validated
    # output_dir and auto-create them. This MUST run AFTER the existence loop
    # above — mkdir(parents=True) on output_dir/<sub> would otherwise create a
    # missing output_dir and defeat the fail-closed contract.
    out_path = Path(resolved["output_dir"])
    for sub in ("episodes", "state", "reports"):
        d = out_path / sub
        d.mkdir(parents=True, exist_ok=True)
        resolved[f"{sub}_dir"] = str(d)

    # Optional vault.root — type-validated when present, but NOT existence-checked:
    # recall degrades gracefully (empty) on a missing root, so this stays lenient.
    root_raw = vault_raw.get("root")
    root_resolved: str | None = None
    if root_raw is not None:
        if not isinstance(root_raw, str) or not root_raw.strip():
            raise ConfigError("vault.root must be a non-empty string when set")
        root_resolved = str(Path(os.path.expanduser(root_raw)))

    return VaultConfig(root=root_resolved, **resolved)


def _validate_tts(tts_raw: dict[str, Any]) -> TtsConfig:
    for key in REQUIRED_TTS_KEYS:
        if key not in tts_raw:
            raise ConfigError(f"missing required key: tts.{key}")
        val = tts_raw[key]
        if not isinstance(val, str) or not val.strip():
            raise ConfigError(f"tts.{key} must be a non-empty string")

    return TtsConfig(provider=tts_raw["provider"], host_voice=tts_raw["host_voice"])


def load_config(path: str | os.PathLike | None = None) -> PodcastTeamConfig:
    """Resolve and validate the podcast-studio config.

    Lookup order for the config path:
    1. explicit `path` arg
    2. PODCAST_STUDIO_CONFIG env var
    3. PERSONAL_OS_ROOT/personal-os.yaml or cwd-walk personal-os.yaml (sentinel: vault|tts)
    4. ~/.podcast-studio/config.yaml (standalone fallback)
    """
    cfg_path = _resolve_config_path(path)

    if not cfg_path.exists():
        raise ConfigError(f"config file not found: {cfg_path}")

    raw = _read_yaml(cfg_path)

    if "vault" not in raw or not isinstance(raw["vault"], dict):
        raise ConfigError("missing required section: vault")
    if "tts" not in raw or not isinstance(raw["tts"], dict):
        raise ConfigError("missing required section: tts")

    vault = _validate_vault_paths(raw["vault"])
    tts = _validate_tts(raw["tts"])

    # exchange_dir: fail-soft. A non-empty str is expanded + resolved to
    # an absolute path; anything else (missing, non-str, empty, blank)
    # becomes `None`. No existence check — Phase 5 owns that.
    exchange_raw = raw.get("exchange_dir")
    exchange_dir: str | None = None
    if isinstance(exchange_raw, str) and exchange_raw.strip():
        exchange_dir = str(Path(os.path.expanduser(exchange_raw)).resolve())

    return PodcastTeamConfig(vault=vault, tts=tts, exchange_dir=exchange_dir)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="lib.config")
    parser.add_argument(
        "--validate",
        metavar="PATH",
        help="Validate the config at PATH; exit 0 if valid, 1 (with the offending "
        "key on stderr) otherwise. Used by the config-studio editor's save gate.",
    )
    args = parser.parse_args()

    if args.validate is not None:
        # Validation mode: resolve + validate the given file, report via exit code.
        try:
            load_config(args.validate)
        except ConfigError as e:
            print(f"config error: {e}", file=sys.stderr)
            sys.exit(1)
        print("ok")
        sys.exit(0)

    # CLI smoke: `python3 -m lib.config` prints a resolved config.
    try:
        c = load_config()
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        sys.exit(1)
    print(f"vault.subjective_dir = {c.vault.subjective_dir}")
    print(f"vault.news_dir       = {c.vault.news_dir}")
    print(f"vault.output_dir     = {c.vault.output_dir}")
    print(f"tts.provider         = {c.tts.provider}")
    print(f"tts.host_voice       = {c.tts.host_voice}")
    if c.exchange_dir is not None:
        print(f"exchange_dir         = {c.exchange_dir}")
