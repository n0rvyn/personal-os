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
and encodes/decode-checks the final MP3 with ffmpeg. It never changes or prepends to the approved spoken script. A separate written
`audio-disclosure.md` is carried into episode show notes with the audio.
The websocket-client dependency is declared and checked before paid narration.
The [official model pricing](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini)
is $0.60/M text input tokens and $20/M audio output tokens. Each request has a
default 1536-output-token ceiling (configurable up to 4096) and reserves all
output at the higher audio rate;
the entire remaining narration job is reserved before its first request.
No approximate per-minute rate is treated as a hard billing ceiling.

## What remains unchanged and unverified

The deterministic DAG, persona separation, A/B/C IDs, scoring math, fact-check
and append-only stance writer remain authoritative. The Responses adapter has
controlled local read/prep tools rather than assuming Claude CLI tools transfer.
The collector must use three actual prep results; the host appends those briefs.
The Bible remains isolated and structured scoring remains Bible-free.
Full English checks default to 2600–3900 words (target 3250), Latin letters and English
function words. These are heuristics, not a semantic language guarantee. Existing
lexical memory checks are not guaranteed to detect cross-language paraphrases.
The first English demo verified account access, seven returned narration
transcripts, PCM acceptance and MP3 decoding. The updated 20–30-minute workflow
and actual learner comprehension have only offline verification; no new paid
provider call is part of the listening-workflow update.

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

## Public news/archive episodes and explicit short length

The existing `production` section can select `source_mode: public` and an exact
`source_manifest` with its `source_manifest_sha256` pinned in that same config. This is source selection inside the same morning/evening DAG,
not another pipeline. Legacy mode remains the default and retains its original
private-viewpoint behavior. The manifest is version 1, lists exact non-hidden
Markdown paths with SHA-256, original HTTPS URL, publication date, title and
`kind: news|archive`, binds each domain/topic candidate to a listed news file,
and identifies one listed archive file plus an editorial question. Declaration
as public is not independent proof: the owner-approved source preparation must
verify the originals first. Recheck manifest/file hashes before reads and requests.

Public mode does not grant a whole-vault or output-directory read root. Read tools
see only manifested files and selected current-run artifacts. Request journals,
private scorecard details, old transcripts, personal notes and voice files are
not readable. It supplies public `archive_context` to the real A/B/C prep command,
sets `pkos_note` to null, and does not perform implicit private recall or IEF reads.
This overrides the old persona/editorial requirement to use a private viewpoint;
it never labels external prose as the host's own experience.

Shared history is retained. Local continuity and deterministic duplicate checks
still read existing history; public provider prompts omit private bets, obsessions,
voice Bible and historical apparatus/thesis summaries. Existing bets are not
invented as settled. The historical semantic thesis judge cannot compare private
text that was not authorized for egress; its existing missing-evidence/fail-soft
behavior remains, while local code checks still run and are recorded. Current
public results are distilled and merged locally by the existing post-publication
stations. Use the ordinary scorecard enforcement flag for the reviewed first run;
no gate is satisfied by fabricated prep or factcheck output.

English defaults are full episodes: `episode_kind: full`, 20/25/30
`min_minutes`/`target_minutes`/`max_minutes`, with `speaking_rate_wpm: 130`.
Word bounds derive from those values: 2600/3250/3900. Explicit word bounds may
refine that range, but cannot silently shrink a full episode into a demo. Short
technical tests require `episode_kind: demo` (defaults 450/520/600 words). The
existing first English 217.65-second artifact remains a demo; it is not rewritten.
The Chinese character floor and existing Chinese files are unchanged.

`listening_level: b1-b2`, `max_average_sentence_words: 18` and
`max_sentence_words: 32` steer clear listening language. These are mechanical
load checks and a writing target, not CEFR certification. Editorial prompts
require first-use term explanations, concrete grounded examples, exact facts,
limitations, counterarguments and complete reasoning. Broadcast word retention
must be at least 95% of the approved manuscript to catch accidental summaries;
this cannot prove semantic preservation. The existing independent critique,
scoring and factcheck remain responsible for evidence and meaning.

TTS reads the supplied script exactly, with no appended disclosure or other
unrequested words. OpenAI requires clear AI-voice disclosure; keep it visible
in written player descriptions/show notes, using the separate disclosure file.
The local publisher refuses to land English audio without that notice. It does
not upload or publish externally. Audio duration is measured after decoding;
full episodes outside the configured range stop with completed chunks retained.
Never use silence padding, repeats, new facts or an automatic paid retry to pass.

Speech still uses `gpt-realtime-2.1-mini`, the configured voice, and fresh
80-word chunks. The default per-chunk output ceiling is now 1536 tokens, above
the demo's observed maximum 851. It is a hard output cap, not a reasoning-effort
setting or a guarantee of completion; incomplete output stops for review. At
3250 ordinary words the conservative narration reservation is about $1.4,
with actual input sizes determining the exact value before any call. Script
generation, other retained commitments and tools also consume budget; no full
episode or free shared balance is promised by this estimate.

The $0.10 per-call and $2 daily ceilings do not authorize credential use or private
data. A real run remains blocked until current-day cross-project commitments and
an authorized process credential are established. Do not create a fresh empty
ledger and declare it verified, reuse Elm's incompatible SQLite as this JSON, or
reinterpret an older cumulative balance as today's spend.

The existing English scorecard station also compares the finalized manuscript and
spoken rewrite. It must return a listening/meaning verdict plus verbatim quote
pairs covering a fact, a caveat and reasoning/opinion. Missing, negative or
untraceable review blocks TTS even when older taste scores are advisory. This
keeps the same station and independent editor; it adds no parallel pipeline.
The judgment remains fallible; mechanical checks alone never certify meaning.
Written disclosure uses a `.txt` companion in `episodes/` so continuity scanners
that read manuscript `*.md` files do not ingest a disclosure as another episode.

The host pins that review to SHA-256 hashes of both exact artifacts. Full TTS
rechecks the review and hashes before reserving or calling the provider; edited
or unreviewed scripts stop, including direct station invocation and resume.


## First English demo: recorded usage and cost calculation (2026-11-06)

The 217.65-second recording is a technical demo, not the full-length show.
Seven completed `gpt-realtime-2.1-mini` responses recorded the usage below in
`reports/first-english-audio-events-2.jsonl` under the existing Podcast project.
The model alias is recorded; an immutable model snapshot/version was not retained.
The detailed accounting is `reports/first-english-exact-audio-cost.json`.

| Billed-token category | Tokens | USD per million | Computed USD |
| --- | ---: | ---: | ---: |
| Uncached text input | 972 | 0.60 | 0.00058320 |
| Cached text input | 64 | 0.06 | 0.00000384 |
| Text output | 1,121 | 2.40 | 0.00269040 |
| Audio output | 4,353 | 20.00 | 0.08706000 |
| **Total** | **1,036 input + 5,474 output** | | **0.09033744** |

Calculation: `(972 × 0.60 + 64 × 0.06 + 1121 × 2.40 + 4353 × 20) / 1,000,000`
`= $0.09033744`, approximately 9.03 US cents. The 215 reported reasoning tokens
are a subset of the 1,121 text-output tokens; do not add them a second time.
Rates: [official model pricing](https://developers.openai.com/api/docs/models/gpt-realtime-2.1-mini).

This is a calculation from returned token usage and published rates, **not a
settled invoice or independently reconciled provider charge**. The successful
job reserved $0.5953454 conservatively. The earlier expired-key attempt retains
another $0.5953454: $1.1906908 in combined retained reservations, not proven
charges. The expired attempt's actual charge remains unrecorded. No reservation
was released or retrospectively rewritten as a refund/settlement.

Script drafting and editorial review were performed in the assistant session,
without a separate paid OpenAI API script-generation call. Assistant-session
subscription/token costs are not included in this audio calculation. The demo's
one-episode daily-cap exception does not authorize future runs or change the
standing $0.10/call and $2/day configuration.

## Review-time implementation and release snapshot (2026-11-06)

This records the state presented for release approval, before the approved
commit/release/update. Consult installed metadata for the later deployed version.

The existing source and `/Users/norvyn/Code/Content/Podcasts/config.yaml` now
implement full/demo separation, 20/25/30-minute settings, B1–B2 listening targets,
sentence-load gates, independent artifact-grounded meaning review, hash-pinned
TTS review, faithful narration without spoken insertions, written disclosure and
measured audio duration. The deterministic pipeline table is unchanged. The old
demo, Chinese history, original-language sources and existing paths remain intact.

Validation recorded 802 passing tests, 1 skipped, 80 focused passes, valid current
configuration/schema and a clean `git diff --check`; two existing warnings remain.
These additions are uncommitted local changes on source HEAD
`6361122112676900b1fb03fcbb220bc228ef23ad`. They have not been pushed or installed.
The existing standard plugin remains version **0.9.0** at
`/Users/norvyn/.claude/plugins/cache/personal-os/podcast-studio/0.9.0`.
The existing project's `studio-dev` link resolves to the updated canonical source;
it is not a second plugin or pipeline. Installed 0.9.0 must not be described as
containing these new listening-workflow fixes.

Remaining: review the scoped changes and use the established release/update
process for the same plugin. Before the first real full-length English episode,
refresh the public news/archive selection and pins, review/authorize its source
scope, reconcile the applicable budget and authorize paid generation. Config
currently keeps `source_authorized: false`; the retained demo inventory is not
blanket authorization for a new episode. Live full-length pacing, completion and
actual learner comprehension are not yet verified. No external publication is
part of these changes.
