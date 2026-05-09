# podcast-transcript

Creates a TTS-ready daily podcast transcript from PKOS and Personal-OS
artifacts. The skill owns selection, deduplication, and state writes before the
writer agent produces spoken prose.

## Standalone command examples

Create a topic plan for a daily episode:

```bash
SCRATCH=$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/personal_os_config.py --get scratch_dir)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/podcast-transcript/scripts/podcast_sources.py \
  plan --date 2026-05-09 --type daily --max-topics 4 \
  --source-window-days 30 --topic-window-days 14 \
  --output "${SCRATCH}/pkos/podcast-transcript/manual/topic-plan.json"
```

Debug one explicit artifact:

```bash
SCRATCH=$(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/personal_os_config.py --get scratch_dir)
python3 ${CLAUDE_PLUGIN_ROOT}/skills/podcast-transcript/scripts/podcast_sources.py \
  plan --date 2026-05-09 --type daily \
  --source-file ~/Obsidian/PKOS/.exchange/domain-intel/2026-05/example.md \
  --output "${SCRATCH}/pkos/podcast-transcript/manual/topic-plan.json"
```

Commit transcript history after the transcript and manifest exist:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/podcast-transcript/scripts/podcast_sources.py \
  commit --manifest ~/Obsidian/PKOS/.state/podcast-transcript/manifests/2026-05/2026-05-09-daily.json
```

Skill entry point:

```text
/podcast-transcript --type daily --date 2026-05-09 --max-topics 4
```

## Input source priority

The planner discovers source material without depending on a runner:

1. `{exchange_dir}/domain-intel/{YYYY-MM}/*.md`
2. `{exchange_dir}/session-reflect/{YYYY-MM}/*.md`
3. `{exchange_dir}/product-lens/**/*.md`
4. `{vault}/60-Digests/{date}*.md`
5. `{vault}/10-Knowledge/**/*.md`
6. `{vault}/50-References/**/*.md`

`--source-file` bypasses default discovery and reads only the explicit markdown
artifact. The path must resolve under `exchange_dir`, the PKOS vault, or
`scratch_dir`.

## State file formats

Podcast state lives under:

```text
~/Obsidian/PKOS/.state/podcast-transcript/
```

Episode history:

```json
{"episode_id":"daily-2026-05-09","episode_date":"2026-05-09","transcript_path":"...","transcript_hash":"...","topic_keys":["agent-platform"],"source_identities":["source:id:domain-intel:001"]}
```

Source index:

```json
{"source_identity":"source:id:domain-intel:001","episode_id":"daily-2026-05-09","episode_date":"2026-05-09","topic_keys":["agent-platform"]}
```

Topic index:

```json
{"topic_key":"agent-platform","episode_id":"daily-2026-05-09","episode_date":"2026-05-09","source_identities":["source:id:domain-intel:001"]}
```

Manifests live under:

```text
~/Obsidian/PKOS/.state/podcast-transcript/manifests/{YYYY-MM}/{date}-daily.json
```

## Downstream boundary

Rule: downstream steps consume the final transcript and do not make dedup decisions.

Downstream polish, audio rendering, and delivery steps consume the final
transcript and do not make dedup decisions. They must not reselect sources,
drop selected topics, add new topics, or rewrite source/topic history.

Runner-specific templates can call `/podcast-transcript`, but the skill does
not depend on the runner. It remains a standalone PKOS entry point.

Boundary: this skill does not depend on the runner.
