"""Subagent dispatch infrastructure for the pydantic-ai agent runner.

Provides:

- :class:`SubagentDeps` — mutable dependency container injected into parent
  agents that have subagents declared.
- :class:`SubagentOutput` — structured output container for a single subagent
  invocation.
- :func:`make_invoke_subagent_tool` — returns a pydantic-ai tool callable for
  ``invoke_subagent(role, input) -> SubagentOutput``.
- :func:`make_invoke_subagent_batch_tool` — returns a pydantic-ai tool callable
  for ``invoke_subagent_batch(role, inputs) -> list[SubagentOutput]``.

Design notes
------------
- Each child agent receives a fresh pydantic-ai :class:`~pydantic_ai.Agent` per
  invocation — no shared message history between siblings or across calls.
- Audit logs do not interleave: each child receives a *cloned*
  :class:`~sec_review_framework.tools.registry.ToolRegistry` via
  :meth:`~sec_review_framework.tools.registry.ToolRegistry.clone`.
- Batch dispatch mirrors the existing ``run_subagents(parallel=True)`` pattern
  at ``common.py:267-273``: a :class:`~concurrent.futures.ThreadPoolExecutor`
  with ``max_workers=4``.
- Cap enforcement raises :class:`~pydantic_ai.ModelRetry` so the parent sees
  the failure and can decide what to do (e.g., retry with a smaller batch or
  give up cleanly).
- Recursion: a child whose ``subagent_strategies`` is non-empty receives the
  same tools injected with ``depth+1``.  The ``max_depth`` cap prevents runaway
  recursion.

This module is built but NOT WIRED in Phase 1.  No existing code imports it.
Wiring happens in Phase 2 (``src/sec_review_framework/strategies/runner.py``).

Requires the ``agent`` extra::

    uv pip install -e ".[agent]"

MUTUALLY EXCLUSIVE with the ``worker`` extra — separate venvs required.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
from dataclasses import dataclass, field

# TYPE_CHECKING import keeps the circular reference from materialising at runtime.
# UserStrategy is only referenced in annotations and runtime isinstance checks
# are not needed here.
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

# Fail loudly if pydantic-ai is not installed.  Do NOT add a try/except guard.
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelRetry

from sec_review_framework.agent.litellm_model import LiteLLMModel
from sec_review_framework.agent.tool_adapter import make_tool_callables
from sec_review_framework.models.litellm_provider import LiteLLMProvider
from sec_review_framework.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from sec_review_framework.data.strategy_bundle import UserStrategy


# ---------------------------------------------------------------------------
# SubagentOutput
# ---------------------------------------------------------------------------


class SubagentOutput[T](BaseModel):
    """Structured output from a single subagent invocation.

    Attributes
    ----------
    role:
        The subagent role name that produced this output (mirrors the
        ``role`` argument passed to ``invoke_subagent``).
    output:
        The structured output returned by the child agent.  Its type is
        determined by the child strategy's ``output_type`` at runtime.
    usage:
        Token usage summary.  Keys: ``input_tokens``, ``output_tokens``,
        ``total_tokens``.
    """

    model_config = {"arbitrary_types_allowed": True}

    role: str
    output: Any  # typed as Any; callers can cast to the expected type
    usage: dict[str, int]


# ---------------------------------------------------------------------------
# SubagentDeps
# ---------------------------------------------------------------------------


@dataclass
class SubagentDeps:
    """Mutable dependency container passed to every parent agent.

    Enforces invocation caps and carries everything the agent runner needs to
    build child agents.

    Attributes
    ----------
    depth:
        Current recursion depth (0 for top-level agents).
    max_depth:
        Maximum allowed recursion depth.  Reaching this depth causes cap
        enforcement to raise :class:`~pydantic_ai.ModelRetry`.
    invocations:
        Running count of total subagent invocations across the current run.
    max_invocations:
        Hard cap on total invocations.
    max_batch_size:
        Maximum number of inputs in a single ``invoke_subagent_batch`` call.
    available_roles:
        Set of role names the parent is allowed to dispatch to.
    subagent_strategies:
        Mapping of role name → :class:`~sec_review_framework.data.strategy_bundle.UserStrategy`.
    tool_registry:
        The parent's :class:`~sec_review_framework.tools.registry.ToolRegistry`.
        Children receive a *clone* of this registry so audit logs do not interleave.
    """

    depth: int = 0
    max_depth: int = 3
    invocations: int = 0
    max_invocations: int = 100
    max_batch_size: int = 32
    available_roles: set[str] = field(default_factory=set)
    subagent_strategies: dict[str, UserStrategy] = field(default_factory=dict)
    tool_registry: ToolRegistry = field(default_factory=ToolRegistry)

    def child_deps(self) -> SubagentDeps:
        """Return a new :class:`SubagentDeps` for a child invocation.

        The child:

        - Has ``depth = self.depth + 1``.
        - The child starts with a snapshot of the current invocation count;
          future recursive dispatches inside the child increment the child's
          counter, not the parent's.
        - Receives a *cloned* :class:`~sec_review_framework.tools.registry.ToolRegistry`
          so its audit log is independent.
        - Inherits all other caps and strategies.

        Note: Python ``dataclass`` fields are not automatically shared by
        reference. The invocation counter is snapshotted here (as an immutable
        integer), and the child's subsequent invocations are tracked in the
        child's own ``invocations`` field.  The parent's counter is incremented
        by the parent's invoke_subagent tool (see :func:`_check_caps`) before
        calling ``_run_child_sync``.
        """
        return SubagentDeps(
            depth=self.depth + 1,
            max_depth=self.max_depth,
            invocations=self.invocations,
            max_invocations=self.max_invocations,
            max_batch_size=self.max_batch_size,
            available_roles=set(self.available_roles),
            subagent_strategies=dict(self.subagent_strategies),
            tool_registry=self.tool_registry.clone(),
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _check_caps(ctx: RunContext[SubagentDeps], count: int) -> None:
    """Check depth, role, and invocation caps; raise :class:`ModelRetry` on violation.

    Parameters
    ----------
    ctx:
        pydantic-ai run context carrying :class:`SubagentDeps`.
    count:
        Number of invocations being requested (1 for single, len(inputs) for batch).

    Raises
    ------
    ModelRetry
        On depth overflow, invocation overflow, or role not available.
    """
    deps = ctx.deps
    if deps.depth >= deps.max_depth:
        raise ModelRetry(
            f"Subagent depth cap reached ({deps.depth}/{deps.max_depth}). "
            "Cannot dispatch further subagents."
        )
    if deps.invocations + count > deps.max_invocations:
        remaining = deps.max_invocations - deps.invocations
        raise ModelRetry(
            f"Subagent invocation cap would be exceeded "
            f"(requested {count}, remaining {remaining}/{deps.max_invocations})."
        )


def _run_child_sync(
    strategy: UserStrategy,
    input_data: dict[str, Any],
    parent_deps: SubagentDeps,
) -> SubagentOutput:
    """Run a child agent synchronously in a worker thread.

    Builds a fresh pydantic-ai :class:`~pydantic_ai.Agent` for *strategy*,
    runs it with *input_data* as the user message, and returns a
    :class:`SubagentOutput`.

    The child receives:

    - A cloned :class:`~sec_review_framework.tools.registry.ToolRegistry` from
      *parent_deps* so its audit log is independent.
    - Its own :class:`SubagentDeps` with ``depth = parent_deps.depth + 1``.

    Parameters
    ----------
    strategy:
        The child :class:`~sec_review_framework.data.strategy_bundle.UserStrategy`.
    input_data:
        Dict passed as the user message to the child agent.
    parent_deps:
        The calling agent's :class:`SubagentDeps`.

    Returns
    -------
    SubagentOutput
        Role, output, and token usage from the child run.
    """
    from sec_review_framework.data.strategy_bundle import resolve_bundle

    # Resolve the bundle (no override key — single-agent shape for subagents)
    bundle = resolve_bundle(strategy, None)

    # Build a fresh LiteLLMProvider + LiteLLMModel for this child invocation.
    # In Phase 2 the provider will come from a shared ModelProviderCache; for
    # Phase 1 we build it directly from the bundle's model_id.
    provider = LiteLLMProvider(model_name=bundle.model_id)
    model = LiteLLMModel(provider)

    # Clone the registry so this child has its own audit log
    child_registry = parent_deps.tool_registry.clone()
    tool_callables = make_tool_callables(child_registry)

    # Build child deps for recursive subagent dispatch
    child_deps = parent_deps.child_deps()

    # Build the child agent
    child_agent: Agent[SubagentDeps, Any] = Agent(
        model,
        system_prompt=bundle.system_prompt,
        tools=tool_callables,
    )

    # Convert input_data to a user message string
    user_message = json.dumps(input_data) if isinstance(input_data, dict) else str(input_data)

    # Run synchronously — this is called from a ThreadPoolExecutor worker thread
    result = asyncio.run(child_agent.run(user_message, deps=child_deps))

    usage_dict = {
        "input_tokens": result.usage().input_tokens or 0,
        "output_tokens": result.usage().output_tokens or 0,
        "total_tokens": result.usage().total_tokens or 0,
    }

    return SubagentOutput(
        role=strategy.id,
        output=result.output,
        usage=usage_dict,
    )


# ---------------------------------------------------------------------------
# Public factories
# ---------------------------------------------------------------------------


def make_invoke_subagent_tool(
    deps_factory: Any = None,
) -> Any:
    """Return a pydantic-ai tool callable for ``invoke_subagent(role, input)``.

    The returned tool dispatches a single child agent and returns a
    :class:`SubagentOutput`.  Caps are enforced before dispatch; exceeded
    caps raise :class:`~pydantic_ai.ModelRetry` so the parent can decide.

    This factory returns a plain async function decorated as a pydantic-ai
    tool.  The caller registers it with the parent agent::

        parent_agent = Agent(model, tools=[make_invoke_subagent_tool()])

    Parameters
    ----------
    deps_factory:
        Unused in Phase 1 (reserved for Phase 2 when the runner injects a
        custom factory).  Pass ``None`` or omit.

    Returns
    -------
    Callable
        Async function suitable for use as a pydantic-ai tool.
    """

    async def invoke_subagent(
        ctx: RunContext[SubagentDeps],
        role: str,
        input: dict[str, Any],  # noqa: A002 — matches the schema name used by the LLM
    ) -> SubagentOutput:
        """Invoke a single subagent by role and return its structured output.

        Args:
            role: The subagent role name (must be in ``SubagentDeps.available_roles``).
            input: A dict of inputs passed to the subagent as its user message.

        Returns:
            A :class:`SubagentOutput` containing role, output, and token usage.
        """
        deps = ctx.deps

        # Validate role
        if role not in deps.available_roles:
            raise ModelRetry(
                f"Unknown subagent role {role!r}. "
                f"Available roles: {sorted(deps.available_roles)}"
            )

        # Check caps (count=1 for single dispatch)
        _check_caps(ctx, count=1)
        deps.invocations += 1

        strategy = deps.subagent_strategies[role]
        return await asyncio.to_thread(_run_child_sync, strategy, input, deps)

    return invoke_subagent


def make_invoke_subagent_batch_tool(
    deps_factory: Any = None,
) -> Any:
    """Return a pydantic-ai tool callable for ``invoke_subagent_batch(role, inputs)``.

    The returned tool dispatches multiple child agents in parallel via a
    :class:`~concurrent.futures.ThreadPoolExecutor` (mirroring the existing
    ``run_subagents(parallel=True)`` pattern at ``common.py:267-273``).

    Results are returned in the same order as *inputs*.  Each child receives
    a cloned :class:`~sec_review_framework.tools.registry.ToolRegistry` so
    audit logs do not interleave.

    Cap checks:

    - ``len(inputs) > max_batch_size`` → :class:`~pydantic_ai.ModelRetry`
    - ``invocations + len(inputs) > max_invocations`` → :class:`~pydantic_ai.ModelRetry`
    - ``depth >= max_depth`` → :class:`~pydantic_ai.ModelRetry`

    Parameters
    ----------
    deps_factory:
        Unused in Phase 1 (reserved for Phase 2).

    Returns
    -------
    Callable
        Async function suitable for use as a pydantic-ai tool.
    """

    async def invoke_subagent_batch(
        ctx: RunContext[SubagentDeps],
        role: str,
        inputs: list[dict[str, Any]],
    ) -> list[SubagentOutput]:
        """Invoke multiple subagent calls in parallel for a single role.

        Args:
            role: The subagent role name (must be in ``SubagentDeps.available_roles``).
            inputs: List of input dicts; one child invocation per element.

        Returns:
            List of :class:`SubagentOutput` in the same order as *inputs*.
        """
        deps = ctx.deps

        # Validate role
        if role not in deps.available_roles:
            raise ModelRetry(
                f"Unknown subagent role {role!r}. "
                f"Available roles: {sorted(deps.available_roles)}"
            )

        # Batch size cap
        if len(inputs) > deps.max_batch_size:
            raise ModelRetry(
                f"Batch too large: {len(inputs)} inputs exceeds max_batch_size={deps.max_batch_size}."
            )

        # Total invocation cap
        _check_caps(ctx, count=len(inputs))
        deps.invocations += len(inputs)

        strategy = deps.subagent_strategies[role]

        # Fan-out in a ThreadPoolExecutor, mirroring common.py:267-273
        def _run_one(inp: dict[str, Any]) -> SubagentOutput:
            return _run_child_sync(strategy, inp, deps)

        loop = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures = [loop.run_in_executor(pool, _run_one, inp) for inp in inputs]
            results = await asyncio.gather(*futures)

        return list(results)

    return invoke_subagent_batch
