"""Adapter that converts a :class:`~sec_review_framework.tools.registry.ToolRegistry` to pydantic-ai tool callables.

Each :class:`~sec_review_framework.tools.registry.Tool` in the registry is wrapped
in a pydantic-ai-compatible async callable.  Every invocation goes through the
registry's :meth:`~sec_review_framework.tools.registry.ToolRegistry.invoke` method,
preserving audit logging.

Usage
-----
::

    from sec_review_framework.agent.tool_adapter import make_tool_callables
    from pydantic_ai import Agent

    callables = make_tool_callables(registry)
    agent = Agent(model, tools=callables)

Schema translation
------------------
The framework's :class:`~sec_review_framework.models.base.ToolDefinition` stores
tool parameters as a standard JSON Schema ``{"type": "object", "properties": {...}}``
dict.  pydantic-ai expects the same shape in ``ToolDefinition.parameters_json_schema``.
The adapter uses :meth:`~pydantic_ai.tools.Tool.from_schema` to bypass pydantic-ai's
own Python-function inspection and supply the schema directly — this avoids any
``**kwargs``-vs-typed-params mismatch.

Parallel-fan-out pattern
------------------------
When using :func:`make_invoke_subagent_batch_tool` (in :mod:`.subagent`) the
caller must pass a *cloned* registry to each worker so audit logs do not
interleave.  Mirror the pattern at ``common.py:268``::

    registry_clone = registry.clone()
    callables = make_tool_callables(registry_clone)

Requires the ``agent`` extra::

    uv pip install -e ".[agent]"

This extra is MUTUALLY EXCLUSIVE with the ``worker`` extra — they must be
installed in separate virtual environments.
"""

from __future__ import annotations

from typing import Any

# Fail loudly if pydantic-ai is not installed.  Do NOT add a try/except guard.
from pydantic_ai import RunContext
from pydantic_ai.tools import Tool as PAITool

from sec_review_framework.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Schema translator
# ---------------------------------------------------------------------------

def _translate_schema(json_schema: dict[str, Any]) -> dict[str, Any]:
    """Translate a framework JSON Schema to a pydantic-ai-compatible schema.

    The framework's :class:`~sec_review_framework.models.base.ToolDefinition`
    stores a JSON Schema in ``input_schema``.  pydantic-ai's
    :meth:`~pydantic_ai.tools.Tool.from_schema` accepts the same standard
    ``{"type": "object", "properties": {...}}`` shape, so no structural
    translation is needed today.

    This function is a pass-through but provides a single place to add
    field-level fixups if a mismatch is discovered during Phase 1 testing
    (e.g., unsupported ``$defs`` or ``additionalProperties`` keys).

    Parameters
    ----------
    json_schema:
        The raw JSON Schema from ``ToolDefinition.input_schema``.

    Returns
    -------
    dict[str, Any]
        Schema suitable for pydantic-ai ``Tool.from_schema``.
    """
    # Defensive copy so we don't mutate the original
    schema = dict(json_schema)

    # Ensure type=object at the top level (required by OpenAI/Anthropic APIs)
    if "type" not in schema:
        schema["type"] = "object"

    return schema


# ---------------------------------------------------------------------------
# Per-tool callable factory
# ---------------------------------------------------------------------------

def _make_tool_callable(registry: ToolRegistry, tool_name: str) -> PAITool[Any, str]:
    """Build a single pydantic-ai :class:`~pydantic_ai.tools.Tool` for *tool_name*.

    Uses :meth:`~pydantic_ai.tools.Tool.from_schema` to supply the JSON schema
    directly, bypassing pydantic-ai's Python-function introspection.  The
    underlying function accepts ``**kwargs`` and forwards them as a flat dict
    to :meth:`~sec_review_framework.tools.registry.ToolRegistry.invoke`.

    The returned tool:

    - Has the same ``name``, ``description``, and parameter schema as the
      registry :class:`~sec_review_framework.tools.registry.Tool`.
    - Delegates execution to
      :meth:`~sec_review_framework.tools.registry.ToolRegistry.invoke`, which
      records every call in the registry's audit log.
    - Generates a stable ``tool_call_id`` from the run context's ``run_id``
      and a monotonic per-invocation counter.

    Parameters
    ----------
    registry:
        The :class:`~sec_review_framework.tools.registry.ToolRegistry` that owns *tool_name*.
    tool_name:
        Name of the tool inside *registry*.

    Returns
    -------
    pydantic_ai.tools.Tool
        pydantic-ai tool that delegates to the registry.

    Raises
    ------
    KeyError
        If *tool_name* is not in *registry*.
    """
    tool_obj = registry.tools[tool_name]
    defn = tool_obj.definition()
    schema = _translate_schema(defn.input_schema)

    # Counter lives in a mutable list so the closure can increment it across calls.
    _counter: list[int] = [0]

    async def _invoke(ctx: RunContext[Any], **kwargs: Any) -> str:
        _counter[0] += 1
        call_id = f"{ctx.run_id}-{tool_name}-{_counter[0]}"
        # kwargs are the schema properties passed by pydantic-ai as keyword arguments.
        return registry.invoke(tool_name, dict(kwargs), call_id)

    return PAITool.from_schema(
        _invoke,
        name=tool_name,
        description=defn.description,
        json_schema=schema,
        takes_ctx=True,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def make_tool_callables(registry: ToolRegistry) -> list[PAITool[Any, str]]:
    """Convert all tools in *registry* to pydantic-ai tool callables.

    Every returned callable goes through the registry's
    :meth:`~sec_review_framework.tools.registry.ToolRegistry.invoke` method so
    audit logging is preserved.

    For parallel-fan-out contexts (e.g. :func:`~.subagent.make_invoke_subagent_batch_tool`),
    pass a **cloned** registry (via :meth:`~sec_review_framework.tools.registry.ToolRegistry.clone`)
    so audit logs from different worker threads do not interleave.

    Parameters
    ----------
    registry:
        A :class:`~sec_review_framework.tools.registry.ToolRegistry` configured
        for the current agent run.

    Returns
    -------
    list[pydantic_ai.tools.Tool]
        One pydantic-ai tool per entry in ``registry.tools``, in iteration order.
    """
    return [_make_tool_callable(registry, name) for name in registry.tools]
