---
name: full-session-review
description: "Use when the user says 'full session review', '日报+反思', 'session report and reflection', 'complete session analysis', or wants both session-report HTML + session-reflect coaching markdown produced in one command. Chains the official session-report skill (HTML usage report) and session-reflect:reflect (coaching feedback enriched with session-report data)."
user_invocable: true
model: haiku
allowed-tools:
  - Bash
  - Read
  - Skill
---

## Overview

Single-command orchestrator that produces TWO artifacts in one chain:
1. HTML session-usage report (via `claude-plugins-official:session-report`)
2. Markdown coaching reflection enriched with the report's structured data (via `session-reflect:reflect`)

This skill exists to eliminate the manual `mkdir -p` / `cp template.html` setup steps that previously cluttered Boris-style daily-review tasks.

## Hard Dependency

This skill REQUIRES the `claude-plugins-official/session-report` plugin to be installed. If it isn't, this skill fails fast with installation instructions. There is no graceful fallback to "reflect-only" — if you only want reflection without the report, invoke `session-reflect:reflect` directly.

## Steps

### Step 1: Runtime check — session-report installed?

Run:
```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/check_session_report_installed.sh
```

- If exit 0: capture stdout (the SKILL.md path) for trace context, proceed to Step 2.
- If non-zero exit: stop. Surface stderr to user verbatim. Do NOT continue.

### Step 2: Invoke session-report

Invoke the `session-report:session-report` skill (default 7-day window unless user passed an override). Wait for it to complete and report the saved HTML path. The skill writes `/tmp/session-report.json` as a side effect of its Step 1 (analyze-sessions.mjs) and writes `session-report-{YYYYMMDD-HHMM}.html` to the current working directory in its Step 3.

### Step 3: Detect and capture both artifacts

Run:
```bash
bash ${CLAUDE_PLUGIN_ROOT}/scripts/detect_session_report_output.sh
```
This echoes the absolute path of the newest `session-report-*.html` in CWD. Capture as `REPORT_HTML`.

Resolve the JSON path:
```bash
SR_JSON=$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/personal_os_config.py --get session_reflect.session_report_json_path)
```

Verify the JSON exists and was just produced (mtime within last 5 minutes — session-report just wrote it):
```bash
find "$SR_JSON" -mmin -5 -print -quit
```
If no output: warn the user that session-report didn't produce the expected JSON, but continue (reflect's Step 4.5 will fall back to JSONL-only).

### Step 4: Invoke session-reflect:reflect with --session-report-json

Invoke the `session-reflect:reflect` skill, passing `--session-report-json $SR_JSON` and the same `--days N` value (if any) used for session-report. The reflect skill detects the JSON, injects its summary into the coach agent prompt, and saves the markdown to the configured `output_dir`.

### Step 5: Final summary

Output:
```
✅ Full session review complete:
- HTML report:    {REPORT_HTML}
- Reflection MD:  {OUTPUT_DIR}/{YYYY-MM-DD}.md
- session-report data injected: {true|false}
```

Resolve `{OUTPUT_DIR}` via:
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/personal_os_config.py --get session_reflect.output_dir
```

## Failure Modes

| Failure | Behavior |
|---|---|
| session-report plugin missing | Fail fast at Step 1 with install hint; no partial artifacts |
| session-report skill itself errors | Surface error; stop. Reflect not invoked. |
| session-report HTML produced but `/tmp/session-report.json` missing | Warn; continue to reflect with JSONL-only |
| session-reflect errors after report succeeded | Surface error; HTML artifact preserved |

## Completion Criteria

- HTML session-report saved to CWD with name `session-report-{date}.html`
- Markdown reflection saved to `{output_dir}/{YYYY-MM-DD}.md`
- User sees both paths in final summary
- No manual `mkdir` or `cp` steps in the trace
