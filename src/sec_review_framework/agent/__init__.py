"""Agent package: pydantic-ai integration for the security review framework.

This package requires the ``agent`` extra to be installed::

    uv pip install -e ".[agent,coordinator,dev]"

It is MUTUALLY EXCLUSIVE with the ``worker`` extra (separate venvs required).
See ``pyproject.toml`` [project.optional-dependencies] for details.

Public API (Phase 1):
    - :class:`~sec_review_framework.agent.litellm_model.LiteLLMModel`
    - :func:`~sec_review_framework.agent.tool_adapter.make_tool_callables`
    - :class:`~sec_review_framework.agent.subagent.SubagentDeps`
    - :class:`~sec_review_framework.agent.subagent.SubagentOutput`
    - :func:`~sec_review_framework.agent.subagent.make_invoke_subagent_tool`
    - :func:`~sec_review_framework.agent.subagent.make_invoke_subagent_batch_tool`
"""
