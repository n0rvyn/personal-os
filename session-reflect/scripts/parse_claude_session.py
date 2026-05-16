#!/usr/bin/env python3
"""Parse a Claude Code session JSONL file into unified session summary JSON."""

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from analyzer_version import ANALYZER_VERSION
from session_enrichment import apply_enrichment

# sessions_db imported lazily below when needed (sqlite-db or enrich flags)

BUILD_PATTERNS = re.compile(
    r"\b(npm run build|yarn build|swift build|cargo build|make\b|gradle build|"
    r"go build|mvn compile|tsc\b|webpack|vite build|next build|pytest|"
    r"python.*-m\s+pytest|npm test|yarn test|swift test|cargo test)\b",
    re.IGNORECASE,
)
CORRECTION_CUES = re.compile(
    r"(wrong|not that|instead|don't|do not|should|fix|only|focus|不要|不是|不对|改成|换成|重新|先别)",
    re.IGNORECASE,
)
ABANDON_CUES = re.compile(
    r"(stop|skip|forget it|never mind|later|算了|不用了|先不|停下)",
    re.IGNORECASE,
)
MANUAL_REDO_CUES = re.compile(
    r"(manual|manually|i[' ]?ll|i will|myself|我自己|我来|直接改|直接做|手动)",
    re.IGNORECASE,
)
ADOPT_CUES = re.compile(
    r"(looks good|thanks|continue|go ahead|ship it|好的|继续|不错|可以了|看起来可以)",
    re.IGNORECASE,
)
TURNS_USED_PATTERN = re.compile(r"turns?\s*used[:=]?\s*(\d+)", re.IGNORECASE)
TURN_RATIO_PATTERN = re.compile(r"(\d+)\s*/\s*(\d+)")
COMMAND_NAME_PATTERN = re.compile(r"<command-name>(/[^<]+)</command-name>")


def parse_claude_session(filepath):
    """Parse a Claude Code JSONL file and return unified session summary dict."""
    session_id = None
    cwd = None
    branch = None
    model = None
    timestamps = []
    user_turns = 0
    assistant_turn_counter = 0
    total_input = 0
    total_output = 0
    cache_read = 0
    cache_create = 0
    tool_calls = Counter()
    tool_sequence = []
    files_read = set()
    files_edited = set()
    files_created = set()
    repeated_edits = Counter()
    bash_errors = 0
    build_attempts = 0
    build_failures = 0
    user_prompts = []
    user_prompt_events = []

    assistant_turns = []
    assistant_turns_by_id = {}
    tool_uses = {}
    plugin_event_order = []
    # Phase 5: per-tool-use record context (cwd, isSidechain)
    tool_use_record_cwd = {}   # tool_use_id -> cwd at time of tool_use
    tool_use_is_sidechain = {}  # tool_use_id -> bool
    # Phase 5: stack of active outer Agent tool_use_ids for nested-skill detection
    agent_stack = []
    # Phase 5: session effort_level (from user prompt entry metadata)
    effort_level = None

    with open(filepath, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            rtype = record.get("type")
            ts = record.get("timestamp")
            if ts:
                timestamps.append(ts)

            if not session_id and record.get("sessionId"):
                session_id = record["sessionId"]
            if not cwd and record.get("cwd"):
                cwd = record["cwd"]
            if not branch and record.get("gitBranch"):
                branch = record["gitBranch"]

            # Phase 5: capture record-level isSidechain flag
            record_is_sidechain = bool(record.get("isSidechain", False))
            # Phase 5: capture record-level cwd (may differ from session cwd in worktree switches)
            record_cwd = record.get("cwd")

            if rtype == "user":
                user_turns += 1
                msg = record.get("message", {})
                content = msg.get("content", "")
                text = _extract_user_text(content).strip()
                if text:
                    if len(user_prompts) < 10:
                        user_prompts.append(text[:500])
                    user_prompt_events.append(
                        {
                            "turn": user_turns,
                            "timestamp": ts,
                            "text": text,
                        }
                    )

                # Phase 5: capture effort.level from user message metadata
                # effort field lives at message["effort"]["level"] per fixture structure
                if effort_level is None:
                    msg_effort = msg.get("effort")
                    if isinstance(msg_effort, dict) and msg_effort.get("level"):
                        effort_level = msg_effort["level"]

                if isinstance(content, list):
                    for block in content:
                        if not isinstance(block, dict) or block.get("type") != "tool_result":
                            continue
                        tool_use_id = block.get("tool_use_id", "")
                        result_text = _extract_tool_result_text(block)
                        is_error = bool(block.get("is_error", False))
                        if tool_use_id in tool_uses:
                            _attach_tool_result(
                                tool_uses[tool_use_id],
                                result_text=result_text,
                                is_error=is_error,
                                result_at=ts,
                            )
                            _attach_turn_tool_result(
                                assistant_turns_by_id[tool_uses[tool_use_id]["assistant_message_id"]],
                                tool_use_id,
                                result_text,
                                is_error,
                            )
                            bash_errors, build_attempts, build_failures = _update_bash_quality_metrics(
                                tool_uses[tool_use_id],
                                result_text,
                                is_error,
                                current_errors=(bash_errors, build_attempts, build_failures),
                            )
                            # Phase 5: when tool_result for an Agent arrives, pop from stack
                            if (tool_use_id in tool_uses
                                    and tool_uses[tool_use_id].get("tool_name") == "Agent"
                                    and agent_stack and agent_stack[-1] == tool_use_id):
                                agent_stack.pop()

            elif rtype == "assistant":
                msg = record.get("message", {})
                msg_id = msg.get("id") or f"assistant-{assistant_turn_counter + 1}"
                if msg_id not in assistant_turns_by_id:
                    assistant_turn_counter += 1
                    assistant_turn = {
                        "turn": assistant_turn_counter,
                        "message_id": msg_id,
                        "timestamp": ts,
                        "text_parts": [],
                        "tool_uses": [],
                    }
                    assistant_turns_by_id[msg_id] = assistant_turn
                    assistant_turns.append(assistant_turn)

                if not model and msg.get("model"):
                    model = msg["model"]

                usage = msg.get("usage", {})
                total_input += usage.get("input_tokens", 0)
                total_output += usage.get("output_tokens", 0)
                cache_read += usage.get("cache_read_input_tokens", 0)
                cache_create += usage.get("cache_creation_input_tokens", 0)

                for block in msg.get("content", []):
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            assistant_turns_by_id[msg_id]["text_parts"].append(text)
                    elif block.get("type") == "tool_use":
                        tool_name = block.get("name", "")
                        tool_id = block.get("id", "")
                        inp = block.get("input", {})
                        tool_calls[tool_name] += 1
                        tool_sequence.append(tool_name)
                        _track_file_ops(
                            tool_name,
                            inp,
                            files_read,
                            files_edited,
                            files_created,
                            repeated_edits,
                        )
                        tool_summary = {
                            "name": tool_name,
                            "id": tool_id,
                            "input": _compact_tool_input(tool_name, inp),
                            "result_text": "",
                            "result_ok": 1,
                        }
                        assistant_turns_by_id[msg_id]["tool_uses"].append(tool_summary)

                        event = _build_tool_use_event(
                            session_id=session_id,
                            message_id=msg_id,
                            timestamp=ts,
                            tool_name=tool_name,
                            tool_id=tool_id,
                            tool_input=inp,
                        )
                        # Phase 5: store record-level context for finalization
                        tool_use_record_cwd[tool_id] = record_cwd
                        tool_use_is_sidechain[tool_id] = record_is_sidechain
                        # Phase 5: capture current agent stack snapshot for nested detection
                        event["_agent_stack_snapshot"] = list(agent_stack)
                        tool_uses[tool_id] = event
                        if tool_name in {"Skill", "Agent"}:
                            plugin_event_order.append(tool_id)
                            # Phase 5: push Agent tool_use_id onto stack AFTER recording snapshot
                            if tool_name == "Agent":
                                agent_stack.append(tool_id)

    time_start = timestamps[0] if timestamps else None
    time_end = timestamps[-1] if timestamps else None
    duration_min = _calc_duration_min(time_start, time_end)

    cache_hit_rate = None
    total_all_input = total_input + cache_read
    if total_all_input > 0:
        cache_hit_rate = round(cache_read / total_all_input, 3)

    plugin_events = []
    for idx, tool_use_id in enumerate(plugin_event_order):
        event = dict(tool_uses[tool_use_id])
        next_invoked_at = None
        if idx + 1 < len(plugin_event_order):
            next_invoked_at = tool_uses[plugin_event_order[idx + 1]].get("invoked_at")
        event["post_dispatch_signals"] = _compute_post_dispatch_signals(
            user_prompt_events,
            event.get("invoked_at"),
            next_invoked_at,
        )

        # Phase 5: populate 4 new columns
        event_tool_id = event.get("tool_use_id", tool_use_id)
        ev_cwd = tool_use_record_cwd.get(event_tool_id)
        ev_is_sidechain = tool_use_is_sidechain.get(event_tool_id, False)
        ev_stack = event.pop("_agent_stack_snapshot", [])

        # cwd: per-event record cwd (may differ from session cwd in worktree switches)
        event["cwd"] = ev_cwd

        # duration_ms: tool_result.timestamp - tool_use.timestamp in milliseconds
        invoked_at = event.get("invoked_at")
        result_at = event.get("_result_at")
        event.pop("_result_at", None)
        if invoked_at and result_at:
            try:
                t_start = datetime.fromisoformat(invoked_at.replace("Z", "+00:00"))
                t_end = datetime.fromisoformat(result_at.replace("Z", "+00:00"))
                event["duration_ms"] = int((t_end - t_start).total_seconds() * 1000)
            except (ValueError, TypeError):
                event["duration_ms"] = None

        # invocation_trigger and parent_tool_use_id
        # Scan user_prompt_events in window (prev_event_invoked_at, current_invoked_at] for a
        # slash command. Use ANY-match semantics — defends against Claude Code's same-timestamp
        # preamble injection ("Base directory for this skill: ...") which would otherwise
        # overshadow the actual slash text under a "last-match-wins" loop and silently
        # mislabel every user-slash invocation as claude-proactive.
        invoked_ts = event.get("invoked_at")
        prev_invoked_ts = None
        if idx > 0:
            prev_invoked_ts = tool_uses[plugin_event_order[idx - 1]].get("invoked_at")

        window_user_texts = []
        for upe in user_prompt_events:
            ts = upe.get("timestamp")
            if not ts or not invoked_ts:
                continue
            if ts > invoked_ts:
                break
            if prev_invoked_ts is None or ts > prev_invoked_ts:
                window_user_texts.append(upe.get("text", ""))

        # preceding_user_text: LAST non-empty message in window (for proactive_trigger excerpt)
        preceding_user_text = ""
        for text in reversed(window_user_texts):
            if text:
                preceding_user_text = text
                break

        any_slash_in_window = any(COMMAND_NAME_PATTERN.search(t) for t in window_user_texts)

        if any_slash_in_window:
            event["invocation_trigger"] = "user-slash"
            event["parent_tool_use_id"] = None
        elif ev_is_sidechain or ev_stack:
            event["invocation_trigger"] = "nested-skill"
            event["parent_tool_use_id"] = ev_stack[-1] if ev_stack else None
        else:
            event["invocation_trigger"] = "claude-proactive"
            event["parent_tool_use_id"] = None

        # Phase 5: proactive trigger row for claude-proactive skill events
        if (event.get("invocation_trigger") == "claude-proactive"
                and event.get("component_type") == "skill"):
            has_correction = _has_correction_within_3_turns(event, user_prompt_events, invoked_ts)
            trigger = {
                "user_prompt_excerpt": preceding_user_text[:500],
                "skill_description_snapshot": None,  # filled by reader join in Phase 2
                "triggered_correctly": 0 if has_correction else 1,
            }
            event["_proactive_trigger"] = trigger

        plugin_events.append(event)

    return {
        "session_id": session_id or os.path.splitext(os.path.basename(filepath))[0],
        "source": "claude-code",
        "project": os.path.basename(cwd) if cwd else None,
        "project_path": cwd,
        "branch": branch,
        "model": model,
        "time": {
            "start": time_start,
            "end": time_end,
            "duration_min": duration_min,
        },
        "turns": {
            "user": user_turns,
            "assistant": assistant_turn_counter,
        },
        "tokens": {
            "input": total_input,
            "output": total_output,
            "cache_read": cache_read,
            "cache_create": cache_create,
            "cache_hit_rate": cache_hit_rate,
        },
        "tools": {
            "distribution": dict(tool_calls),
            "total_calls": sum(tool_calls.values()),
            "sequence": tool_sequence,
        },
        "files": {
            "read": sorted(files_read),
            "edited": sorted(files_edited),
            "created": sorted(files_created),
        },
        "quality": {
            "repeated_edits": {f: c for f, c in repeated_edits.items() if c > 2},
            "bash_errors": bash_errors,
            "build_attempts": build_attempts,
            "build_failures": build_failures,
        },
        "assistant_turns": [_finalize_assistant_turn(turn) for turn in assistant_turns],
        "plugin_events": plugin_events,
        "ai_behavior_audit": [],
        "analyzer_version": ANALYZER_VERSION,
        "session_dna": "mixed",
        "user_prompts": user_prompts,
        "task_summary": "",
        "corrections": [],
        "prompt_assessments": [],
        "process_gaps": [],
        # Phase 5: session-level effort_level (from user prompt entry metadata)
        # AWAITING future Claude Code version emit effort.level in JSONL;
        # design doc §5b E4 mentions hook-level emission but JSONL not yet observed
        # in 2.1.143. Parser keeps schema column ready, writes NULL until field appears.
        "effort_level": effort_level,
    }


def _extract_user_text(content):
    """Extract plain text from user message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                texts.append(block.get("text", ""))
            elif isinstance(block, str):
                texts.append(block)
        return " ".join(texts)
    return ""


def _extract_tool_result_text(block):
    """Extract text content from a tool_result block."""
    content = block.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        return "\n".join(texts)
    return ""


def _looks_like_error(text):
    """Check if tool result text indicates an error (exit code != 0)."""
    if not text:
        return False
    if "Exit code" in text and "Exit code 0" not in text:
        return True
    return False


def _track_file_ops(tool_name, inp, files_read, files_edited, files_created, repeated_edits):
    """Track file operations from tool calls."""
    if tool_name == "Read":
        path = inp.get("file_path")
        if path:
            files_read.add(path)
    elif tool_name == "Edit":
        path = inp.get("file_path")
        if path:
            files_edited.add(path)
            repeated_edits[path] += 1
    elif tool_name == "Write":
        path = inp.get("file_path")
        if path:
            files_created.add(path)


def _calc_duration_min(start_str, end_str):
    """Calculate duration in minutes between two ISO timestamps."""
    if not start_str or not end_str:
        return None
    try:
        start = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        delta = (end - start).total_seconds() / 60
        return round(delta, 1)
    except (ValueError, TypeError):
        return None


def _compact_tool_input(tool_name, tool_input):
    """Compact a tool input payload for schema output and LLM audit context."""
    if not isinstance(tool_input, dict):
        return tool_input
    if tool_name in {"Read", "Edit", "Write"}:
        keys = ("file_path", "old_string", "new_string")
    elif tool_name == "Bash":
        keys = ("command",)
    elif tool_name in {"Skill", "Agent"}:
        keys = ("command", "skill", "skill_name", "subagent_type", "agent_type",
                "task", "prompt", "args", "model", "max_turns")
    else:
        keys = tuple(tool_input.keys())
    compact = {}
    for key in keys:
        if key in tool_input:
            compact[key] = tool_input[key]
    return compact or tool_input


def _build_tool_use_event(session_id, message_id, timestamp, tool_name, tool_id, tool_input):
    """Create a normalized tool-use event record."""
    event = {
        "session_id": session_id,
        "tool_use_id": tool_id,
        "tool_name": tool_name,
        "assistant_message_id": message_id,
        "invoked_at": timestamp,
        "input_text": json.dumps(tool_input, ensure_ascii=False, sort_keys=True) if isinstance(tool_input, dict) else str(tool_input),
        "result_text": "",
        "result_ok": 1,
        # Phase 5 new fields (populated during finalization)
        "invocation_trigger": None,
        "duration_ms": None,
        "parent_tool_use_id": None,
        "cwd": None,
    }
    if tool_name in {"Skill", "Agent"}:
        plugin, component = _extract_plugin_component(tool_name, tool_input)
        agent_turns_used, agent_max_turns = _extract_agent_turn_counts("", tool_input)
        event.update(
            {
                "component_type": "skill" if tool_name == "Skill" else "agent",
                "plugin": plugin,
                "component": component,
                "agent_turns_used": agent_turns_used,
                "agent_max_turns": agent_max_turns,
                "model_override": tool_input.get("model") if isinstance(tool_input, dict) else None,
            }
        )
    return event


def _extract_plugin_component(tool_name, tool_input):
    """Split plugin namespace and component leaf from a Skill/Agent payload."""
    raw_name = None
    if isinstance(tool_input, dict):
        candidates = (
            tool_input.get("command"),
            tool_input.get("skill"),         # current Claude Code 2.1.x Skill input field
            tool_input.get("skill_name"),    # legacy backwards compat
            tool_input.get("subagent_type"), # current Claude Code 2.1.x Agent input field
            tool_input.get("agent_type"),    # legacy backwards compat
            tool_input.get("name"),
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate:
                raw_name = candidate
                break
    if not raw_name:
        return None, tool_name.lower()
    if ":" in raw_name:
        plugin, component = raw_name.split(":", 1)
        return plugin, component
    return None, raw_name


def _extract_agent_turn_counts(result_text, tool_input):
    """Extract used/max turn counts from agent input and result text."""
    max_turns = None
    if isinstance(tool_input, dict):
        max_turns = tool_input.get("max_turns") or tool_input.get("maxTurns")

    used_turns = None
    if result_text:
        ratio_match = TURN_RATIO_PATTERN.search(result_text)
        if ratio_match:
            used_turns = int(ratio_match.group(1))
            if max_turns is None:
                max_turns = int(ratio_match.group(2))
        else:
            used_match = TURNS_USED_PATTERN.search(result_text)
            if used_match:
                used_turns = int(used_match.group(1))
    return used_turns, max_turns


def _attach_tool_result(event, result_text, is_error, result_at=None):
    """Update a normalized tool-use event with tool_result content."""
    if result_text:
        event["result_text"] = f"{event['result_text']}\n{result_text}".strip() if event["result_text"] else result_text
    if is_error:
        event["result_ok"] = 0
    # Phase 5: store tool_result timestamp for duration_ms calculation
    if result_at and "_result_at" not in event:
        event["_result_at"] = result_at
    if event.get("component_type") == "agent":
        used_turns, max_turns = _extract_agent_turn_counts(event.get("result_text", ""), {"max_turns": event.get("agent_max_turns")})
        if used_turns is not None:
            event["agent_turns_used"] = used_turns
        if max_turns is not None:
            event["agent_max_turns"] = max_turns


def _has_correction_within_3_turns(event, all_user_turns, invoked_ts=None):
    """Return True if a CORRECTION_CUES match appears in the 3 user turns after this event.

    Signature accepts (event, all_user_turns) — when invoked_ts is None (e.g. unit tests
    passing an empty event {}), the list is treated directly as the following turns.
    When invoked_ts is provided, turns are filtered to those after invoked_ts.
    """
    if invoked_ts is None:
        # Unit test mode: treat all_user_turns directly as the following turns
        following = all_user_turns[:3]
    else:
        following = []
        for upe in all_user_turns:
            if upe.get("timestamp") and upe["timestamp"] > invoked_ts:
                following.append(upe)
                if len(following) >= 3:
                    break
    return any(CORRECTION_CUES.search(upe.get("text", "")) for upe in following)


def _attach_turn_tool_result(assistant_turn, tool_use_id, result_text, is_error):
    """Attach correlated tool_result text to the assistant turn summary."""
    for tool_summary in assistant_turn["tool_uses"]:
        if tool_summary.get("id") == tool_use_id:
            if result_text:
                current = tool_summary.get("result_text", "")
                tool_summary["result_text"] = f"{current}\n{result_text}".strip() if current else result_text
            if is_error:
                tool_summary["result_ok"] = 0
            return


def _update_bash_quality_metrics(tool_event, result_text, is_error, current_errors):
    """Update aggregate bash/build metrics from a correlated Bash tool result."""
    bash_errors, build_attempts, build_failures = current_errors
    if tool_event.get("tool_name") != "Bash":
        return bash_errors, build_attempts, build_failures
    command = ""
    try:
        command = json.loads(tool_event.get("input_text") or "{}").get("command", "")
    except json.JSONDecodeError:
        pass
    if BUILD_PATTERNS.search(command):
        build_attempts += 1
        if is_error or _looks_like_error(result_text):
            build_failures += 1
    elif is_error or _looks_like_error(result_text):
        bash_errors += 1
    return bash_errors, build_attempts, build_failures


def _compute_post_dispatch_signals(user_prompt_events, invoked_at, next_invoked_at=None):
    """Compute 3-user-turn post-dispatch signals for a Skill/Agent invocation."""
    following = [
        event for event in user_prompt_events
        if _timestamp_after(event.get("timestamp"), invoked_at)
        and not _timestamp_at_or_after(event.get("timestamp"), next_invoked_at)
    ][:3]
    texts = [event.get("text", "") for event in following]
    correction = any(CORRECTION_CUES.search(text) for text in texts)
    abandon = any(ABANDON_CUES.search(text) for text in texts)
    repeated_manual = any(MANUAL_REDO_CUES.search(text) for text in texts)
    adopted = bool(texts) and not correction and not abandon and not repeated_manual and any(
        ADOPT_CUES.search(text) for text in texts
    )
    return {
        "user_correction_within_3_turns": correction,
        "user_abandoned_topic": abandon,
        "user_repeated_manually": repeated_manual,
        "result_adopted": adopted,
    }


def _timestamp_after(candidate, reference):
    """Return True when candidate timestamp is after reference."""
    if not candidate or not reference:
        return False
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")) > datetime.fromisoformat(reference.replace("Z", "+00:00"))
    except ValueError:
        return False


def _timestamp_at_or_after(candidate, reference):
    """Return True when candidate timestamp is at or after reference."""
    if not candidate or not reference:
        return False
    try:
        return datetime.fromisoformat(candidate.replace("Z", "+00:00")) >= datetime.fromisoformat(reference.replace("Z", "+00:00"))
    except ValueError:
        return False


def _finalize_assistant_turn(turn):
    """Convert assistant turn builder state into JSON-ready output."""
    return {
        "turn": turn["turn"],
        "timestamp": turn["timestamp"],
        "text": "\n".join(turn["text_parts"]).strip(),
        "tool_uses": turn["tool_uses"],
    }


def _flatten_for_db(result):
    """Flatten nested parser output for sessions.db upsert."""
    flat = dict(result)
    flat["time_start"] = result.get("time", {}).get("start")
    flat["time_end"] = result.get("time", {}).get("end")
    flat["duration_min"] = result.get("time", {}).get("duration_min")
    flat["turns_user"] = result.get("turns", {}).get("user")
    flat["turns_asst"] = result.get("turns", {}).get("assistant")
    flat["tokens_in"] = result.get("tokens", {}).get("input")
    flat["tokens_out"] = result.get("tokens", {}).get("output")
    flat["cache_read"] = result.get("tokens", {}).get("cache_read")
    flat["cache_create"] = result.get("tokens", {}).get("cache_create")
    flat["cache_hit_rate"] = result.get("tokens", {}).get("cache_hit_rate")
    return flat


def main():
    parser = argparse.ArgumentParser(
        description="Parse a Claude Code session JSONL file into unified JSON"
    )
    parser.add_argument(
        "--input", required=True, help="Path to Claude Code session JSONL file"
    )
    parser.add_argument(
        "--output", default=None, help="Output JSON file (default: stdout)"
    )
    parser.add_argument(
        "--sqlite-db",
        default=None,
        help="Path to sessions.db to upsert results",
    )
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Invoke session-parser agent for dimension enrichment",
    )
    args = parser.parse_args()

    result = parse_claude_session(args.input)

    if args.sqlite_db:
        sys.path.insert(0, str(Path(__file__).parent))
        import sessions_db

        sessions_db.DB_PATH = Path(args.sqlite_db)
        sessions_db.init_db()
        flat = _flatten_for_db(result)
        sessions_db.upsert_session(result["session_id"], flat)

        tool_list = []
        for name in result.get("tools", {}).get("sequence", []):
            tool_list.append({"tool_name": name, "file_path": None, "is_error": 0})
        sessions_db.upsert_tool_calls(result["session_id"], tool_list)

        for plugin_event in result.get("plugin_events", []):
            sessions_db.upsert_plugin_event(plugin_event)

    if args.enrich:
        result, warning = apply_enrichment(result, db_path=args.sqlite_db)
        if warning:
            print(f"Warning: {warning}", file=sys.stderr)

    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
    else:
        print(output)


if __name__ == "__main__":
    main()
