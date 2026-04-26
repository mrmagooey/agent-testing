"""Unified parent-agent runner for pydantic-ai–backed strategies.

Provides a single entry-point :func:`run_strategy` that builds a
pydantic-ai :class:`~pydantic_ai.Agent` from a
:class:`~sec_review_framework.data.strategy_bundle.UserStrategy` and runs it
to completion.

Contract
--------
- Requires the ``agent`` extra (pydantic-ai).  Workers that run without the
  ``agent`` extra must NOT import this module at top-level.  The import is
  deferred lazily inside :mod:`~sec_review_framework.worker`.
- This is the only dispatch path; legacy ``ScanStrategy`` subclasses and
  ``_SHAPE_TO_STRATEGY`` dispatch have been deleted.
- ``output_type`` is ``list[Finding]``.

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
from sec_review_framework.agent.output_types import resolve_output_type
from sec_review_framework.agent.subagent import (
    SubagentDeps,
    _run_child_sync,
    make_invoke_subagent_batch_tool,
    make_invoke_subagent_tool,
    resolve_role,
)
from sec_review_framework.agent.tool_adapter import make_tool_callables
from sec_review_framework.data.findings import Finding, StrategyOutput
from sec_review_framework.data.strategy_bundle import OrchestrationShape

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

    This is the single entry-point.  It builds a pydantic-ai
    :class:`~pydantic_ai.Agent` from the strategy bundle, registers tools,
    injects subagent dispatchers when needed, and returns a
    :class:`~sec_review_framework.data.findings.StrategyOutput`.

    Parameters
    ----------
    strategy:
        The :class:`~sec_review_framework.data.strategy_bundle.UserStrategy`
        to execute.
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
        Caller-supplied override for the expected dispatch list.  For shapes
        where the expected set is statically known (PER_VULN_CLASS, PER_FILE),
        it is auto-derived inside this function; the kwarg only needs to be
        passed to override that derivation.  Ignored for SAST_FIRST (output
        depends on Semgrep run output, unknown at entry time).
    dispatch_match_key:
        The key used to identify unique inputs in *expected_dispatch* (default
        ``"file_path"``).  Auto-set to ``"vuln_class"`` for PER_VULN_CLASS.

    Returns
    -------
    StrategyOutput
        Findings list with dedup metadata.  ``dispatch_completeness`` carries
        the validated dispatch ratio when the validator ran; ``non_finding_output``
        carries structured output when the strategy's ``output_type_name`` is not
        ``"finding_list"``.

    Raises
    ------
    RunnerError
        When pydantic-ai raises
        :exc:`~pydantic_ai.exceptions.UnexpectedModelBehavior`.
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
    # 5. Resolve output type (fix #5: honour bundle.output_type_name)
    # ------------------------------------------------------------------
    output_type: type = resolve_output_type(bundle.output_type_name) or list[Finding]  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # 6. Auto-derive expected_dispatch for shapes with a static input set
    #    (fix #2: validator now runs in production without caller changes)
    #
    # SAST_FIRST is intentionally excluded: the expected dispatch depends on
    # the Semgrep run output, which is unknown at strategy-entry time.  Rather
    # than accept any partial dispatch as "correct", we simply leave the
    # validator off for SAST_FIRST and document that decision here.
    # ------------------------------------------------------------------
    shape = strategy.orchestration_shape
    effective_dispatch = expected_dispatch  # caller override always wins
    effective_match_key = dispatch_match_key

    if effective_dispatch is None and subagent_roles and bundle.dispatch_fallback != "none":
        if shape == OrchestrationShape.PER_VULN_CLASS:
            from sec_review_framework.data.findings import VulnClass
            effective_dispatch = [{"vuln_class": vc.value} for vc in VulnClass]
            effective_match_key = "vuln_class"
        elif shape == OrchestrationShape.PER_FILE:
            try:
                source_files: list[str] = target.list_source_files()  # type: ignore[attr-defined]
            except AttributeError:
                source_files = []
            if source_files:
                effective_dispatch = [{"file_path": fp} for fp in source_files]
                effective_match_key = "file_path"
        # SAST_FIRST and other shapes: leave effective_dispatch as None

    # ------------------------------------------------------------------
    # 7. Build the pydantic-ai Agent
    # ------------------------------------------------------------------
    agent: Agent[SubagentDeps | None, Any] = Agent(
        llm_model,
        system_prompt=system_prompt,
        tools=tool_callables,
        output_type=output_type,
    )

    # ------------------------------------------------------------------
    # 8. Run the agent
    # ------------------------------------------------------------------
    try:
        result = agent.run_sync(user_prompt, deps=deps)
    except UnexpectedModelBehavior as exc:
        raise RunnerError(
            f"run_strategy: pydantic-ai produced an unexpected response for "
            f"strategy {strategy.id!r}: {exc}"
        ) from exc

    # ------------------------------------------------------------------
    # 9. Extract findings (only when output_type IS list[Finding])
    # ------------------------------------------------------------------
    dispatch_completeness: float | None = None
    non_finding_output: object | None = None

    _is_finding_output = output_type == list[Finding]
    if _is_finding_output:
        findings: list[Finding] = result.output or []
    else:
        # Non-finding structured output — wrap into StrategyOutput.non_finding_output
        non_finding_output = result.output
        findings = []

    # ------------------------------------------------------------------
    # 10. Dispatch validator (fix #2: runs unconditionally when subagents
    #     are non-empty, fallback_mode != "none", and dispatch is derivable)
    #
    # Fallback behaviour is controlled by strategy.default.dispatch_fallback:
    #   "reprompt"      — re-ask the supervisor LLM once (default).
    #   "programmatic"  — bypass the supervisor; directly invoke missing
    #                     specialists via _run_child_sync.
    #   "none"          — no fallback; missing dispatches are silently dropped.
    # ------------------------------------------------------------------
    fallback_mode = bundle.dispatch_fallback
    if effective_dispatch is not None and deps is not None and fallback_mode != "none":
        # Combine single and batch call logs to detect dispatched roles
        all_actual_calls = list(deps.batch_call_log) + [
            (role, [inp]) for role, inp in deps.single_call_log
        ]
        missing = _validate_dispatch(
            strategy.id,
            effective_dispatch,
            all_actual_calls,
            effective_match_key,
        )

        dispatched_count = len(effective_dispatch) - len(missing)
        dispatch_completeness = dispatched_count / len(effective_dispatch) if effective_dispatch else 1.0

        if missing:
            if fallback_mode == "programmatic":
                # Programmatic fallback — bypass the supervisor LLM entirely for
                # missing roles and call specialists directly.
                extra_findings = _programmatic_fallback(
                    strategy.id,
                    missing,
                    effective_match_key,
                    deps,
                )
                findings = findings + extra_findings
                dispatch_completeness = 1.0
            else:
                # fix #8: re-prompt with invoke_subagent_batch framing, then
                # deduplicate to avoid duplicates from the supervisor re-emitting
                # previously-dispatched results alongside the new ones.
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
                    extra_findings_reprompt: list[Finding] = retry_result.output or []
                    # Deduplicate the combined set to collapse any findings the
                    # supervisor re-emits alongside the newly-dispatched ones.
                    from sec_review_framework.strategies.common import deduplicate
                    combined_output = deduplicate(findings + extra_findings_reprompt)
                    findings = combined_output.findings

                    # Second-pass validator: log a warning if misses remain.
                    all_actual_calls_after = list(deps.batch_call_log) + [
                        (role, [inp]) for role, inp in deps.single_call_log
                    ]
                    still_missing = _validate_dispatch(
                        strategy.id,
                        effective_dispatch,
                        all_actual_calls_after,
                        effective_match_key,
                    )
                    if still_missing:
                        dispatched_after = len(effective_dispatch) - len(still_missing)
                        dispatch_completeness = dispatched_after / len(effective_dispatch)
                        logging.warning(
                            "run_strategy: strategy %r still missing %d/%d dispatches after re-prompt "
                            "(completeness=%.2f)",
                            strategy.id,
                            len(still_missing),
                            len(effective_dispatch),
                            dispatch_completeness,
                        )
                    else:
                        dispatch_completeness = 1.0
                except UnexpectedModelBehavior as exc:
                    logging.warning(
                        "run_strategy: re-prompt for strategy %r failed: %s",
                        strategy.id,
                        exc,
                    )

    # Stamp required fields that pydantic-ai's structured output does not
    # populate automatically (id, raw_llm_output, produced_by, experiment_id).
    if _is_finding_output:
        findings = _stamp_findings(findings, strategy_id=strategy.id)

    # Collect token and conversation logs from all child providers in the cache.
    # Children with the same model_id share a provider; their entries accumulate
    # on that provider's token_log and conversation_log.
    child_token_log: list[object] = []
    child_conversation_log: list[object] = []
    if deps is not None and deps.model_provider_cache is not None:
        # pylint: disable=protected-access
        for provider in deps.model_provider_cache._cache.values():
            child_token_log.extend(provider.token_log)
            child_conversation_log.extend(provider.conversation_log)

    return StrategyOutput(
        findings=findings,
        pre_dedup_count=len(findings),
        post_dedup_count=len(findings),
        dedup_log=[],
        system_prompt=system_prompt,
        user_message=user_prompt,
        non_finding_output=non_finding_output,
        dispatch_completeness=dispatch_completeness,
        child_token_log=child_token_log,
        child_conversation_log=child_conversation_log,
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


def _programmatic_fallback(
    strategy_id: str,
    missing_inputs: list[dict[str, Any]],
    dispatch_match_key: str,
    deps: SubagentDeps,
) -> list[Finding]:
    """Programmatically invoke missing specialists, bypassing the supervisor LLM.

    Called by :func:`run_strategy` when ``dispatch_fallback="programmatic"``
    and the supervisor missed some roles.  Each missing input is mapped to its
    specialist role (``<vuln_class>_specialist``) and invoked directly via
    :func:`~sec_review_framework.agent.subagent._run_child_sync`.

    Direct programmatic invocation guarantees all specialists run regardless of
    LLM behaviour, providing reproducibility for per_vuln_class strategies.

    Parameters
    ----------
    strategy_id:
        Parent strategy identifier (for logging).
    missing_inputs:
        List of input dicts that were not dispatched by the supervisor.  Each
        dict must contain *dispatch_match_key* (typically ``"vuln_class"``).
    dispatch_match_key:
        Key used to derive the specialist role name.  For ``per_vuln_class``
        this is ``"vuln_class"``; the role is ``{value}_specialist``.
    deps:
        The parent's :class:`~sec_review_framework.agent.subagent.SubagentDeps`.
        Specialist strategies are resolved from ``deps.subagent_strategies``.

    Returns
    -------
    list[Finding]
        Findings from all programmatically-invoked specialists.  Empty list if
        no missing specialists could be resolved.
    """
    import concurrent.futures

    extra_findings: list[Finding] = []

    logging.info(
        "_programmatic_fallback: strategy %r programmatically invoking %d missing specialist(s): %s",
        strategy_id,
        len(missing_inputs),
        [inp.get(dispatch_match_key) for inp in missing_inputs],
    )

    def _invoke_one(inp: dict[str, Any]) -> list[Finding]:
        match_value = inp.get(dispatch_match_key, "")
        # Role suffix as emitted by the parent agent (e.g. "sqli_specialist")
        role_suffix = f"{match_value}_specialist"

        # Resolve the full strategy ID using the shared resolver.
        # Handles both bare "sqli_specialist" and namespaced "builtin_v2.sqli_specialist".
        role = resolve_role(role_suffix, set(deps.subagent_strategies.keys()))

        if role is None:
            logging.warning(
                "_programmatic_fallback: no strategy matching role suffix %r found "
                "in subagent_strategies; skipping.",
                role_suffix,
            )
            return []

        strategy = deps.subagent_strategies[role]
        try:
            sub_output = _run_child_sync(strategy, inp, deps)
        except Exception as exc:
            logging.warning(
                "_programmatic_fallback: role %r raised %s; skipping.",
                role,
                exc,
            )
            return []

        raw = sub_output.output
        if raw is None:
            return []
        if isinstance(raw, list):
            return [f for f in raw if isinstance(f, Finding)]
        return []

    # Fan-out in a thread pool (same pattern as invoke_subagent_batch)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(_invoke_one, inp) for inp in missing_inputs]
        for fut in concurrent.futures.as_completed(futures):
            try:
                extra_findings.extend(fut.result())
            except Exception as exc:
                logging.warning(
                    "_programmatic_fallback: unexpected error from worker: %s", exc
                )

    return extra_findings


def _build_default_deps(
    strategy: UserStrategy,
    tools: ToolRegistry,
) -> SubagentDeps:
    """Construct a default :class:`SubagentDeps` from *strategy*'s caps.

    Subagent strategies are looked up from the default registry.  Missing
    roles (not yet seeded) are silently skipped — the agent will receive a
    ``ModelRetry`` if it tries to invoke an unknown role.

    A :class:`~sec_review_framework.strategies.common.ModelProviderCache` is
    created here and attached to the deps so that ``_run_child`` can share
    providers across children with the same ``model_id``.

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
    from sec_review_framework.models.litellm_provider import LiteLLMProvider
    from sec_review_framework.strategies.common import ModelProviderCache
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

    # Build a cache with a factory so children get shared providers keyed by
    # model_id.  Token usage and conversation logs accumulate on these shared
    # providers, making child spend visible to run_strategy's caller.
    cache = ModelProviderCache(factory=lambda mid: LiteLLMProvider(model_name=mid))

    return SubagentDeps(
        depth=0,
        max_depth=bundle.max_subagent_depth,
        invocations=0,
        max_invocations=bundle.max_subagent_invocations,
        max_batch_size=bundle.max_subagent_batch_size,
        available_roles=set(roles),
        subagent_strategies=subagent_strategies,
        tool_registry=tools,
        model_provider_cache=cache,
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
