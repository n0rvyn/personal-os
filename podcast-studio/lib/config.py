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
    # Optional bible voice-corpus override. When set, the bible-distill step
    # reads its corpus from here instead of subjective_dir (the host's dev-log
    # as a VOICE reference, not a CONTENT/topic source). When unset, the bible
    # falls back to subjective_dir. Existence-checked (fail-closed) when present.
    voice_corpus_dir: str | None = None
    # Phase 4 (paper line): derived subdirs of `output_dir/papers/`. Created
    # ONLY when the `papers.*` section is present (DP-402=A). Same shape as
    # the opinion episodes_dir/state_dir/reports_dir trio. `None` when the
    # papers section is absent — opinion-only configs stay zero-change.
    papers_episodes_dir: str | None = None
    papers_state_dir: str | None = None
    papers_reports_dir: str | None = None


@dataclass(frozen=True)
class TtsConfig:
    provider: str
    host_voice: str
    # Which TTS vendors to try, in order. OPTIONAL — absent means the `tts`
    # skill's own built-in default (minimax → volc-2.0 → volc-1.0 → local).
    # Set it to reorder or to drop a vendor: `["local", "minimax"]` stops
    # spending money; `["minimax", "volc-2.0", "volc-1.0"]` (no `local`) makes
    # an exhausted pool fail rather than ship the offline voice.
    # `lib/podcast-env.sh` exports it as `TTS_VENDOR_PRIORITY`.
    vendor_priority: tuple[str, ...] | None = None
    # Which offline speaker the `local` vendor uses (vits-zh-ll-sid0 … sid4).
    # OPTIONAL — absent means sid0. Exported as `TTS_LOCAL_VOICE`.
    local_voice: str | None = None


@dataclass(frozen=True)
class PapersConfig:
    """Phase 2: optional `papers.*` config section for the paper-digest line.

    `categories` is a tuple of arXiv category strings (e.g. ("cs.CL", "cs.LG"))
    used by `lib.paperline.discovery.fetch_candidates`. `max_candidates` is the
    upper bound for the discovery fetch (positive int, defaults to 60 when the
    YAML omits it).
    """
    categories: tuple[str, ...]
    max_candidates: int


@dataclass(frozen=True)
class PodcastIdentity:
    name: str | None = None
    english_name: str | None = None


@dataclass(frozen=True)
class ProductionConfig:
    provider: str = "claude"
    language: str = "zh"
    options: dict[str, Any] | None = None


@dataclass(frozen=True)
class PodcastTeamConfig:
    vault: VaultConfig
    tts: TtsConfig
    # Phase 3: project-anchor `personal-os.yaml` top-level `exchange_dir`,
    # exposed for the IEF producer/consumer path (DP-002=A). Fail-soft
    # when absent: legacy `~/.podcast-studio/config.yaml` has no key →
    # `None`. Existence/read of this directory is Phase 5's concern.
    exchange_dir: str | None = None
    # Phase 2 (paper line): optional `papers.*` section. OPTIONAL at resolve
    # time — opinion configs without it stay zero-change. The paper line's
    # config station requires it fail-closed via `require_papers(cfg)` at the
    # use site, never here.
    papers: PapersConfig | None = None
    podcast: PodcastIdentity | None = None
    production: ProductionConfig | None = None


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
    # Existing podcast projects already use config.yaml. Recognize only files
    # carrying both podcast data sections, so arbitrary app configs are ignored.
    for directory in [Path.cwd(), *Path.cwd().parents]:
        candidate = directory / "config.yaml"
        try:
            if candidate.is_file():
                raw = _read_yaml(candidate)
                if all(isinstance(raw.get(k), dict) for k in ("vault", "tts")):
                    return candidate
        except (ConfigError, OSError):
            continue
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


def _validate_vault_paths(
    vault_raw: dict[str, str], has_papers: bool = False
) -> VaultConfig:
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

    # Phase 4 (paper line): when `papers.*` is configured, derive and create
    # the papers output subdirs (DP-402=A). Gated on has_papers — opinion-only
    # configs (no papers.*) stay zero-change (no `papers/` subdir created).
    # Same mkdir-after-existence discipline: output_dir is already validated
    # above, so this can safely mkdir under it.
    papers_resolved: dict[str, str | None] = {
        "papers_episodes_dir": None,
        "papers_state_dir": None,
        "papers_reports_dir": None,
    }
    if has_papers:
        papers_root = out_path / "papers"
        for sub in ("episodes", "state", "reports"):
            d = papers_root / sub
            d.mkdir(parents=True, exist_ok=True)
            papers_resolved[f"papers_{sub}_dir"] = str(d)

    # Optional vault.root — type-validated when present, but NOT existence-checked:
    # recall degrades gracefully (empty) on a missing root, so this stays lenient.
    root_raw = vault_raw.get("root")
    root_resolved: str | None = None
    if root_raw is not None:
        if not isinstance(root_raw, str) or not root_raw.strip():
            raise ConfigError("vault.root must be a non-empty string when set")
        root_resolved = str(Path(os.path.expanduser(root_raw)))

    # Optional vault.voice_corpus_dir — fail-closed existence check when present
    # (unlike root, which is lenient): a typo'd voice corpus must HALT, not
    # silently degrade the bible to subjective_dir. This also forces the
    # seed-first order (run tools/sync-voice-corpus.py before adding the key).
    voice_raw = vault_raw.get("voice_corpus_dir")
    voice_resolved: str | None = None
    if voice_raw is not None:
        if not isinstance(voice_raw, str) or not voice_raw.strip():
            raise ConfigError("vault.voice_corpus_dir must be a non-empty string when set")
        vpath = Path(os.path.expanduser(voice_raw))
        if not vpath.exists():
            raise ConfigError(
                f"vault.voice_corpus_dir does not exist: {vpath} "
                f"(run tools/sync_voice_corpus.py to seed it)"
            )
        if not vpath.is_dir():
            raise ConfigError(f"vault.voice_corpus_dir is not a directory: {vpath}")
        voice_resolved = str(vpath)

    return VaultConfig(
        root=root_resolved,
        voice_corpus_dir=voice_resolved,
        **papers_resolved,
        **resolved,
    )


def _validate_tts(tts_raw: dict[str, Any]) -> TtsConfig:
    for key in REQUIRED_TTS_KEYS:
        if key not in tts_raw:
            raise ConfigError(f"missing required key: tts.{key}")
        val = tts_raw[key]
        if not isinstance(val, str) or not val.strip():
            raise ConfigError(f"tts.{key} must be a non-empty string")

    return TtsConfig(
        provider=tts_raw["provider"],
        host_voice=tts_raw["host_voice"],
        vendor_priority=_validate_vendor_priority(tts_raw.get("vendor_priority")),
        local_voice=_validate_local_voice(tts_raw.get("local_voice")),
    )


def _validate_vendor_priority(raw: Any) -> tuple[str, ...] | None:
    """Parse the optional `tts.vendor_priority` ordering.

    Accepts a YAML list (`["minimax", "local"]`) or a comma string
    (`"minimax,local"`) — both are natural to write, and the value ends up as a
    comma string in `TTS_VENDOR_PRIORITY` either way.

    OPTIONAL: absent → `None`, and the `tts` skill uses its own default order.
    ⛔ Present-but-malformed raises rather than silently falling back — a
    misspelled ordering that quietly reverted would be a setting that looks
    applied and is not. (The skill applies the same rule to unknown vendor
    NAMES; this function only checks shape, since the vendor roster lives in
    the skill, not here.)
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        # ⚠️ BOTH parsers must produce the same tuple for the same file.
        #    PyYAML turns `[local, minimax]` into a real list; the minimal
        #    fallback reader (used whenever PyYAML is absent — which is the
        #    live path on a machine that never `pip install -r`'d) hands the
        #    WHOLE flow sequence over as the string "[local, minimax]".
        #    Splitting that on commas yields ('[local', 'minimax]') — two
        #    names no vendor roster contains, and nothing raises. 2026-09-22:
        #    this shipped and was caught only by sourcing the env shim.
        #    So strip the brackets here rather than letting the result depend
        #    on whether a dependency happens to be installed.
        s = raw.strip()
        if s.startswith("[") and s.endswith("]"):
            s = s[1:-1]
        items = [p.strip() for p in s.split(",")]
    elif isinstance(raw, (list, tuple)):
        items = [str(p).strip() if not isinstance(p, str) else p.strip() for p in raw]
    else:
        raise ConfigError("tts.vendor_priority must be a list or a comma-separated string")

    # Per-item quote strip: the minimal parser keeps `['local', 'minimax']`
    # quoting verbatim, PyYAML does not.
    items = [p[1:-1].strip() if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p
             for p in items]

    if not items:
        raise ConfigError("tts.vendor_priority must name at least one vendor")
    if any(not p for p in items):
        raise ConfigError("tts.vendor_priority contains an empty entry")
    return tuple(items)


def _validate_local_voice(raw: Any) -> str | None:
    """Parse the optional `tts.local_voice` (offline speaker id)."""
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError("tts.local_voice must be a non-empty string")
    return raw.strip()


def _validate_papers(papers_raw: Any) -> PapersConfig:
    """Parse + type-validate the optional `papers.*` config section.

    Mirrors the `vault.voice_corpus_dir` optional pattern: when the key is
    absent the caller resolves to `None` (opinion configs unaffected). When
    present, the schema is `categories: list[str]` (non-empty) + optional
    `max_candidates: positive int` (default 60). Bad shapes raise ConfigError
    naming the offending field — never silent defaults.
    """
    if not isinstance(papers_raw, dict):
        raise ConfigError("papers must be a section (mapping)")

    cats_raw = papers_raw.get("categories")
    if not isinstance(cats_raw, list) or not cats_raw:
        raise ConfigError(
            "papers.categories must be a non-empty list of category strings"
        )
    for i, c in enumerate(cats_raw):
        if not isinstance(c, str) or not c.strip():
            raise ConfigError(
                f"papers.categories[{i}] must be a non-empty string"
            )
    categories = tuple(cats_raw)

    max_raw = papers_raw.get("max_candidates", 60)
    if not isinstance(max_raw, int) or isinstance(max_raw, bool):
        raise ConfigError("papers.max_candidates must be a positive int")
    if max_raw <= 0:
        raise ConfigError("papers.max_candidates must be a positive int")

    return PapersConfig(categories=categories, max_candidates=max_raw)


def require_papers(cfg: PodcastTeamConfig) -> PapersConfig:
    """Fail-closed accessor for the paper-line use site.

    Returns `cfg.papers` or raises ConfigError naming the missing key. This
    is the ONLY place the paper line consults its config: by keeping the
    requirement here (not in `resolve()`), existing opinion configs stay
    zero-change — no `papers.*` key, no raise.
    """
    if cfg.papers is None:
        raise ConfigError("missing required key: papers.categories")
    return cfg.papers


def _validate_podcast(raw: Any, *, required=False) -> PodcastIdentity | None:
    if raw is None and not required:
        return None  # Old projects retain their exact behavior and history.
    if not isinstance(raw, dict):
        raise ConfigError("podcast.name is required when creating/naming a project")
    for key in ("name", "english_name"):
        value = raw.get(key)
        if key == "name" or value is not None:
            if not isinstance(value, str) or not value.strip() or "\n" in value or len(value) > 200:
                raise ConfigError(f"podcast.{key} must be a nonempty single-line name")
    return PodcastIdentity(name=raw["name"].strip(), english_name=(raw.get("english_name") or "").strip() or None)


def _validate_production(raw: Any) -> ProductionConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("production must be a mapping")
    provider, language = raw.get("provider", "claude"), raw.get("language", "zh")
    if (provider, language) not in {("claude", "zh"), ("openai", "en")}:
        raise ConfigError("production currently supports claude/zh or openai/en in the same opinion pipeline")
    options = {k:v for k,v in raw.items() if k not in {"provider", "language"}}
    auth = options.get("source_authorized", False)
    if isinstance(auth, str) and auth.lower() in {"true", "false"}:
        auth = auth.lower() == "true"
    if type(auth) is not bool:
        raise ConfigError("production.source_authorized must be boolean")
    options["source_authorized"] = auth
    for key in ("per_call_limit_usd", "daily_limit_usd"):
        if key in options and isinstance(options[key], str):
            try: options[key] = float(options[key])
            except ValueError: raise ConfigError(f"production.{key} must be numeric") from None
    for key in ("max_output_tokens", "speech_output_tokens", "min_words", "target_words", "max_words",
                "min_minutes", "target_minutes", "max_minutes", "speaking_rate_wpm",
                "max_sentence_words", "max_average_sentence_words"):
        if key in options and isinstance(options[key], str):
            try: options[key] = int(options[key])
            except ValueError: raise ConfigError(f"production.{key} must be an integer") from None
    if provider == "openai":
        from lib.english import length_settings, listening_settings
        try:
            length_settings(options)
            listening_settings(options)
        except ValueError as e:
            raise ConfigError(str(e)) from None
        if options.get("source_mode", "legacy") not in {"legacy", "public"}:
            raise ConfigError("production.source_mode must be legacy or public")
        if options.get("source_mode") == "public" and not isinstance(options.get("source_manifest"), str):
            raise ConfigError("Public production requires source_manifest")
        if options.get("source_mode") == "public":
            import re
            if not isinstance(options.get("source_manifest_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", options["source_manifest_sha256"]):
                raise ConfigError("Public production requires source_manifest_sha256")
    return ProductionConfig(provider=provider, language=language, options=options)


def initialize_config(path, *, name, template_path):
    """Existing config handler's project-creation phase: require/store the name.

    Copies an explicitly selected config template; creates no directories and
    refuses to overwrite an existing config. Does not run a provider or migrate data.
    """
    identity = _validate_podcast({"name": name}, required=True)
    raw = _read_yaml(Path(template_path))
    raw["podcast"] = {"name": identity.name}
    if not isinstance(raw.get("vault"), dict) or not isinstance(raw.get("tts"), dict):
        raise ConfigError("Initialization template requires vault and tts sections")
    _validate_tts(raw["tts"])
    _validate_production(raw.get("production"))
    for key in REQUIRED_VAULT_KEYS:
        if not isinstance(raw["vault"].get(key), str) or not raw["vault"][key].strip():
            raise ConfigError(f"initialization template missing vault.{key}")
    try:
        import yaml
    except ImportError:
        raise ConfigError("PyYAML is required for project initialization; install requirements.txt") from None
    with Path(path).open("x", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)


def load_config(path: str | os.PathLike | None = None) -> PodcastTeamConfig:
    """Resolve and validate the podcast-studio config.

    Lookup order for the config path:
    1. explicit `path` arg
    2. PODCAST_STUDIO_CONFIG env var
    3. PERSONAL_OS_ROOT/personal-os.yaml or cwd-walk personal-os.yaml (sentinel: vault|tts)
    4. cwd-walk config.yaml containing both vault and tts (existing project)
    5. ~/.podcast-studio/config.yaml (standalone fallback)
    """
    cfg_path = _resolve_config_path(path)

    if not cfg_path.exists():
        raise ConfigError(
            f"config file not found: {cfg_path}. "
            f"解析顺序: 显式 path 参数 → $PODCAST_STUDIO_CONFIG → "
            f"cwd 向上找带 vault/tts 段的 personal-os.yaml / config.yaml → "
            f"~/.podcast-studio/config.yaml。"
            f"把 $PODCAST_STUDIO_CONFIG 指向你的 config,或在默认路径建文件。"
        )

    raw = _read_yaml(cfg_path)

    if "vault" not in raw or not isinstance(raw["vault"], dict):
        raise ConfigError("missing required section: vault")
    if "tts" not in raw or not isinstance(raw["tts"], dict):
        raise ConfigError("missing required section: tts")

    tts = _validate_tts(raw["tts"])
    identity = _validate_podcast(raw.get("podcast"))
    production = _validate_production(raw.get("production"))
    if production is not None and production.provider == "openai" and identity is None:
        raise ConfigError("podcast.name is required; add the existing show's name to this config")

    # exchange_dir: fail-soft. A non-empty str is expanded + resolved to
    # an absolute path; anything else (missing, non-str, empty, blank)
    # becomes `None`. No existence check — Phase 5 owns that.
    exchange_raw = raw.get("exchange_dir")
    exchange_dir: str | None = None
    if isinstance(exchange_raw, str) and exchange_raw.strip():
        exchange_dir = str(Path(os.path.expanduser(exchange_raw)).resolve())

    # Phase 2 (paper line): optional `papers.*` section. When absent the
    # field stays `None` so opinion configs resolve zero-change. The paper
    # line's use site calls `require_papers(cfg)` for its fail-closed check.
    # `has_papers` feeds the Phase 4 paper output subdir derivation
    # (gated on `papers.*` presence — opinion-only configs stay zero-change).
    papers_raw = raw.get("papers")
    has_papers = papers_raw is not None
    papers: PapersConfig | None = None
    if has_papers:
        papers = _validate_papers(papers_raw)

    vault = _validate_vault_paths(raw["vault"], has_papers=has_papers)

    return PodcastTeamConfig(
        vault=vault, tts=tts, exchange_dir=exchange_dir, papers=papers,
        podcast=identity, production=production
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(prog="lib.config")
    parser.add_argument(
        "--validate",
        metavar="PATH",
        help="Validate the config at PATH; exit 0 if valid, 1 (with the offending "
        "key on stderr) otherwise. Used by the config-studio editor's save gate.",
    )
    parser.add_argument("--init", metavar="PATH", help="Create a new project config; requires --name and --from-config")
    parser.add_argument("--name", help="Required podcast/channel name for a new project")
    parser.add_argument("--from-config", metavar="PATH", help="Explicit existing configuration template")
    args = parser.parse_args()
    if args.init is not None:
        if args.validate or not args.from_config:
            parser.error("--init requires --name and --from-config, and cannot combine with --validate")
        try:
            initialize_config(args.init, name=args.name, template_path=args.from_config)
        except (ConfigError, OSError) as e:
            print(f"config error: {e}", file=sys.stderr)
            sys.exit(1)
        print("created named project config")
        sys.exit(0)

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
    print(f"vault.voice_corpus_dir = {c.vault.voice_corpus_dir}")
    print(f"tts.provider         = {c.tts.provider}")
    print(f"tts.host_voice       = {c.tts.host_voice}")
    if c.exchange_dir is not None:
        print(f"exchange_dir         = {c.exchange_dir}")
