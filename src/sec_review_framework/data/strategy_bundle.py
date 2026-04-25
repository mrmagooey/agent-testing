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

from pydantic import BaseModel, Field, model_validator

from sec_review_framework.data.findings import VulnClass


# ---------------------------------------------------------------------------
# Orchestration shape
# ---------------------------------------------------------------------------


class OrchestrationShape(str, Enum):
    """Enum tag carried by every UserStrategy.

    Deprecated (Phase 4): new code should not branch on this value; runner.py
    treats all UserStrategies uniformly via run_strategy(). Enum members are
    kept for backward-compatible deserialization of historical BundleSnapshot /
    ExperimentRun rows — do not remove them.
    """

    SINGLE_AGENT = "single_agent"
    PER_FILE = "per_file"
    PER_VULN_CLASS = "per_vuln_class"
    SAST_FIRST = "sast_first"
    DIFF_REVIEW = "diff_review"


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

    # Phase 2: subagent role IDs this strategy may dispatch to.
    # Each ID must resolve in the StrategyRegistry at expand time.
    # Empty list = no subagent dispatch (all current built-in strategies).
    # Phase 4 will add validation (no self-reference, cap fields, etc.).
    subagents: list[str] = Field(default_factory=list)
    # Per-subagent invocation caps — enforced by SubagentDeps at runtime.
    max_subagent_depth: int = 3
    max_subagent_invocations: int = 100
    max_subagent_batch_size: int = 32

    # Phase 3c: dispatch-completeness fallback behaviour for parent strategies
    # with large fixed subagent sets (e.g. per_vuln_class with 16 specialists).
    #
    # "reprompt"      — re-ask the supervisor LLM once for missing roles
    #                   (Phase 3b behaviour, default for most strategies).
    # "programmatic"  — bypass the supervisor entirely; directly invoke missing
    #                   specialists via _run_child_sync (per_vuln_class only).
    # "none"          — no fallback; missing dispatches are silently dropped.
    #
    # Phase 4 will normalise this across all strategies.
    dispatch_fallback: Literal["reprompt", "programmatic", "none"] = "reprompt"

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
        if shape in (OrchestrationShape.SINGLE_AGENT, OrchestrationShape.DIFF_REVIEW):
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

    if shape in (OrchestrationShape.SINGLE_AGENT, OrchestrationShape.DIFF_REVIEW):
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
