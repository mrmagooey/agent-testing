"""Phase 0 spike demo: pydantic-ai + LiteLLMProvider round-trip.

This demo covers the four scenarios from the Phase 0 exit criteria:

1. Single agent + tool calling via ``LiteLLMModel``.
2. ``output_type=list[Finding]`` with structured output validation.
3. Two-level agent-as-tool chain (parent invokes child, gets structured output).
4. Token-usage accounting comparison between ``LiteLLMModel`` and raw
   ``LiteLLMProvider``.

Since live API credentials may not be available, all scenarios run against a
``FakeLiteLLMProvider`` that returns scripted responses.  Where the real
provider *is* reachable, set ``ANTHROPIC_API_KEY`` in the environment and
the demo will attempt a live call for scenario 1.

Run with::

    uv run python scripts/spike_pydantic_ai/demo.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Any

# Allow running the demo without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from litellm_model import LiteLLMModel
from pydantic_ai import Agent

from sec_review_framework.data.findings import Finding, Severity, VulnClass
from sec_review_framework.models.base import Message, ModelResponse, ToolDefinition
from sec_review_framework.models.litellm_provider import LiteLLMProvider

# ---------------------------------------------------------------------------
# Fake provider for offline / CI testing
# ---------------------------------------------------------------------------

class ScriptedLiteLLMProvider(LiteLLMProvider):
    """LiteLLMProvider subclass that returns pre-scripted responses.

    Each call pops the next response from ``self._responses``.  This lets
    tests verify the full call stack without hitting an external API.

    Parameters
    ----------
    responses:
        List of dicts, each accepted by ``ModelResponse(**)``.
        Consumed in order; raises ``IndexError`` if exhausted.
    model_name:
        Fake model name string.
    """

    def __init__(self, responses: list[dict[str, Any]], model_name: str = "fake/test") -> None:
        super().__init__(model_name=model_name)
        self._responses: list[dict[str, Any]] = list(responses)

    def _do_complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        if not self._responses:
            raise RuntimeError("ScriptedLiteLLMProvider: no more scripted responses")
        data = self._responses.pop(0)
        return ModelResponse(
            content=data.get("content", ""),
            tool_calls=data.get("tool_calls", []),
            input_tokens=data.get("input_tokens", 10),
            output_tokens=data.get("output_tokens", 5),
            model_id=self.model_name,
            raw={},
        )


# ---------------------------------------------------------------------------
# Scenario 1: Single agent + tool calling
# ---------------------------------------------------------------------------

async def demo_scenario_1_tool_calling() -> None:
    print("\n=== Scenario 1: Single agent + tool calling ===")

    # Two responses: first returns a tool call, second returns the final answer
    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "get_file_content",
                        "id": "call_001",
                        "input": {"path": "src/auth.py"},
                    }
                ],
                "input_tokens": 120,
                "output_tokens": 30,
            },
            {
                "content": "Found a hardcoded secret in src/auth.py at line 42.",
                "tool_calls": [],
                "input_tokens": 200,
                "output_tokens": 50,
            },
        ]
    )

    model = LiteLLMModel(provider)

    file_reads: list[str] = []

    agent: Agent[None, str] = Agent(
        model,
        system_prompt="You are a security reviewer. Use tools to inspect code.",
    )

    @agent.tool_plain
    def get_file_content(path: str) -> str:
        """Read a source file."""
        file_reads.append(path)
        return f"# {path}\nSECRET_KEY = 'hardcoded_secret_value_here'  # line 42\n"

    result = await agent.run("Review src/auth.py for hardcoded secrets.")

    print(f"  Tool was called with: {file_reads}")
    print(f"  Final output: {result.output!r}")
    usage = result.usage()
    print(f"  Usage: input_tokens={usage.input_tokens}, output_tokens={usage.output_tokens}")

    assert "hardcoded secret" in result.output.lower(), "Expected finding mention in output"
    assert file_reads == ["src/auth.py"], "Expected tool called once with correct path"
    print("  PASS: tool calling scenario")


# ---------------------------------------------------------------------------
# Scenario 2: output_type=list[Finding]
# ---------------------------------------------------------------------------

async def demo_scenario_2_structured_output() -> None:
    print("\n=== Scenario 2: output_type=list[Finding] ===")

    # Build a valid Finding JSON to return
    finding_data = {
        "id": str(uuid.uuid4()),
        "file_path": "src/auth.py",
        "line_start": 42,
        "line_end": 42,
        "vuln_class": "hardcoded_secret",
        "cwe_ids": ["CWE-798"],
        "severity": "high",
        "title": "Hardcoded API secret in auth module",
        "description": "A hardcoded secret key was found in the authentication module.",
        "recommendation": "Move to environment variable or secrets manager.",
        "confidence": 0.95,
        "raw_llm_output": "...",
        "produced_by": "spike_demo",
        "experiment_id": "spike_exp_001",
    }

    # pydantic-ai uses "tool" output mode by default for complex types;
    # the model must call the output tool with the structured data.
    # The documented default output-tool name is "final_result" (see pydantic-ai
    # docs: Agent output tools).  The test suite already hardcodes this name.

    # pydantic-ai wraps list output_type in {"response": [...]} when using
    # the "tool" output mode — the model must call the output tool with this
    # wrapper object.
    provider2 = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "final_result",
                        "id": "call_sr_001",
                        "input": {"response": [finding_data]},
                    }
                ],
                "input_tokens": 300,
                "output_tokens": 100,
            }
        ]
    )
    model2 = LiteLLMModel(provider2)
    agent2: Agent[None, list[Finding]] = Agent(
        model2,
        output_type=list[Finding],
        system_prompt="Extract security findings from code.",
    )

    result2 = await agent2.run("Analyse src/auth.py for secrets.")
    print(f"  LiteLLMModel result: {len(result2.output)} finding(s)")
    print(f"  First finding vuln_class: {result2.output[0].vuln_class}")
    usage2 = result2.usage()
    print(f"  Usage: input_tokens={usage2.input_tokens}, output_tokens={usage2.output_tokens}")

    assert len(result2.output) == 1
    assert result2.output[0].vuln_class == VulnClass.HARDCODED_SECRET
    assert result2.output[0].severity == Severity.HIGH
    print("  PASS: structured output (list[Finding])")


# ---------------------------------------------------------------------------
# Scenario 3: Two-level agent-as-tool chain
# ---------------------------------------------------------------------------

async def demo_scenario_3_agent_as_tool() -> None:
    print("\n=== Scenario 3: Two-level agent-as-tool chain ===")

    # Child agent: given a file path, returns a list of findings
    finding_data = {
        "id": str(uuid.uuid4()),
        "file_path": "src/login.py",
        "line_start": 10,
        "line_end": 10,
        "vuln_class": "sqli",
        "cwe_ids": ["CWE-89"],
        "severity": "critical",
        "title": "SQL injection in login",
        "description": "User input not sanitised before SQL query.",
        "recommendation": "Use parameterised queries.",
        "confidence": 0.97,
        "raw_llm_output": "...",
        "produced_by": "child_agent",
        "experiment_id": "spike_exp_002",
    }

    # The documented default output-tool name is "final_result".
    # Build child agent with scripted provider
    # pydantic-ai wraps list output_type in {"response": [...]}
    child_provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "final_result",
                        "id": "call_child_001",
                        "input": {"response": [finding_data]},
                    }
                ],
                "input_tokens": 150,
                "output_tokens": 80,
            }
        ]
    )
    child_model = LiteLLMModel(child_provider)
    child_agent: Agent[None, list[Finding]] = Agent(
        child_model,
        output_type=list[Finding],
        system_prompt="You are a file-level security reviewer.",
    )

    # Parent agent: calls child as a tool, aggregates results
    parent_provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "review_file",
                        "id": "call_parent_001",
                        "input": {"file_path": "src/login.py"},
                    }
                ],
                "input_tokens": 100,
                "output_tokens": 25,
            },
            {
                "content": "Child agent found 1 finding: SQL injection in login.",
                "tool_calls": [],
                "input_tokens": 200,
                "output_tokens": 40,
            },
        ]
    )
    parent_model = LiteLLMModel(parent_provider)
    parent_agent: Agent[None, str] = Agent(
        parent_model,
        system_prompt="You are a parent orchestrator. Use review_file tool to review each file.",
    )

    child_results: list[list[Finding]] = []

    @parent_agent.tool_plain
    async def review_file(file_path: str) -> str:
        """Invoke the child agent to review a single file."""
        child_run = await child_agent.run(f"Review {file_path} for vulnerabilities.")
        child_results.append(child_run.output)
        return json.dumps(
            [f.model_dump(mode="json", exclude={"raw_llm_output"}) for f in child_run.output]
        )

    result = await parent_agent.run("Review the following files: src/login.py")

    print(f"  Parent output: {result.output!r}")
    print(f"  Child agent was called {len(child_results)} time(s)")
    print(f"  Child findings: {[f.vuln_class for findings in child_results for f in findings]}")
    parent_usage = result.usage()
    print(f"  Parent usage: input_tokens={parent_usage.input_tokens}, output_tokens={parent_usage.output_tokens}")

    assert len(child_results) == 1
    assert len(child_results[0]) == 1
    assert child_results[0][0].vuln_class == VulnClass.SQLI
    print("  PASS: two-level agent-as-tool chain")


# ---------------------------------------------------------------------------
# Scenario 4: Token usage accounting
# ---------------------------------------------------------------------------

async def demo_scenario_4_token_accounting() -> None:
    print("\n=== Scenario 4: Token-usage accounting ===")

    # Create a provider that reports specific token counts
    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "No findings.",
                "tool_calls": [],
                "input_tokens": 123,
                "output_tokens": 456,
            }
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model, system_prompt="Review code.")

    result = await agent.run("Review this file.")
    usage = result.usage()

    # Check pydantic-ai usage
    print(f"  pydantic-ai usage: input={usage.input_tokens}, output={usage.output_tokens}")

    # Check framework provider token_log
    token_log = provider.token_log
    print(f"  Provider token_log entries: {len(token_log)}")
    if token_log:
        print(f"  Provider token_log[0]: input={token_log[0].input_tokens}, output={token_log[0].output_tokens}")

    # pydantic-ai usage.input_tokens may not exactly match provider because
    # pydantic-ai adds its own usage tracking overhead.  We check that the
    # provider-reported tokens appear in pydantic-ai's usage.
    assert token_log, "Provider should have recorded token usage"
    assert token_log[0].input_tokens == 123
    assert token_log[0].output_tokens == 456

    # pydantic-ai's usage.input_tokens should include the provider's tokens
    # (it may be higher due to output-tool tokens being counted separately)
    assert usage.input_tokens >= 123, (
        f"pydantic-ai input_tokens ({usage.input_tokens}) should be >= provider's 123"
    )
    assert usage.output_tokens >= 456, (
        f"pydantic-ai output_tokens ({usage.output_tokens}) should be >= provider's 456"
    )

    print(
        f"  Delta: input +{usage.input_tokens - 123}, output +{usage.output_tokens - 456}"
    )
    print("  PASS: token accounting")


# ---------------------------------------------------------------------------
# Optional: live API call (Scenario 1 with real Claude)
# ---------------------------------------------------------------------------

async def demo_live_if_available() -> None:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("\n=== Live API: skipped (ANTHROPIC_API_KEY not set) ===")
        return

    print("\n=== Live API: single call to Anthropic Claude ===")
    try:
        live_provider = LiteLLMProvider(
            model_name="anthropic/claude-3-haiku-20240307",
            api_key=api_key,
        )
        live_model = LiteLLMModel(live_provider)
        live_agent: Agent[None, str] = Agent(
            live_model, system_prompt="You are a terse assistant."
        )
        live_result = await live_agent.run("Say 'hello spike' and nothing else.")
        print(f"  Live response: {live_result.output!r}")
        usage = live_result.usage()
        print(f"  Usage: input_tokens={usage.input_tokens}, output_tokens={usage.output_tokens}")
        print("  PASS: live API call")
    except Exception as exc:
        print(f"  SKIPPED: live call failed — {exc}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    print("=" * 60)
    print("Phase 0 Spike: pydantic-ai + LiteLLM round-trip")
    print("=" * 60)

    await demo_scenario_1_tool_calling()
    await demo_scenario_2_structured_output()
    await demo_scenario_3_agent_as_tool()
    await demo_scenario_4_token_accounting()
    await demo_live_if_available()

    print("\n" + "=" * 60)
    print("All scenarios completed successfully.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
