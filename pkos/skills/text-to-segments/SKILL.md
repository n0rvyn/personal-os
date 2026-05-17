---
name: text-to-segments
description: "Chunk markdown or plain text into TTS-ready segments. Strips markdown to natural speech, breaks on paragraph/sentence/pause boundaries in that priority order, and refuses to split inside paired quotes, number+unit phrases, inline code, or caller-supplied preserve-terms. Output adapts to MiniMax, Volcengine, or a generic schema. Use when feeding long text to any TTS provider, especially before invoking the tts-toolkit synth-batch flow."
allowed-tools:
  - Bash
  - Read
---

## When to use

Invoke before sending long text to any TTS provider. The chunker centralizes a job that gets reinvented in every Role-local TTS script and accumulates per-script bugs (split numbers, broken English terms, leaked markdown markers).

Use for:

- Podcast / audiobook segmenting before `tts-toolkit/skills/tts/scripts/synth.sh --segments`
- Cross-vendor text preparation (one chunker, many providers)
- Markdown → speech-ready plain text (clean-only mode: `--max-chars 100000` effectively disables chunking)

Do not use for:

- Audio post-processing — out of scope.
- Translation or style rewriting — caller controls the source text.

## CLI

```bash
pkos/skills/text-to-segments/scripts/chunker.py \
  --input transcript.md \
  --output segments.json \
  --max-chars 280 \
  [--clean-markdown true] \
  [--preserve-terms "ComposableArchitecture,llama.swift"] \
  [--vendor-format generic|minimax|volcengine]
```

Defaults:

| Flag | Default | Notes |
| --- | --- | --- |
| `--max-chars` | 280 | Safe for Volcengine V1 (~1024 UTF-8 bytes ≈ 340 chars Chinese); MiniMax can take more but 280 keeps memory/network even. Floor: 10. |
| `--clean-markdown` | true | Strip headings, bold, italic, inline code, code fences, list bullets, horizontal rules, HTML comments. |
| `--preserve-terms` | (empty) | Comma-separated; case-insensitive substring match. |
| `--vendor-format` | `generic` | `generic` returns `{metadata, segments[]}`. `minimax` returns `[{id,text,voice_id,emotion},…]` ready for the official `cmd_generate` flow. `volcengine` returns `{metadata, segments[{id,text}]}`. |

Input source: `--input <path>` or stdin. Output: `--output <path>` or stdout.

## Boundary rules

Priority order when looking for a cut inside the current window:

1. **Paragraph** — `\n\n`
2. **Sentence** — `。！？；!?;`
3. **Pause** — `，、,:：—–` (only when at least 80% of `max_chars` is filled, to avoid stranded short fragments)
4. **Whitespace** — last resort for English-heavy text
5. **Hard** — when nothing else fits

Hard "do not cut" guarantees:

- Inside any paired quote or bracket. Directional pairs (`「」 『』 （） 《》 【】 () [] {}`) tracked by depth; ambiguous ASCII quotes (`" '`) tracked by parity.
- Across a number + unit phrase: `4.2 倍`, `32 kHz`, `2026 年`, `100%`, etc. See `NUM_UNIT_RE` in `scripts/chunker.py`.
- Inside inline code spans (after markdown clean, the backticks are gone but the text inside is treated as a unit).
- Across any string supplied via `--preserve-terms`.

When a protected span itself is longer than `max_chars`, the chunker emits it as one oversized chunk rather than break the rule. The metadata's `segment_count` and `max_chars` will reflect the actual produced sizes.

## Output schema (generic)

```json
{
  "metadata": {
    "source": "transcript.md",
    "total_chars": 5324,
    "segment_count": 24,
    "avg_chars": 221,
    "max_chars": 280,
    "preserved_terms_count": 0
  },
  "segments": [
    {
      "id": "seg_001",
      "text": "卞旸的每日知趣播客 2026年5月17日 — Swift AI 工具链成熟与卡拉马佐夫的虚无主义……",
      "char_count": 234,
      "ends_with": "。",
      "boundary_priority": "sentence"
    }
  ]
}
```

`boundary_priority` is one of `paragraph | sentence | pause | whitespace | hard | eof`.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Success |
| 1 | Argument error (unknown flag, max-chars too small) |
| 2 | Input file not found |
| 3 | Output write failure |

## Tests

```bash
python3 -m pytest pkos/skills/text-to-segments/tests/test_chunker.py -v
```

Eighteen cases cover markdown cleaning, boundary priority, quote/bracket preservation, number+unit preservation, preserve-terms CLI, vendor-format adapters, and CLI happy-path / error paths.

## Consumer notes

`tts-toolkit/skills/tts/scripts/synth.sh` calls this script in `--input` mode and feeds the resulting `segments[].text` to a provider script per chunk. See `tts-toolkit/skills/tts/SKILL.md`.
