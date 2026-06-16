"""podcast-studio pipeline topology — the 17-step contract as data.

Phase 1 (phase1-code-runner-plan) collapses the SKILL.md prose pipeline into a
deterministic step table so a Python runner (`lib/runner.py`) — not session
self-discipline — drives ordering, gating, retry, and halt semantics.

This module is the **single source of truth** for the 17-step topology. It
holds DATA only — no IO, no dispatch, no execution. The runner imports this,
the tests pin this, the SKILL.md references this.

Topology model (per the plan's task contract):

    Each step is a dict with the following fields:

      name        (str)        — station identifier (e.g. "5b" / "assemble-briefs")
      kind        ("code" |
                    "agent")    — runner dispatches differently per kind
      agent       (str|None)   — persona name; required when kind=="agent"
      inputs      (list)       — symbolic input references (e.g. filenames,
                                 "magnitude-verdict.json", "vault.output_dir")
      artifact    (str|None)   — primary artifact filename (under scratch or
                                 output_dir, depending on station)
      gate        (list|None)  — composite gate spec; see "Gate spec" below
      parallel    (list|None)  — parallel fan-out tags, e.g. ["A","B","C"]
      retry       (int|None)   — retry cap for this station (used on its
                                 companion retry station: 12a, 16a)
      skip_when   (str|None)   — conditional skip sentinel; e.g. "no_tts" for
                                 TTS-only stations (step 14 + mp3 move)

    `kind=="code"` stations are deterministic helpers the runner calls
    directly (no LLM). `kind=="agent"` stations are persona dispatches the
    runner hands to `lib.dispatch.dispatch_persona`.

Gate spec (composite list — advisory-corrected, per the plan's must-revise
fix):

    A gate is a LIST of `{fn, args}` items. The runner evaluates them in
    order; ANY `ok=False` halts the pipeline. Each item is shaped:

        {"fn": <gate-function-name>, "args": {...optional...}}

    `fn` is resolved to a function at runner time (see `lib.runner.gate_map`).
    This module only stores the NAMES — it must not import the execution
    layer (kept pure data, so validate_pipeline can be used on any candidate
    step table without side effects).

    The sentinel `min_chars: "floor"` in args is a runner-side placeholder
    that gets resolved to `floor_chars_for_show(show)` — a coded length
    floor that lives in `lib/episode.py`. This module stores the sentinel
    verbatim.

The 17 steps (per SKILL.md contract table at SKILL.md:571-595, with the two
must-revise code bridges from the Phase 1 plan):

    1    config            code    — load PodcastTeamConfig (load_config)
    2    editorial         code    — read references/{show}.md
    3    scratch           code    — make_scratch(output_dir, f"{date}-{show}")
    3a   stance-card-exists code   — pre-flight guard: same-day re-run → fail-fast
                                     (stance.stance_card_exists returns True)
    4    continuity-read   code    — due_bets + carried_open_questions +
                                     pick_to_deepen; inject into brief
    5    collect           agent=davinci — material-summary.md
    5b   magnitude         agent=liangchen — magnitude-verdict.json
                                     (DP-001=A: only magnitude; recent_anchors
                                     retired — covered-ground owns the
                                     anti-repeat memory)
    (assemble-briefs)      code    — read magnitude-verdict + brief-A/B/C +
                                     continuity → magnitude_to_airtime +
                                     render covered-ground `avoid_memo` →
                                     writing-brief-A/B/C.json
    7    drafts            agent=davinci, parallel[A,B,C] — draft-A/B/C.md
                                     (davinci respects covered-ground
                                     `avoid_memo` for apparatus避让)
    8    critiques         agent=laohei, parallel[A,B,C] — critique-A/B/C.json
    9    polishes          agent=kuaidao, parallel[A,B,C] — polish-A/B/C.md
    10   scoring           agent=qianzhongshu — score-verdict.json
    11   select-draft      code    — select_draft(verdict, candidates)
    12   finalize          agent=kuaidao — finalize-result.json
    12a  factcheck         agent=zhijianyuan, retry=1 — factcheck-verdict.json
                              (re-dispatches step 12 with EXPAND brief on miss)
    13   broadcast-rewrite agent=bianyang — broadcast-script-{date}.txt
    13a  scorecard         agent=scorecard — scorecard-verdict.json
                             (Phase 3: craft-gate + scorecard; advisory by
                              default, --enforce-scorecard enables halt on
                              hard-gate red. Sits between 13 and 14 — the
                              only window where 念稿 + factcheck + finalize
                              + score-verdict all coexist in scratch.)
    14   tts               agent=jay, skip_when="no_tts" — audio-files.mp3
    15   publish-paths     code    — episode_paths(output_dir, date, title, show)
    15a  resonance         code    — in-memory resonance self-critique
    15b  topic-log         code    — topic_log.yaml append (orchestrator.finalize)
    16   stance-write      code    — stance.write_card (resonance gate passes
                                     iff resonance is non-empty or explicit "";
                                     runner injects deterministic
                                     `apparatus_used` from covered-ground
                                     store ∩ finalize body + card concepts)
    16a  stance-card-gate  code, retry=1 — check_stance_card (re-dispatches
                                     step 16 with regenerate on miss)
    17   cleanup           code    — cleanup_scratch(scratch)
    18   coveredground-    agent=coveredground-distiller, fail_soft=True —
         distill                  ISOLATED night-shift distiller: reads
                                     published.md + recent_bodies + store
                                     (read-only), writes apparatus json
                                     (catches new anchors). Fail-soft: a
                                     distiller miss does NOT halt the run.
    19   coveredground-    code, fail_soft=True — reads apparatus json,
         update                  runs embedding-based dedup against the
                                     existing covered-ground.yaml store,
                                     writes the updated store atomically
                                     to output_dir. Fail-soft.

Editorial branches (morning / evening) do NOT change the topology — they
change the brief the davinci collector reads. So `load_pipeline(show)`
returns the same step list for both shows; the runner injects the show
name into prompts/paths at dispatch time.

Step schema adds a `fail_soft` field (None | bool). The default is None
(fail-closed: a halt propagates). The two post-publish stations
(18 coveredground-distill, 19 coveredground-update) set `fail_soft=True`
so their failure surfaces as a `status=skipped` log line — the published
episode is preserved. Pre-publish stations do NOT mark fail_soft (Phase 1
invariant: a missing pre-publish artifact halts the run).
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Agent whitelist — must match the personas the pipeline can dispatch.
# (Mirrors AGENT_WHITELIST in test_pipeline.py.)
# ---------------------------------------------------------------------------
AGENT_WHITELIST = frozenset({
    "davinci",
    "liangchen",
    "bible-distiller",
    "coveredground-distiller",
    "laohei",
    "kuaidao",
    "qianzhongshu",
    "bianyang",
    "jay",
    "zhijianyuan",
    "scorecard",
})


# ---------------------------------------------------------------------------
# The 17-step topology. `load_pipeline` returns a deep-ish copy of this
# list (lists/dicts are new instances, scalars are values) so a caller
# mutating the returned list cannot affect subsequent calls.
# ---------------------------------------------------------------------------
def _build_steps() -> list[dict[str, Any]]:
    """Construct the canonical 17-step topology.

    Returns a list of step dicts. Each dict is a fresh copy (callers
    receive a new list of new dicts on every `load_pipeline` call).
    """
    return [
        # --- step 1: load config -------------------------------------------
        {
            "name": "config",
            "kind": "code",
            "agent": None,
            "inputs": ["~/.podcast-studio/config.yaml"],
            "artifact": None,
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 2: editorial branch (references/{show}.md) ---------------
        {
            "name": "editorial",
            "kind": "code",
            "agent": None,
            "inputs": ["references/{show}.md"],
            "artifact": None,
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 3: make scratch ------------------------------------------
        {
            "name": "scratch",
            "kind": "code",
            "agent": None,
            "inputs": ["vault.output_dir", "{date}-{show}"],
            "artifact": None,  # returns a Path, no artifact file
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 3a: same-day re-run early tripwire -----------------------
        # Fails fast on a same-day re-run (stance card slot already taken)
        # before any expensive dispatch or publish. Prevents ship-then-orphan.
        {
            "name": "stance-card-exists",
            "kind": "code",
            "agent": None,
            "inputs": ["vault.output_dir", "{date}", "{show}"],
            "artifact": None,
            "gate": [{"fn": "check_stance_card_absent"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 4: continuity-read (CODE BRIDGE, must-revise) -----------
        # Pulls due bets, carried open-questions, and the throughline
        # obsession to deepen. Output is in-memory continuity data
        # injected into the davinci drafting brief.
        {
            "name": "continuity-read",
            "kind": "code",
            "agent": None,
            "inputs": ["vault.output_dir", "throughline.yaml", "{date}", "{show}"],
            "artifact": "continuity.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 5: davinci collection ------------------------------------
        {
            "name": "collect",
            "kind": "agent",
            "agent": "davinci",
            "inputs": ["brief", "vault", "continuity"],
            "artifact": "material-summary.md",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 5b: liangchen magnitude verdict -------------------------
        # magnitude-verdict.json is the per-candidate none/light/medium/heavy.
        # check_artifact is the presence gate; the runner then calls
        # safe_parse_verdict (fail-soft) — degraded but present artifact
        # passes; missing artifact halts.
        {
            "name": "magnitude",
            "kind": "agent",
            "agent": "liangchen",
            "inputs": ["recent_cards", "candidates", "recent_bodies"],
            "artifact": "magnitude-verdict.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- (assemble-briefs) CODE BRIDGE between 5b and 7 ----------------
        # Reads magnitude-verdict.json (via safe_parse_verdict) + brief-A/B/C
        # (embedded in material-summary.md) + continuity. Computes per-
        # candidate airtime tier (magnitude_to_airtime). Renders the
        # covered-ground `avoid_memo` (the night-shift "recently overused,
        # avoid next episode" memo) into each writing-brief-{A,B,C}.json.
        # This is the routing+避让 channel into the drafting step — its
        # absence makes the anti-homogenization guard a no-op.
        {
            "name": "assemble-briefs",
            "kind": "code",
            "agent": None,
            "inputs": [
                "magnitude-verdict.json",
                "material-summary.md",  # carries brief-A/B/C from davinci
                "continuity.json",
                "covered-ground.yaml",  # source for the avoid_memo
            ],
            "artifact": "writing-brief-A.json",  # the runner writes all three
            "gate": [{"fn": "check_artifact"}],
            "parallel": ["A", "B", "C"],  # the runner fans out per-candidate
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 6: bible-distill (ISOLATED Character Bible distiller) ----
        # Produces the host Character Bible — the voice-unification source
        # that finalize(12) + broadcast-rewrite(13) read to make every
        # committee draft sound like the same person. CUSTOM executor
        # (_bible_distill_step); the generic agent path will NOT work:
        #   (a) the input is gather_corpus(vault.subjective_dir) — OUTSIDE
        #       scratch — so the generic input-resolver (which points the
        #       persona at scratch) can't supply it;
        #   (b) ISOLATION (D-105 anti-echo) requires feeding the persona
        #       ONLY the corpus — never episodes/news/cards (a leaky distill
        #       once made "obsessions" = episode topics 霍尔木兹/苏伊士 and
        #       homogenized every show);
        #   (c) the artifact must land in state_dir (persistent continuity),
        #       not scratch.
        # `inputs: ["corpus"]` is CONCEPTUAL (the executor gathers it from
        # config) — NOT a scratch file the resolver should look up.
        # fail_soft=True: a distiller miss writes a MINIMAL_BIBLE (base
        # persona 卞旸) so the artifact ALWAYS lands — the daily run never
        # halts on a bible miss (downstream 12/13 then unify against the
        # minimal voice instead of falling back to the bare base persona).
        {
            "name": "bible-distill",
            "kind": "agent",
            "agent": "bible-distiller",
            "inputs": ["corpus"],
            "artifact": "character-bible.md",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": True,
        },
        # --- step 7: 3-way davinci drafting (parallel) --------------------
        # Each of the 3 dispatches consumes its corresponding
        # writing-brief-X.json (the routing + covered-ground avoid_memo the
        # bridge station produced). Composite gate: check_artifact AND
        # check_min_chars with the 'floor' sentinel (resolved to
        # floor_chars_for_show(show) at runner time).
        {
            "name": "drafts",
            "kind": "agent",
            "agent": "davinci",
            "inputs": ["writing-brief-A.json"],
            "artifact": "draft-A.md",
            "gate": [
                {"fn": "check_artifact"},
                {"fn": "check_min_chars", "args": {"min_chars": "floor"}},
            ],
            "parallel": ["A", "B", "C"],
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 8: 3-way laohei critique (parallel) --------------------
        {
            "name": "critiques",
            "kind": "agent",
            "agent": "laohei",
            "inputs": ["draft-A.md"],
            "artifact": "critique-A.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": ["A", "B", "C"],
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 9: 3-way kuaidao polish (parallel) --------------------
        # Composite gate: check_artifact + check_min_chars(floor).
        # Kuaidao unifies voice against the Character Bible.
        {
            "name": "polishes",
            "kind": "agent",
            "agent": "kuaidao",
            "inputs": ["draft-A.md", "critique-A.json"],
            "artifact": "polish-A.md",
            "gate": [
                {"fn": "check_artifact"},
                {"fn": "check_min_chars", "args": {"min_chars": "floor"}},
            ],
            "parallel": ["A", "B", "C"],
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 10: qianzhongshu scoring (structured, no persona binding)
        {
            "name": "scoring",
            "kind": "agent",
            "agent": "qianzhongshu",
            "inputs": ["polish-A.md", "polish-B.md", "polish-C.md"],
            "artifact": "score-verdict.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 11: select_draft (deterministic, by scores.total) ------
        {
            "name": "select-draft",
            "kind": "code",
            "agent": None,
            "inputs": ["score-verdict.json", "polish-A.md", "polish-B.md", "polish-C.md"],
            "artifact": None,  # returns (chosen_id, chosen_path)
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 12: kuaidao finalize (voice-unification) ---------------
        # Composite gate: check_artifact + check_min_chars with
        # json_field="body" (the finalize `body` field — gates BEFORE
        # the expensive broadcast rewrite + TTS).
        {
            "name": "finalize",
            "kind": "agent",
            "agent": "kuaidao",
            "inputs": ["chosen-polish.md", "score-verdict.json", "character-bible.md"],
            "artifact": "finalize-result.json",
            "gate": [
                {"fn": "check_artifact"},
                {"fn": "check_min_chars", "args": {"min_chars": "floor", "json_field": "body"}},
            ],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 12a: zhijianyuan factcheck (RETRY STATION) -------------
        # retry=1 means: on gate failure, re-dispatch step 12 with an
        # EXPAND brief. A second failure halts the pipeline.
        {
            "name": "factcheck",
            "kind": "agent",
            "agent": "zhijianyuan",
            "inputs": ["finalize-result.json", "material-summary.md"],
            "artifact": "factcheck-verdict.json",
            "gate": [{"fn": "check_factcheck"}],
            "parallel": None,
            "retry": 1,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 13: bianyang broadcast-rewrite -------------------------
        {
            "name": "broadcast-rewrite",
            "kind": "agent",
            "agent": "bianyang",
            "inputs": ["finalize-result.json", "character-bible.md"],
            "artifact": "broadcast-script-{date}.txt",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 13a: scorecard (Phase 3 craft-gate + scorecard) --------
        # The 13a scorecard station lives in the ONLY window where the
        # broadcast script, factcheck verdict, finalize body, and
        # score-verdict all coexist in scratch (cleanup at step 17 wipes
        # scratch afterwards). The deterministic hard gates
        # (structlint + dedup + 必产 artifact) plus the scorecard judge
        # persona's 3 dimensions (有观点/有温度/不同质化) assemble into
        # `scorecard-verdict.json` (scratch) + `{date}-{show}.scorecard.md`
        # (output_dir, human-readable). fail_soft=None — advisory is
        # enforced by the runner's --enforce-scorecard flag, NOT by
        # fail_soft (which would silently swallow halts).
        #
        # NOTE on `gate`: the runner intercepts "scorecard" with a custom
        # executor (_scorecard_step) that ALWAYS writes scorecard-verdict.json
        # before returning, so the generic gate block never runs for this
        # station — the `check_artifact` gate below is a DOCUMENTARY artifact
        # contract (the verdict is the station's product), pinned by
        # test_scorecard_station_gate_uses_check_artifact. Verification of the
        # verdict's PASS/FAIL is the runner's advisory/enforce logic, not this gate.
        {
            "name": "scorecard",
            "kind": "agent",
            "agent": "scorecard",
            "inputs": [
                "finalize-result.json",
                "broadcast-script-{date}.txt",
                "factcheck-verdict.json",
                "score-verdict.json",
            ],
            "artifact": "scorecard-verdict.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 14: jay TTS -------------------------------------------
        # Skipped when no_tts=True (Phase 1 adds the no-TTS mode).
        {
            "name": "tts",
            "kind": "agent",
            "agent": "jay",
            "inputs": ["broadcast-script-{date}.txt"],
            "artifact": "audio-files.mp3",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": "no_tts",
            "fail_soft": None,
        },
        # --- step 15: publish-paths (3 named paths under output_dir) ----
        {
            "name": "publish-paths",
            "kind": "code",
            "agent": None,
            "inputs": ["vault.output_dir", "{date}", "{title}", "{show}"],
            "artifact": None,  # returns a dict of named paths
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 15a: resonance (in-memory self-critique) ---------------
        # Free-text value (str | list[str] | explicit ""). The runner
        # captures this from the dispatched station; the gate runs at
        # step 16 (write_card) and re-validates the field.
        {
            "name": "resonance",
            "kind": "code",
            "agent": None,
            "inputs": ["finalize-result.json"],
            "artifact": None,  # in-memory value
            "gate": [{"fn": "check_resonance_present"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 15b: topic_log append (orchestrator.finalize) ---------
        # Cross-day cooldown store. Uses vendored prep's
        # orchestrator.finalize(topic_log_path, ...). Non-zero exit
        # surfaces as a halt.
        {
            "name": "topic-log",
            "kind": "code",
            "agent": None,
            "inputs": ["published.md", "approved_topics", "topic_log_path"],
            "artifact": "topic_log.yaml",
            "gate": [{"fn": "check_topic_log_appended"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 16: stance-write (stance.write_card) ------------------
        # write_card raises on overwrite / fabricated ref / future date /
        # numeric resonance — its own validation is the gate. The runner
        # also injects a deterministic `apparatus_used` (intersection of
        # store-known anchors with the finalize body + card named_concept)
        # before the write — fail-soft to [] on any extraction error.
        {
            "name": "stance-write",
            "kind": "code",
            "agent": None,
            "inputs": ["vault.output_dir", "{date}", "{show}", "card_dict"],
            "artifact": "{date}-{show}.stance.yaml",
            "gate": [{"fn": "check_write_card_returned"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 16a: stance-card-gate (RETRY STATION) -----------------
        # retry=1: on gate failure, re-dispatch stance-write with a
        # regenerate brief. A second failure halts.
        {
            "name": "stance-card-gate",
            "kind": "code",
            "agent": None,
            "inputs": ["vault.output_dir", "{date}", "{show}"],
            "artifact": None,
            "gate": [{"fn": "check_stance_card"}],
            "parallel": None,
            "retry": 1,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 17: cleanup (scratch lifecycle) ----------------------
        {
            "name": "cleanup",
            "kind": "code",
            "agent": None,
            "inputs": ["scratch_dir"],
            "artifact": None,
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": None,
        },
        # --- step 18: coveredground-distill (POST-PUBLISH, FAIL-SOFT) ---
        # The night-shift distiller — runs AFTER the published artifacts
        # are written. Isolated persona reads the published body + the
        # recent bodies + the current store (read-only) and writes a
        # `coveredground-apparatus.json` listing the signature anchors /
        # analogies / frameworks used in this episode. Fails soft: a
        # distiller failure does NOT halt the pipeline (the episode is
        # already published). The next episode just gets a stale store.
        {
            "name": "coveredground-distill",
            "kind": "agent",
            "agent": "coveredground-distiller",
            "inputs": [
                "published.md",
                "recent_bodies",
                "covered-ground.yaml",
            ],
            "artifact": "coveredground-apparatus.json",
            "gate": [{"fn": "check_artifact"}],
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": True,
        },
        # --- step 19: coveredground-update (POST-PUBLISH, FAIL-SOFT) ----
        # Reads the distiller's `coveredground-apparatus.json` from
        # scratch, runs embedding-based dedup against the existing
        # store, and writes the updated `covered-ground.yaml` atomically
        # to output_dir. Fails soft: a code-station error here does not
        # halt the pipeline (the episode is already published).
        {
            "name": "coveredground-update",
            "kind": "code",
            "agent": None,
            "inputs": [
                "coveredground-apparatus.json",
                "vault.output_dir",
            ],
            "artifact": None,
            "gate": None,
            "parallel": None,
            "retry": None,
            "skip_when": None,
            "fail_soft": True,
        },
    ]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def load_pipeline(show: str) -> list[dict[str, Any]]:
    """Return the ordered 17-step topology for the given show.

    `show` is one of "morning" / "evening" (the only two editorial branches
    in this plugin). The two shows share the SAME topology — they differ
    only in the brief the davinci collector reads. We still validate the
    `show` argument here so a typo'd show name is caught immediately
    (fail-closed), not later at the gate.

    Returns a fresh list of fresh step dicts on every call — callers can
    safely mutate the returned list without affecting subsequent loads.
    """
    if show not in ("morning", "evening"):
        raise ValueError(
            f"unknown show {show!r}; expected 'morning' or 'evening'"
        )
    # Fresh copies so test isolation / runtime mutations are safe.
    return [dict(step) for step in _build_steps()]


def validate_pipeline(steps: list[dict[str, Any]]) -> None:
    """Validate a step table; raise ValueError naming the offending field.

    Checks (fail-closed — ANY violation raises, the runner does not get
    a "best-effort" step list):

      - step is a dict
      - required fields present: name, kind, agent, inputs, artifact, gate,
        parallel, retry, skip_when, fail_soft
      - `name` is a non-empty string
      - `kind` ∈ {"code", "agent"}
      - `agent` is a non-empty string when kind=="agent", and is in
        AGENT_WHITELIST (prevents path-traversal reads of arbitrary files
        via the dispatch whitelist)
      - `agent` is None when kind=="code"
      - `inputs` is a list
      - `artifact` is str|None
      - `gate` is None or a list of `{fn, args?}` dicts, each with a
        non-empty `fn`
      - `parallel` is None or a list (the runner fans out per element)
      - `retry` is None or a positive int
      - `skip_when` is None or a string
      - `fail_soft` is None or a bool (post-publish stations mark
        fail_soft=True; the runner translates their halt into
        status=skipped so the episode is still shipped)

    Used by:
      - `load_pipeline` (so the canonical table self-validates)
      - external tools that load step tables from non-code sources
        (future-proofing; not used today but the contract says it should
        be a reusable gate)
    """
    if not isinstance(steps, list):
        raise ValueError(
            f"steps must be a list, got {type(steps).__name__}"
        )
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ValueError(
                f"steps[{i}] must be a dict, got {type(step).__name__}"
            )
        # All required fields must be present (explicit list — fail-closed
        # on missing key, even if the value would have been None).
        for field in (
            "name", "kind", "agent", "inputs", "artifact",
            "gate", "parallel", "retry", "skip_when", "fail_soft",
        ):
            if field not in step:
                raise ValueError(
                    f"steps[{i}] missing required field: {field!r}"
                )

        # name: non-empty str
        if not isinstance(step["name"], str) or not step["name"]:
            raise ValueError(
                f"steps[{i}].name must be a non-empty string, got "
                f"{step['name']!r}"
            )

        # kind: 'code' or 'agent'
        if step["kind"] not in ("code", "agent"):
            raise ValueError(
                f"steps[{i}].kind must be 'code' or 'agent', got "
                f"{step['kind']!r}"
            )

        # agent: required when kind=='agent', None when kind=='code';
        # must be in whitelist when kind=='agent'
        if step["kind"] == "agent":
            if not isinstance(step["agent"], str) or not step["agent"]:
                raise ValueError(
                    f"steps[{i}] (name={step['name']!r}) kind='agent' requires "
                    f"a non-empty string `agent` field"
                )
            if step["agent"] not in AGENT_WHITELIST:
                raise ValueError(
                    f"steps[{i}] (name={step['name']!r}) agent={step['agent']!r} "
                    f"not in whitelist {sorted(AGENT_WHITELIST)}"
                )
        else:  # kind == "code"
            if step["agent"] is not None:
                raise ValueError(
                    f"steps[{i}] (name={step['name']!r}) kind='code' must have "
                    f"agent=None, got {step['agent']!r}"
                )

        # inputs: list
        if not isinstance(step["inputs"], list):
            raise ValueError(
                f"steps[{i}] (name={step['name']!r}) inputs must be a list, got "
                f"{type(step['inputs']).__name__}"
            )

        # artifact: str or None
        if step["artifact"] is not None and not isinstance(step["artifact"], str):
            raise ValueError(
                f"steps[{i}] (name={step['name']!r}) artifact must be str|None, "
                f"got {type(step['artifact']).__name__}"
            )

        # gate: None or list of {fn, args?}
        if step["gate"] is not None:
            if not isinstance(step["gate"], list):
                raise ValueError(
                    f"steps[{i}] (name={step['name']!r}) gate must be a list or "
                    f"None, got {type(step['gate']).__name__}"
                )
            for j, gate_item in enumerate(step["gate"]):
                if not isinstance(gate_item, dict):
                    raise ValueError(
                        f"steps[{i}] (name={step['name']!r}) gate[{j}] must be "
                        f"a dict, got {type(gate_item).__name__}"
                    )
                if "fn" not in gate_item:
                    raise ValueError(
                        f"steps[{i}] (name={step['name']!r}) gate[{j}] missing "
                        f"required field: 'fn'"
                    )
                if not isinstance(gate_item["fn"], str) or not gate_item["fn"]:
                    raise ValueError(
                        f"steps[{i}] (name={step['name']!r}) gate[{j}].fn must "
                        f"be a non-empty string, got {gate_item['fn']!r}"
                    )
                if "args" in gate_item and not isinstance(gate_item["args"], dict):
                    raise ValueError(
                        f"steps[{i}] (name={step['name']!r}) gate[{j}].args "
                        f"must be a dict, got {type(gate_item['args']).__name__}"
                    )

        # parallel: None or list
        if step["parallel"] is not None and not isinstance(step["parallel"], list):
            raise ValueError(
                f"steps[{i}] (name={step['name']!r}) parallel must be a list or "
                f"None, got {type(step['parallel']).__name__}"
            )

        # retry: None or positive int
        if step["retry"] is not None:
            if not isinstance(step["retry"], int) or isinstance(step["retry"], bool):
                raise ValueError(
                    f"steps[{i}] (name={step['name']!r}) retry must be int or "
                    f"None, got {step['retry']!r}"
                )
            if step["retry"] < 1:
                raise ValueError(
                    f"steps[{i}] (name={step['name']!r}) retry must be ≥1, got "
                    f"{step['retry']!r}"
                )

        # skip_when: None or str
        if step["skip_when"] is not None and not isinstance(step["skip_when"], str):
            raise ValueError(
                f"steps[{i}] (name={step['name']!r}) skip_when must be str or "
                f"None, got {step['skip_when']!r}"
            )

        # fail_soft: None or bool (fail-closed on strings/ints/lists)
        if step["fail_soft"] is not None and not isinstance(step["fail_soft"], bool):
            raise ValueError(
                f"steps[{i}] (name={step['name']!r}) fail_soft must be bool or "
                f"None, got {type(step['fail_soft']).__name__}: "
                f"{step['fail_soft']!r}"
            )


# Validate the canonical table at import time so a malformed topology is
# caught the moment the module is loaded — not on the first runner call.
# This is the cheapest possible regression shield: if someone edits the
# step table incorrectly, the next pytest run fails at collection, not
# halfway through an episode.
validate_pipeline(_build_steps())
