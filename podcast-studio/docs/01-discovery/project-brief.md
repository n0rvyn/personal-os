---
type: project-brief
status: active
tags: [podcast, claude-code-plugin, self-contained, vault, tts]
---

# podcast-studio — Project Brief

## One-liner

A standalone, self-contained **Claude Code plugin** that generates a complete
morning/evening podcast (final script + audio + a continuity "stance card")
from the user's Obsidian Vault — with **no dependency on Adam runtime or the
personal-os codebase**. The plugin + Claude Code IS the runtime.

## Why this exists / the thesis

The podcast currently runs on the **Adam** platform (ExecutionPool + DB
templates + 6 roles) and leans on **personal-os** plugins (`podcast-prep`,
`tts-toolkit`) via absolute filesystem paths. The goal is to collapse that
whole pipeline into one portable Claude Code plugin that anyone can install and
run, and that the author can run via `/loop` instead of Adam cron.

The differentiator is **not** "it can generate a podcast." It is:

> **A show grown continuously from the host's private corpus, that debates its
> own past self, and that makes falsifiable bets and settles them on the
> record.**

The moat is the **Vault**: the host's worldview is a *living projection* of the
user's accumulated private thinking (GetNote notes, reading notes, course
insights, recorded viewpoints), refreshed as the corpus grows. Nobody can clone
that corpus, so nobody can clone the show.

## Scope (locked with user — do not expand or shrink)

### In scope

- **Generation only.** Output three co-named local artifacts per episode:
  - `{date}-{title}` final script (`.md`)
  - `{date}-{title}` audio (`.mp3`)
  - `{date}-{show}` stance card (`.yaml`)
- **Two shows**, two command variants:
  - `/podcast morning` — event-centric, multi-segment.
  - `/podcast evening` — essayistic / "spine reversal", carries forward the
    morning's open questions.
- **Single host, single voice.** The host is the living projection of the
  user's corpus. The "诤友" (foil) is a **writing-phase tension device** that
  sharpens the host's stance; the final voiceover is delivered in ONE host
  voice that answers the counterargument. v1 is single-voice; two-voice
  dialogue audio is an explicit future enhancement, NOT in this scope.
- **Continuity via append-only stance cards** (new native mechanism, NOT a port
  of Adam's belief store).
- **Character Bible** distilled + continuously refreshed from the Vault corpus,
  applied as a final voice-unification gate.

### Out of scope (explicitly)

- **Delivery** — no WeChat / QQ / email / any channel send. The plugin writes
  local files only.
- **Scheduling** — no cron. The user drives cadence via Claude Code `/loop`;
  the README documents the one-liner.
- **News fetching** — the plugin does NOT crawl. It reads a configured Vault
  directory that an external tool (domain-intel) populates.
- **Two-voice dialogue audio** — future enhancement.
- **Any dependency on Adam or personal-os code at runtime.**

## Input contract (read from Vault, write to Vault; only credential = TTS key)

Single config file `~/.podcast-studio/config.yaml`:

```yaml
vault:
  subjective_dir: <Vault dir where GetNote / 心得 land>   # subjective source (read-only)
  news_dir:       <Vault dir where domain-intel lands>     # objective source (read-only)
  output_dir:     <Vault podcast archive dir>              # scripts + audio + stance cards land here;
                                                           # also the read source for past transcripts / stance cards
tts:
  provider: volc            # single vendor
  host_voice: volc-xxx      # single voice
```

- `subjective_dir`: populated by personal-os inbox sync (user-confirmed: fixed
  directory). The plugin only reads it; it does not call personal-os.
- `news_dir`: populated by domain-intel (user-confirmed). The plugin only reads
  it; it does not crawl.

## Source material (where the existing logic comes from)

- **Lift directly (vendored copies):**
  - `podcast-prep` scripts (orchestrator / minhash dedup / cross_domain /
    contrarian / topic_log / source_log / angle_slots / domain_select) — from
    `/Users/norvyn/code/Skills/personal-os/podcast-prep`.
  - `tts-toolkit` (synth / synth-auto / chunker / merge / providers /
    voice-catalog / provider-quirks) — from
    `/Users/norvyn/code/Skills/personal-os/tts-toolkit`.
  - Both are already self-contained native Claude Code skills; vendoring must
    strip the `~/.claude/personal-os.yaml` dependency and read the plugin config
    instead. Each vendored copy must record its source-of-truth + re-vendor
    procedure (avoid the 0.3.1↔0.4.0 drift that already happened).
- **Re-author natively:**
  - 6 personas (达芬奇 / 老黑 / 快刀青衣 / 钱钟书 / 卞旸 / 周杰伦) — extracted
    from Adam role `cag_prompt`, with all machine-absolute paths
    (`/Users/norvyn/Code/InfoHub`, hardcoded `orchestrator.py` paths) replaced
    by `${CLAUDE_PLUGIN_ROOT}` + config-injected Vault paths.
  - The 16-step pipeline orchestration (Adam template DAG → a Claude-driven
    `skills/podcast` SKILL that dispatches subagents and self-checks per-step
    artifacts — the same native primitive `prep`/`tts` use).
  - The stance-card continuity mechanism (fresh design).
  - The Character Bible mechanism (fresh).

## The continuity mechanism: append-only "stance cards"

Per episode, write a structured YAML sidecar next to the transcript in
`output_dir`, e.g. `{date}-{show}.stance.yaml`:

- `bets`: `[{id, claim, horizon, settle_by, status}]` — falsifiable judgments
  (qualitative, **no confidence numbers** — temperature principle).
- `open_questions`: `[{id, q}]` — unresolved threads, carried morning→evening
  and across days.
- `settles`: `[{ref, verdict, note}]` — this episode's settlement of a PAST
  card's bet, **referencing the old bet id**.
- `named_concept`, `topics`, `resonance` (reserved for future feedback inflow).

Rules:

- **Append-only.** Never edit a past card. Settlement = a NEW card whose
  `settles` references the old bet id. This makes a fabricated track record
  structurally impossible (the card either exists or it doesn't — addresses the
  "捏造六成命中率" failure class).
- **No confidence numbers** — bets are qualitative.
- Episode N reads N-1's open cards, writes settlement passages, carries open
  questions forward.

## Quality principles (the "world-class show" layer)

The pipeline machinery is necessary but not sufficient. These principles target
warmth / opinion / insight / popularity:

1. **Throughline portfolio over pure novelty.** Maintain 3–5 long-term
   obsessions the show deliberately returns to and deepens (seeded by
   `open_questions`), balanced against fresh topics — not just "don't repeat."
2. **Vault leads, news is the occasion.** The episode core is "here's what I've
   long thought about X, and today's news is a lens on it," not "today's news +
   a take."
3. **Narrative / emotional arc.** Cold open, stakes, turn, payoff + a personal
   stake and self-disclosure. Warmth lives in story and candor, not analysis.
4. **Selection axis = "most memorable that doesn't break + most like the host
   would actually say,"** not highest rubric score.
5. **Resonance proxy.** A hard self-critique gate: "what would make a listener
   forward / re-listen to this?" Reserve a `resonance` field for real feedback
   once delivery exists.
6. **Settlement as a recurring dramatized segment** ("last episode's report
   card"), not back-office bookkeeping.

## Constraints & historical landmines (must be honored)

- **No MVP / no half-done** (global rule). Machine-absolute paths must become
  portable references (`${CLAUDE_PLUGIN_ROOT}` / config).
- **Self-containment red line.** The plugin must not depend on or call
  personal-os code, and must not depend on Adam. Acceptance must prove it runs
  in a clean environment from config + TTS key alone.
- **TTS = single vendor, single voice.** `merge.sh` uses ffmpeg `-c copy`
  (works when all segments share codec/rate — guaranteed same-vendor). Do NOT
  introduce cross-vendor mixing (would require re-encode + loudness matching;
  out of scope).
- **Scoring step landmine.** In Adam, binding a persona to the scoring step
  triggered tone validation (Layer B) that is mutually exclusive with JSON
  output — it bombed repeatedly. The native scoring/selection step must NOT
  bind a narrative persona; keep it pure structured output.
- **Anti-fabrication targeting (temperature principle, user-ratified).** Only
  block *fabricated* data / track records / notes. Allow weak sources /
  second-hand / "a friend said." Strip confidence numbers to restore warmth.
  "Go deep" = perspective, not citations.
- **Naming.** Three artifacts share `{date}-{title}` (script/audio) and
  `{date}-{show}` (stance card).

## Reference sources (read-only, not modified)

- Adam codebase: `/Users/norvyn/Code/Projects/Adam` (role prompts, delivery
  adapter logic, podcast template structure — reference only).
- personal-os: `/Users/norvyn/code/Skills/personal-os` (vendoring source for
  podcast-prep + tts-toolkit — copied, not depended on).
