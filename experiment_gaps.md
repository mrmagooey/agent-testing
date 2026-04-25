# Experiment gaps vs code.claude.com docs

Comparison of what the `sec_review_framework` strategies and experiment matrix
exercise against what the public Claude Code docs at https://code.claude.com/docs
describe. Sources: the Code Review page (`/en/code-review`) and the full doc
index at `/en/llms.txt`.

Current experimental axes (`ExperimentRun`):
`strategy`, `tool_variant`, `tool_extensions` (TREE_SITTER/LSP/DEVDOCS),
`review_profile`, `verification_variant`, `verifier_model_id`, `model_id`,
`repetition_index`. Outcomes measured: findings (pre/post verification),
precision/recall, tokens, cost, duration.

---

## Part A — Gaps vs the Code Review docs

The framework's strategies are **security-vulnerability-focused** with a
CVSS-style 5-level severity (`critical/high/medium/low/info`) and a fixed
16-class taxonomy. Anthropic Code Review is a broader correctness-and-security
reviewer with a 3-tag severity model.

### A1. Scope / category gaps
The 16 vuln classes don't cover most of what Code Review explicitly catches:
- Logic errors that aren't security (off-by-one, wrong control flow, state-machine
  bugs not covered by `logic_bug`'s narrow TOCTOU/race framing).
- Subtle regressions (changed behavior breaking callers).
- Broken edge cases (null/undefined, malformed input not reaching a security sink).
- Resource leaks, API contract drift, migration backward-compat,
  performance regressions (N+1 queries), CLAUDE.md docs drift.

No strategy emits a non-security correctness finding — `logic_bug` is the only
escape valve and it's framed as a security class.

### A2. Severity model mismatch
- Docs define **Important / Nit / Pre-existing**. The framework has no `Nit` tier
  and no `Pre-existing` flag on the `Finding` schema. `diff_review` *prompts* the
  agent to consider three buckets (new / pre-existing-in-touched /
  interaction-with-unchanged) but the output schema can't carry that distinction
  — only ground-truth labels (`introduced_in_diff`) track it.
- No strategy tests whether **severity calibration overrides** (REVIEW.md-style
  "Reserve Important for…") actually shift the model's severity assignments.

### A3. Verification step
Docs describe a dedicated verification pass that "checks candidates against
actual code behavior to filter false positives." Framework status:
- `verification_variant` / `verifier_model_id` / `findings_pre_verification` /
  `verification_tokens` exist as **matrix-level axes** — verification is a
  pluggable post-strategy pass.
- At the **strategy level**, only `sast_first` does explicit triage
  (TP/FP/escalate of Semgrep hits). `single_agent`, `per_file`, `per_vuln_class`,
  `diff_review` produce findings in a single pass.
- Gap: no strategy-internal verification (run-the-code, follow-up turn,
  cite-line-evidence). The matrix-level verifier is one-shot post-hoc.

### A4. Custom-instructions adherence
The docs' core customization surface — **REVIEW.md / CLAUDE.md** — has no eval
probing whether the model honors:
- Path-scoped skip rules ("don't flag in `src/gen/`", lockfiles, vendored deps).
- Branch-pattern skips (machine-authored branches).
- "Always check X" repo-specific rules ("new API routes need an integration test").
- Verification bar ("behavior claims need a `file:line` citation, not naming
  inference").
- CLAUDE.md violations as nits, including the bidirectional "PR makes CLAUDE.md
  outdated" check.

`review_profile` injects a `system_prompt_modifier` but no eval grades adherence.

### A5. Volume / signal-to-noise behaviors
- No **nit-cap** test ("report at most 5 nits, mention rest as count").
- No **re-review convergence** test ("after first review, suppress new nits, post
  Important only") — strategies are stateless single-shot.
- No **summary-shape** test ("lead with `2 factual, 4 style`" / "no blocking
  issues").

### A6. Iterative / multi-push behavior
Docs describe push-triggered re-reviews that auto-resolve threads when fixed.
No strategy models a multi-revision sequence; everything runs once against a
fixed snapshot.

### A7. Pre-existing-in-unchanged-but-interacting code
`diff_review`'s prompt mentions this third bucket but the schema can't tag it,
and there's no eval label distinguishing "interaction with unchanged code" from
"pre-existing in touched file" — the strategy can be prompted but not graded
on that axis.

### A8. False-positive / noise metrics
Strategies are evaluated on recall/precision over the 16 vuln classes. No
metric for:
- Nit volume per review (could a repo set a cap and have it respected?).
- Severity-distribution shift under REVIEW.md overrides.
- Cost-vs-PR-size scaling (docs claim $15-25/review on average).

---

## Part B — Broader docs surface not exercised by experiments

### B1. Agent SDK primitives the strategies don't exercise
- **Skills** — docs treat Skills as a first-class extension. The 16 vuln-class
  specialists are bespoke prompt files in `system/per_vuln_class/*.txt`, not
  skills. No experiment compares "specialist as skill" vs "specialist as
  system-prompt override".
- **Hooks** — no strategy uses `PreToolUse` / `PostToolUse` / `Stop` hooks.
  Could enforce "re-read CLAUDE.md before posting", "run linter before flagging
  style", "cap nits at N at the Stop hook". Not a tested variant.
- **Plugins** — strategies aren't distributed as plugins; the plugin/marketplace
  path isn't probed.
- **Subagents (SDK abstraction)** — strategies fan out via their own bespoke
  orchestration. The Agent SDK's first-class `Subagents` (isolated context,
  parallel, specialized instructions) isn't compared against the bespoke
  fan-out. Could matter for `per_vuln_class` and `per_file`.
  _Status (post-Phase-7, 2026-04-25): **Closed.** Bespoke fan-out replaced by
  the SDK-style subagents pattern via pydantic-ai's agent-as-tool mechanism.
  `invoke_subagent` and `invoke_subagent_batch` in `strategies/runner.py`
  provide isolated-context parallel dispatch. Implemented in Phases 1–4._
- **Tool search** — extensions load eagerly. The "scale to thousands of tools
  by loading on demand" pattern isn't tested as the extension matrix grows.
- **Structured outputs** — findings are parsed from free-text ```json blocks.
  Schema-validated structured outputs (Pydantic/Zod) aren't a tested variant;
  could affect parse-failure rate and hallucinated-field rate.
  _Status (post-Phase-7, 2026-04-25): **Closed.** `output_type_name` on
  `StrategyBundleDefault` enforces pydantic-ai structured outputs for subagents
  (six registered types in `agent/output_types.py`). The top-level runner uses
  `output_type=list[Finding]`. Implemented in Phase 5._
- **Sessions / continue / resume / fork** — every strategy is single-shot.
  Iterative behavior — "follow-up turn: verify finding #3 against the test that
  exercises it", or fork-and-explore — isn't tested.
- **Modifying system prompts** — docs describe three approaches (output styles,
  `systemPrompt` append, custom). `review_profile` only does append.
- **Output styles** — not varied. Could change downstream usability of findings.
- **Permissions** (declarative allow/deny rules) — not varied. Restricting vs
  allowing certain tools could change what evidence agents seek.
- **Sandboxing** — the sandboxed-bash docs cover safer autonomous execution.
  Not tested as a condition for verification-by-running-the-code.
- **File checkpointing** — agents could explore-and-revert during verification
  (e.g., apply a hypothetical fix to confirm the bug). Not exercised.
- **Todo tracking** — multi-step verification with todos isn't tested.
- **User input and approvals**, **Streaming output / streaming-vs-single mode**
  — not measured.

### B2. Cost & performance variables the docs treat as load-bearing
- **Prompt caching** — Cost Tracking docs emphasize this. `RunResult` records
  `total_input_tokens` but no cached-token field. Whether enabling prompt
  caching changes cost-vs-quality (especially on repeat strategies that re-read
  the same repo) isn't measured.
- **Extended thinking** budget — known to move quality on hard problems; not a
  varied axis.
- **Spend caps** — Code Review docs describe per-service spend caps. Framework
  has `max_experiment_cost_usd` (similar idea) but doesn't test behavior under
  cap-hit.
- **Fast mode** (Opus 4.6 faster-output variant) — not a model variant tested.

### B3. Process modes
- **Auto mode** behaviors — not a condition.
- **Plan mode** — does pre-planning before reviewing improve recall? Untested.
- **Computer use** — out of scope for code review.

### B4. Observability
- **OpenTelemetry** — framework has its own metrics; SDK-native otel traces
  aren't compared.

### B5. Out of scope (named for clarity)
GitHub Actions, GitLab CI/CD, GitHub Enterprise Server, Web sessions, Remote
Control, Slack, Chrome, Channels, Routines, Scheduled tasks, Agent teams,
Desktop scheduled tasks, Voice dictation, IDE integrations — operational /
integration surfaces, not experimental conditions for a benchmarking framework.

---

## Highest-impact gaps

The four most likely to change experimental outcomes if added as conditions:

1. **Structured outputs** — direct effect on parse-failure and
   field-hallucination rates.
2. **Hooks + Skills** as a way to deliver per-class specialization, vs the
   current bespoke prompt files.
3. **Prompt caching** as a cost axis — likely large effect when the same repo
   is reviewed under many strategy/extension combinations.
4. **Sessions / multi-turn verification** — current `verification_variant` is
   one-shot post-hoc; the SDK's continue/fork primitives would let a verifier
   dig in iteratively.

Secondary, but high signal for the Code Review use case specifically:

5. **Important / Nit / Pre-existing severity model** on the `Finding` schema,
   with an eval that grades severity-calibration adherence.
6. **REVIEW.md / CLAUDE.md adherence eval** — generate synthetic REVIEW.md
   files with skip rules, "always check" rules, severity overrides, and
   measure compliance.

---

## Note: graph node execution via Kubernetes Jobs

If/when strategies are modeled as a DAG of nodes (e.g. fan-out per file or per
vuln class, fan-in for aggregation/verification), use **Kubernetes Jobs** as
the execution mechanism for each node rather than long-lived worker pods.
Each graph node = one Job with its own pod, image, resource request, and
retry policy. Rationale:

- Per-node isolation (one node's OOM/crash doesn't take down siblings) and
  independent retry/backoff via `backoffLimit` and `restartPolicy: OnFailure`.
- Resource requests sized per node class — Semgrep/SAST nodes get CPU,
  LSP-extension nodes get memory, lightweight verifier nodes stay cheap.
- Natural fan-out/fan-in: parallel Jobs for independent nodes; the
  coordinator waits on Job completion before scheduling downstream nodes.
- Tool-extension matrix (TREE_SITTER/LSP/DEVDOCS) maps cleanly to per-Job
  sidecars or init containers, so the existing `workerTools.*.enabled`
  Helm switches translate to Job-template overrides.
- Pod logs and exit codes give per-node observability without bespoke
  instrumentation; ties into the existing `RunResult` token/cost capture
  by reading Job status + sidecar metrics on completion.

Trade-offs to validate: Job startup latency (image pull + scheduling) per
node vs. amortized cost in a long-lived pod; control-plane load when a
single experiment fans out to hundreds of Jobs; cleanup policy
(`ttlSecondsAfterFinished`) to keep the namespace tidy.
