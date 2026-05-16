"""Shared analyzer version constants for session-reflect parsers and backfill.

Bump rules: bump on schema change OR parser semantic change.
Effects: existing sessions get analysis_checkpoints.re_analyze_pending=1.
"""

ANALYZER_VERSION = "2026-05-16-phase5"
