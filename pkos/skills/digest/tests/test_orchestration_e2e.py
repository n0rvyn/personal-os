"""E2E test: trigger /digest via Adam API, confirm 3 sub-skill invocations land in step_logs.

Prerequisites (test auto-skips with a clear message if any are missing):
1. Adam server reachable at ADAM_BASE (default localhost:7100)
2. ~/.adam/adam.key file present
3. Role identified by ADAM_E2E_ROLE env var (default: first role found via /roles whose
   installed_plugins.json contains pkos) actually has the pkos plugin installed

Set ADAM_E2E=0 to skip unconditionally (CI without Adam)."""
import os, time, json, urllib.request, urllib.error, datetime
from pathlib import Path
import pytest

ADAM_BASE = os.environ.get("ADAM_BASE", "http://localhost:7100")
API_KEY_FILE = Path.home() / ".adam" / "adam.key"

def _api_key():
    if not API_KEY_FILE.exists():
        pytest.skip("Adam api key not present -- skipping E2E")
    return API_KEY_FILE.read_text().strip()

def _post(path, body):
    req = urllib.request.Request(
        ADAM_BASE + path,
        data=json.dumps(body).encode(),
        headers={"x-api-key": _api_key(), "content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def _get(path):
    req = urllib.request.Request(ADAM_BASE + path, headers={"x-api-key": _api_key()})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())

def _resolve_e2e_role():
    """Return the name of a role with pkos plugin installed, or pytest.skip.

    Resolution order:
    1. If ADAM_E2E_ROLE env var is set, verify it has pkos and use it (skip if not).
    2. Else scan ~/.adam/roles/<name>/installed_plugins.json for any role with pkos.
    3. If no role qualifies, skip with an actionable installation hint.
    """
    roles_dir = Path.home() / ".adam" / "roles"
    requested = os.environ.get("ADAM_E2E_ROLE")

    def _has_pkos(role_name: str) -> bool:
        ipj = roles_dir / role_name / "installed_plugins.json"
        if not ipj.exists():
            return False
        try:
            doc = json.loads(ipj.read_text())
        except json.JSONDecodeError:
            return False
        # Claude Code plugin registry shape; accept either flat list of names
        # or an object keyed by plugin name.
        if isinstance(doc, list):
            return any("pkos" in (str(x) if isinstance(x, str) else str(x.get("name", ""))) for x in doc)
        if isinstance(doc, dict):
            return any("pkos" in k for k in doc.keys())
        return False

    if requested:
        if _has_pkos(requested):
            return requested
        pytest.skip(
            f"ADAM_E2E_ROLE='{requested}' lacks pkos plugin. Install via: "
            f"curl -X POST -H 'x-api-key: $KEY' "
            f"-H 'Content-Type: application/json' "
            f"-d '{{\"scope\":\"project\",\"cwd\":\"~/.adam/roles/{requested}\"}}' "
            f"http://localhost:7100/plugins/install/pkos"
        )

    if not roles_dir.exists():
        pytest.skip("~/.adam/roles/ does not exist -- Adam not initialized on this machine")

    for role_path in roles_dir.iterdir():
        if role_path.is_dir() and _has_pkos(role_path.name):
            return role_path.name

    pytest.skip(
        "No role with pkos plugin found in ~/.adam/roles/. "
        "Install pkos to a role first: POST /plugins/install/pkos with scope=project, "
        "cwd=~/.adam/roles/<role-name>/"
    )


@pytest.mark.skipif(os.environ.get("ADAM_E2E", "1") == "0", reason="ADAM_E2E=0")
def test_digest_invokes_all_three_sub_skills_in_order():
    """Skip if server unreachable or no role has pkos; otherwise trigger /digest and verify trace."""
    try:
        urllib.request.urlopen(ADAM_BASE + "/healthz", timeout=2)
    except (urllib.error.URLError, OSError):
        pytest.skip("Adam server not running on " + ADAM_BASE)

    e2e_role = _resolve_e2e_role()  # may pytest.skip with actionable message

    today = datetime.date.today().isoformat()
    task = _post("/tasks", {
        "prompt": f"/digest --type daily --date {today}",
        "rolePreference": e2e_role,
    })
    task_id = task["id"]

    # Poll up to 5 minutes
    deadline = time.time() + 300
    while time.time() < deadline:
        t = _get(f"/tasks/{task_id}")
        if t.get("status") in ("completed", "failed", "cancelled"):
            break
        time.sleep(5)
    else:
        pytest.fail(f"task {task_id} did not finish in 5 min")

    logs = _get(f"/tasks/{task_id}/logs")
    blob = json.dumps(logs)
    collect_pos = blob.find("pkos:digest-collect")
    render_pos = blob.find("pkos:digest-render")
    publish_pos = blob.find("pkos:digest-publish")
    assert collect_pos != -1, f"digest-collect not invoked. Logs prefix: {blob[:1500]}"
    assert render_pos != -1, f"digest-render not invoked. Logs prefix: {blob[:1500]}"
    assert publish_pos != -1, f"digest-publish not invoked. Logs prefix: {blob[:1500]}"
    assert collect_pos < render_pos < publish_pos, \
        f"sub-skills out of order. collect@{collect_pos} render@{render_pos} publish@{publish_pos}"
