#!/usr/bin/env python3
"""Select PKOS podcast transcript topics and persist dedup history.

The script intentionally uses only the Python standard library. It owns the
deterministic parts of the podcast flow: config resolution, artifact discovery,
normalization, source/topic deduplication, nearest-history lookup, and history
state writes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import signal
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit


DEFAULT_EXCHANGE_DIR = "~/Obsidian/PKOS/.exchange"
DEFAULT_SCRATCH_DIR = "~/.personal-os/scratch"
DEFAULT_PKOS_VAULT = "~/Obsidian/PKOS"
STATE_DIR_NAME = ".state/podcast-transcript"
STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "in",
    "into",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
    "daily",
    "weekly",
}


class PodcastSourceError(Exception):
    pass


@dataclass(frozen=True)
class Roots:
    exchange_dir: Path
    scratch_dir: Path
    vault: Path
    config_path: Path
    pkos_config_path: Path


@dataclass
class Candidate:
    path: Path
    meta: dict[str, Any]
    body: str
    producer: str
    title: str
    significance: float
    date: date
    source_identity: str
    topic_key: str
    input_artifacts: list[str]
    evidence: list[str] = field(default_factory=list)
    speaker_notes: list[str] = field(default_factory=list)
    novelty: str = "new"
    score: float = 0.0


def strip_inline_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(line):
        if char == "\\" and in_double:
            escaped = not escaped
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        elif char == "#" and not in_single and not in_double:
            if index == 0 or line[index - 1].isspace():
                return line[:index].rstrip()
        escaped = False
    return line.rstrip()


def split_inline_list(raw: str) -> list[str]:
    inner = raw.strip()[1:-1].strip()
    if not inner:
        return []
    items: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    for char in inner:
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        if char == "," and not in_single and not in_double:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if current:
        items.append("".join(current).strip())
    return [parse_scalar(item) for item in items]


def parse_scalar(raw: str) -> Any:
    value = raw.strip()
    if value == "":
        return ""
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if value.startswith("[") and value.endswith("]"):
        return split_inline_list(value)
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none"}:
        return None
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except ValueError:
            return value
    if re.fullmatch(r"-?\d+\.\d+", value):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def parse_yaml_subset(text: str) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    pending_key: tuple[int, dict[str, Any], str] | None = None

    for raw_line in text.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        line = strip_inline_comment(raw_line)
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" "))
        content = line.strip()

        if content.startswith("- "):
            if pending_key is None:
                raise PodcastSourceError(f"List item without key: {raw_line}")
            pending_indent, pending_parent, pending_name = pending_key
            if indent <= pending_indent:
                raise PodcastSourceError(f"List item indentation invalid: {raw_line}")
            if not isinstance(pending_parent.get(pending_name), list):
                pending_parent[pending_name] = []
            pending_parent[pending_name].append(parse_scalar(content[2:].strip()))
            continue

        pending_key = None
        while stack and indent <= stack[-1][0]:
            stack.pop()
        if ":" not in content:
            raise PodcastSourceError(f"Unsupported YAML line: {raw_line}")
        key, raw_value = content.split(":", 1)
        key = key.strip()
        if not key:
            raise PodcastSourceError(f"Empty YAML key: {raw_line}")
        parent = stack[-1][1]
        if not isinstance(parent, dict):
            raise PodcastSourceError(f"Cannot add mapping under list: {raw_line}")
        raw_value = raw_value.strip()
        if raw_value == "":
            parent[key] = {}
            stack.append((indent, parent[key]))
            pending_key = (indent, parent, key)
        else:
            parent[key] = parse_scalar(raw_value)
    return root


def format_yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    if not text or any(char in text for char in ":#[]") or text.startswith(("~", "{", "}")):
        return json.dumps(text)
    return text


def write_yaml_subset(data: dict[str, Any], indent: int = 0) -> str:
    lines: list[str] = []
    prefix = " " * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f"{prefix}{key}:")
            lines.append(write_yaml_subset(value, indent + 2).rstrip())
        elif isinstance(value, list):
            if not value:
                lines.append(f"{prefix}{key}: []")
            else:
                lines.append(f"{prefix}{key}:")
                for item in value:
                    lines.append(f"{prefix}  - {format_yaml_scalar(item)}")
        else:
            lines.append(f"{prefix}{key}: {format_yaml_scalar(value)}")
    return "\n".join(line for line in lines if line != "") + "\n"


def parse_markdown_frontmatter(raw_text: str) -> tuple[dict[str, Any], str]:
    if not raw_text.startswith("---\n"):
        return {}, raw_text
    closing = raw_text.find("\n---\n", 4)
    if closing == -1:
        raise PodcastSourceError("Markdown frontmatter is missing closing marker")
    meta = parse_yaml_subset(raw_text[4:closing])
    body = raw_text[closing + 5 :].lstrip()
    return meta, body


def render_markdown_frontmatter(meta: dict[str, Any], body: str) -> str:
    return "---\n" + write_yaml_subset(meta) + "---\n\n" + body.lstrip()


def get_nested(data: dict[str, Any], path: list[str]) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def expand_path(raw: str | os.PathLike[str]) -> Path:
    return Path(os.path.expanduser(str(raw))).resolve()


def load_yaml_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return parse_yaml_subset(path.read_text(encoding="utf-8"))


def resolve_roots(
    config_path: Path | None = None,
    pkos_config_path: Path | None = None,
    create_missing: bool = True,
) -> Roots:
    home = Path.home()
    config_path = config_path or expand_path(os.environ.get("PERSONAL_OS_CONFIG", home / ".claude" / "personal-os.yaml"))
    pkos_config_path = pkos_config_path or expand_path(os.environ.get("PKOS_CONFIG", home / ".claude" / "pkos" / "config.yaml"))

    if create_missing and not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(
            write_yaml_subset({"exchange_dir": DEFAULT_EXCHANGE_DIR, "scratch_dir": DEFAULT_SCRATCH_DIR}),
            encoding="utf-8",
        )

    personal = load_yaml_file(config_path)
    pkos_config = load_yaml_file(pkos_config_path)
    exchange_dir = expand_path(personal.get("exchange_dir") or DEFAULT_EXCHANGE_DIR)
    scratch_dir = expand_path(personal.get("scratch_dir") or DEFAULT_SCRATCH_DIR)
    vault_raw = get_nested(personal, ["pkos", "vault", "path"])
    if not vault_raw:
        vault_raw = get_nested(pkos_config, ["vault", "path"])
    vault = expand_path(vault_raw or DEFAULT_PKOS_VAULT)

    if create_missing:
        exchange_dir.mkdir(parents=True, exist_ok=True)
        scratch_dir.mkdir(parents=True, exist_ok=True)

    return Roots(
        exchange_dir=exchange_dir,
        scratch_dir=scratch_dir,
        vault=vault,
        config_path=config_path,
        pkos_config_path=pkos_config_path,
    )


def parse_iso_date(raw: str) -> date:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        raise PodcastSourceError(f"Invalid date {raw!r}; expected YYYY-MM-DD")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise PodcastSourceError(f"Invalid date {raw!r}; expected real YYYY-MM-DD") from exc


def parse_candidate_date(meta: dict[str, Any], path: Path) -> date:
    raw = meta.get("date") or meta.get("created") or meta.get("created_at")
    if raw:
        text = str(raw)[:10]
        try:
            return parse_iso_date(text)
        except PodcastSourceError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def canonical_url(raw: str) -> str:
    parsed = urlsplit(raw.strip())
    path = re.sub(r"/+$", "", parsed.path)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def slug_tokens(*values: Any) -> list[str]:
    tokens: list[str] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list):
            tokens.extend(slug_tokens(*value))
            continue
        text = str(value).lower()
        for token in re.findall(r"[a-z0-9]+", text):
            if token not in STOP_WORDS and len(token) > 1:
                tokens.append(token)
    return tokens


def slugify_topic(*values: Any) -> str:
    tokens = slug_tokens(*values)
    if not tokens:
        return "general"
    seen: set[str] = set()
    ordered = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            ordered.append(token)
    return "-".join(ordered)[:80].strip("-") or "general"


def derive_product_lens_title(meta: dict[str, Any]) -> str:
    intent = str(meta.get("intent") or "product-lens").replace("_", " ")
    project = meta.get("project")
    targets = meta.get("targets") if isinstance(meta.get("targets"), list) else []
    subject = project or (targets[0] if targets else "")
    decision = meta.get("decision") or ""
    return " ".join(part for part in [intent, str(subject), str(decision)] if part).strip()


def derive_identity(meta: dict[str, Any], path: Path, producer: str) -> str:
    explicit = meta.get("source_identity")
    if explicit:
        return str(explicit)
    if meta.get("url"):
        return f"source:url:{canonical_url(str(meta['url']))}"
    if meta.get("id") and meta.get("source"):
        return f"source:id:{meta['source']}:{meta['id']}"
    if producer == "product-lens":
        intent = meta.get("intent") or "unknown"
        project_or_target = meta.get("project")
        targets = meta.get("targets") if isinstance(meta.get("targets"), list) else []
        if not project_or_target and targets:
            project_or_target = targets[0]
        decision = meta.get("decision") or ""
        created = meta.get("created") or meta.get("date") or ""
        raw = f"{intent}:{project_or_target or ''}:{decision}:{created}"
        return f"source:product-lens:{slugify_topic(raw)}"
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()
    return f"source:path-sha:{digest}"


def significance_weight(value: Any, producer: str) -> float:
    if producer == "product-lens":
        mapping = {"high": 4.0, "medium": 3.0, "low": 2.0}
        return mapping.get(str(value or "").lower(), 2.0)
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").lower()
    mapping = {"critical": 5.0, "high": 4.0, "medium": 3.0, "low": 2.0}
    if text in mapping:
        return mapping[text]
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    return 2.0


def first_heading(body: str) -> str | None:
    for line in body.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match:
            return match.group(1).strip()
    return None


def producer_for(path: Path, meta: dict[str, Any], roots: Roots) -> str:
    producer = meta.get("producer") or meta.get("source")
    if producer:
        return str(producer)
    try:
        rel = path.resolve().relative_to(roots.exchange_dir)
        if rel.parts:
            return rel.parts[0]
    except ValueError:
        pass
    try:
        rel = path.resolve().relative_to(roots.vault)
        if rel.parts:
            return rel.parts[0]
    except ValueError:
        pass
    return "pkos-vault"


def normalize_candidate(path: Path, roots: Roots) -> tuple[Candidate | None, dict[str, str] | None]:
    try:
        raw = path.read_text(encoding="utf-8")
        meta, body = parse_markdown_frontmatter(raw)
    except Exception as exc:
        return None, {"path": str(path), "status": "skipped", "reason": f"parse_error:{exc}"}

    producer = producer_for(path, meta, roots)
    if producer == "product-lens":
        title = derive_product_lens_title(meta)
        if not title:
            return None, {"path": str(path), "status": "skipped", "reason": "missing_title"}
        evidence = [str(item) for item in (meta.get("source_refs") or []) if item]
        evidence.extend(str(item) for item in (meta.get("targets") or []) if item)
        speaker_notes = []
        if meta.get("intent"):
            speaker_notes.append(f"intent: {meta['intent']}")
        if meta.get("decision"):
            speaker_notes.append(f"decision: {meta['decision']}")
        source_date = parse_candidate_date(meta, path)
        significance = significance_weight(meta.get("confidence"), producer)
    else:
        title = str(meta.get("title") or first_heading(body) or "").strip()
        if not title:
            return None, {"path": str(path), "status": "skipped", "reason": "missing_title"}
        evidence = [str(item) for item in (meta.get("source_refs") or meta.get("evidence") or []) if item]
        speaker_notes = [str(meta.get("summary") or meta.get("significance") or "").strip()]
        speaker_notes = [item for item in speaker_notes if item]
        source_date = parse_candidate_date(meta, path)
        significance = significance_weight(meta.get("significance"), producer)

    identity = derive_identity(meta, path, producer)
    topic_key = slugify_topic(
        meta.get("tags"),
        meta.get("domain"),
        meta.get("category"),
        title,
        meta.get("source"),
        meta.get("intent"),
        meta.get("project"),
        meta.get("targets"),
    )
    return (
        Candidate(
            path=path.resolve(),
            meta=meta,
            body=body,
            producer=producer,
            title=title,
            significance=significance,
            date=source_date,
            source_identity=identity,
            topic_key=topic_key,
            input_artifacts=[str(path.resolve())],
            evidence=evidence,
            speaker_notes=speaker_notes,
        ),
        None,
    )


def file_mtime_in_window(path: Path, target: date, window_days: int) -> bool:
    mdate = datetime.fromtimestamp(path.stat().st_mtime).date()
    return target - timedelta(days=window_days) <= mdate <= target


def discover_artifacts(roots: Roots, target: date, source_window_days: int, source_file: Path | None = None) -> list[Path]:
    if source_file:
        validate_source_file(source_file, roots)
        return [source_file.resolve()]
    paths: set[Path] = set()
    month = target.strftime("%Y-%m")
    for rel in [f"domain-intel/{month}", f"session-reflect/{month}"]:
        base = roots.exchange_dir / rel
        if base.exists():
            paths.update(path.resolve() for path in base.glob("*.md") if path.is_file())
    product_lens = roots.exchange_dir / "product-lens"
    if product_lens.exists():
        paths.update(path.resolve() for path in product_lens.rglob("*.md") if path.is_file())
    for pattern in [f"60-Digests/{target.isoformat()}*.md", "10-Knowledge/**/*.md", "50-References/**/*.md"]:
        for path in roots.vault.glob(pattern):
            if path.is_file() and file_mtime_in_window(path, target, source_window_days):
                paths.add(path.resolve())
    return sorted(paths)


def is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def validate_source_file(path: Path, roots: Roots) -> None:
    resolved = path.resolve()
    if not resolved.exists():
        raise PodcastSourceError(f"--source-file does not exist: {resolved}")
    if not resolved.is_file():
        raise PodcastSourceError(f"--source-file is not a regular file: {resolved}")
    if resolved.suffix.lower() != ".md":
        raise PodcastSourceError(f"--source-file must be a markdown file: {resolved}")
    if not any(is_under(resolved, root) for root in [roots.exchange_dir, roots.vault, roots.scratch_dir]):
        raise PodcastSourceError("--source-file must resolve under exchange_dir, PKOS vault, or scratch_dir")


def read_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    corrupt = 0
    if not path.exists():
        return rows, corrupt
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
            else:
                corrupt += 1
        except json.JSONDecodeError:
            corrupt += 1
    return rows, corrupt


def state_dir(vault: Path) -> Path:
    return vault / STATE_DIR_NAME


def row_date(row: dict[str, Any]) -> date | None:
    raw = row.get("episode_date") or row.get("date") or row.get("created_at")
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def is_recent_row(row: dict[str, Any], target: date, window_days: int) -> bool:
    rdate = row_date(row)
    if not rdate:
        return False
    return target - timedelta(days=window_days) <= rdate <= target


def candidate_score(candidate: Candidate, target: date, topic_recent: bool) -> float:
    age = max(0, (target - candidate.date).days)
    recency = max(0.0, 4.0 - min(age, 30) / 10.0)
    producer_bonus = {
        "product-lens": 2.5,
        "domain-intel": 2.0,
        "session-reflect": 1.8,
        "60-Digests": 1.6,
        "10-Knowledge": 1.4,
        "50-References": 1.2,
    }.get(candidate.producer, 1.0)
    completeness = 1.0
    if candidate.meta.get("url") or candidate.meta.get("id") or candidate.evidence:
        completeness += 1.0
    pkos_signal = 1.0 if str(candidate.path).find("/PKOS/") >= 0 else 0.0
    repeat_penalty = -1.5 if topic_recent else 0.0
    return candidate.significance + recency + producer_bonus + completeness + pkos_signal + repeat_penalty


def select_topics(
    candidates: list[Candidate],
    roots: Roots,
    target: date,
    max_topics: int,
    source_window_days: int,
    topic_window_days: int,
) -> tuple[list[Candidate], list[dict[str, Any]], int]:
    sdir = state_dir(roots.vault)
    source_rows, corrupt_sources = read_jsonl(sdir / "source-index.jsonl")
    topic_rows, corrupt_topics = read_jsonl(sdir / "topic-index.jsonl")
    used_sources = {
        str(row.get("source_identity"))
        for row in source_rows
        if row.get("source_identity") and is_recent_row(row, target, source_window_days)
    }
    used_topics = {
        str(row.get("topic_key"))
        for row in topic_rows
        if row.get("topic_key") and is_recent_row(row, target, topic_window_days)
    }

    selected: list[Candidate] = []
    diagnostics: list[dict[str, Any]] = []
    duplicate_count = 0
    for candidate in candidates:
        if candidate.source_identity in used_sources:
            duplicate_count += 1
            diagnostics.append(
                {
                    "path": str(candidate.path),
                    "status": "skipped",
                    "reason": "recent_source_duplicate",
                    "source_identity": candidate.source_identity,
                }
            )
            continue
        topic_recent = candidate.topic_key in used_topics
        candidate.novelty = "update" if topic_recent else "new"
        candidate.score = candidate_score(candidate, target, topic_recent)
        selected.append(candidate)

    if corrupt_sources or corrupt_topics:
        diagnostics.append(
            {
                "status": "warning",
                "reason": "corrupt_history_lines",
                "source_index": corrupt_sources,
                "topic_index": corrupt_topics,
            }
        )
    selected.sort(key=lambda item: (-item.score, item.topic_key, str(item.path)))
    return selected[:max_topics], diagnostics, duplicate_count


def tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if token not in STOP_WORDS]


def bm25_nearest(query: str, docs: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    tokenized_docs = [tokenize(str(doc.get("transcript_body") or doc.get("body") or "")) for doc in docs]
    query_terms = tokenize(query)
    if not query_terms or not tokenized_docs:
        return []
    doc_freq: dict[str, int] = {}
    for tokens in tokenized_docs:
        for term in set(tokens):
            doc_freq[term] = doc_freq.get(term, 0) + 1
    avgdl = sum(len(tokens) for tokens in tokenized_docs) / max(1, len(tokenized_docs))
    k1 = 1.5
    b = 0.75
    scored: list[tuple[float, dict[str, Any]]] = []
    for doc, tokens in zip(docs, tokenized_docs):
        if not tokens:
            continue
        score = 0.0
        for term in query_terms:
            freq = tokens.count(term)
            if freq == 0:
                continue
            idf = math.log(1 + (len(tokenized_docs) - doc_freq.get(term, 0) + 0.5) / (doc_freq.get(term, 0) + 0.5))
            denom = freq + k1 * (1 - b + b * len(tokens) / max(avgdl, 1))
            score += idf * (freq * (k1 + 1) / denom)
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda item: -item[0])
    return [
        {
            "episode_id": doc.get("episode_id"),
            "episode_date": doc.get("episode_date") or doc.get("date"),
            "score": round(score, 4),
            "transcript_path": doc.get("transcript_path"),
        }
        for score, doc in scored[:limit]
    ]


def excerpt(text: str, limit: int = 700) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def topic_to_plan_item(candidate: Candidate, index: int) -> dict[str, Any]:
    return {
        "topic_key": candidate.topic_key,
        "role": "lead" if index == 0 else "main",
        "novelty": candidate.novelty,
        "source_identities": [candidate.source_identity],
        "input_artifacts": candidate.input_artifacts,
        "source_excerpts": [
            {
                "source_identity": candidate.source_identity,
                "title": candidate.title,
                "producer": candidate.producer,
                "excerpt": excerpt(candidate.body or candidate.title),
            }
        ],
        "evidence": candidate.evidence,
        "speaker_notes": candidate.speaker_notes,
    }


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name, suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def append_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def build_topic_plan(args: argparse.Namespace) -> dict[str, Any]:
    target = parse_iso_date(args.date)
    roots = resolve_roots()
    if not roots.vault.exists():
        raise PodcastSourceError(f"PKOS vault does not exist: {roots.vault}")
    source_file = expand_path(args.source_file) if args.source_file else None
    artifact_paths = discover_artifacts(roots, target, args.source_window_days, source_file)
    diagnostics: list[dict[str, Any]] = []
    candidates: list[Candidate] = []
    for path in artifact_paths:
        candidate, diagnostic = normalize_candidate(path, roots)
        if diagnostic:
            diagnostics.append(diagnostic)
        if candidate:
            candidates.append(candidate)
    selected, selection_diagnostics, duplicate_count = select_topics(
        candidates,
        roots,
        target,
        args.max_topics,
        args.source_window_days,
        args.topic_window_days,
    )
    diagnostics.extend(selection_diagnostics)
    topics = [topic_to_plan_item(candidate, index) for index, candidate in enumerate(selected)]
    run_id = hashlib.sha1(f"{args.date}:{os.getpid()}:{len(topics)}".encode("utf-8")).hexdigest()[:10]
    output_path = expand_path(args.output) if args.output else roots.scratch_dir / "pkos" / "podcast-transcript" / f"{args.date}-{run_id}" / "topic-plan.json"
    args._generated_scratch_dir = None if args.output else output_path.parent
    excerpt_bundle_path = output_path.parent / "topic-excerpts.json"
    source_excerpts = [item for topic in topics for item in topic["source_excerpts"]]
    write_json(excerpt_bundle_path, {"date": args.date, "source_excerpts": source_excerpts})
    episode_rows, corrupt_episodes = read_jsonl(state_dir(roots.vault) / "episodes.jsonl")
    if corrupt_episodes:
        diagnostics.append({"status": "warning", "reason": "corrupt_history_lines", "episodes": corrupt_episodes})
    query = " ".join(topic["topic_key"] + " " + " ".join(ex["excerpt"] for ex in topic["source_excerpts"]) for topic in topics)
    history_matches = bm25_nearest(query, episode_rows)
    plan = {
        "date": args.date,
        "type": args.type,
        "excerpt_bundle_path": str(excerpt_bundle_path),
        "topics": topics,
        "diagnostics": diagnostics,
        "history_matches": history_matches,
        "skipped_duplicate_count": duplicate_count,
    }
    write_json(output_path, plan)
    return plan


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def commit_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    transcript_path = expand_path(manifest.get("transcript_path") or "")
    if not transcript_path.exists() or not transcript_path.is_file():
        raise PodcastSourceError(f"Transcript path does not exist: {transcript_path}")
    roots = resolve_roots()
    sdir = state_dir(roots.vault)
    transcript_hash = sha256_file(transcript_path)
    topics = manifest.get("topic_plan", {}).get("topics", manifest.get("topics", []))
    topic_keys = [topic["topic_key"] for topic in topics if topic.get("topic_key")]
    source_identities = [
        source_id
        for topic in topics
        for source_id in topic.get("source_identities", [])
        if source_id
    ]
    episode_date = manifest.get("date") or manifest.get("topic_plan", {}).get("date")
    episode_id = manifest.get("episode_id") or f"{manifest.get('type', 'daily')}-{episode_date}"
    created_at = datetime.now(timezone.utc).isoformat()
    transcript_body = transcript_path.read_text(encoding="utf-8")
    episode_row = {
        "episode_id": episode_id,
        "episode_date": episode_date,
        "created_at": created_at,
        "transcript_path": str(transcript_path),
        "transcript_hash": transcript_hash,
        "topic_keys": topic_keys,
        "source_identities": source_identities,
        "transcript_body": transcript_body,
    }
    append_jsonl(sdir / "episodes.jsonl", [episode_row])
    append_jsonl(
        sdir / "source-index.jsonl",
        [
            {
                "source_identity": identity,
                "episode_id": episode_id,
                "episode_date": episode_date,
                "created_at": created_at,
                "topic_keys": topic_keys,
            }
            for identity in source_identities
        ],
    )
    append_jsonl(
        sdir / "topic-index.jsonl",
        [
            {
                "topic_key": key,
                "episode_id": episode_id,
                "episode_date": episode_date,
                "created_at": created_at,
                "source_identities": source_identities,
            }
            for key in topic_keys
        ],
    )
    manifest["transcript_hash"] = transcript_hash
    write_json(manifest_path, manifest)
    return {"status": "committed", "episode_id": episode_id, "transcript_hash": transcript_hash}


def positive_int_in_range(raw: str, name: str, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if value < minimum or value > maximum:
        raise argparse.ArgumentTypeError(f"{name} must be between {minimum} and {maximum}")
    return value


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="PKOS podcast transcript source planner")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--date", required=True)
    plan.add_argument("--type", choices=["daily"], default="daily")
    plan.add_argument("--max-topics", type=lambda value: positive_int_in_range(value, "--max-topics", 1, 8), default=4)
    plan.add_argument("--source-window-days", type=lambda value: positive_int_in_range(value, "--source-window-days", 1, 365), default=30)
    plan.add_argument("--topic-window-days", type=lambda value: positive_int_in_range(value, "--topic-window-days", 1, 365), default=14)
    plan.add_argument("--source-file")
    plan.add_argument("--output")
    plan.add_argument("--keep-scratch", action="store_true")
    commit = sub.add_parser("commit")
    commit.add_argument("--manifest", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.command == "plan" and not getattr(args, "keep_scratch", False):
        signal.signal(signal.SIGINT, lambda signum, frame: sys.exit(130))
        signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(143))
    try:
        if args.command == "plan":
            plan = build_topic_plan(args)
            print(json.dumps(plan, indent=2, sort_keys=True))
        elif args.command == "commit":
            result = commit_manifest(expand_path(args.manifest))
            print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except PodcastSourceError as exc:
        print(f"[podcast_sources] {exc}", file=sys.stderr)
        return 2
    finally:
        generated_scratch_dir = getattr(args, "_generated_scratch_dir", None)
        if generated_scratch_dir and not getattr(args, "keep_scratch", False):
            shutil.rmtree(generated_scratch_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
