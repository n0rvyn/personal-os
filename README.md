# Personal-OS Marketplace

Multi-plugin marketplace for Personal OS data plugins: health intelligence, session reflection, domain intelligence, YouTube curation, and knowledge management.

## Plugins

| Plugin | Category | Description |
|--------|----------|-------------|
| health-insights | health | Personal health intelligence: ingest Apple Health data, establish baselines, generate AI-driven insights |
| session-reflect | coaching | AI collaboration coach: analyze sessions, improve prompting and workflow |
| domain-intel | intelligence | Domain intelligence engine: GitHub, RSS, changelogs, deep research |
| youtube-scout | intelligence | YouTube video intelligence: scrape, transcript extraction, IEF-compliant export |
| pkos | productivity | Personal Knowledge Operating System: inbox, signal, digest, vault operations |
| portfolio-lens | product | Indie project portfolio management: scan, progress pulse, verdict refresh |

See [personal-os-spec.md](./docs/personal-os-spec.md) for the shared config contract and IEF exchange conventions.

## Claude Code

Add the marketplace:

```bash
/plugin marketplace add n0rvyn/personal-os
```

Install plugins:

```bash
/plugin install health-insights@personal-os
/plugin install session-reflect@personal-os
/plugin install domain-intel@personal-os
/plugin install youtube-scout@personal-os
/plugin install pkos@personal-os
/plugin install portfolio-lens@personal-os
```

## Repository Layout

```text
.
├── health-insights/
├── session-reflect/
├── domain-intel/
├── youtube-scout/
├── pkos/
├── portfolio-lens/
├── docs/
│   ├── personal-os-spec.md
│   └── ief-format.md
├── .claude-plugin/   # marketplace manifest
└── .github/workflows/ # CI/CD
```
