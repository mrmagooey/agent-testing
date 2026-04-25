"""Output-type resolver for subagent structured output.

Maps the string name declared in ``StrategyBundleDefault.output_type_name``
to the corresponding Pydantic model class (or generic alias like
``list[Finding]``) so that ``_run_child_sync`` can pass ``output_type=...``
to the child :class:`~pydantic_ai.Agent`.

Usage::

    from sec_review_framework.agent.output_types import resolve_output_type

    output_type = resolve_output_type(bundle.output_type_name)
    if output_type is not None:
        child_agent = Agent(model, output_type=output_type, ...)

Registry
--------
``"finding_list"``              → ``list[Finding]``
``"verifier_verdict"``          → :class:`~sec_review_framework.data.verification.VerifierVerdict`
``"source_list"``               → ``list[Source]``
``"taint_path_list"``           → ``list[TaintPath]``
``"sanitization_verdict"``      → :class:`~sec_review_framework.data.taint.SanitizationVerdict`
``"classifier_judgement_list"`` → ``list[ClassifierJudgement]``
"""

from __future__ import annotations

from typing import Any


def resolve_output_type(name: str | None) -> type | None:
    """Return the Pydantic output type for *name*, or ``None`` for free-form text.

    Parameters
    ----------
    name:
        The ``output_type_name`` declared on a
        :class:`~sec_review_framework.data.strategy_bundle.StrategyBundleDefault`,
        or ``None`` (free-form text — legacy behaviour).

    Returns
    -------
    type | None
        The matching Pydantic class or generic alias (e.g. ``list[Finding]``),
        or ``None`` if *name* is ``None``.

    Raises
    ------
    ValueError
        If *name* is not ``None`` and not found in the registry.
    """
    if name is None:
        return None

    # Lazy imports to avoid circular dependencies and keep import cost zero
    # when output_types.py is loaded but this function is never called.
    from sec_review_framework.data.classification import ClassifierJudgement
    from sec_review_framework.data.findings import Finding
    from sec_review_framework.data.taint import SanitizationVerdict, Source, TaintPath
    from sec_review_framework.data.verification import VerifierVerdict

    _REGISTRY: dict[str, Any] = {
        "finding_list": list[Finding],
        "verifier_verdict": VerifierVerdict,
        "source_list": list[Source],
        "taint_path_list": list[TaintPath],
        "sanitization_verdict": SanitizationVerdict,
        "classifier_judgement_list": list[ClassifierJudgement],
    }

    if name not in _REGISTRY:
        raise ValueError(
            f"resolve_output_type: unknown output_type_name {name!r}. "
            f"Valid names: {sorted(_REGISTRY)}"
        )

    return _REGISTRY[name]
