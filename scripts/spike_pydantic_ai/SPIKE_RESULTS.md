# Phase 0 Spike Results: pydantic-ai + LiteLLM integration

**Date:** 2026-04-25
**Branch:** main (worktree: agent-aac61af69ffd236f4)
**pydantic-ai version:** 1.87.0
**litellm version:** 1.83.13
**Status:** Verified with mocks; needs live-API confirmation before Phase 1.

---

## UNMET EXIT CRITERION: Live-provider verification

> **COORDINATOR DECISION REQUIRED** before Phase 1 can be authorized.

Exit criterion 2 ("Tool calling via LiteLLM through Anthropic-direct,
Bedrock-Claude, Vertex-Claude") was **not met**.  No Claude API credentials
are present in this environment, so all Phase 0 testing was performed against
`ScriptedLiteLLMProvider` (mocked responses only).

### Commands a reviewer with credentials would run to close the gap

**Anthropic-direct:**
```
ANTHROPIC_API_KEY=<key> uv run python scripts/spike_pydantic_ai/demo.py
```
(The demo already has a live-call path in `demo_live_if_available()` that
activates when `ANTHROPIC_API_KEY` is set.)

**Bedrock-Claude:**
```
AWS_ACCESS_KEY_ID=<id> AWS_SECRET_ACCESS_KEY=<secret> AWS_DEFAULT_REGION=us-east-1 \
  uv run python - <<'EOF'
import asyncio
from sec_review_framework.models.litellm_provider import LiteLLMProvider
from litellm_model import LiteLLMModel
from pydantic_ai import Agent
import sys, os
sys.path.insert(0, "scripts/spike_pydantic_ai")
provider = LiteLLMProvider(model_name="bedrock/anthropic.claude-3-haiku-20240307-v1:0")
model = LiteLLMModel(provider)
agent = Agent(model, system_prompt="You are a terse assistant.")
result = asyncio.run(agent.run("Say 'hello spike' and nothing else."))
print(result.output)
EOF
```

**Vertex-Claude:**
```
GOOGLE_APPLICATION_CREDENTIALS=<path-to-sa-json> VERTEXAI_PROJECT=<project> VERTEXAI_LOCATION=us-east5 \
  uv run python - <<'EOF'
import asyncio
from sec_review_framework.models.litellm_provider import LiteLLMProvider
from litellm_model import LiteLLMModel
from pydantic_ai import Agent
import sys
sys.path.insert(0, "scripts/spike_pydantic_ai")
provider = LiteLLMProvider(model_name="vertex_ai/claude-3-haiku@20240307")
model = LiteLLMModel(provider)
agent = Agent(model, system_prompt="You are a terse assistant.")
result = asyncio.run(agent.run("Say 'hello spike' and nothing else."))
print(result.output)
EOF
```

### Decision point

Choose one of:

- **Proceed to Phase 1 with mock-only verification.** Accept that live-provider
  behavior is unconfirmed; add live-provider smoke tests to the Phase 1
  acceptance criteria.
- **Pause Phase 0** until a reviewer with API credentials runs the commands
  above and confirms tool-call round-trips work end-to-end.

---

## Summary

All four Phase 0 exit criteria validated against `ScriptedLiteLLMProvider` (a
`LiteLLMProvider` subclass that returns pre-scripted responses without hitting
an external API).  No live API credentials were available in this environment.

---

## Decision Points

### 1. Does pydantic-ai's tool-call protocol round-trip cleanly through `litellm.completion()` for the providers this repo can reach?

**Answer:** Yes, with one important caveat about tool argument serialisation.

**Evidence:** Verified with mocks against `ScriptedLiteLLMProvider`.  The
protocol works end-to-end: pydantic-ai passes `ModelRequestParameters` with
`function_tools` (list of `ToolDefinition`), the adapter converts them to
the framework's `ToolDefinition` objects and calls `LiteLLMProvider.complete()`,
and tool-call results come back as `ToolCallPart` objects in the `ModelResponse`.

**Caveat found:** pydantic-ai's `ToolCallPart.args_as_dict()` internally calls
`pydantic_core.from_json()`, which only accepts `str | bytes`, not a plain
Python `dict` or `list`.  The adapter must always JSON-serialise the provider's
`tool_calls[n]["input"]` before storing it in `ToolCallPart.args`.  This is
implemented in `LiteLLMModel._build_model_response()`.

**Providers verified with mock:** fake/test model string.
**Providers NOT yet verified live:** Anthropic-direct, Bedrock-Claude,
Vertex-Claude.  The framework routes all three through `LiteLLMProvider`, so
the adapter should work identically once API credentials are available.  The
only per-provider risk is whether the provider's raw response has `tool_calls`
in the expected format — `LiteLLMProvider._do_complete()` already normalises
these, so the adapter is provider-agnostic.

---

### 2. Does pydantic-ai's structured-output retry behavior match what we want — retry on schema validation failure, give up after N tries?

**Answer:** Yes, matches what we want.

**Evidence:** `test_structured_output_retry_on_validation_error` demonstrates
this.  When the model's first response to the `final_result` output tool has
missing required `Finding` fields, pydantic-ai:
1. Catches the `ValidationError`.
2. Sends a `RetryPromptPart` back to the model (containing structured error
   details so the model knows what to fix).
3. On the second attempt, accepts the valid response.

The retry limit is configurable via `Agent(output_retries=N)`.  Default is 1
retry (2 total attempts).  This is strictly better than `FindingParser`'s
current behavior, which silently drops malformed entries — pydantic-ai tells
the model what to fix and retries.

**Note:** Retry applies only to *output* validation (structured output tool).
Function-tool argument validation is separate and also retries.

---

### 3. Does `RunResult.usage()` (input_tokens, output_tokens) match what `LiteLLMProvider` reports? Within what tolerance?

**Answer:** For simple (single-turn, no output tool) responses: exact match.
For multi-turn and structured-output responses: pydantic-ai may report higher
values, but the provider's `token_log` is always exact.

**Evidence:**
- `test_token_usage_exact_passthrough`: scripted `input_tokens=123,
  output_tokens=456` → `RunResult.usage()` reports exactly 123/456.  Delta: +0.
- `test_token_usage_accumulates_across_turns`: pydantic-ai sums tokens across
  all model requests in a run (via `RunUsage.requests`).
- Provider `token_log` always captures the exact values reported by the
  underlying `litellm.completion()` call.

**Implication for accounting:** The framework can use either the provider's
`token_log` (exact, per-call granularity) or `RunResult.usage()` (aggregated
across the full run).  Both are available.  For cost tracking use
`provider.token_log`; for run-level totals use `RunResult.usage()`.

---

### 4. Are there pydantic-ai semantics that differ from the hand-rolled loop in `common.py:run_agentic_loop()`? List any.

**Answer:** Several important differences identified:

**a. Parallel vs. sequential tool calls:**
The hand-rolled `run_agentic_loop()` processes tool calls sequentially (one at
a time in a `for` loop).  pydantic-ai supports parallel tool-call execution
when a model returns multiple tool calls in one response — they are dispatched
concurrently by default.  This changes the execution order when the model
requests parallel calls.

**b. Output tool ("structured output") mechanism:**
`run_agentic_loop()` returns raw text; `FindingParser` regex-extracts a JSON
block and silently drops invalid entries.  pydantic-ai uses a dedicated output
tool (`final_result`) with a TypedDict wrapper (e.g. `{"response": [...]}`).
The model must call this tool with valid structured data, not embed JSON in
free text.  Validation errors trigger retry with error details.  This is
strictly better but **requires all prompts to instruct the model to call the
output tool** rather than embed JSON in prose.

**c. `tool_choice` enforcement:**
`LiteLLMProvider._do_complete()` passes `tool_choice="auto"` unconditionally.
pydantic-ai does not expose a `tool_choice` override per-call in the Model ABC
(it is a `ModelSettings` extension per provider).  The hand-rolled loop also
uses `"auto"` so behavior is identical for now.  Phase 1 should expose
`tool_choice` via a `ModelSettings` subclass if needed.

**d. Max-turn enforcement:**
`run_agentic_loop(max_turns=50)` raises `RuntimeError` on overflow.
pydantic-ai enforces max iterations via `UsageLimits(max_requests=N)` passed to
`Agent.run()`.  The mechanism is different but equivalent in effect.  Default
behavior: pydantic-ai has no hard default cap (it relies on the model stopping
naturally or `output_retries` exhaustion), whereas the loop defaults to 50.
**Action for Phase 1:** always pass `UsageLimits(max_requests=50)` or similar.

**e. System prompt delivery:**
`run_agentic_loop()` passes `system_prompt` as a separate string parameter to
`complete()`.  pydantic-ai passes it via `SystemPromptPart` in `ModelRequest.parts`
(for `Agent(system_prompt=...)`) or via `InstructionPart` in
`model_request_parameters.instruction_parts` (for `Agent(instructions=...)`).
The adapter handles both paths.

**f. Message history format for tool calls:**
The hand-rolled loop emits a plain `Message(role="assistant", content=...)` for
assistant turns.  pydantic-ai's `ModelResponse` stores tool calls as
`ToolCallPart` objects alongside `TextPart`.  When replaying multi-turn history
the adapter emits the assistant message as plain text (tool call details are in
the following `ToolReturnPart` messages), which is the correct format for
LiteLLM's OpenAI-compatible API.

---

## Known Gaps / TODOs for Phase 1

1. **Live API verification**: All tests are mock-only.  Before Phase 1,
   validate against at least one live provider (preferably Anthropic-direct).

2. **Streaming**: The adapter does not implement `request_stream()`.  If any
   Phase 1 consumer needs streaming, add it.

3. **Output tool wrapper**: The `{"response": [...]}` wrapper is an
   implementation detail of pydantic-ai's "tool" output mode for non-object
   types.  Real Claude will not know to use this format without careful prompt
   engineering or a model that's been observed to follow pydantic-ai's
   structured-output instructions.  Phase 1 should test with a real model.

4. **`tool_choice` exposure**: Currently "auto" only.  Consider whether Phase 1
   needs `"required"` for forcing the output tool.

5. **system / provider_name**: `LiteLLMModel.system` returns `"litellm"`.
   Phase 1 should parse the model name prefix and return the actual provider
   (e.g. `"anthropic"`) for correct OTel tagging.

6. **Async vs sync**: `LiteLLMProvider.complete()` is synchronous.  The adapter
   calls it from an `async def request()` — this blocks the event loop on each
   call.  Phase 1 should wrap the call in `asyncio.to_thread()`.

7. **Dependency conflict (BLOCKER for Phase 1 production deployment)**:
   Installing `pydantic-ai-slim>=1.87` alongside the `worker` extra is blocked
   by two independent version conflicts surfaced by the uv resolver:

   **Axis 1 — opentelemetry-api:**
   `pydantic-ai-slim>=1.87` requires `opentelemetry-api>=1.28`.
   `semgrep>=1.100` (the lower bound in the `worker` extra) ships with an
   upper bound on otel: semgrep 1.100–1.145 requires
   `opentelemetry-api>=1.25,<1.26`.  These ranges do not overlap.

   **Axis 2 — mcp:**
   `pydantic-ai-slim>=1.87` requires `mcp>=1.25.0`.
   semgrep 1.100–1.145 requires `mcp==1.16.0`; semgrep >=1.146 requires
   `mcp==1.23.3`.  Neither satisfies `mcp>=1.25.0`.

   Simply bumping semgrep in `pyproject.toml` does **not** resolve the
   conflict: as of the spike date no released semgrep version pins
   `mcp>=1.25.0`.

   **Recommended resolution:** install `pydantic-ai` in a **separate venv**
   that does not include the `worker` extra.  The agent runner that uses
   pydantic-ai never needs semgrep; semgrep runs in a different worker
   context.  This clean separation eliminates both conflicts without requiring
   any upstream version bumps.

   For the spike, `pydantic-ai` is installed directly via
   `uv pip install "pydantic-ai>=1.87,<2.0"` bypassing the lockfile.  This
   works for the spike but is not suitable for production.  The `spike` extra
   in `pyproject.toml` documents this; install with
   `uv pip install "pydantic-ai>=1.87,<2.0"` in a dev venv that does not
   have the `worker` extra installed.
