# podcast-studio

personal-os fleet-member podcast production plugin. The headline is a
Claude-driven 6-persona pipeline (`/podcast morning` / `/podcast evening`) that
reads your Vault, runs prep, dispatches six persona subagents in sequence, and
produces a reader-facing script + mp3 with stance-card continuity across
episodes. It is backed by a vendored `prep` skill (topic dedup, angle rotation,
MinHash, cross-domain / self-past vault pulls); TTS is dispatched to the
personal-os fleet's `tts` skill (Volcengine + MiniMax unified, with
chunk/merge). Inputs arrive via the personal-os IEF exchange. Wired together
by a single Python config module and a thin bash env shim.

**Fleet member** — podcast-studio participates in the personal-os marketplace
(prep remains vendored in-tree for a config patch + upstream drift; TTS is
consumed as a fleet skill). Drop a `~/.podcast-studio/config.yaml` and run.

## Install

**System prerequisites** — these must be on your `PATH`:
- `ffmpeg` — TTS chunk merge / concat (long-form audio assembly)
- `curl` — TTS vendor API calls (Volcengine / MiniMax)

First install the Python dependency (PyYAML is required for the stance-card
continuity mechanics):

```
pip install -r requirements.txt
```

Point Claude Code at this marketplace:

```
/plugin marketplace add /path/to/podcast-studio
```

Then enable the plugin:

```
/plugin enable podcast-studio
```

## Config

Create `~/.podcast-studio/config.yaml` (see `config.example.yaml` in this plugin
for the full schema):

```yaml
vault:
  subjective_dir: ~/Obsidian/PKOS/30-Logs/30-Journal
  news_dir: ~/Obsidian/PKOS/40-Inputs/News
  output_dir: ~/Obsidian/PKOS/50-Outputs/podcast

tts:
  provider: volc        # volc | minimax
  host_voice: BV001_streaming
```

The three vault directories must exist on disk; the plugin fails-closed
otherwise (it will not silently fall back to a default location).

TTS credentials live in your shell environment, **not** in the config file:
`VOLC_TTS_APPID` + `VOLC_TTS_TOKEN` for Volcengine (plus optional `VOLC_IAM_*`
for usage/quota), and `MINIMAX_API_KEY` for MiniMax (plus `MM_SUB` / the `sk-cp`
subscription key for quota checks). Export them in your shell rc.

## Cadence with `/loop`

Run the full pipeline on a schedule via Claude Code's `/loop` (the pipeline
calls prep and tts internally — you don't schedule those separately):

```
/loop 24h /podcast morning
```

For both shows, run morning in the AM and evening in the PM (the evening show
carries the morning's open questions forward):

```
/podcast morning      # AM
/podcast evening       # PM
```

## What lives where

- `skills/podcast/` — the orchestrator skill: the 17-step `/podcast morning|evening`
  pipeline (config → continuity read → collection → drafts A/B/C → critique →
  polish → score → finalize → broadcast script → TTS → stance card).
- `agents/` — the seven persona subagents the pipeline dispatches in sequence:
  达芬奇/davinci (collection + drafting), 老黑/laohei (critique), 快刀青衣/kuaidao
  (polish + finalize), 钱钟书/qianzhongshu (structured scoring), 质检员/zhijianyuan
  (structured-only data fact-check gate between 定稿 and 口播稿), 卞旸/bianyang
  (broadcast-script rewrite), 周杰伦/jay (TTS).
- `lib/episode.py`, `lib/stance.py`, `lib/bible.py`, `lib/throughline.py`,
  `lib/factcheck.py` — pipeline helpers (naming + artifact gate + draft
  selection; stance-card continuity; Character Bible; throughline obsession;
  fact-check gate = source parsing + claim traceability + the coded
  check_factcheck). Python modules — imported, not run as CLIs.
- `lib/config.py` — single source of truth for `~/.podcast-studio/config.yaml`.
- `lib/podcast-env.sh` — exports `tts.*` from config into the env vars the
  fleet `tts` skill's scripts expect (incl. `TTS_LEDGER_DIR`).
- `skills/podcast-studio-prep/` — vendored from personal-os `podcast-prep` @ 0.8.0
  (see `skills/podcast-studio-prep/VENDORED.md`; remains vendored for the
  `_resolve_vault_root` config patch + upstream drift to 0.10.0).
- TTS is not vendored — the pipeline dispatches to the personal-os fleet's
  `tts` skill (from `tts-toolkit`). `references/voice-catalog.md` is a
  snapshot of the upstream voice-catalog, re-synced when upstream voices
  change.

## Fleet membership

podcast-studio is a personal-os fleet member (sibling plugin of `podcast-prep`
and `tts-toolkit` in the same marketplace). Inputs arrive via the personal-os
IEF exchange; TTS synthesis is consumed as the fleet `tts` skill. Prep remains
vendored in-tree for its config patch and upstream drift. Configure
`~/.podcast-studio/config.yaml` and the TTS credential env, and run.
