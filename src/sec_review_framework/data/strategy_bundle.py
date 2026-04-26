"""Strategy bundle data models.

Defines the portable, self-contained description of how a strategy should
run: prompts, model, tools, verification variant, max_turns, and per-subagent
overrides keyed by VulnClass name or glob pattern.
"""

from __future__ import annotations

import fnmatch
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from sec_review_framework.data.findings import VulnClass


# ---------------------------------------------------------------------------
# Orchestration shape
# ---------------------------------------------------------------------------


class OrchestrationShape(str, Enum):
    """Enum tag carried by every UserStrategy.

    Retained for back-compat deserialization. New code should not BRANCH on
    this value (use UserStrategy fields instead), but new shapes may add enum
    members so older snapshots can deserialize.
    """

    SINGLE_AGENT = "single_agent"
    PER_FILE = "per_file"
    PER_VULN_CLASS = "per_vuln_class"
    SAST_FIRST = "sast_first"
    DIFF_REVIEW = "diff_review"
    # Phase 5 capability strategies
    SINGLE_AGENT_WITH_VERIFIER = "single_agent_with_verifier"
    CLASSIFIER_DISPATCH = "classifier_dispatch"
    TAINT_PIPELINE = "taint_pipeline"
    DIFF_BLAST_RADIUS = "diff_blast_radius"


# ---------------------------------------------------------------------------
# Bundle models
# ---------------------------------------------------------------------------


class StrategyBundleDefault(BaseModel):
    """Fully-specified bundle — all fields required."""

    system_prompt: str
    user_prompt_template: str
    profile_modifier: str = ""
    model_id: str
    tools: frozenset[str]
    verification: str  # VerificationVariant value (e.g. "none" / "with_verification")
    max_turns: int
    tool_extensions: frozenset[str]

    # Subagent role IDs this strategy may dispatch to.
    # Each ID must resolve in the StrategyRegistry at expand time.
    # Empty list = no subagent dispatch (all current built-in strategies).
    subagents: list[str] = Field(default_factory=list)
    # Per-subagent invocation caps — enforced by SubagentDeps at runtime.
    max_subagent_depth: int = 3
    max_subagent_invocations: int = 100
    max_subagent_batch_size: int = 32

    # Dispatch-completeness fallback behaviour for parent strategies with large
    # fixed subagent sets (e.g. per_vuln_class with 16 specialists).
    #
    # "reprompt"      — re-ask the supervisor LLM once for missing roles (default).
    # "programmatic"  — bypass the supervisor entirely; directly invoke missing
    #                   specialists via _run_child_sync (per_vuln_class only).
    # "none"          — no fallback; missing dispatches are silently dropped.
    dispatch_fallback: Literal["reprompt", "programmatic", "none"] = "reprompt"

    # Structured output type name for subagent output.
    # When set, _run_child_sync resolves the type and passes output_type=...
    # to the child Agent so pydantic-ai validates/coerces the LLM response into
    # the declared Pydantic model.  None means free-form text (legacy behaviour).
    #
    # Valid names (see agent/output_types.py):
    #   "finding_list"               → list[Finding]
    #   "verifier_verdict"           → VerifierVerdict
    #   "source_list"                → list[Source]
    #   "taint_path_list"            → list[TaintPath]
    #   "sanitization_verdict"       → SanitizationVerdict
    #   "classifier_judgement_list"  → list[ClassifierJudgement]
    output_type_name: str | None = None

    @field_validator("output_type_name")
    @classmethod
    def _validate_output_type_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        _VALID_OUTPUT_TYPE_NAMES = {
            "finding_list",
            "verifier_verdict",
            "source_list",
            "taint_path_list",
            "sanitization_verdict",
            "classifier_judgement_list",
        }
        if v not in _VALID_OUTPUT_TYPE_NAMES:
            raise ValueError(
                f"output_type_name {v!r} is not a known output type. "
                f"Valid names: {sorted(_VALID_OUTPUT_TYPE_NAMES)}"
            )
        return v

    model_config = {"frozen": True}


class StrategyBundleOverride(BaseModel):
    """Partial bundle — unset fields inherit from the strategy default."""

    system_prompt: str | None = None
    user_prompt_template: str | None = None
    profile_modifier: str | None = None
    model_id: str | None = None
    tools: frozenset[str] | None = None
    verification: str | None = None
    max_turns: int | None = None
    tool_extensions: frozenset[str] | None = None

    model_config = {"frozen": True}


class OverrideRule(BaseModel):
    """A single keyed override rule.

    ``key`` semantics depend on the owning strategy's ``orchestration_shape``:

    - ``per_vuln_class``: exact VulnClass name (e.g. ``"sqli"``).
    - ``per_file`` / ``sast_first``: glob pattern matched against file paths.
    - ``single_agent`` / ``diff_review``: not used (overrides list must be empty).
    """

    key: str
    override: StrategyBundleOverride

    model_config = {"frozen": True}


# ---------------------------------------------------------------------------
# UserStrategy
# ---------------------------------------------------------------------------

# Set of valid VulnClass values for fast membership tests
_VALID_VULN_CLASS_VALUES: frozenset[str] = frozenset(vc.value for vc in VulnClass)


class UserStrategy(BaseModel):
    """A user-visible strategy that bundles prompts, model config, and overrides."""

    id: str
    name: str
    parent_strategy_id: str | None
    orchestration_shape: OrchestrationShape
    default: StrategyBundleDefault
    overrides: list[OverrideRule] = []
    created_at: datetime
    is_builtin: bool = False

    @model_validator(mode="after")
    def _validate_overrides(self) -> "UserStrategy":
        shape = self.orchestration_shape

        # Shapes that must have no overrides
        if shape in (
            OrchestrationShape.SINGLE_AGENT,
            OrchestrationShape.DIFF_REVIEW,
            OrchestrationShape.SINGLE_AGENT_WITH_VERIFIER,
            OrchestrationShape.CLASSIFIER_DISPATCH,
            OrchestrationShape.TAINT_PIPELINE,
            OrchestrationShape.DIFF_BLAST_RADIUS,
        ):
            if self.overrides:
                raise ValueError(
                    f"orchestration_shape={shape.value!r} must have no overrides, "
                    f"but {len(self.overrides)} override(s) were provided."
                )
            return self

        # per_vuln_class: keys must be valid VulnClass names
        if shape == OrchestrationShape.PER_VULN_CLASS:
            for rule in self.overrides:
                if rule.key not in _VALID_VULN_CLASS_VALUES:
                    raise ValueError(
                        f"Override key {rule.key!r} is not a valid VulnClass name. "
                        f"Valid values: {sorted(_VALID_VULN_CLASS_VALUES)}"
                    )
            return self

        # per_file / sast_first: keys are glob patterns — validate they compile
        if shape in (OrchestrationShape.PER_FILE, OrchestrationShape.SAST_FIRST):
            for rule in self.overrides:
                try:
                    fnmatch.translate(rule.key)
                except Exception as exc:
                    raise ValueError(
                        f"Override key {rule.key!r} is not a valid glob pattern: {exc}"
                    ) from exc
            return self

        return self  # pragma: no cover — exhaustive enum


# ---------------------------------------------------------------------------
# ResolvedBundle
# ---------------------------------------------------------------------------

# A resolved bundle is simply a fully-populated StrategyBundleDefault
# (no Optionals). Type alias for clarity at call sites.
ResolvedBundle = StrategyBundleDefault


def resolve_bundle(strategy: UserStrategy, key: str | None) -> ResolvedBundle:
    """Merge the strategy default with the first matching override for *key*.

    Parameters
    ----------
    strategy:
        The strategy whose bundle should be resolved.
    key:
        - ``single_agent`` / ``diff_review``: must be ``None``.
        - ``per_vuln_class``: exact VulnClass name.
        - ``per_file`` / ``sast_first``: file path matched against glob patterns.

    Returns
    -------
    ResolvedBundle
        A fully-populated ``StrategyBundleDefault`` with overrides applied.

    Raises
    ------
    ValueError
        If *key* is non-``None`` for a single-agent/diff-review strategy, or
        if *key* is ``None`` for a keyed strategy shape.
    """
    shape = strategy.orchestration_shape
    default = strategy.default

    if shape in (
        OrchestrationShape.SINGLE_AGENT,
        OrchestrationShape.DIFF_REVIEW,
        OrchestrationShape.SINGLE_AGENT_WITH_VERIFIER,
        OrchestrationShape.CLASSIFIER_DISPATCH,
        OrchestrationShape.TAINT_PIPELINE,
        OrchestrationShape.DIFF_BLAST_RADIUS,
    ):
        if key is not None:
            raise ValueError(
                f"orchestration_shape={shape.value!r} does not accept a key, "
                f"but key={key!r} was provided."
            )
        return default

    if key is None:
        raise ValueError(
            f"orchestration_shape={shape.value!r} requires a key, but None was given."
        )

    # Find the first matching override
    override: StrategyBundleOverride | None = None

    if shape == OrchestrationShape.PER_VULN_CLASS:
        for rule in strategy.overrides:
            if rule.key == key:
                override = rule.override
                break
    elif shape in (OrchestrationShape.PER_FILE, OrchestrationShape.SAST_FIRST):
        for rule in strategy.overrides:
            if fnmatch.fnmatchcase(key, rule.key):
                override = rule.override
                break

    if override is None:
        return default

    # Merge: override wins for non-None fields
    merged: dict[str, Any] = default.model_dump()
    for field_name, value in override.model_dump(exclude_none=True).items():
        merged[field_name] = value

    return StrategyBundleDefault(**merged)


# ---------------------------------------------------------------------------
# canonical_json
# ---------------------------------------------------------------------------


def _make_json_serializable(obj: Any) -> Any:
    """Recursively convert frozensets → sorted lists and datetimes → isoformat."""
    if isinstance(obj, frozenset):
        return sorted(_make_json_serializable(v) for v in obj)
    if isinstance(obj, set):
        return sorted(_make_json_serializable(v) for v in obj)
    if isinstance(obj, dict):
        return {k: _make_json_serializable(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_make_json_serializable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    return obj


def canonical_json(strategy: UserStrategy) -> str:
    """Return deterministic JSON for *strategy*.

    - All dict keys sorted.
    - All frozenset/set values serialised as sorted lists.
    - Datetimes as ISO-8601 strings.

    Used to derive a stable content hash for the strategy.
    """
    raw = strategy.model_dump()
    serialisable = _make_json_serializable(raw)
    return json.dumps(serialisable, sort_keys=True, separators=(",", ":"))
