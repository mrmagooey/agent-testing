# CONTINUATION — parent-subagent architecture rollout

This branch contains the completed implementation of the plan in
`plan_subagents_pydantic_ai.md`. All phases 0–7 are complete.

## Phase status

| Phase | Status | Branch tip                                |
|-------|--------|-------------------------------------------|
| 0     | done + reviewed (PASS_WITH_NITS, fixed) | spike under `scripts/spike_pydantic_ai/` |
| 1     | done + reviewed (PASS_WITH_NITS, fixed) | `src/sec_review_framework/agent/`         |
| 2     | done + reviewed (PASS, nits fixed)      | `strategies/runner.py` + `worker.py` flag |
| 3a    | done + reviewed (PASS_WITH_NITS)        | `builtin_v2.single_agent`, `builtin_v2.diff_review` |
| 3b    | done + reviewed                         | `sast_first`, `per_file` reimplementations |
| 3c    | done + reviewed                         | `per_vuln_class` (16 specialists, dispatch validator) |
| 4     | done + reviewed                         | delete legacy code paths, rename IDs |
| 5     | done + reviewed                         | new capability strategies (verifier, classifier, taint pipeline, blast radius) |
| 6     | done + reviewed                         | frontend (`StrategyEditor.tsx`)           |
| 7     | done                                    | docs (ARCHITECTURE rewrite, README how-to, gaps/expansions update) |

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
- After Phase 2: +45 runner tests
- After Phase 3a: +30 parity tests
- After Phase 4: parity tests removed, legacy tests removed; net reduction
- After Phase 5: +634 strategy tests (`test_phase5_strategies.py`), +508 dispatch validator tests
- After Phase 6: +209 frontend Playwright tests (`StrategyEditor.test.tsx`)
- Phase 7: documentation only, no new tests

## Open items (post-Phase-7)

### Live-provider verification (open since Phase 0)

All tests use scripted (mock) providers. The plan's exit criterion 2
("Tool calling via LiteLLM through Anthropic-direct, Bedrock-Claude,
Vertex-Claude") is unmet. See `scripts/spike_pydantic_ai/SPIKE_RESULTS.md`
§ "UNMET EXIT CRITERION" for the commands. Run with real credentials
before any production deployment.

### Dependency conflict (open since Phase 0)

`pydantic-ai` and `semgrep` cannot coexist in one venv (otel and mcp
version pins disjoint). Separate venvs — `agent` extra for the runner,
`worker` extra for SAST workers. Documented in `pyproject.toml` and
`SPIKE_RESULTS.md` § 7.

### Precision/recall baselines not re-run

No live experiment has compared the new pydantic-ai runner against the
Phase 3 parity baselines under real LLM traffic. The `dispatch_fallback=
"programmatic"` path for `per_vuln_class` guarantees dispatch completeness
but the supervisor-variance delta vs the old deterministic runner is
unmeasured.

### Phase 4 nit: `tool_adapter.py` schema validation

`make_tool_callables()` uses `PAITool.from_schema()` which skips schema
validation of arguments at the tool boundary. The tool's own `invoke()`
is responsible for input validation. Non-blocking; track for hardening.

## Worktrees on this machine

These should be cleaned up after merging:

| Worktree branch                          | Phase | Tip      |
|------------------------------------------|-------|----------|
| `worktree-agent-aac61af69ffd236f4`       | 0     | b4008d2  |
| `worktree-agent-a0f8bf622e72a828e`       | 1     | dda1cfe  |
| `worktree-agent-ab7b0c16a98d3cbe5`       | 2     | 4a87375  |
| `worktree-agent-a9404be13efb5b714`       | 3a    | f390776  |
| `worktree-agent-a94fc6aec68fb5343`       | 4–6   | 5a80731  |
| `worktree-agent-aa47fee4178a2d399`       | 7     | (this)   |

Rebase into a single feature branch before opening a PR, or open a
stacked-PR sequence. After merging, delete all listed worktree branches.
