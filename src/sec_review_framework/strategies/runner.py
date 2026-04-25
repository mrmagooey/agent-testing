"""Unified parent-agent runner for pydantic-ai–backed strategies.

This module is the **Phase 2/3b** implementation of the unified runner described
in ``plan_subagents_pydantic_ai.md`` § 7.  It provides a single entry-point,
:func:`run_strategy`, that builds a pydantic-ai :class:`~pydantic_ai.Agent`
from a :class:`~sec_review_framework.data.strategy_bundle.UserStrategy` and
runs it to completion.

Contract
--------
- Requires the ``agent`` extra (pydantic-ai).  Workers that run without the
  ``agent`` extra must NOT import this module at top-level.  The import is
  deferred inside the feature-flag branch in :mod:`~sec_review_framework.worker`.
- All existing :class:`~sec_review_framework.strategies.base.ScanStrategy`
  subclasses are untouched.  Strategies that do NOT set ``use_new_runner=True``
  continue to go through the legacy ``_SHAPE_TO_STRATEGY`` dispatch in
  ``worker.py``.  Phase 3 migrates the five built-in shapes; Phase 4 deletes
  the old code paths.
- ``output_type`` is always ``list[Finding]`` in Phase 2/3b.  Richer per-strategy
  schemas (e.g. ``ReviewSummary``) are deferred to Phase 3.

Feature-flag mechanism
----------------------
:class:`~sec_review_framework.data.strategy_bundle.UserStrategy` carries a
``use_new_runner: bool = False`` field with ``exclude=True`` so it is not
persisted to the DB and does not affect the content-hash ID.  The worker
reads this flag at runtime; if ``True`` it calls :func:`run_strategy`
(importing this module lazily); if ``False`` it falls through to the legacy
dispatch.

Subagent injection
------------------
When ``strategy.default.subagents`` is non-empty the runner injects two tools
into the parent agent:

- ``invoke_subagent(role, input)`` — single dispatch.
- ``invoke_subagent_batch(role, inputs)`` — parallel batch dispatch.

Both tools are built by
:func:`~sec_review_framework.agent.subagent.make_invoke_subagent_tool` and
:func:`~sec_review_framework.agent.subagent.make_invoke_subagent_batch_tool`.
A :class:`~sec_review_framework.agent.subagent.SubagentDeps` is constructed
(or provided by the caller via *deps_factory*) and passed as ``deps`` to
``agent.run_sync()``.

Dispatch validator
------------------
After ``agent.run_sync()`` completes, :func:`_validate_dispatch` inspects the
``invoke_subagent_batch`` calls recorded on the deps object.  If any expected
inputs were not dispatched, the validator re-prompts the agent with the missing
list (continuation message).  The re-prompt is bounded to 1 attempt.

Error handling
--------------
:exc:`~pydantic_ai.exceptions.UnexpectedModelBehavior` from pydantic-ai is
caught and re-raised as a :exc:`RunnerError` (a domain-level exception) so
callers do not need to import pydantic-ai just to catch errors.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

# pydantic-ai imports — fail loudly if the "agent" extra is not installed.
# Do NOT guard with try/except; the caller (worker.py) gates this import
# behind the feature flag.
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior

from sec_review_framework.agent.litellm_model import LiteLLMModel
from sec_review_framework.agent.subagent import (
    SubagentDeps,
    make_invoke_subagent_batch_tool,
    make_invoke_subagent_tool,
)
from sec_review_framework.agent.tool_adapter import make_tool_callables
from sec_review_framework.data.findings import Finding, StrategyOutput

if TYPE_CHECKING:
    from sec_review_framework.data.strategy_bundle import UserStrategy
    from sec_review_framework.models.base import ModelProvider
    from sec_review_framework.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Domain error
# ---------------------------------------------------------------------------


class RunnerError(RuntimeError):
    """Raised when pydantic-ai produces an unexpected response.

    Wraps :exc:`~pydantic_ai.exceptions.UnexpectedModelBehavior` so callers do
    not need the ``agent`` extra installed to catch this error.
    """


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_strategy(
    strategy: UserStrategy,
    target: object,
    model: ModelProvider,
    tools: ToolRegistry,
    *,
    deps_factory: Callable[[], SubagentDeps] | None = None,
    expected_dispatch: list[dict[str, Any]] | None = None,
    dispatch_match_key: str = "file_path",
) -> StrategyOutput:
    """Run *strategy* using the pydantic-ai unified runner.

    This is the single entry-point for Phase 2 and beyond.  It builds a
    pydantic-ai :class:`~pydantic_ai.Agent` from the strategy bundle, registers
    tools, injects subagent dispatchers when needed, and returns a
    :class:`~sec_review_framework.data.findings.StrategyOutput`.

    Parameters
    ----------
    strategy:
        The :class:`~sec_review_framework.data.strategy_bundle.UserStrategy`
        to execute.  Must have ``use_new_runner=True`` (the worker checks this
        before calling; the runner itself does not enforce it).
    target:
        The target codebase.  Passed to ``strategy.default.user_prompt_template``
        via ``target.get_file_tree()`` (with fallback to ``list_source_files()``).
    model:
        A :class:`~sec_review_framework.models.base.ModelProvider` (typically
        :class:`~sec_review_framework.models.litellm_provider.LiteLLMProvider`).
        Wrapped in a :class:`~sec_review_framework.agent.litellm_model.LiteLLMModel`.
    tools:
        The :class:`~sec_review_framework.tools.registry.ToolRegistry` for this
        run.  All registered tools are adapted and passed to the agent.
    deps_factory:
        Optional callable that returns a pre-constructed
        :class:`~sec_review_framework.agent.subagent.SubagentDeps`.  Used in
        tests to inject controlled deps.  When ``None`` and subagents are
        declared, a default ``SubagentDeps`` is built from the strategy caps.
    expected_dispatch:
        Optional list of input dicts the parent is expected to dispatch via
        ``invoke_subagent_batch``.  When provided, the dispatch validator runs
        after the first agent turn; if any inputs were missed the agent is
        re-prompted with the missing items (one re-prompt only).
    dispatch_match_key:
        The key used to identify unique inputs in *expected_dispatch* (default
        ``"file_path"``).

    Returns
    -------
    StrategyOutput
        Findings list with dedup metadata.  In Phase 2/3b ``pre_dedup_count``
        and ``post_dedup_count`` are both equal to ``len(findings)`` (no
        deduplication — single agent, no overlap).

    Raises
    ------
    RunnerError
        When pydantic-ai raises
        :exc:`~pydantic_ai.exceptions.UnexpectedModelBehavior`.

    Notes
    -----
    - Existing strategies still use their ``ScanStrategy.run()`` implementation
      until Phase 3 migrates them.  Do NOT call this function for strategies
      whose ``use_new_runner`` flag is ``False``.
    - ``output_type=list[Finding]`` is hard-coded in Phase 2.  Richer schemas
      come in Phase 3.
    """
    bundle = strategy.default

    # ------------------------------------------------------------------
    # 1. Build the system prompt
    # ------------------------------------------------------------------
    system_prompt = bundle.system_prompt
    if bundle.profile_modifier:
        system_prompt = f"{system_prompt}\n\n{bundle.profile_modifier}"

    # ------------------------------------------------------------------
    # 2. Adapt model + tools for pydantic-ai
    # ------------------------------------------------------------------
    llm_model = LiteLLMModel(model)  # type: ignore[arg-type]
    tool_callables = make_tool_callables(tools)

    # ------------------------------------------------------------------
    # 3. Build the user prompt from the template + target
    # ------------------------------------------------------------------
    user_prompt = _build_user_prompt(bundle.user_prompt_template, target)

    # ------------------------------------------------------------------
    # 4. Decide whether subagent tools are needed
    # ------------------------------------------------------------------
    subagent_roles: list[str] = list(bundle.subagents)
    deps: SubagentDeps | None = None

    if subagent_roles:
        # Build or accept a SubagentDeps
        if deps_factory is not None:
            deps = deps_factory()
        else:
            deps = _build_default_deps(strategy, tools)

        # Inject subagent dispatch tools
        tool_callables = tool_callables + [
            make_invoke_subagent_tool(),
            make_invoke_subagent_batch_tool(),
        ]

    # ------------------------------------------------------------------
    # 5. Build the pydantic-ai Agent
    # ------------------------------------------------------------------
    agent: Agent[SubagentDeps | None, list[Finding]] = Agent(
        llm_model,
        system_prompt=system_prompt,
        tools=tool_callables,
        output_type=list[Finding],
    )

    # ------------------------------------------------------------------
    # 6. Run the agent
    # ------------------------------------------------------------------
    try:
        result = agent.run_sync(user_prompt, deps=deps)
    except UnexpectedModelBehavior as exc:
        raise RunnerError(
            f"run_strategy: pydantic-ai produced an unexpected response for "
            f"strategy {strategy.id!r}: {exc}"
        ) from exc

    findings: list[Finding] = result.output or []

    # ------------------------------------------------------------------
    # 7. Dispatch validator (optional, bounded to 1 re-prompt)
    # ------------------------------------------------------------------
    if expected_dispatch and deps is not None:
        missing = _validate_dispatch(
            strategy.id,
            expected_dispatch,
            deps.batch_call_log,
            dispatch_match_key,
        )
        if missing:
            re_prompt = (
                "You missed dispatching the following inputs. "
                "Please call invoke_subagent_batch now for these missing items:\n"
                + "\n".join(str(m) for m in missing)
            )
            try:
                retry_result = agent.run_sync(
                    re_prompt,
                    message_history=result.all_messages(),
                    deps=deps,
                )
                extra_findings: list[Finding] = retry_result.output or []
                findings = findings + extra_findings
            except UnexpectedModelBehavior as exc:
                logging.warning(
                    "run_strategy: re-prompt for strategy %r failed: %s",
                    strategy.id,
                    exc,
                )

    # Stamp required fields that pydantic-ai's structured output does not
    # populate automatically (id, raw_llm_output, produced_by, experiment_id).
    findings = _stamp_findings(findings, strategy_id=strategy.id)

    return StrategyOutput(
        findings=findings,
        pre_dedup_count=len(findings),
        post_dedup_count=len(findings),
        dedup_log=[],
        system_prompt=system_prompt,
        user_message=user_prompt,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_user_prompt(
    template: str,
    target: object,
    context: dict[str, Any] | None = None,
) -> str:
    """Render *template* with *target* context and optional extra *context*.

    Supports all placeholders used by the built-in strategy prompt files:

    - ``{repo_summary}`` — file tree or source-file list from *target*.
    - ``{finding_output_format}`` — from ``common.FINDING_OUTPUT_FORMAT``.
    - ``{glob}`` — from ``target.config.file_glob`` if available (else ``""``)
      or from *context*.
    - ``{diff_text}`` — from ``target.diff_text`` if available (else ``""``)
      or from *context*.
    - ``{file_path}`` — per-subagent placeholder; filled from *context* only.
    - ``{file_content}`` — per-subagent placeholder; filled from *context* only.
    - ``{sast_findings}`` — per-subagent placeholder; filled from *context* only.
    - ``{vuln_class}`` — per-subagent placeholder; filled from *context* only.

    Any placeholder not present in the built-in mapping or *context* is left
    untouched so that templates with partial placeholders do not fail.

    Parameters
    ----------
    template:
        The raw prompt template string.
    target:
        The target codebase object.  Attributes are introspected for standard
        placeholders (``get_file_tree``, ``list_source_files``, ``diff_text``,
        ``config.file_glob``).
    context:
        Optional extra key→value pairs that override or supplement the values
        derived from *target*.  Useful for per-subagent placeholders like
        ``file_path``, ``file_content``, ``sast_findings``, ``vuln_class``.
    """
    from sec_review_framework.strategies.common import FINDING_OUTPUT_FORMAT

    # Build the repo summary from the target
    try:
        repo_summary: str = target.get_file_tree()  # type: ignore[attr-defined]
    except AttributeError:
        try:
            files = target.list_source_files()  # type: ignore[attr-defined]
            repo_summary = "\n".join(files)
        except AttributeError:
            repo_summary = str(target)

    # Extract optional target attributes with safe fallbacks
    try:
        diff_text: str = target.diff_text  # type: ignore[attr-defined]
    except AttributeError:
        diff_text = ""

    try:
        glob: str = target.config.file_glob  # type: ignore[attr-defined]
    except AttributeError:
        glob = ""

    class _Missing(dict):
        """Leave unknown placeholders as-is."""

        def __missing__(self, key: str) -> str:
            return "{" + key + "}"

    # Base values from target
    base: dict[str, Any] = {
        "repo_summary": repo_summary,
        "finding_output_format": FINDING_OUTPUT_FORMAT,
        "diff_text": diff_text,
        "glob": glob,
        # Per-subagent placeholders — empty by default; callers pass via context
        "file_path": "",
        "file_content": "",
        "sast_findings": "",
        "vuln_class": "",
    }

    # Caller-supplied context overrides target-derived values
    if context:
        base.update(context)

    return template.format_map(_Missing(base))


def _validate_dispatch(
    strategy_id: str,
    expected_inputs: list[dict[str, Any]],
    actual_calls: list[tuple[str, list[dict[str, Any]]]],
    match_key: str,
) -> list[dict[str, Any]]:
    """Check that all *expected_inputs* were dispatched in *actual_calls*.

    Parameters
    ----------
    strategy_id:
        Strategy identifier (for logging only).
    expected_inputs:
        List of input dicts the parent was expected to dispatch (e.g. one per
        file or one per flagged file).  Each dict must contain *match_key*.
    actual_calls:
        The ``deps.batch_call_log`` recorded during the run — a list of
        ``(role, inputs)`` tuples.
    match_key:
        The key used to identify unique inputs (e.g. ``"file_path"``).

    Returns
    -------
    list[dict[str, Any]]
        Sub-list of *expected_inputs* that were NOT dispatched.  Empty list
        means all inputs were dispatched correctly.
    """
    dispatched_keys: set[str] = set()
    for _role, inputs in actual_calls:
        for inp in inputs:
            if match_key in inp:
                dispatched_keys.add(str(inp[match_key]))

    missing = [
        inp
        for inp in expected_inputs
        if str(inp.get(match_key, "")) not in dispatched_keys
    ]

    if missing:
        logging.warning(
            "_validate_dispatch: strategy %r missed %d/%d expected inputs (key=%r). "
            "Missing: %s",
            strategy_id,
            len(missing),
            len(expected_inputs),
            match_key,
            [inp.get(match_key) for inp in missing],
        )

    return missing


def _build_default_deps(
    strategy: UserStrategy,
    tools: ToolRegistry,
) -> SubagentDeps:
    """Construct a default :class:`SubagentDeps` from *strategy*'s caps.

    Subagent strategies are looked up from the default registry.  Missing
    roles (not yet seeded) are silently skipped — the agent will receive a
    ``ModelRetry`` if it tries to invoke an unknown role.

    Parameters
    ----------
    strategy:
        Parent strategy whose ``default.subagents`` list defines roles.
    tools:
        Tool registry passed to the parent; cloned by children.

    Returns
    -------
    SubagentDeps
        Ready-to-use deps for the parent agent.
    """
    from sec_review_framework.strategies.strategy_registry import load_default_registry

    bundle = strategy.default
    roles: list[str] = list(bundle.subagents)

    # Try to resolve each role from the registry
    subagent_strategies: dict[str, UserStrategy] = {}
    try:
        registry = load_default_registry()
        for role in roles:
            try:
                subagent_strategies[role] = registry.get(role)
            except KeyError:
                pass  # Missing roles handled at runtime via ModelRetry
    except Exception as e:
        logging.warning(
            "Failed to load strategy registry; subagent dispatch will treat all roles as unknown: %s",
            e,
        )

    return SubagentDeps(
        depth=0,
        max_depth=bundle.max_subagent_depth,
        invocations=0,
        max_invocations=bundle.max_subagent_invocations,
        max_batch_size=bundle.max_subagent_batch_size,
        available_roles=set(roles),
        subagent_strategies=subagent_strategies,
        tool_registry=tools,
    )


def _stamp_findings(
    findings: list[Finding],
    *,
    strategy_id: str,
    experiment_id: str = "",
) -> list[Finding]:
    """Ensure every finding has a non-empty ``id``, ``produced_by``, and ``experiment_id``.

    pydantic-ai's structured-output path fills in the fields the model
    returned.  Fields with defaults (``id`` defaults to empty string in some
    usages, ``produced_by`` and ``experiment_id`` may be missing) are stamped
    here so downstream code that assumes non-empty IDs works correctly.

    Parameters
    ----------
    findings:
        Findings as returned by ``agent.run_sync().output``.
    strategy_id:
        Strategy identifier stamped into ``produced_by`` when absent.
    experiment_id:
        Experiment identifier (empty in Phase 2; populated by Phase 3).

    Returns
    -------
    list[Finding]
        New list with required fields guaranteed non-empty.
    """
    stamped: list[Finding] = []
    for f in findings:
        needs_update = (
            not f.id
            or not f.produced_by
            or not f.experiment_id
            or not f.raw_llm_output
        )
        if needs_update:
            f = f.model_copy(
                update={
                    "id": f.id or str(uuid.uuid4()),
                    "produced_by": f.produced_by or strategy_id,
                    "experiment_id": f.experiment_id or experiment_id,
                    "raw_llm_output": f.raw_llm_output or "",
                }
            )
        stamped.append(f)
    return stamped
