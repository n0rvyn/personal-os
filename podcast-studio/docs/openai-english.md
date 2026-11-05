# English production in the existing Podcast Studio pipeline

The existing `lib.runner.run_pipeline` and `lib.pipeline` step table remain the
only morning/evening orchestrator. There is no second pipeline, English output
root, cloned continuity store, profile file or English activation command.
The ordinary `/podcast morning` / `/podcast evening` invocation reads the existing
project configuration and dispatches its selected service/language. The config
handler also recognizes the existing project `config.yaml` automatically when
no explicit config/environment selection or Personal OS anchor takes precedence.

## Name is part of project configuration and creation

`podcast.name` is a required field when creating a podcast project. The existing
config handler now supports the name phase:

```sh
python3 -m lib.config --init /selected/project/config.yaml \
  --name "Your podcast name" --from-config /selected/config-template.yaml
```

This requires an explicit template, stores the supplied name, creates no
folders, refuses an existing config target, and runs no model. The config-editor schema requires a nonempty name when the identity section is
present or OpenAI production is selected; legacy unnamed configs remain valid. No program name is hardcoded in
runtime code. An optional `podcast.english_name` provides an English display
name when a project retains an existing Chinese show name.

Backward compatibility: old configs without `podcast` continue their original
Chinese behavior and are never automatically rewritten or assigned a guessed
name. Before selecting OpenAI English production, add the real name to that
same file. The user's requested English name is **By the Way**; this belongs
in the reviewed project config, not in a parallel pipeline or provider constant.

## Existing paths stay authoritative

The inspected working project is `~/Code/Content/Podcasts/`, whose existing
`config.yaml` specifies:

- Sources: `~/Obsidian/PKOS/20-Ideas/观点心得`
- News: `~/Obsidian/PKOS/60-Digests`
- Output root: `~/Code/Content/Podcasts/`
- Existing derived directories: `episodes/`, `state/`, `reports/`
- Voice reference: `~/Code/Content/Podcasts/voice-corpus`

These are verified current paths, not new defaults. The reusable plugin never
hardcodes them. Originals stay in their original language and location.

English manuscripts/audio use `{date}-{title}-en.md/.mp3` in the existing
`episodes/`. English stance cards use `{date}-{morning|evening}-en.stance.yaml`,
with `episode.language: en` and distinct bet IDs. Existing Chinese names and
files retain their naming. Both languages read the same existing stance history
and memory stores. No snapshot/import/migration is performed. The English run
reuses an existing Character Bible without overwriting it; source text is voice
reference data, not content instructions. Scorecards and scratch identifiers
also carry the English suffix. An existing different final file is never
clobbered; identical completed outputs can be recovered. Runs sharing the same
state directory are serialized by a file lock to prevent lost continuity writes.
Legacy Chinese resume excludes English/OpenAI scratch folders and rejects
explicit reuse of their identity or request receipts.

## Reviewed configuration change

Keep the existing `vault:` and `tts:` sections exactly where they are. A proposed
addition to the same config is:

```yaml
podcast:
  name: 拾余光
  english_name: By the Way
production:
  provider: openai
  language: en
  model: gpt-4.1-mini-2025-04-14
  speech_model: gpt-realtime-2.1-mini
  voice: marin
  source_authorized: false
  budget_ledger: EXISTING_VERIFIED_SHARED_LEDGER
  budget_timezone: APPROVED_IANA_TIMEZONE
  per_call_limit_usd: 0
  daily_limit_usd: 0
```

This is a review proposal; the actual project config has not been changed.
The Chinese name above comes from the existing show; change it only if the owner
selects a different identity. No output-directory setting is added.

`production` chooses the provider/language inside the ordinary orchestrator.
There is no `--openai-profile` or extra invocation to activate an English pipeline.
Omitting `production` keeps legacy behavior; `claude/zh` keeps the established
Chinese provider path. The paper line remains outside this scoped change.

`source_authorized` must reflect actual owner authorization for the selected
configured sources to be sent to OpenAI. Installing code is not that approval.
The zero caps/placeholders intentionally prevent paid calls until actual limits,
timezone and daily commitments are verified. The shared locked ledger must
already exist; the adapter never invents a zero opening balance or resets a day.
Each reservation includes prior commitments and is fsynced before a request.
A durable in-flight receipt is written before transport starts. Completed raw
responses and intermediate tool conversations are checkpointed locally; a stale
receipt after interruption blocks automatic repeat billing and requires outcome
review. Failed/uncertain requests keep reservations. The ledger is rechecked
before every request and immediately before Realtime generation, including after
the WebSocket handshake, so crossing midnight stops on the old verified day. This ledger does not query OpenAI billing or account for jobs that ignore
it; reconcile actual commitments before using it. `OPENAI_API_KEY` is process
configuration only; no key is read from YAML or stored by this code.

## Narration model and transport

OpenAI's [2026-10-01 deprecation notice](https://developers.openai.com/api/docs/deprecations)
marks `tts-1`, `tts-1-hd`, and `gpt-4o-mini-tts` deprecated, with shutdown on
2027-01-06. It recommends **gpt-realtime-2.1-mini**. That model uses the
[Realtime WebSocket API](https://developers.openai.com/api/docs/guides/voice-websockets?api=realtime),
not `/v1/audio/speech`. Simply swapping a model string on the old endpoint
would be incorrect.

The existing TTS station now uses a bounded text-input Realtime response for
each small narration chunk, with one voice, PCM 24 kHz output and no tools,
microphone input, conversation history or ephemeral credential creation.
It requests an exact reading, validates completed status and the returned
transcript word boundaries against the supplied English text, caches successful PCM chunks,
and encodes/decode-checks the final MP3 with ffmpeg. It adds an AI-voice disclosure.
The websocket-client dependency is declared and checked before paid narration.
The [official model pricing](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini)
is $0.60/M text input tokens and $20/M audio output tokens. Each request has a
hard 4096-output-token ceiling and reserves all output at the higher audio rate;
the entire remaining narration job is reserved before its first request.
No approximate per-minute rate is treated as a hard billing ceiling.

## What remains unchanged and unverified

The deterministic DAG, persona separation, A/B/C IDs, scoring math, fact-check
and append-only stance writer remain authoritative. The Responses adapter has
controlled local read/prep tools rather than assuming Claude CLI tools transfer.
The collector must use three actual prep results; the host appends those briefs.
The Bible remains isolated and structured scoring remains Bible-free.
English checks require 2340 words (target 2600), Latin letters and English
function words. These are heuristics, not a semantic language guarantee. Existing
lexical memory checks are not guaranteed to detect cross-language paraphrases.
Live English quality, exact narration fidelity, account access and full-episode
operation remain unverified; no paid provider call or episode is part of review.

The optional existing `studio-dev` source-loader link remains developer tooling;
it is not a new pipeline, required English command or updated installed release.
Ordinary installed-plugin behavior changes only after the owner reviews and
approves the source/config release. The old JSON profile example is retained only as superseded local work, is
excluded from the candidate release, and is no longer read by the runner.

## Offline regression verification

Run `python3 -m pip install pytest jsonschema` in a test environment, then
`python3 -m pytest lib/tests`. Provider fixtures are synthetic; no live credential
or paid call is required. Review regressions exercise actual evening prep shape,
legacy/English filename collisions, budget rollover, pre-generation checks,
interrupted requests and parallel receipts, title drift, transcript boundaries,
and legacy config-schema compatibility. These tests do not establish live
editorial quality or provider voice fidelity.
