---
name: podcast-prep
description: "Use when the user says '/podcast-prep', 'prep podcast', 'check podcast topics', 'finalize podcast script', or when a podcast generation template invokes it. Provides two subcommands: 'check' returns a structured brief (approved topics + required angle + PKOS note + contrarian source) given candidate topics; 'finalize' MinHash-dedupes the final script against past 7 days and writes topic_log on accept."
model: sonnet
allowed-tools:
  - Read
  - Write
  - Bash
---

## Overview

`podcast-prep` is the orchestrator skill for the daily podcast pipeline. It owns
topic-cooldown logic, angle-slot rotation, MinHash 4-gram script dedup,
PKOS serendipity pulls, and a small reverse-source pool. Writer agents
(达芬奇, 快刀青衣) call `check` for a structured brief before writing and
`finalize` after writing to gate retry vs accept.

**DP-001 A — Caller protocol**: PKOS note selection is the CALLER's responsibility.
达芬奇 must invoke the `pkos:serendipity` SKILL (agent-dispatch mode) and pass
the resulting `{id, title, excerpt}` object as `--pkos-note` to `/podcast-prep check`.
The orchestrator validates the note is present but does NOT pull PKOS itself.
If the note is missing, `check` returns `{"error": "pkos_note required ..."}` and
达芬奇 must retry after invoking pkos:serendipity.

## Subcommands

### check

```
/podcast-prep check \
  --candidates='["topic-tag-1","topic-tag-2"]' \
  --date=YYYY-MM-DD \
  --topic-log={exchange_dir}/podcast-prep/topic_log.yaml \
  --pkos-note='{"id":"PKOS/note-id","title":"Note Title","excerpt":"brief excerpt"}' \
  [--seed=N] \
  [--vault-root=/path/to/PKOS]
```

Returns a JSON brief with:
- `approved_topics`: list of `{topic_tag: str, novelty_score: float, required_angle: str}`
- `pkos_note`: the caller-provided PKOS note, propagated verbatim
- `contrarian_source`: `{source: str, category: str, url: str}` from the curated pool
- `cross_domain_candidates`: PKOS notes from NON-tech domains (philosophy / management / cognition / history / literature / natural-science), one per domain — seeds cross-domain synthesis
- `self_past_candidates`: past notes from `20-Ideas/观点心得/` (the user's recorded viewpoints) and `90-Podcasts/` (past episodes — dated on-record stances), created 7-90 days ago on a topic similar to today's — the writer picks the one whose stance contradicts today's argument. Directory-scoped per the vault directory contract (`pkos/references/vault-directory-contract.md`).
- `named_concept_prompt`: a directive nudging the writer to name an emergent pattern
- `generated_at`: ISO timestamp

On missing or invalid `pkos_note`, returns `{"error": "pkos_note required ...", "approved_topics": [], cross_domain_candidates: [], self_past_candidates: [], named_concept_prompt: "...", ...}` — the error brief keeps the full schema.

`--vault-root` defaults to `pkos_root` (or `exchange_dir`'s parent) in `~/.claude/personal-os.yaml`.

### Insight-density fields — writer protocol

These three fields target the podcast's core KPI: selection → insight → opinion → thought-provocation. They are advisory inputs the writer agent (达芬奇) MUST engage with:

1. **`cross_domain_candidates`** — the writer must weave at least ONE non-tech-domain note into the script as a genuine cross-domain connection (not decoration). The strongest episodes emerge when a tech news item collides with a philosophy / cognition / management note (e.g. an AI-agent story × a Kahneman cognition note).
2. **`self_past_candidates`** — the writer scans these past notes, finds the one whose stance most contradicts today's main argument, and writes a "我 X 天前是这么想的 → 为什么变了 / 哪部分没变" passage. Debating one's past self is the highest-yield insight engine.
3. **`named_concept_prompt`** — the writer attempts to name one emergent pattern (3-5 字, 画面感, 可复用) with a dedicated naming-ritual paragraph. If nothing today merits naming, say so explicitly — do not force it.

Each PKOS reference in the script must carry an origin date ("X 月 X 日的笔记" / "最近读到"); undated references read as decoration, not knowledge connection.

The episode quality bar (4 KPI: 洞察 / 命名 / 跨域 / 思考问句, 1-5 each) is defined in
`references/quality-rubric.md` — shared by Phase-1 manual assessment and the Phase-2
review-editor Role.

**Novelty scoring**: `score = 1 - (matching_days / 7)` where `matching_days` is the
count of past-7-day episodes with the same topic_tag.
- `score < 0.3` → topic dropped (seen 5+ times in past week)
- `0.3 <= score <= 0.7` → topic kept with `pick_unused_angle` (avoids repeating angles)
- `score > 0.7` → topic kept; angle defaults to first in rotation (fully novel)

### finalize

```
/podcast-prep finalize \
  --script=<PATH_TO_FINAL_SCRIPT.md> \
  --topic-log={exchange_dir}/podcast-prep/topic_log.yaml \
  --date=YYYY-MM-DD \
  --approved-topics='[{"topic_tag":"x","required_angle":"y"}]' \
  [--script-archive-dir=<PATH>]
```

Returns:
- `{"action": "accept", "jaccard": float, "topics_appended": int}` — topic_log updated
- `{"action": "retry", "jaccard": float, "reason": str}` — too similar to past script; topic_log NOT updated

**Jaccard threshold**: 0.15 (4-gram character shingles). Scripts more similar than 15%
to any past-7-day script trigger retry. 快刀 must rewrite per the returned `reason`.

## State File Locations

- **topic_log.yaml**: `{exchange_dir}/podcast-prep/topic_log.yaml` (IEF-spec compliant)
  - `exchange_dir` is configured in `~/.claude/personal-os.yaml`
  - Created automatically on first `finalize` accept
- **Script archive** (optional): `~/.adam/roles/快刀青衣/` — writer Role's workspace;
  pass as `--script-archive-dir` to `finalize` for Jaccard dedup against past scripts

## Angle Rotation

Five slots in order: `技术内核 -> 商业影响 -> 用户体验 -> 历史类比 -> 反对意见`.
The orchestrator picks the first angle not seen for that topic_tag in the past 14 days.
When all five angles have been used, rotation wraps back to `技术内核`.

## Contrarian Source Pool

Six curated reverse-source entries spanning: business strategy, finance/macro,
economics/cognition, natural science, rationality, personal knowledge. Selected
deterministically by `seed` (for testing) or randomly per run.

## Examples

### Happy path — check returns full brief

```bash
python3 orchestrator.py check \
  --candidates '["ai-agents","swift6"]' \
  --date 2026-05-19 \
  --topic-log /tmp/topic_log.yaml \
  --pkos-note '{"id":"PKOS/note-42","title":"Slowing Down to Think","excerpt":"..."}'
# Returns:
# {
#   "approved_topics": [{"topic_tag":"ai-agents","novelty_score":1.0,"required_angle":"技术内核"}, ...],
#   "pkos_note": {"id":"PKOS/note-42", ...},
#   "contrarian_source": {"source":"stratechery", ...},
#   "cross_domain_candidates": [{"domain":"philosophy","title":"...","created":"2026-05-18", ...}, ...],
#   "self_past_candidates": [{"title":"...","created":"2026-05-03", ...}, ...],
#   "named_concept_prompt": "命名任务：通读今天的素材 ...",
#   "generated_at": "2026-05-19T00:00:00Z"
# }
```

### Missing pkos_note — error brief

```bash
python3 orchestrator.py check \
  --candidates '["ai-agents"]' \
  --date 2026-05-19 \
  --topic-log /tmp/topic_log.yaml
# Returns: {"error": "pkos_note required — invoke pkos:serendipity SKILL ...", ...}
# 达芬奇 must re-invoke pkos:serendipity and retry with --pkos-note.
```

### Finalize — accept

```bash
python3 orchestrator.py finalize \
  --script /tmp/today-script.md \
  --topic-log /tmp/topic_log.yaml \
  --date 2026-05-19 \
  --approved-topics '[{"topic_tag":"ai-agents","required_angle":"技术内核"}]'
# Returns: {"action": "accept", "jaccard": 0.03, "topics_appended": 1}
# topic_log.yaml updated with today's episode.
```

### Finalize — retry (too similar)

```bash
# Returns: {"action": "retry", "jaccard": 0.87, "reason": "4-gram Jaccard similarity 0.8700 >= threshold 0.15"}
# 快刀 must revise the script to introduce more novel phrasing.
```
