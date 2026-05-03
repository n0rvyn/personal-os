#!/usr/bin/env python3
"""Audit and repair Notion properties that should open PKOS Obsidian notes."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None


DEFAULT_PIPELINE_DB_ID = "32a1bde4-ddac-81ff-8f82-f2d8d7a361d7"
DEFAULT_LINK_PROPERTIES = ("obsidian_link", "source_note_path")
NOTION_VERSION = "2022-06-28"


class AuditError(Exception):
    pass


@dataclass
class LinkFinding:
    database_id: str
    page_id: str
    title: str
    property_name: str
    property_type: str
    status: str
    current_value: str
    repaired_value: str = ""
    note_path: str = ""
    reason: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit and repair Notion Obsidian links for PKOS")
    parser.add_argument("--vault-root", default="~/Obsidian/PKOS", help="PKOS vault root")
    parser.add_argument("--vault-name", default="PKOS", help="Obsidian vault name or id")
    parser.add_argument("--config-path", default="~/.claude/pkos/config.yaml", help="PKOS config path")
    parser.add_argument("--database-id", action="append", default=[], help="Notion database id to scan")
    parser.add_argument("--hub-page-id", default=None, help="Notion page id whose child databases should be scanned")
    parser.add_argument("--property", dest="properties", action="append", default=[], help="Link property name to scan")
    parser.add_argument("--notion-token", default=None, help="Override NOTION_TOKEN")
    parser.add_argument("--timeout", type=float, default=20.0, help="Notion API request timeout in seconds")
    parser.add_argument("--apply", action="store_true", help="Update repairable Notion properties")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    parser.add_argument("--report-file", default=None, help="Write YAML report to this path")
    return parser.parse_args()


def expand_path(raw: str | None) -> Path | None:
    if not raw:
        return None
    return Path(os.path.expanduser(raw)).resolve()


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    if yaml is None:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise AuditError(f"Expected YAML mapping at {path}")
    return data


def config_database_ids(config: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    audit_cfg = config.get("notion_link_audit", {})
    if isinstance(audit_cfg, dict):
        for database_id in audit_cfg.get("database_ids", []) or []:
            if database_id:
                ids.append(str(database_id))
    product_lens_cfg = config.get("product_lens_notion", {})
    if isinstance(product_lens_cfg, dict) and product_lens_cfg.get("database_id"):
        ids.append(str(product_lens_cfg["database_id"]))
    ids.append(DEFAULT_PIPELINE_DB_ID)
    return dedupe(ids)


def config_hub_page_id(config: dict[str, Any]) -> str | None:
    audit_cfg = config.get("notion_link_audit", {})
    if isinstance(audit_cfg, dict) and audit_cfg.get("hub_page_id"):
        return str(audit_cfg["hub_page_id"])
    return None


def config_link_properties(config: dict[str, Any]) -> list[str]:
    audit_cfg = config.get("notion_link_audit", {})
    if isinstance(audit_cfg, dict):
        configured = [str(name) for name in audit_cfg.get("link_properties", []) or [] if name]
        if configured:
            return configured
    return list(DEFAULT_LINK_PROPERTIES)


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            ordered.append(normalized)
    return ordered


def normalized_property_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def obsidian_open_uri(note_path: Path, vault_root: Path, vault_name: str) -> str:
    relative_note_path = note_path.relative_to(vault_root).as_posix()
    return (
        "obsidian://open?vault="
        f"{urllib.parse.quote(vault_name, safe='')}&file={urllib.parse.quote(relative_note_path, safe='')}"
    )


def title_from_page(page: dict[str, Any]) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(part.get("plain_text", "") for part in prop.get("title", [])).strip()
    return ""


def property_text(prop: dict[str, Any]) -> str:
    prop_type = prop.get("type")
    if prop_type == "url":
        return prop.get("url") or ""
    if prop_type == "rich_text":
        chunks = prop.get("rich_text", [])
        for chunk in chunks:
            href = chunk.get("href")
            if href:
                return str(href)
            text = chunk.get("text", {})
            link = text.get("link") or {}
            if link.get("url"):
                return str(link["url"])
        return "".join(chunk.get("plain_text", "") for chunk in chunks).strip()
    return ""


def source_note_id(page: dict[str, Any]) -> str:
    props = page.get("properties", {})
    for name, prop in props.items():
        if name.lower() == "source_note_id":
            return property_text(prop)
    return ""


def build_notion_property_value(prop_type: str, value: str) -> dict[str, Any]:
    if prop_type == "url":
        return {"url": value}
    if prop_type == "rich_text":
        return {"rich_text": [{"type": "text", "text": {"content": value, "link": {"url": value}}}]}
    return {"rich_text": [{"type": "text", "text": {"content": value}}]}


def relative_candidate_to_path(candidate: str, vault_root: Path) -> Path:
    decoded = urllib.parse.unquote(candidate)
    path = vault_root / decoded
    if path.suffix != ".md":
        md_path = path.with_suffix(".md")
        if md_path.exists():
            return md_path
    return path


def path_candidate_to_note(current_value: str, vault_root: Path) -> Path | None:
    value = os.path.expanduser(urllib.parse.unquote(current_value.strip()))
    if not value:
        return None
    if value.startswith("file://"):
        parsed = urllib.parse.urlparse(value)
        value = urllib.parse.unquote(parsed.path)
    path = Path(value)
    if path.is_absolute():
        try:
            return path.resolve()
        except OSError:
            return path
    if value.endswith(".md") or "/" in value:
        return relative_candidate_to_path(value, vault_root)
    return None


def obsidian_uri_to_note(current_value: str, vault_root: Path) -> Path | None:
    parsed = urllib.parse.urlparse(current_value)
    if parsed.scheme != "obsidian" or parsed.netloc != "open":
        return None
    query = urllib.parse.parse_qs(parsed.query)
    if query.get("path"):
        return path_candidate_to_note(query["path"][0], vault_root)
    if query.get("file"):
        return relative_candidate_to_path(query["file"][0], vault_root)
    return None


def note_from_current_value(current_value: str, vault_root: Path) -> Path | None:
    if current_value.startswith("obsidian://"):
        return obsidian_uri_to_note(current_value, vault_root)
    return path_candidate_to_note(current_value, vault_root)


def find_by_source_note_id(note_id: str, vault_root: Path) -> list[Path]:
    if not note_id:
        return []
    return sorted(vault_root.rglob(f"{note_id}.md"))


def find_by_title(title: str, vault_root: Path) -> list[Path]:
    if not title:
        return []
    matches: list[Path] = []
    needle = f"# {title}".strip()
    for path in vault_root.rglob("*.md"):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("# "):
                    if stripped == needle:
                        matches.append(path)
                    break
        except UnicodeDecodeError:
            continue
    return sorted(matches)


def infer_note_path(current_value: str, page: dict[str, Any], vault_root: Path) -> tuple[Path | None, str]:
    from_value = note_from_current_value(current_value, vault_root)
    if from_value:
        return from_value, "from_current_value"

    return infer_note_path_from_page(page, vault_root)


def infer_note_path_from_page(page: dict[str, Any], vault_root: Path) -> tuple[Path | None, str]:

    id_matches = find_by_source_note_id(source_note_id(page), vault_root)
    if len(id_matches) == 1:
        return id_matches[0], "from_source_note_id"
    if len(id_matches) > 1:
        return None, "ambiguous_source_note_id"

    title_matches = find_by_title(title_from_page(page), vault_root)
    if len(title_matches) == 1:
        return title_matches[0], "from_title_heading"
    if len(title_matches) > 1:
        return None, "ambiguous_title_heading"
    return None, "no_candidate"


def inspect_link(
    database_id: str,
    page: dict[str, Any],
    property_name: str,
    vault_root: Path,
    vault_name: str,
) -> LinkFinding:
    page_id = str(page.get("id", ""))
    title = title_from_page(page)
    prop = page.get("properties", {}).get(property_name, {})
    prop_type = str(prop.get("type", ""))
    current_value = property_text(prop)
    note_path, reason = infer_note_path(current_value, page, vault_root)

    if note_path is None:
        return LinkFinding(database_id, page_id, title, property_name, prop_type, "unresolved", current_value, reason=reason)

    if not note_path.exists():
        alternate_path, alternate_reason = infer_note_path_from_page(page, vault_root)
        if alternate_path and alternate_path.exists():
            note_path = alternate_path
            reason = f"{alternate_reason}_after_missing_current"
        else:
            detail = alternate_reason if alternate_reason != "no_candidate" else reason
            return LinkFinding(
                database_id,
                page_id,
                title,
                property_name,
                prop_type,
                "missing_file",
                current_value,
                note_path=str(note_path),
                reason=detail,
            )

    try:
        repaired = obsidian_open_uri(note_path.resolve(), vault_root, vault_name)
    except ValueError:
        return LinkFinding(
            database_id,
            page_id,
            title,
            property_name,
            prop_type,
            "outside_vault",
            current_value,
            note_path=str(note_path),
            reason=reason,
        )

    status = "valid" if current_value == repaired else "repairable"
    return LinkFinding(
        database_id,
        page_id,
        title,
        property_name,
        prop_type,
        status,
        current_value,
        repaired_value=repaired if status == "repairable" else "",
        note_path=str(note_path.relative_to(vault_root)),
        reason=reason,
    )


class NotionClient:
    def __init__(self, token: str, timeout: float = 20.0) -> None:
        self.token = token
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(
            f"https://api.notion.com{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise AuditError(f"Notion API {method} {path} failed: {exc.code} {body}") from exc
        except urllib.error.URLError as exc:
            raise AuditError(f"Notion API {method} {path} failed: {exc}") from exc

    def query_database(self, database_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        payload: dict[str, Any] = {"page_size": 100}
        while True:
            data = self.request("POST", f"/v1/databases/{database_id}/query", payload)
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                return results
            payload["start_cursor"] = data.get("next_cursor")

    def database_property_names(self, database_id: str, candidates: list[str]) -> list[str]:
        data = self.request("GET", f"/v1/databases/{database_id}")
        properties = data.get("properties", {})
        lookup = {normalized_property_name(name): name for name in properties}
        return [
            lookup[normalized_property_name(candidate)]
            for candidate in candidates
            if normalized_property_name(candidate) in lookup
        ]

    def child_database_ids(self, page_id: str) -> list[str]:
        ids: list[str] = []
        next_cursor: str | None = None
        while True:
            suffix = f"&start_cursor={next_cursor}" if next_cursor else ""
            data = self.request("GET", f"/v1/blocks/{page_id}/children?page_size=100{suffix}")
            for block in data.get("results", []):
                if block.get("type") == "child_database":
                    ids.append(str(block["id"]))
            if not data.get("has_more"):
                return dedupe(ids)
            next_cursor = data.get("next_cursor")

    def update_property(self, page_id: str, property_name: str, property_type: str, value: str) -> None:
        payload = {"properties": {property_name: build_notion_property_value(property_type, value)}}
        self.request("PATCH", f"/v1/pages/{page_id}", payload)


def resolve_scan_targets(args: argparse.Namespace, config: dict[str, Any], client: NotionClient | None) -> list[str]:
    database_ids = list(args.database_id) if args.database_id else config_database_ids(config)
    hub_page_id = args.hub_page_id or config_hub_page_id(config)
    if hub_page_id and client:
        database_ids.extend(client.child_database_ids(hub_page_id))
    return dedupe(database_ids)


def audit_database(
    client: NotionClient,
    database_id: str,
    link_properties: list[str],
    vault_root: Path,
    vault_name: str,
) -> list[LinkFinding]:
    page_properties = client.database_property_names(database_id, link_properties)
    if not page_properties:
        return []
    findings: list[LinkFinding] = []
    for page in client.query_database(database_id):
        for property_name in page_properties:
            findings.append(inspect_link(database_id, page, property_name, vault_root, vault_name))
    return findings


def summarize(findings: list[LinkFinding]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.status] = counts.get(finding.status, 0) + 1
    return {
        "scanned_links": len(findings),
        "counts": counts,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def write_report(path: Path, summary: dict[str, Any], findings: list[LinkFinding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "findings": [asdict(finding) for finding in findings]}
    if yaml is None:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    path.write_text(yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8")


def print_report(summary: dict[str, Any], findings: list[LinkFinding], as_json: bool) -> None:
    payload = {"summary": summary, "findings": [asdict(finding) for finding in findings]}
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return
    print("PKOS Notion Obsidian Link Audit")
    print(f"  Scanned links: {summary['scanned_links']}")
    for status, count in sorted(summary["counts"].items()):
        print(f"  {status}: {count}")
    for finding in findings:
        if finding.status == "valid":
            continue
        print(f"- {finding.status}: {finding.title or finding.page_id} [{finding.property_name}]")
        if finding.current_value:
            print(f"  current: {finding.current_value}")
        if finding.repaired_value:
            print(f"  repair:  {finding.repaired_value}")
        if finding.reason:
            print(f"  reason:  {finding.reason}")


def main() -> int:
    args = parse_args()
    vault_root = expand_path(args.vault_root)
    config_path = expand_path(args.config_path)
    if vault_root is None or config_path is None:
        raise AuditError("Invalid path argument")
    if not vault_root.exists():
        raise AuditError(f"Vault root does not exist: {vault_root}")

    config = load_yaml(config_path)
    link_properties = args.properties or config_link_properties(config)
    token = args.notion_token or os.getenv("NOTION_TOKEN")
    if not token:
        raise AuditError("NOTION_TOKEN is required")
    client = NotionClient(token, args.timeout)

    database_ids = resolve_scan_targets(args, config, client)
    if not database_ids:
        raise AuditError("No Notion database ids configured or provided")

    findings: list[LinkFinding] = []
    for database_id in database_ids:
        findings.extend(audit_database(client, database_id, link_properties, vault_root, args.vault_name))

    if args.apply:
        for finding in findings:
            if finding.status != "repairable" or not finding.repaired_value:
                continue
            client.update_property(finding.page_id, finding.property_name, finding.property_type, finding.repaired_value)

    summary = summarize(findings)
    report_file = expand_path(args.report_file) if args.report_file else None
    if args.apply and report_file is None:
        report_file = vault_root / ".state" / "notion-obsidian-link-audit.yaml"
    if report_file:
        write_report(report_file, summary, findings)
    print_report(summary, findings, args.json)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AuditError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
