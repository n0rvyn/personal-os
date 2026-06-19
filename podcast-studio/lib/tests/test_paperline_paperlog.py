"""Tests for lib/paperline/paperlog.py — paper-log store (论文线连续性).

Written before `lib/paperline/paperlog.py` exists. At this point:
  - `lib.paperline.paperlog` does NOT exist → `ModuleNotFoundError`.
  - `load_paperlog` / `append_paper` / `is_covered` are unimportable.

Pinned contracts (Task 2 plan + design doc §paper-log 模型 + Threat Model
§Input validation + DP-403=A):

  - `load_paperlog(state_dir) -> list[dict]`:
      - Missing file → `[]` (first-run legal — paper-log starts empty).
      - YAML parse error → RAISE (fail-CLOSED; a corrupt log cannot be
        silently treated as empty, or dedup will silently re-select a
        covered paper). Mirror `lib.stance.load_cards` discipline: empty
        `{}` placeholder is skipped, genuinely malformed non-empty raises.

  - `append_paper(state_dir, entry)`:
      - Schema validation: required fields are `arxiv_id`, `title`,
        `date`, `concepts` (list[str]); raise on any missing field.
      - arxiv_id regex: r'^\d{4}\.\d{4,5}(v\d+)?$' (mirror
        `lib.paperline.discovery._ARXIV_ID_RE`); invalid → raise.
      - title sanitized: replace newlines / control characters with
        space (data sanitization — paper-log feeds curator prompt, so
        newlines could break the YAML block / inject structure).
      - append-only: load existing entries, append, write back; the
        store is NEVER overwritten with fewer entries.
      - Atomic write: temp file in `state_dir` + `os.replace`; mirrors
        `lib.stance.write_card` discipline.

  - `is_covered(paperlog, arxiv_id) -> bool`: exact arxiv_id match
    (DP-403=A — arXiv-id 精确 is the hard dedup gate; concept
    similarity stays with curator persona).

  - **Line isolation**: `paperlog.py` MUST NOT import
    `lib.stance` / `lib.coveredground` / `lib.magnitude` / `lib.bible`
    (mirrors `test_line_isolation.py` firewall). Tests assert this.

Regression shield: corrupt file → raise (NOT `[]`). A silent-empty log
would let the next run's curator re-select a paper that was already
covered (D-013 dedup-命脉 invariant).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

# Ensure the plugin root (parent of lib/) is on sys.path so
# `from lib.paperline.paperlog import ...` resolves once the module exists.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))


# arxiv_id regex — mirror of lib.paperline.discovery._ARXIV_ID_RE.
_ARXIV_ID_PATTERN = r"^\d{4}\.\d{4,5}(v\d+)?$"


# ---------------------------------------------------------------------------
# Module-level pins (FAIL-first: expect ModuleNotFoundError pre-impl)
# ---------------------------------------------------------------------------

def test_module_imports():
    """The `lib.paperline.paperlog` module must exist after Task 2-impl.

    Before Task 2-impl this raises `ModuleNotFoundError: No module named
    'lib.paperline.paperlog'` (the test-FAIL-first contract).
    """
    from lib.paperline import paperlog  # noqa: F401

    # Public surface: load + append + is_covered.
    assert hasattr(paperlog, "load_paperlog"), (
        "paperlog.load_paperlog is the public read API; must be exposed "
        "at module level"
    )
    assert hasattr(paperlog, "append_paper"), (
        "paperlog.append_paper is the public write API; must be exposed "
        "at module level"
    )
    assert hasattr(paperlog, "is_covered"), (
        "paperlog.is_covered is the public dedup API; must be exposed "
        "at module level"
    )
    assert callable(paperlog.load_paperlog)
    assert callable(paperlog.append_paper)
    assert callable(paperlog.is_covered)


# ---------------------------------------------------------------------------
# load_paperlog
# ---------------------------------------------------------------------------

def test_load_paperlog_missing_file_returns_empty(tmp_path):
    """`load_paperlog` on a state_dir with NO paper-log.yaml must return
    `[]` (first-run legal — no prior episode, no log yet)."""
    from lib.paperline.paperlog import load_paperlog

    result = load_paperlog(str(tmp_path))
    assert result == [], (
        f"missing file must yield empty list, got {result!r}"
    )


def test_load_paperlog_roundtrip(tmp_path):
    """`load_paperlog` must YAML-roundtrip an entry written via
    `append_paper` — schema preserved exactly."""
    from lib.paperline.paperlog import append_paper, load_paperlog

    entry = _make_entry(
        arxiv_id="2606.19341",
        title="OmniAgent: Long video understanding",
        date="2026-06-19",
        concepts=["video understanding", "agent loop"],
    )
    append_paper(str(tmp_path), entry)

    result = load_paperlog(str(tmp_path))
    assert isinstance(result, list), f"expected list, got {type(result).__name__}"
    assert len(result) == 1, f"expected 1 entry, got {len(result)}"
    # Schema preserved
    got = result[0]
    assert got["arxiv_id"] == entry["arxiv_id"]
    assert got["title"] == entry["title"]
    assert got["date"] == entry["date"]
    assert list(got["concepts"]) == list(entry["concepts"])


def test_load_paperlog_multiple_entries(tmp_path):
    """`load_paperlog` must return ALL entries in append order."""
    from lib.paperline.paperlog import append_paper, load_paperlog

    e1 = _make_entry(arxiv_id="2606.19341", title="Paper A", date="2026-06-17",
                     concepts=["concept-a"])
    e2 = _make_entry(arxiv_id="2606.19342", title="Paper B", date="2026-06-18",
                     concepts=["concept-b"])
    e3 = _make_entry(arxiv_id="2606.19343", title="Paper C", date="2026-06-19",
                     concepts=["concept-c"])
    append_paper(str(tmp_path), e1)
    append_paper(str(tmp_path), e2)
    append_paper(str(tmp_path), e3)

    result = load_paperlog(str(tmp_path))
    assert len(result) == 3, f"expected 3 entries, got {len(result)}"
    assert [e["arxiv_id"] for e in result] == [
        "2606.19341", "2606.19342", "2606.19343",
    ]


def test_load_paperlog_corrupt_raises(tmp_path):
    """A YAML-corrupt paper-log MUST RAISE (fail-closed — D-013 dedup
    命脉). A silent empty return would let the next run re-select a
    covered paper (the worst-case silent failure for paper-log)."""
    from lib.paperline.paperlog import load_paperlog

    log = tmp_path / "paper-log.yaml"
    log.write_text("this: is: not: valid: yaml: : :", encoding="utf-8")

    with pytest.raises(Exception) as exc_info:
        load_paperlog(str(tmp_path))
    # The exception type/message should help debugging — accept any
    # exception but require it to be raised (NOT silently return []).
    assert exc_info.value is not None


def test_load_paperlog_empty_file_returns_empty(tmp_path):
    """An empty (zero-byte) paper-log.yaml must return `[]` (not raise) —
    mirrors `lib.stance.load_cards`'s empty-file skip discipline."""
    from lib.paperline.paperlog import load_paperlog

    log = tmp_path / "paper-log.yaml"
    log.write_text("", encoding="utf-8")

    result = load_paperlog(str(tmp_path))
    assert result == [], f"empty file must yield [], got {result!r}"


# ---------------------------------------------------------------------------
# append_paper — schema validation
# ---------------------------------------------------------------------------

def test_append_paper_missing_field_raises(tmp_path):
    """`append_paper` must RAISE on a missing required field (fail-closed
    per Threat Model §Input validation — dirty data must not enter the
    dedup 命脉)."""
    from lib.paperline.paperlog import append_paper, load_paperlog

    # Missing `date`
    bad = _make_entry(arxiv_id="2606.19341", title="T", date="NOT_SET",
                      concepts=["c"])
    bad.pop("date")

    with pytest.raises(Exception):
        append_paper(str(tmp_path), bad)

    # Verify nothing was written — load still returns []
    assert load_paperlog(str(tmp_path)) == []


def test_append_paper_invalid_arxiv_id_raises(tmp_path):
    """`append_paper` must RAISE on an arxiv_id that doesn't match the
    strict regex (Threat Model §Input validation). Bad ids would
    silently break dedup."""
    from lib.paperline.paperlog import append_paper

    # Missing dot
    bad = _make_entry(arxiv_id="260619341", title="T", date="2026-06-19",
                      concepts=["c"])
    with pytest.raises(Exception):
        append_paper(str(tmp_path), bad)

    # Wrong shape: 5-digit second half
    bad2 = _make_entry(arxiv_id="2606.193411", title="T", date="2026-06-19",
                       concepts=["c"])
    with pytest.raises(Exception):
        append_paper(str(tmp_path), bad2)

    # Trailing junk
    bad3 = _make_entry(arxiv_id="2606.19341XYZ", title="T", date="2026-06-19",
                       concepts=["c"])
    with pytest.raises(Exception):
        append_paper(str(tmp_path), bad3)


def test_append_paper_valid_arxiv_id_variants_accepted(tmp_path):
    """`append_paper` must accept all valid arxiv_id shapes per the
    regex — bare id and versioned id."""
    from lib.paperline.paperlog import append_paper, load_paperlog

    append_paper(str(tmp_path), _make_entry(
        arxiv_id="2606.19341", title="Bare", date="2026-06-19",
        concepts=["c"],
    ))
    append_paper(str(tmp_path), _make_entry(
        arxiv_id="2606.19341v2", title="Versioned", date="2026-06-20",
        concepts=["c"],
    ))
    append_paper(str(tmp_path), _make_entry(
        arxiv_id="2606.1234", title="Short second half", date="2026-06-21",
        concepts=["c"],
    ))

    result = load_paperlog(str(tmp_path))
    ids = {e["arxiv_id"] for e in result}
    assert ids == {"2606.19341", "2606.19341v2", "2606.1234"}


def test_append_paper_invalid_date_raises(tmp_path):
    """`append_paper` must RAISE on a non-ISO `date` (Threat Model
    §Input validation — `date` regex `YYYY-MM-DD`)."""
    from lib.paperline.paperlog import append_paper

    bad = _make_entry(arxiv_id="2606.19341", title="T",
                      date="2026/06/19", concepts=["c"])
    with pytest.raises(Exception):
        append_paper(str(tmp_path), bad)


def test_append_paper_concepts_must_be_list(tmp_path):
    """`concepts` must be a list of strings (schema)."""
    from lib.paperline.paperlog import append_paper

    # Build the entry dict directly — `_make_entry` does `list(concepts)`, which
    # would coerce the string into a list of chars (a valid list[str]) and
    # defeat this negative test. The schema requires `concepts` be an actual list.
    bad = {"arxiv_id": "2606.19341", "title": "T", "date": "2026-06-19",
           "concepts": "not-a-list"}
    with pytest.raises(Exception):
        append_paper(str(tmp_path), bad)


def test_append_paper_sanitizes_title_newlines(tmp_path):
    """`title` with embedded newlines / control characters must be
    sanitized (Threat Model §Input validation — paper-log feeds the
    curator prompt, newlines could break YAML block structure / inject
    sections). The stored title MUST NOT contain newlines or control
    characters after write."""
    from lib.paperline.paperlog import append_paper, load_paperlog

    raw = "Line 1\nLine 2\rLine 3\x07Bell\x1bEsc"
    entry = _make_entry(arxiv_id="2606.19341", title=raw, date="2026-06-19",
                         concepts=["c"])
    append_paper(str(tmp_path), entry)

    result = load_paperlog(str(tmp_path))
    assert len(result) == 1
    sanitized = result[0]["title"]
    # No newline chars anywhere in the stored title
    assert "\n" not in sanitized, (
        f"newline must be stripped, got {sanitized!r}"
    )
    assert "\r" not in sanitized
    # Control characters stripped
    assert "\x07" not in sanitized
    assert "\x1b" not in sanitized


# ---------------------------------------------------------------------------
# append_paper — append-only + atomic
# ---------------------------------------------------------------------------

def test_append_paper_is_append_only(tmp_path):
    """A second `append_paper` MUST NOT overwrite the first entry —
    the store is append-only (D-013). After N appends, load yields N
    entries with all originals preserved."""
    from lib.paperline.paperlog import append_paper, load_paperlog

    first = _make_entry(arxiv_id="2606.19341", title="First",
                        date="2026-06-17", concepts=["a"])
    second = _make_entry(arxiv_id="2606.19342", title="Second",
                         date="2026-06-18", concepts=["b"])

    append_paper(str(tmp_path), first)
    append_paper(str(tmp_path), second)

    result = load_paperlog(str(tmp_path))
    assert len(result) == 2, (
        f"append-only violated: expected 2 entries, got {len(result)}"
    )
    # First entry preserved verbatim
    first_loaded = next(e for e in result if e["arxiv_id"] == "2606.19341")
    assert first_loaded["title"] == "First"
    assert first_loaded["date"] == "2026-06-17"


def test_append_paper_atomic_write_no_orphan(tmp_path):
    """Atomic-write discipline (mirror `lib.stance.write_card`):
    after a successful `append_paper`, the temp file (`.paper-log.yaml.*.tmp`)
    MUST NOT linger in state_dir — it is replaced onto the target."""
    from lib.paperline.paperlog import append_paper

    append_paper(str(tmp_path), _make_entry(
        arxiv_id="2606.19341", title="T", date="2026-06-19", concepts=["c"],
    ))

    orphans = list(tmp_path.glob(".paper-log.yaml.*.tmp"))
    assert not orphans, (
        f"orphan temp file(s) after append: {orphans}"
    )
    # Target file exists
    assert (tmp_path / "paper-log.yaml").exists()


# ---------------------------------------------------------------------------
# is_covered — DP-403=A exact arxiv_id dedup
# ---------------------------------------------------------------------------

def test_is_covered_present_returns_true():
    """`is_covered(log, id)` returns True iff the id is in any entry's
    `arxiv_id` field (DP-403=A — arXiv-id 精确 is the hard gate)."""
    from lib.paperline.paperlog import is_covered

    log = [
        {"arxiv_id": "2606.19341", "title": "A", "date": "2026-06-19",
         "concepts": ["c"]},
        {"arxiv_id": "2606.19342", "title": "B", "date": "2026-06-20",
         "concepts": ["c"]},
    ]
    assert is_covered(log, "2606.19341") is True
    assert is_covered(log, "2606.19342") is True


def test_is_covered_absent_returns_false():
    """`is_covered` returns False for ids not in the log."""
    from lib.paperline.paperlog import is_covered

    log = [
        {"arxiv_id": "2606.19341", "title": "A", "date": "2026-06-19",
         "concepts": ["c"]},
    ]
    assert is_covered(log, "2606.99999") is False


def test_is_covered_empty_log_returns_false():
    """Empty log → False for any id (first-run legal — no coverage)."""
    from lib.paperline.paperlog import is_covered

    assert is_covered([], "2606.19341") is False


def test_is_covered_versioned_id_distinct():
    """A versioned arxiv_id and its bare id are distinct keys for dedup
    purposes — `is_covered` does exact-string match, not version-folded
    match. (The dedup contract per DP-403=A is arXiv-id exact; if the
    user later covers both `2606.19341` and `2606.19341v2` they
    intentionally mean two episodes.)"""
    from lib.paperline.paperlog import is_covered

    log = [{"arxiv_id": "2606.19341", "title": "A", "date": "2026-06-19",
            "concepts": ["c"]}]
    # bare id is covered
    assert is_covered(log, "2606.19341") is True
    # versioned id is NOT covered (distinct string)
    assert is_covered(log, "2606.19341v2") is False


# ---------------------------------------------------------------------------
# Roundtrip + dedup integration
# ---------------------------------------------------------------------------

def test_append_then_is_covered_roundtrip(tmp_path):
    """End-to-end: append → load → is_covered returns True for the
    appended id, False for a fresh id. This is the DP-403=A dedup gate
    as the next-run curator will exercise it."""
    from lib.paperline.paperlog import append_paper, is_covered, load_paperlog

    append_paper(str(tmp_path), _make_entry(
        arxiv_id="2606.19341", title="A", date="2026-06-19", concepts=["x"],
    ))

    log = load_paperlog(str(tmp_path))
    assert is_covered(log, "2606.19341") is True
    assert is_covered(log, "2606.99999") is False


# ---------------------------------------------------------------------------
# Line isolation (firewall — mirrors test_line_isolation.py)
# ---------------------------------------------------------------------------

_FORBIDDEN_IN_PAPERLOG = {
    "stance", "coveredground", "magnitude", "bible",
    "lib.stance", "lib.coveredground", "lib.magnitude", "lib.bible",
    "runner", "pipeline", "episode", "dispatch",
    "lib.runner", "lib.pipeline", "lib.episode", "lib.dispatch",
}


def _imported_modules_in_paperlog() -> set[str]:
    """AST-walk `lib/paperline/paperlog.py` and collect every imported
    module name (both `import x` and `from x import …`). Returns the
    set of fully-qualified module strings."""
    path = PLUGIN_ROOT / "lib" / "paperline" / "paperlog.py"
    if not path.exists():
        # Pre-impl: the file doesn't exist yet, so isolation is trivially
        # satisfied (we test this explicitly so the FAIL-first contract is
        # loud and clear).
        return set()
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_paperlog_does_not_import_opinion_line():
    """`paperlog.py` MUST NOT import any opinion-only module
    (`lib.stance` / `lib.coveredground` / `lib.magnitude` / `lib.bible` /
    `lib.runner` / `lib.pipeline` / `lib.episode` / `lib.dispatch`) —
    the line isolation firewall. paper-log is a paper-line primitive;
    if it needed opinion-line infrastructure it would be a hidden
    coupling."""
    names = _imported_modules_in_paperlog()
    if not names:
        pytest.fail(
            "lib/paperline/paperlog.py does not exist yet — this is "
            "expected FAIL-first; the implementation task must create it."
        )
    bad = sorted(
        n for n in names
        if any(n == f or n.startswith(f + ".") for f in _FORBIDDEN_IN_PAPERLOG)
    )
    assert not bad, (
        f"paperlog.py imports forbidden opinion/shared modules: {bad}; "
        "paperlog must NOT cross the line firewall"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(*, arxiv_id: str, title: str, date: str,
                concepts: list[str]) -> dict:
    """Build a valid paperlog entry dict for use in append tests."""
    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "date": date,
        "concepts": list(concepts),
    }