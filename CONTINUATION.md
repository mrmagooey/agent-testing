# CONTINUATION — parent-subagent architecture rollout

This branch contains the work-in-progress implementation of the plan in
`plan_subagents_pydantic_ai.md`. Phases 0-4 are now complete.

## Phase status

| Phase | Status | Branch tip                                |
|-------|--------|-------------------------------------------|
| 0     | done + reviewed (PASS_WITH_NITS, fixed) | spike under `scripts/spike_pydantic_ai/` |
| 1     | done + reviewed (PASS_WITH_NITS, fixed) | `src/sec_review_framework/agent/`         |
| 2     | done + reviewed (PASS, nits fixed)      | `strategies/runner.py` + `worker.py` flag |
| 3a    | done + reviewed (PASS_WITH_NITS)        | `builtin_v2.single_agent`, `builtin_v2.diff_review` |
| 3b    | done + reviewed                         | `sast_first`, `per_file` reimplementations |
| 3c    | done + reviewed                         | `per_vuln_class` (16 specialists, dispatch validator) |
| 4     | done                                    | delete legacy code paths, rename IDs |
| 5     | not started                             | new capability strategies (verifier, classifier, taint pipeline, blast radius) |
| 6     | not started                             | frontend (`StrategyEditor.tsx`)           |
| 7     | not started                             | docs (ARCHITECTURE rewrite, README how-to) |

## Phase 4 changes (ID migration and deletions)

### ID migration
`builtin.<shape>` IDs now refer to the pydantic-ai (v2) implementations.
Legacy `builtin_v2.*` IDs have been removed. Any existing DB rows referencing
`builtin.single_agent`, `builtin.diff_review`, `builtin.per_file`,
`builtin.sast_first`, or `builtin.per_vuln_class` will resolve to the new
implementations — this is intentional, as parity tests confirmed equivalence.

### Files deleted
- `src/sec_review_framework/strategies/base.py` (ScanStrategy ABC)
- `src/sec_review_framework/strategies/single_agent.py`
- `src/sec_review_framework/strategies/diff_review.py`
- `src/sec_review_framework/strategies/per_file.py`
- `src/sec_review_framework/strategies/sast_first.py`
- `src/sec_review_framework/strategies/per_vuln_class.py`
- All `tests/unit/test_phase3_parity_*.py` (parity tests, purpose served)
- `tests/unit/test_runner_single_agent_smoke.py` (superseded)
- `tests/unit/test_strategies.py`, `tests/unit/test_diff_review_strategy.py`,
  `tests/unit/test_strategy_model_factories.py` (tested deleted classes)
- `tests/unit/test_finding_parser.py`, `tests/unit/test_common_bundle.py`
  (tested deleted FindingParser and run_subagents)
- `tests/integration/test_single_agent_strategy.py`
- Prompt files renamed: `*_v2.txt` → `*.txt` (old `*.txt` overwritten)

### Functions/fields removed
- `_SHAPE_TO_STRATEGY` dict in `worker.py`
- `_should_use_new_runner()` in `worker.py`
- `StrategyFactory` class in `worker.py`
- `use_new_runner` field from `UserStrategy` in `strategy_bundle.py`
- `run_subagents()`, `FindingParser`, `_resolve_task_fields()` from `common.py`
- `run_agentic_loop()` kept (used by `verification/verifier.py`)

### OrchestrationShape
Enum kept for backward-compatible deserialization of historical
`BundleSnapshot`/`ExperimentRun` rows. Marked deprecated in the docstring.
New code should not branch on this value; `runner.py` treats all
`UserStrategy` objects uniformly.

## Test counts

- Pre-Phase-0: 957 unit tests
- After Phase 0 spike: +30 tests (mock-only verification under `scripts/spike_pydantic_ai/`)
- After Phase 1: +87 agent-extra tests, 1239 total when `agent` extra installed
- After Phase 2: +45 runner tests, 1239 → 1239 (some test re-counts)
- After Phase 3a: +30 parity tests, **1269 total passing**
- After Phase 4: parity tests removed, legacy tests removed; test count reduced (see Phase 4 run)

## Known unfinished items

### Live-provider verification (carried from Phase 0)

The Phase 0 spike was verified with mocks only — no live Claude API
credentials available in the environment. The plan's exit criterion 2
("Tool calling via LiteLLM through Anthropic-direct, Bedrock-Claude,
Vertex-Claude") remains unmet. See `scripts/spike_pydantic_ai/SPIKE_RESULTS.md`
§ "UNMET EXIT CRITERION" for the exact commands to run with credentials.
Decision was made to proceed to Phase 1 with mock verification; before
Phase 5 ships (the new capability strategies), a live verification round
is recommended.

### Dependency conflict (carried from Phase 0)

`pydantic-ai` and `semgrep` cannot coexist in one venv (otel and mcp
version pins disjoint). Resolution: separate venvs — `agent` extra for
the new runner, `worker` extra for SAST workers. Documented in
`pyproject.toml` and `SPIKE_RESULTS.md` § 7. No path forward without
either a `semgrep` upstream change or running pydantic-ai-using code in
its own pod.

### Phase 3a nits (non-blocking)

From the Phase 3a review (PASS_WITH_NITS):

- `src/sec_review_framework/strategies/strategy_registry.py:81` — docstring
  says "5 builtin" but now registers 7. Update to "7 builtin strategies
  (5 original + 2 v2 entries)".
- `src/sec_review_framework/strategies/strategy_registry.py:281` —
  `load_default_registry()` docstring says "5 builtin strategies". Update
  to "7 builtin strategies".
- `src/sec_review_framework/strategies/strategy_registry.py:226` — extra
  blank line in the new builtin_v2 block; existing blocks use one blank
  line separator.
- `tests/unit/test_phase3_parity_diff_review.py:189` — `_run_new_runner`
  uses a simplified user_prompt_template (the real one references
  `{diff_text}` which `_build_user_prompt` doesn't support yet). Add a
  one-line comment cross-referencing this limitation. Phase 3b's
  `_build_user_prompt` improvements should make this go away.

### Phase 1 finding deferred to Phase 2/3

`tool_adapter.py` uses `PAITool.from_schema()` which skips schema
validation of arguments. The tool's own `invoke()` bears responsibility
for input validation. This is pre-existing (inherent to the chosen
approach) and no worse than today; track for hardening when
`args_validator=` becomes worth wiring in (Phase 5 capability strategies
that take complex inputs).

## How to resume

Each subsequent phase should:

1. Branch from the tip of this branch (`worktree-agent-a9404be13efb5b714`).
2. Read `plan_subagents_pydantic_ai.md` § 7 for that phase's exit criteria.
3. Read this file for known carry-forward issues.
4. Spawn a separate code-review subagent after each phase per AGENTS.md.

### Phase 3b — `sast_first` and `per_file`

Add `builtin_v2.sast_first` and `builtin_v2.per_file` to `seed_builtins()`.
Both have non-empty `subagents` (single role each). The parent prompt drives
dispatch via `invoke_subagent_batch`. Per the plan, dispatch validators are
needed (the plan calls these "non-trivial" for `per_file`).

The `_build_user_prompt` helper in `runner.py` currently only renders
`{repo_summary}` and `{finding_output_format}`. To support `diff_review`
parity in Phase 3a, a workaround was used. For 3b, extend
`_build_user_prompt` to support all the placeholders listed in
`prompts/user/*.txt` (repo_summary, diff_text, file_content, sast_summary,
file_path, vuln_class).

### Phase 3c — `per_vuln_class`

The riskiest phase. 16 specialists. Dispatch-completeness validator is
critical: a supervisor LLM may not dispatch all 16. Per the plan, if pure
LLM dispatch can't reach parity within the +20% token-drift bound, fall
back to a programmatic dispatch helper that fills in any missed
specialists. This is the explicit mitigation in the plan; do not invent
new mitigations.

### Phase 4 — Deletions

After all five `builtin_v2.*` shapes pass parity:
- Delete `_SHAPE_TO_STRATEGY` in `worker.py`.
- Delete the five `ScanStrategy.run()` methods.
- Delete `run_agentic_loop()`, `run_subagents()` in `common.py`.
- Delete `FindingParser`.
- Mark `OrchestrationShape` deprecated; keep enum for snapshot deserialization.
- Remove the `use_new_runner` flag from `UserStrategy` (everything is
  now the new runner).
- Drop the `feature-flag` dispatch in `worker.py` (only one path).

### Phase 5 — New capability strategies

The interesting phase. See `potential_expansions.md` § 1 for capability
priorities: verifier_wrapping, classifier_dispatch, taint_pipeline,
diff_blast_radius. Each is a new `UserStrategy` in `seed_builtins()` with
non-empty `subagents`.

## Worktrees on this machine

These should be cleaned up after merging:

| Worktree branch                          | Phase | Tip      |
|------------------------------------------|-------|----------|
| `worktree-agent-aac61af69ffd236f4`       | 0     | b4008d2  |
| `worktree-agent-a0f8bf622e72a828e`       | 1     | dda1cfe  |
| `worktree-agent-ab7b0c16a98d3cbe5`       | 2     | 4a87375  |
| `worktree-agent-a9404be13efb5b714`       | 3a    | f390776  |

The branches are stacked. Either rebase them all into a single feature
branch before opening a PR, or open a stacked-PR sequence.
