# Potential framework expansions

A list of larger, optional changes to the framework. Each entry is a complete
proposal: what to add, why it's worth it, what it costs, and what it enables.

These are not committed; they are candidates surfaced from analysis of the
current strategies and the gaps in `experiment_gaps.md`.

---

## 1. Agent topology: `for_each` + recursive subagents

> **Status (delivered):** The hybrid for_each + recursive subagents architecture is
> implemented. `invoke_subagent_batch` subsumes the proposed `for_each` primitive —
> parents can declare a list of inputs in one tool call and the runtime fans out in
> parallel. Recursive `subagents` works with bounded depth and invocation caps. See
> ARCHITECTURE.md § 7 for the implementation. The five existing shapes are
> reimplemented as parent-subagent strategies; four new capability strategies
> (`single_agent_with_verifier`, `classifier_dispatch`, `taint_pipeline`,
> `diff_blast_radius`) ship as builtins.
>
> **Caveats:** Live-API verification is still pending — all tests use scripted
> providers. Real precision/recall comparisons against the Phase 3 baselines have
> not been run. The `dispatch_fallback="programmatic"` mechanism on `per_vuln_class`
> exists as the reproducibility lifeline but has not been measured against supervisor
> variance under live conditions.

### Summary

Replace the hard-coded five `OrchestrationShape` enum with two composable
primitives in `UserStrategy`:

- **`for_each: ForEachSpec | None`** — declarative static fan-out. The worker
  (not an LLM) instantiates the strategy once per item in a static collection
  (file glob, vuln class enum, list of Semgrep findings, etc.) and aggregates
  results.
- **`subagents: list[UserStrategy]`** — recursive. When non-empty, the worker
  injects an `invoke_subagent(role, input)` tool into the parent's bundle. The
  parent decides at runtime whether to invoke, which one, and how often.
  Subagent runs in isolated context, returns its output as a tool result.

This is a hybrid between "just a supervisor LLM" and a full graph IR. It's
strictly more expressive than today's five fixed shapes; it's strictly less
complex than a DAG executor with edges, types, and a graph editor canvas.

### Motivation

Today's five shapes (`single_agent`, `per_file`, `per_vuln_class`, `sast_first`,
`diff_review`) are hard-coded in Python via `_SHAPE_TO_STRATEGY` in `worker.py`.
Users can change prompts/model/tools/overrides but cannot introduce new
orchestration. Every shape worth adding for the experiments in
`experiment_gaps.md` (verifier wrapping, classifier→specialist routing,
reviewer↔verifier dialogue, diff blast-radius) needs orchestration the current
schema can't express.

A full graph IR (nodes, edges, types, DAG executor, validator, canvas UI) is
the maximalist answer but is over-engineered for the cases in scope:

- The five existing shapes are all static fan-out. They benefit from no
  supervisor.
- The new shapes worth adding are inherently *dynamic* (parent decides what to
  invoke based on what it finds). A static graph IR doesn't help.

The opposite extreme — replace all orchestration with a supervisor LLM — is
also wrong. A supervisor driving `per_vuln_class` would burn 16 turns of
supervisor tokens just to dispatch the specialists, lose the executor's
parallel fan-out (1× → 16× wall clock), and reintroduce variance in which
specialists fire on each run (a benchmarking poison).

The hybrid keeps deterministic parallel fan-out for the static cases and adds
LLM judgement only for the cases that require it.

### Mapping today's shapes onto the new schema

| Shape              | Expression                                                                       |
|--------------------|----------------------------------------------------------------------------------|
| `single_agent`     | Plain `UserStrategy`, no `for_each`, no `subagents`.                              |
| `diff_review`      | Same, with `diff` and `changed_files` as inputs.                                  |
| `per_file`         | `for_each` over file glob.                                                        |
| `per_vuln_class`   | `for_each` over `VulnClass` enum.                                                 |
| `sast_first`       | Wrapper strategy: Semgrep tool call, then `for_each` over flagged files.          |

All five remain expressible. The hard-coded `_SHAPE_TO_STRATEGY` dispatch goes
away in favor of a single graph-aware strategy runner.

### New shapes this enables

| Pattern                            | Mechanism                                                                                       |
|------------------------------------|-------------------------------------------------------------------------------------------------|
| Verifier wrapping any strategy     | Wrapper with `subagents: [inner_strategy]`; parent runs inner, post-processes findings.          |
| Classifier → specialist routing    | Parent with `subagents: [sqli, xss, ...]`; parent dispatches per file based on classification.  |
| Reviewer ↔ verifier dialogue      | Reviewer with `subagents: [verifier]`; recursion bounded by `max_subagent_depth`.                |
| Progressive deepening              | Cheap-model survey → expensive specialist on candidates → verifier on confirmed.                 |
| Hypothesis-driven follow-up        | Reviewer forms hypothesis, spawns focused verifier with explicit context, iterates.              |
| Multi-stage dataflow analysis      | Source-finder → sink-tracer (`for_each` over sources) → sanitization-checker (per path).         |

### Security review capabilities enabled

The reason this is worth doing. Each of these is structurally impossible in
today's shapes (or possible only at the cost of stuffing the entire reasoning
chain into one agent's transcript, which is what makes the current strategies
shallow on chained vulns).

#### Multi-stage dataflow analysis (uses sequential edges + typed handoffs)

- **Taint tracing.** Stage 1 enumerates user-input sources (handlers, CLI, env,
  file reads). Stage 2 traces each source through the call graph. Stage 3
  checks for sanitization along each path. Each stage's output is the next
  stage's fan-out input. Applies to SQLi, SSRF, command injection, path
  traversal, XSS in templated output.
- **Trust-boundary / deserialization chain.** Stage 1 finds entry points where
  serialized data crosses a boundary. Stage 2 maps to deserialization sites.
  Stage 3 reasons about gadget chains. Single-site flagging (what every SAST
  already does) misses the chain.
- **Cryptographic protocol verification.** Stage 1 catalogs key/IV/nonce
  generation. Stage 2 traces reuse across cipher operations. Stage 3 checks for
  nonce reuse, ECB mode, weak parameters across the protocol — not just at one
  site.
- **Authorization model verification.** Stage 1 lists endpoints with declared
  required-roles/scopes. Stage 2 lists the resources each endpoint touches.
  Stage 3 checks that resource access policy matches endpoint authorization.
  Not expressible as a single-pass scan; it is a join across specialist
  outputs.

#### Adaptive analysis (uses dynamic dispatch — supervisor decides what to invoke)

- **Verification-by-execution.** Reviewer flags a candidate vuln; parent
  invokes a "PoC writer" subagent that constructs an input, runs it in a
  sandbox, and returns confirmed/refuted. Only confirmed vulns get Important
  severity. The single biggest false-positive lever in security review.
- **Hypothesis-driven follow-up.** Reviewer forms a hypothesis ("IDOR at
  `/api/users/:id/profile`"); parent spawns a focused verifier with explicit
  context (controller, auth middleware, related tests) and a yes/no/needs-more
  question. Iterates if needed. Closer to how a human pentester works than
  "scan everything for everything."
- **Diff blast-radius.** For a PR, a blast-radius agent finds transitive
  callers of changed functions; parent then invokes one specialist per caller
  to check whether the behavior change breaks an assumption. Cardinality
  unknown until runtime — `for_each` over a static collection cannot express
  this.
- **Multi-step exploit chain construction.** Specialist A finds an info
  disclosure; specialist B finds an auth bypass conditioned on the disclosed
  info; specialist C finds an RCE conditioned on the bypass; a chain-combiner
  supervisor argues whether the three compose. Single-pass scanners report
  each individually and never make the chain argument — which is what makes
  most real CVEs interesting.

#### Cross-context reconciliation (uses LLM-driven aggregation)

The current `deduplicate()` is structural (`(file, vuln_class)` + 5-line
proximity). It cannot do:

- **Cross-language taint reconciliation.** Python backend specialist finds a
  SQL handler; JS frontend specialist finds a fetch call to it; Go service
  specialist finds a queue producer. A reconciler subagent stitches the
  cross-language flow. Each language specialist alone sees only its half of
  the boundary.
- **Cross-microservice posture comparison.** "The auth check in
  `service_a/login.py` differs from `service_b/login.py` — is the difference
  intentional?" Two specialist runs plus a comparator subagent.
- **Cross-class deduplication.** Today, sqli and rce specialists flagging the
  same `subprocess.run(sql_query)` produce two findings forever. A semantic
  aggregator can collapse them with a single "this is one bug; classify as
  RCE-via-SQLi-sink."
- **Severity recalibration with context.** A finding's severity often depends
  on context the specialist didn't see (is this code reachable in production?
  behind auth?). A context-aware re-rater subagent adjusts severity using
  cross-cutting info no single specialist had.

#### Iterative dialogue (uses recursion / cycles with termination)

- **Reviewer ↔ verifier loop.** Reviewer claims; verifier challenges; reviewer
  either retracts or produces stronger evidence; loop until convergence or
  depth cap.
- **False-positive triage as a conversation.** Verifier asks the reviewer for
  specific evidence ("show me the test that would fail"); reviewer either
  produces it or downgrades the finding. Each round prunes noise.
- **Progressive deepening.** Reviewer surveys broadly with a cheap model; for
  each candidate, invokes a deeper specialist; for each confirmed, invokes a
  verifier. A budget hierarchy that matches the funnel.

### Highest-leverage capabilities for benchmarking

If implementation effort needs to be staged, prioritize the additions that
attack the framework's known precision/recall weaknesses with the least
benchmark-noise from supervisor variance:

1. **Verification-by-execution** — directly attacks precision. The single
   highest-leverage addition.
2. **Multi-stage taint** for dataflow vuln classes (SQLi, SSRF, command
   injection, path traversal) — directly attacks recall on chained vulns.
3. **Cross-class semantic aggregation** — directly attacks the duplicate /
   noise problem `deduplicate()` cannot solve.
4. **Diff blast-radius** — fills the obvious shallow point in `diff_review`.

The remaining patterns (hypothesis-driven follow-up, cross-language
reconciliation, exploit chains, full dialogues) are higher-variance and harder
to benchmark cleanly across experiment conditions; defer until 1–4 are in.

### Implementation cost

- **Schema:** two fields on `UserStrategy` (`for_each`, `subagents: list[UserStrategy]`).
  No new top-level concept. The existing `StrategyBundleDefault` content is the
  per-node bundle unchanged.
- **Worker:** `for_each` loop around strategy execution; `invoke_subagent` tool
  injection plus a recursive call into the existing strategy runner. ~200 lines.
- **Validator:** check `subagents` references resolve, recursion has a depth
  cap, `for_each` source is a known collection. ~20 lines.
- **Frontend:** two form fields in `StrategyEditor.tsx`. The existing per-key
  override UI generalizes to `for_each`.
- **Caps:** add `max_subagent_depth`, `max_subagent_invocations` to
  `StrategyBundleDefault`. The existing `max_experiment_cost_usd` covers the
  cost ceiling.
- **Tests:** unit tests per primitive, integration tests for at least one new
  shape (verifier wrapping is the cheapest first integration target).

The full graph IR alternative (DAG executor, edge types, I/O type system,
cycle detection, graph editor canvas, per-edge tracing) is at least an order
of magnitude more code and surface, and the cases it uniquely enables (user-
drawn arbitrary topologies) are not on the experiment roadmap.

### Hard parts

- **Determinism / replay.** With dynamic subagent invocation, the topology of
  a specific run depends on what the parent decided. Replaying requires
  logging the full dispatch trace, not just the strategy snapshot. Solvable
  but new infrastructure.
- **Isolation vs context inheritance.** SDK Subagents start with fresh context
  (the right default — no priming bias for verification). Sometimes a subagent
  should inherit a slice of parent context. Default to fresh; add explicit
  `inherit_context: [...]` opt-in.
- **Sibling communication.** Two subagents spawned by the same parent cannot
  talk to each other (isolated context). Solvable with a message substrate;
  not needed for the prioritized capabilities above. Defer.
- **Async vs sync.** Sync (parent's `invoke_subagent` blocks until child
  returns) is dramatically simpler. Async (parent gets a handle, polls)
  multiplies complexity. Defer until a use case forces it.
- **SDK choice.** The framework runs on LiteLLM with a hand-rolled agent loop;
  it is not on the Claude Agent SDK. Subagents-as-isolated-sessions, hooks,
  skills, structured outputs, and sessions/fork are SDK features that would
  have to be re-implemented in the bespoke loop or unlocked by a stack swap.
  This is independent of the topology change but worth flagging — the
  topology mechanics are achievable on the existing stack; the SDK-native
  features in `experiment_gaps.md` are not.

### Out of scope for this expansion

- A general agent graph with arbitrary cycles, edge types, and reactive
  execution.
- A graph editor canvas in the frontend.
- Sibling-subagent communication / message substrate.
- Async subagent dispatch.
- Replacing LiteLLM with the Claude Agent SDK.

These are deliberately deferred. The hybrid above achieves the experiment
goals at a fraction of the cost.
