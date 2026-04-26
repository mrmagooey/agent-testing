# Implementation plan: unified parent-subagent strategies via pydantic-ai

A staged plan to convert **all strategies** in the framework to a parent-agent
+ subagents model, implemented on top of pydantic-ai while preserving LiteLLM
as the underlying provider transport.

This supersedes the additive-only version of the plan. Every strategy — old
and new — becomes a parent agent that may invoke subagents. The existing five
shapes (`single_agent`, `per_file`, `per_vuln_class`, `sast_first`,
`diff_review`) are reimplemented as built-in parent strategies. The `for_each`
primitive sketched in `potential_expansions.md` § 1 is subsumed by an
`invoke_subagent_batch` tool exposed to parents.

## 1. Goal

Eliminate `OrchestrationShape` and the `_SHAPE_TO_STRATEGY` Python dispatch in
`worker.py`. Replace them with one runner that builds a pydantic-ai `Agent`
from a `UserStrategy` and runs it. When the strategy declares `subagents`,
the runner injects two tools:

- **`invoke_subagent(role, input)`** — single dispatch, returns one result.
- **`invoke_subagent_batch(role, inputs)`** — batch dispatch, runtime fans
  out in parallel, returns a list of results in input order.

Both tools are bounded by depth and invocation caps in `SubagentDeps`.

The five existing shapes become five built-in parent strategies whose
orchestration is encoded in their system prompts (and, where reproducibility
demands it, in a programmatic dispatch validator).

## 2. Why this unification

### Wins

- **One execution path.** Single runner, single mental model. Delete
  `_SHAPE_TO_STRATEGY`, `run_agentic_loop()`, `run_subagents()`,
  `FindingParser` — collapse the multi-path runtime.
- **Composability.** Any strategy wraps any other. A verifier strategy wraps
  `per_vuln_class` exactly the same way it wraps `single_agent`. New
  capabilities don't have to be plumbed through two orchestration paradigms.
- **Uniform telemetry.** Token usage, depth, invocation counts, per-node
  traces — one shape regardless of strategy.
- **User-extensible orchestration.** Users editing strategies in the UI work
  with one paradigm: a parent prompt + a subagent list. No second
  Python-orchestration tier they can't customize.

### Costs (and mitigations)

- **Supervisor tokens.** A `per_vuln_class` parent burns tokens dispatching
  16 specialists. With Haiku as supervisor and a strict enumerated prompt:
  <$0.05 per run. Negligible vs. specialist cost, but tracked.
  → Per-agent `model_id` lets the parent be cheap independent of specialist
  models.
- **Parallelism.** Series of `invoke_subagent` calls loses today's parallel
  `ThreadPoolExecutor` fan-out.
  → `invoke_subagent_batch` — one tool call, runtime parallelizes via the
  existing thread pool.
- **Variance / reproducibility.** A supervisor might omit specialists or
  permute inputs across runs. For a benchmarking framework this is a
  confound.
  → (a) Strict prompts with full dispatch enumeration; (b) temperature=0 on
  supervisors; (c) a programmatic dispatch validator that verifies the
  parent dispatched the expected list and re-prompts (or completes) on
  miss; (d) parity tolerance bands on Phase-3 tests.
- **Parity for existing shapes.** Reproducing today's deterministic Python
  fan-out exactly under a supervisor LLM is the dominant Phase-3 risk.
  → Per-shape dispatch validators baked into the parent strategy's runner
  (small backslide from "pure parent-agent," invisible to users).

The mitigations are sound and the architectural payoff is large. The
trade is supervisor-token cost + parity-tuning effort in exchange for
collapsing two orchestration paradigms into one.

## 3. Architecture

```
run_strategy(user_strategy, target, model, tools)     # ONE runner
   └── pydantic_ai.Agent.run_sync()
         ├── pydantic-ai tool decorators              # tools from ToolRegistry
         ├── invoke_subagent          (if subagents declared)
         ├── invoke_subagent_batch    (if subagents declared)
         ├── output_type              (Pydantic, per UserStrategy)
         └── LiteLLMModel adapter
              └── existing LiteLLMProvider
                    └── litellm.completion()
```

`_SHAPE_TO_STRATEGY` is gone. The five existing `ScanStrategy` subclasses are
gone — replaced by `UserStrategy` definitions in `seed_builtins()` whose
orchestration is the parent prompt + subagent list.

What survives unchanged:

- `UserStrategy` schema (gains four fields, keeps everything else).
- `StrategyRegistry`, `BundleSnapshot`, `ExperimentRun.bundle_json`
  immutability and content-hash IDs.
- `LiteLLMProvider`, model catalog, provider routing.
- `deduplicate()` — used by parents that post-process subagent outputs
  structurally.
- `ToolRegistry` with audit logs (adapted to pydantic-ai tool registration).
- The matrix-level verification axis (`verification_variant`,
  `verifier_model_id`, `findings_pre_verification`).

## 4. Schema changes

In `data/strategy_bundle.py`:

```python
class StrategyBundleDefault(BaseModel):
    # ... existing fields ...
    subagents: list[str] = Field(default_factory=list)
    max_subagent_depth: int = 3
    max_subagent_invocations: int = 100
    max_subagent_batch_size: int = 32
```

`OrchestrationShape` is deprecated. Existing serialized snapshots may still
carry it; new strategies omit it (or set a single `parent_agent` value for
backward compat). The five shape values become metadata tags on the
corresponding builtin strategies, used only for analytics.

Validation:

- Every ID in `subagents` resolves in the registry at expand time.
- No direct self-reference.
- All caps positive; `max_subagent_batch_size <= max_subagent_invocations`.

`BundleSnapshot.capture()` content-hash includes the new fields, so any
edit produces a new strategy ID the same way today's edits do.

## 5. Subagent runtime mechanics

### Tool injection

When `strategy.default.subagents` is non-empty, the runner registers both
tools on the parent agent. Sketch:

```python
@parent_agent.tool
async def invoke_subagent(
    ctx: RunContext[SubagentDeps],
    role: str,
    input: dict,
) -> SubagentOutput:
    _check_caps(ctx, count=1)
    child_strategy = ctx.deps.subagent_strategies[role]
    return await _run_child(child_strategy, input, ctx.deps)

@parent_agent.tool
async def invoke_subagent_batch(
    ctx: RunContext[SubagentDeps],
    role: str,
    inputs: list[dict],
) -> list[SubagentOutput]:
    _check_caps(ctx, count=len(inputs))
    if len(inputs) > ctx.deps.max_batch_size:
        raise ModelRetry(f"Batch too large; max {ctx.deps.max_batch_size}")
    child_strategy = ctx.deps.subagent_strategies[role]
    with ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(
            lambda inp: _run_child_sync(child_strategy, inp, ctx.deps),
            inputs,
        ))
```

Properties:

- **Isolation.** Each child gets a fresh pydantic-ai `Agent` — no shared
  message history. Parent only sees children's structured outputs as
  tool results.
- **Parallelism.** `invoke_subagent_batch` mirrors today's
  `run_subagents(parallel=True)` (`common.py:267-273`) — same `ThreadPool`,
  same audit-log cloning, exposed as a tool.
- **Recursion.** A child with its own `subagents` list gets the same tools
  injected. `SubagentDeps.depth` increments; cap stops runaway.
- **Caps.** Depth, total invocation count, and batch size all enforced;
  exceeded caps raise `ModelRetry` so the parent sees the failure and can
  decide.
- **Audit.** Each child gets a cloned `ToolRegistry` (existing pattern at
  `common.py:268`).

### Output handling

Parent's `output_type` is whatever the strategy declares — typically
`list[Finding]`, but a parent that does its own synthesis can declare a
richer schema (e.g., `ReviewSummary`). Subagent outputs are
`SubagentOutput { role, output, usage }` so the parent receives structured
data it can reason about, not raw text.

`FindingParser`'s regex extraction is replaced entirely by pydantic-ai's
structured output. Validation failures are auto-retried by pydantic-ai
instead of silently dropped.

## 6. The five existing shapes, reimplemented as parent strategies

Each becomes a `UserStrategy` in `seed_builtins()`. Orchestration moves
from Python into the parent prompt; reproducibility-critical dispatch is
backed by a programmatic validator.

### `single_agent`
- `subagents: []`
- Parent does the review itself, output `list[Finding]`. Functionally
  identical to today.

### `diff_review`
- `subagents: []`
- Parent reviews the diff itself, output `list[Finding]`. Functionally
  identical to today.

### `per_file`
- `subagents: ["file_reviewer"]`
- Parent prompt: "List source files matching `{glob}` using the
  `list_files` tool. Then issue exactly one `invoke_subagent_batch` call
  with role=`file_reviewer` and inputs containing one `{file_path: f}`
  per file. Aggregate the returned `list[Finding]`s and dedupe by (file,
  line, vuln_class)."
- Dispatch validator: post-run, the runner checks that
  `invoke_subagent_batch` was called with the expected file list. If
  files were missed, re-prompt with the missing list.
- Post-processing: `deduplicate()` on the union.

### `per_vuln_class`
- `subagents: ["sqli_specialist", ..., "other_specialist"]` (16 entries)
- Parent prompt: enumerates the 16 vuln classes verbatim and instructs:
  "Issue exactly one `invoke_subagent_batch` call with one input per role
  in the list. Aggregate findings."
- Dispatch validator: verifies all 16 roles were dispatched. If any
  missed, programmatically completes them (calls the missing specialists
  directly, bypassing the supervisor for those). This is the
  reproducibility lifeline for benchmarking.
- Post-processing: `deduplicate()` (existing structural dedup, since each
  specialist is class-constrained).

### `sast_first`
- `subagents: ["triage_agent"]`
- Parent prompt: "Run `run_semgrep` (tool). For each flagged file, issue
  one `invoke_subagent_batch` call with role=`triage_agent` and inputs
  `[{file_path, sast_findings} for each flagged file]`. Return triaged
  findings."
- Dispatch validator: verifies every flagged file was dispatched.

### Builtin specialist subagents

The 16 vuln-class specialists, the `file_reviewer`, and the
`triage_agent` are themselves `UserStrategy` objects in `seed_builtins()`.
Their system prompts come from the existing per-class prompt files
(`prompts/system/per_vuln_class/{vuln_class}.txt`) — those files don't
move; the registry just references them as the bundle's
`system_prompt`.

## 7. Phased rollout

### Phase 0 — Spike (1–2 days)

Confirm pydantic-ai + LiteLLM round-trips correctly:
- Custom `LiteLLMModel` against pydantic-ai's `Model` ABC.
- Tool calling via LiteLLM through Anthropic-direct, Bedrock-Claude,
  Vertex-Claude.
- `output_type=list[Finding]` validates and retries.
- Two-level agent-as-tool chain works (parent invokes child, gets
  structured output back).
- Token-usage accounting matches `LiteLLMProvider`.

Exit: working demo + written answers to each decision point.

### Phase 1 — Adapter layer (2–3 days)

- `src/sec_review_framework/agent/litellm_model.py` — pydantic-ai Model
  adapter over `LiteLLMProvider`.
- `src/sec_review_framework/agent/tool_adapter.py` — converts
  `ToolDefinition` → pydantic-ai tool callable, preserves audit logging.
- `src/sec_review_framework/agent/subagent.py` — `SubagentDeps`,
  `SubagentOutput`, `invoke_subagent` / `invoke_subagent_batch` factories.
- Unit tests per module. Existing test suite still passes (no strategy
  code changes yet).

### Phase 2 — The unified runner (3–4 days)

- `src/sec_review_framework/strategies/runner.py` —
  `run_strategy(user_strategy, target, model, tools) -> StrategyOutput`.
  Builds a pydantic-ai Agent from a `UserStrategy`, registers tools,
  injects subagent dispatchers if `subagents` is non-empty, runs the
  agent, returns the parsed output.
- `worker.py` gains a code path that uses `run_strategy()` for any
  strategy that opts into the new runner via a per-strategy feature
  flag. Existing `_SHAPE_TO_STRATEGY` dispatch still active for unflagged
  strategies.
- Smoke test: `single_agent` reimplemented as a `UserStrategy` going
  through `run_strategy`, parity-tested against existing
  `SingleAgentStrategy`.

### Phase 3 — Reimplement the five built-in shapes (5–10 days)

In order of risk (lowest first):
- `single_agent` — trivial; parity expected.
- `diff_review` — trivial; parity expected.
- `sast_first` — one subagent role; dispatch validator straightforward.
- `per_file` — one subagent role over a dynamic file list; dispatch
  validator non-trivial (the file list is enumerated by the parent
  itself). Tolerance: ±5% findings count, recall within ±5% on labeled
  set.
- `per_vuln_class` — 16 subagent roles; the dispatch-completeness
  validator is critical. Same tolerance bands.

Each shape ships when its parity test passes. Cost-drift bound: +20% on
total tokens (covers supervisor turns + dispatch validator re-prompts).
If a shape can't reach parity within the bound, its parent runner gains
a programmatic-dispatch fallback that bypasses the supervisor for the
fan-out step (still parent-subagent in shape; deterministic in
dispatch).

### Phase 4 — Delete the old code (1–2 days)

Once all five shapes pass parity:
- Delete `_SHAPE_TO_STRATEGY` in `worker.py`.
- Delete the five `ScanStrategy` subclass `run()` methods.
- Delete `run_subagents()`, `run_agentic_loop()` in `common.py`.
- Delete `FindingParser` (replaced by `output_type`).
- Mark `OrchestrationShape` deprecated; keep enum for snapshot
  deserialization.
- Per-strategy feature flag from Phase 2 goes away — `run_strategy()`
  is the only path.

### Phase 5 — New capability strategies (5–10 days)

Author new built-in strategies that exploit subagents:
- `verifier_wrapping` — generic wrapper; parent invokes inner strategy,
  then verifier subagent per finding.
- `classifier_dispatch` — replaces `per_vuln_class` for cost-sensitive
  use; classifier picks (file, class) pairs, only those specialists
  dispatched.
- `taint_pipeline` — multi-stage source → sink → sanitization.
- `diff_blast_radius` — extends `diff_review` with transitive-caller
  specialists.

Run experiments comparing these to the Phase-3 baselines on the labeled
eval set. Record precision/recall delta and cost delta.

### Phase 6 — Frontend (2–3 days)

`StrategyEditor.tsx` gains:
- Subagents multi-select (against the registry).
- Caps fields (`max_subagent_depth`, `max_subagent_invocations`,
  `max_subagent_batch_size`).
- The existing per-key override UI stays — it's now used by parents that
  want to specialize subagent bundles per dispatched key.

### Phase 7 — Documentation

- `ARCHITECTURE.md` rewritten around the unified runner.
- `potential_expansions.md` § 1 marked "delivered (with caveats)" and
  `for_each` noted as subsumed by `invoke_subagent_batch`.
- `experiment_gaps.md` updated where the new architecture closes gaps.
- README how-to: "authoring a parent-subagent strategy."

## 8. Test plan

Per phase:

- **Phase 0:** demo green; decision-point doc.
- **Phase 1:** unit tests for adapter, tool conversion, subagent
  injection; existing suite green.
- **Phase 2:** parity test for `single_agent` through `run_strategy`.
- **Phase 3:** parity tests for all five shapes within tolerance bands;
  cost-drift bound checks; dispatch-validator unit tests.
- **Phase 4:** existing suite green after deletions; no regressions on
  labeled eval set.
- **Phase 5:** end-to-end measured precision/recall for each new
  capability strategy.
- **Phase 6:** API + frontend tests for new fields.

Every change ships with tests (per `AGENTS.md`). Memory cap (2 GB peak
RSS) on integration tests applies — fixture repos stay small.

## 9. Risks and mitigations

- **Phase 3 parity fails for `per_vuln_class`** — supervisor LLM omits or
  permutes specialists.
  → Dispatch-completeness validator with programmatic fallback; tolerance
  bands on parity tests; if all else fails, ship `per_vuln_class` with a
  Python-driven fan-out inside its parent runner (still in the
  parent-subagent shape, just deterministic in dispatch). This is a
  small backslide invisible to users.
- **Cost regression on `per_vuln_class`.**
  → Cheap supervisor model (Haiku); cost-drift bound in tests.
- **Reproducibility for benchmarking** — supervisor variance becomes a
  confound across experiment conditions.
  → Temperature=0; strict prompts; dispatch validator; document the
  variance bound in eval reports.
- **pydantic-ai tool-call semantics differ from hand-rolled loop** —
  parallel calls, `tool_choice`, max-turn enforcement.
  → Lock settings to match current loop in Phase 1; document
  unavoidable diffs in parity test commentary.
- **Snapshot replay** — runs captured under the old loop won't replay
  identically.
  → Evaluation paths that re-parse raw LLM output keep `FindingParser`
  available as a legacy utility; the runtime change only affects new
  runs.
- **pydantic-ai version churn.**
  → Pin minor version in `pyproject.toml`; bump deliberately.

## 10. Out of scope

- A general agent graph with arbitrary cycles, edge types, reactive
  execution, or a graph-editor canvas.
- Sibling-subagent communication / message substrate.
- Async (non-blocking) subagent dispatch.
- Replacing LiteLLM with pydantic-ai native providers.
- Adopting the Claude Agent SDK (Skills, Hooks, Sessions, Plugins) —
  worth reconsidering after this lands and we have data on what's still
  missing.
- Custom orchestration in Python — once the unified runner ships, all
  strategy authoring is via `UserStrategy` (parent prompt + subagent
  list). Strategies cannot ship Python code.

## 11. Estimated effort

20–30 engineer-days. Phase 3 is the dominant schedule risk; expect 1–2
weeks of parity-tuning iteration before the existing shapes converge.
The new-capability work in Phase 5 is straightforward once the unified
runner is solid. If Phase 3 stalls on `per_vuln_class`, falling back to
the programmatic-dispatch fallback unblocks the rest of the rollout
without compromising the architecture.
