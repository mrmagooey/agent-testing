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
- Batch dispatch uses :func:`asyncio.gather` over async ``_run_child`` coroutines;
  no thread-pool nesting is required.
- Cap enforcement raises :class:`~pydantic_ai.ModelRetry` so the parent sees
  the failure and can decide what to do (e.g., retry with a smaller batch or
  give up cleanly).
- Recursion: a child whose ``subagent_strategies`` is non-empty receives the
  same tools injected with ``depth+1``.  The ``max_depth`` cap prevents runaway
  recursion.
- Provider sharing: when :attr:`SubagentDeps.model_provider_cache` is set,
  children with the same ``model_id`` reuse the cached
  :class:`~sec_review_framework.models.litellm_provider.LiteLLMProvider`.
  Their ``token_log`` and ``conversation_log`` entries accumulate on the shared
  provider, making child token usage and conversation history visible to any
  caller holding a reference to that provider (or to the cache itself).

Subagent dispatch is wired in :mod:`sec_review_framework.strategies.runner`.

Requires the ``agent`` extra::

    uv pip install -e ".[agent]"
"""

from __future__ import annotations

import asyncio
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
    from sec_review_framework.strategies.common import ModelProviderCache


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
    model_provider_cache:
        Optional shared provider cache.  When set, ``_run_child`` reads the
        provider for the child's ``model_id`` from the cache (creating it on
        first access) rather than constructing a fresh one.  Providers are
        shared across children with the same ``model_id``, so their
        ``token_log`` and ``conversation_log`` accumulate on the shared
        instance — fixing the token-usage and conversation-log fragmentation
        bugs for multi-specialist runs.
    """

    depth: int = 0
    max_depth: int = 3
    invocations: int = 0
    max_invocations: int = 100
    max_batch_size: int = 32
    available_roles: set[str] = field(default_factory=set)
    subagent_strategies: dict[str, UserStrategy] = field(default_factory=dict)
    tool_registry: ToolRegistry = field(default_factory=ToolRegistry)
    # Audit log of invoke_subagent_batch calls: list of (role, inputs) tuples.
    # Populated by the invoke_subagent_batch tool; read by the dispatch validator
    # in runner.py after agent.run_sync() returns.
    batch_call_log: list[tuple[str, list[dict[str, Any]]]] = field(default_factory=list)
    # Audit log of invoke_subagent (single) calls: list of (role, input) tuples.
    # Populated by the invoke_subagent tool; read by the programmatic fallback
    # in runner.py (Phase 3c per_vuln_class dispatch completeness).
    single_call_log: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    # Shared model provider cache — None means each child builds a fresh provider.
    model_provider_cache: ModelProviderCache | None = field(default=None)

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
        - Inherits the same :attr:`model_provider_cache` by reference so that
          providers are shared across all children in the same run.

        Note: Python ``dataclass`` fields are not automatically shared by
        reference. The invocation counter is snapshotted here (as an immutable
        integer), and the child's subsequent invocations are tracked in the
        child's own ``invocations`` field.  The parent's counter is incremented
        by the parent's invoke_subagent tool (see :func:`_check_caps`) before
        calling ``_run_child``.
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
            # Child gets its own call logs; parent's logs are not shared.
            batch_call_log=[],
            single_call_log=[],
            # Cache is shared by reference so all providers accumulate on the
            # same instances.
            model_provider_cache=self.model_provider_cache,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def resolve_role(role: str, available_roles: set[str]) -> str | None:
    """Resolve *role* to a fully-namespaced role ID in *available_roles*.

    Accepts either the full namespaced ID (e.g. ``"builtin_v2.sqli_specialist"``)
    or a bare suffix (e.g. ``"sqli_specialist"``).

    Resolution order
    ----------------
    1. Exact match: if *role* is in *available_roles*, return it immediately.
    2. Suffix match: find all ``r`` in *available_roles* where
       ``r.endswith("." + role)`` or ``r == role``.  If exactly one match,
       return it.  If multiple, raise :class:`~pydantic_ai.ModelRetry` with
       an "Ambiguous role" message.
    3. No match: return ``None``.

    Parameters
    ----------
    role:
        The role name as passed by the calling LLM (bare or namespaced).
    available_roles:
        The full set of available role IDs from
        :attr:`SubagentDeps.available_roles`.

    Returns
    -------
    str | None
        The resolved (namespaced) role ID, or ``None`` if no match.

    Raises
    ------
    ModelRetry
        If the bare suffix matches more than one entry in *available_roles*.
    """
    # 1. Exact match
    if role in available_roles:
        return role

    # 2. Suffix match
    suffix = "." + role
    matches = [r for r in available_roles if r.endswith(suffix)]

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ModelRetry(
            f"Ambiguous role {role!r}: matches multiple available roles: {sorted(matches)}"
        )

    # 3. No match
    return None


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


async def _run_child(
    strategy: UserStrategy,
    input_data: dict[str, Any],
    parent_deps: SubagentDeps,
) -> SubagentOutput:
    """Run a child agent asynchronously.

    Builds a pydantic-ai :class:`~pydantic_ai.Agent` for *strategy*, runs it
    with *input_data* as the user message, and returns a
    :class:`SubagentOutput`.

    Provider selection
    ------------------
    When ``parent_deps.model_provider_cache`` is set the provider for the
    child's ``model_id`` is fetched from (or created in) the cache so that
    all children with the same model share one provider instance.  This lets
    token usage and conversation history accumulate on the shared provider,
    making child spend visible to the caller that holds the cache.

    When the cache is ``None`` a fresh :class:`LiteLLMProvider` is built for
    this invocation (original Phase 1 behaviour).

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

    # Resolve the provider: use the shared cache when available so that token
    # usage and conversation logs accumulate on the shared instance.
    if parent_deps.model_provider_cache is not None:
        provider = parent_deps.model_provider_cache.get(bundle.model_id)
    else:
        provider = LiteLLMProvider(model_name=bundle.model_id)
    model = LiteLLMModel(provider)  # type: ignore[arg-type]

    # Clone the registry so this child has its own audit log
    child_registry = parent_deps.tool_registry.clone()
    tool_callables = make_tool_callables(child_registry)

    # Build child deps for recursive subagent dispatch
    child_deps = parent_deps.child_deps()

    # Resolve structured output type (if the strategy declares one)
    from sec_review_framework.agent.output_types import resolve_output_type

    output_type = resolve_output_type(bundle.output_type_name)

    # Build the child agent
    child_agent_kwargs: dict[str, Any] = {
        "system_prompt": bundle.system_prompt,
        "tools": tool_callables,
    }
    if output_type is not None:
        child_agent_kwargs["output_type"] = output_type

    child_agent: Agent[SubagentDeps, Any] = Agent(model, **child_agent_kwargs)

    # Format the child's user_prompt_template with the input_data dict.
    # Unknown placeholders are left as-is (via a defaultdict-style fallback).
    user_prompt_template = bundle.user_prompt_template
    if user_prompt_template and isinstance(input_data, dict):
        from sec_review_framework.strategies.common import FINDING_OUTPUT_FORMAT

        class _Missing(dict):
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        ctx_map = _Missing(finding_output_format=FINDING_OUTPUT_FORMAT)
        ctx_map.update(input_data)
        user_message = user_prompt_template.format_map(ctx_map)
    else:
        # Fallback: JSON-encode the input dict
        user_message = json.dumps(input_data) if isinstance(input_data, dict) else str(input_data)

    result = await child_agent.run(user_message, deps=child_deps)

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


def _run_child_sync(
    strategy: UserStrategy,
    input_data: dict[str, Any],
    parent_deps: SubagentDeps,
) -> SubagentOutput:
    """Synchronous wrapper around :func:`_run_child` for thread-pool callers.

    Called from :func:`~sec_review_framework.strategies.runner._programmatic_fallback`
    which runs inside a :class:`~concurrent.futures.ThreadPoolExecutor` where
    there is no running event loop, so ``asyncio.run`` is safe.
    """
    return asyncio.run(_run_child(strategy, input_data, parent_deps))


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
        Optional factory (reserved for future use when custom factories are needed).
        Pass ``None`` or omit.

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

        # Resolve role — accepts bare suffix OR full namespaced ID
        resolved_role = resolve_role(role, deps.available_roles)
        if resolved_role is None:
            raise ModelRetry(
                f"Unknown subagent role {role!r}. "
                f"Available roles: {sorted(deps.available_roles)}"
            )

        # Check caps (count=1 for single dispatch)
        _check_caps(ctx, count=1)
        deps.invocations += 1

        # Record the *resolved* (namespaced) role so the dispatch validator's
        # set-difference computation works regardless of how the parent named it.
        deps.single_call_log.append((resolved_role, dict(input)))

        strategy = deps.subagent_strategies[resolved_role]
        # Await directly — no to_thread wrapper needed since _run_child is async.
        return await _run_child(strategy, input, deps)

    return invoke_subagent


def make_invoke_subagent_batch_tool(
    deps_factory: Any = None,
) -> Any:
    """Return a pydantic-ai tool callable for ``invoke_subagent_batch(role, inputs)``.

    The returned tool dispatches multiple child agents in parallel via
    :func:`asyncio.gather` over async ``_run_child`` coroutines.

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
        Optional factory (reserved for future use).

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

        # Resolve role — accepts bare suffix OR full namespaced ID
        resolved_role = resolve_role(role, deps.available_roles)
        if resolved_role is None:
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

        # Record the *resolved* (namespaced) role so the dispatch validator's
        # set-difference computation works regardless of how the parent named it.
        deps.batch_call_log.append((resolved_role, list(inputs)))

        strategy = deps.subagent_strategies[resolved_role]

        # Fan-out via asyncio.gather — each coroutine runs in the current event
        # loop without requiring a thread pool.
        results = await asyncio.gather(
            *[_run_child(strategy, inp, deps) for inp in inputs]
        )

        return list(results)

    return invoke_subagent_batch

