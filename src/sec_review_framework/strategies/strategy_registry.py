"""Strategy registry — in-memory store of UserStrategy objects.

Deliberately named ``strategy_registry`` (not ``registry``) to avoid
collision with ``sec_review_framework.prompts.registry``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    OverrideRule,
    StrategyBundleDefault,
    StrategyBundleOverride,
    UserStrategy,
)

# ---------------------------------------------------------------------------
# StrategyRegistry
# ---------------------------------------------------------------------------


class StrategyRegistry:
    """In-memory registry of UserStrategy objects keyed by strategy ID."""

    def __init__(self) -> None:
        self._strategies: dict[str, UserStrategy] = {}

    def register(self, strategy: UserStrategy) -> None:
        """Add or replace *strategy* in the registry."""
        self._strategies[strategy.id] = strategy

    def get(self, strategy_id: str) -> UserStrategy:
        """Return the strategy with *strategy_id*.

        Raises
        ------
        KeyError
            If no strategy with *strategy_id* is registered.
        """
        try:
            return self._strategies[strategy_id]
        except KeyError:
            raise KeyError(
                f"Strategy {strategy_id!r} is not registered. "
                f"Available IDs: {sorted(self._strategies)}"
            )

    def list_all(self) -> list[UserStrategy]:
        """Return all registered strategies, sorted by ID."""
        return [self._strategies[k] for k in sorted(self._strategies)]


# ---------------------------------------------------------------------------
# Builtin seeding
# ---------------------------------------------------------------------------

_SYSTEM_DIR = Path(__file__).parent.parent / "prompts" / "system"
_USER_DIR = Path(__file__).parent.parent / "prompts" / "user"

# Default values mirrored from worker.py + strategy files
_DEFAULT_MODEL_ID = "claude-opus-4-5"
_DEFAULT_TOOLS: frozenset[str] = frozenset(
    ["read_file", "list_directory", "grep", "doc_lookup"]
)
_DEFAULT_VERIFICATION = "none"
_DEFAULT_TOOL_EXTENSIONS: frozenset[str] = frozenset()
_CREATED_AT = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC).replace(tzinfo=None)


def _read(path: Path) -> str:
    """Read a file and return its stripped text, or "" if it doesn't exist."""
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def seed_builtins(registry: StrategyRegistry) -> None:
    """Construct the builtin UserStrategy objects and register them.

    All strategies use the pydantic-ai runner (runner.py). The legacy
    ScanStrategy subclasses were removed in Phase 4.

    Phase 5 adds four new capability strategies that exploit subagents:
    - ``builtin.single_agent_with_verifier`` — verifier wrapping
    - ``builtin.classifier_dispatch`` — classifier-guided specialist dispatch
    - ``builtin.taint_pipeline`` — multi-stage taint analysis
    - ``builtin.diff_blast_radius`` — diff review with blast-radius analysis

    ID migration (Phase 4): ``builtin.<shape>`` IDs now refer to the v2
    (pydantic-ai) implementations. Legacy ScanStrategy-based implementations
    have been deleted. Any existing DB rows referencing ``builtin.<shape>``
    will resolve to the new implementations — this is intentional, as parity
    tests confirmed equivalence.
    """

    # ------------------------------------------------------------------
    # builtin.single_agent — pydantic-ai runner (Phase 3)
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.single_agent",
            name="Single Agent (builtin)",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "single_agent.txt"),
                user_prompt_template=_read(_USER_DIR / "single_agent.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=80,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.diff_review — pydantic-ai runner (Phase 3)
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.diff_review",
            name="Diff Review (builtin)",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.DIFF_REVIEW,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "diff_review.txt"),
                user_prompt_template=_read(_USER_DIR / "diff_review.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=60,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.file_reviewer — subagent invoked by builtin.per_file
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.file_reviewer",
            name="File Reviewer subagent (builtin)",
            parent_strategy_id="builtin.per_file",
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "file_reviewer.txt"),
                user_prompt_template=_read(_USER_DIR / "file_reviewer.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=20,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                output_type_name="finding_list",
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
            # subagent — dispatched via _run_child_sync, not worker.py
        )
    )

    # ------------------------------------------------------------------
    # builtin.per_file — pydantic-ai runner (Phase 3b)
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.per_file",
            name="Per File (builtin)",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.PER_FILE,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "per_file.txt"),
                user_prompt_template=_read(_USER_DIR / "per_file.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=20,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                subagents=["builtin.file_reviewer"],
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.*_specialist — 16 specialist subagents for per_vuln_class
    #
    # One per VulnClass enum value.  Each specialist:
    #   - uses the existing per-class system prompt from
    #     prompts/system/per_vuln_class/{vuln_class}.txt
    #   - uses a shared user prompt template from
    #     prompts/user/per_vuln_class/specialist.txt
    #   - has parent_strategy_id="builtin.per_vuln_class"
    #   - dispatched via _run_child_sync, not worker.py
    # ------------------------------------------------------------------
    from sec_review_framework.data.findings import VulnClass  # local import to avoid circular

    _pvc_user_template = _read(
        _USER_DIR / "per_vuln_class" / "specialist.txt"
    )

    for _vc in VulnClass:
        _specialist_id = f"builtin.{_vc.value}_specialist"
        _system_prompt = _read(_SYSTEM_DIR / "per_vuln_class" / f"{_vc.value}.txt")
        registry.register(
            UserStrategy(
                id=_specialist_id,
                name=f"{_vc.value.replace('_', ' ').title()} Specialist subagent (builtin)",
                parent_strategy_id="builtin.per_vuln_class",
                orchestration_shape=OrchestrationShape.SINGLE_AGENT,
                default=StrategyBundleDefault(
                    system_prompt=_system_prompt,
                    user_prompt_template=_pvc_user_template,
                    profile_modifier="",
                    model_id=_DEFAULT_MODEL_ID,
                    tools=_DEFAULT_TOOLS,
                    verification=_DEFAULT_VERIFICATION,
                    max_turns=40,
                    tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                    output_type_name="finding_list",
                ),
                overrides=[],
                created_at=_CREATED_AT,
                is_builtin=True,
                # subagent — dispatched via _run_child_sync, not worker.py
            )
        )

    # ------------------------------------------------------------------
    # builtin.per_vuln_class — pydantic-ai runner (Phase 3c)
    #
    # Dispatches all 16 specialists via invoke_subagent (one call per role).
    # dispatch_fallback="programmatic" ensures missing specialists are invoked
    # directly, bypassing the supervisor LLM — the Phase 3c reproducibility
    # lifeline for benchmarking.
    #
    # Overrides: each VulnClass key maps to its own specialist system prompt
    # so that resolve_bundle can apply class-specific instructions when the
    # coordinator resolves a bundle for a particular key (e.g. in tests or
    # future per-class customisation).
    # ------------------------------------------------------------------
    _pvc_specialist_ids = [
        f"builtin.{vc.value}_specialist" for vc in VulnClass
    ]
    _pvc_overrides = [
        OverrideRule(
            key=_vc.value,
            override=StrategyBundleOverride(
                system_prompt=_read(_SYSTEM_DIR / "per_vuln_class" / f"{_vc.value}.txt"),
            ),
        )
        for _vc in VulnClass
    ]
    registry.register(
        UserStrategy(
            id="builtin.per_vuln_class",
            name="Per Vuln Class (builtin)",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.PER_VULN_CLASS,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "per_vuln_class.txt"),
                user_prompt_template=_read(_USER_DIR / "per_vuln_class.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=40,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                subagents=_pvc_specialist_ids,
                dispatch_fallback="programmatic",
            ),
            overrides=_pvc_overrides,
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.triage_agent — subagent invoked by builtin.sast_first
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.triage_agent",
            name="Triage Agent subagent (builtin)",
            parent_strategy_id="builtin.sast_first",
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "triage_agent.txt"),
                user_prompt_template=_read(_USER_DIR / "triage_agent.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=20,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                output_type_name="finding_list",
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
            # subagent — dispatched via _run_child_sync, not worker.py
        )
    )

    # ------------------------------------------------------------------
    # builtin.sast_first — pydantic-ai runner (Phase 3b)
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.sast_first",
            name="SAST First (builtin)",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.SAST_FIRST,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "sast_first.txt"),
                user_prompt_template=_read(_USER_DIR / "sast_first.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=25,
                tool_extensions=frozenset(["semgrep"]),
                subagents=["builtin.triage_agent"],
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ==================================================================
    # Phase 5 capability strategies
    # ==================================================================

    # ------------------------------------------------------------------
    # builtin.verifier — subagent invoked by builtin.single_agent_with_verifier
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.verifier",
            name="Verifier subagent (builtin)",
            parent_strategy_id="builtin.single_agent_with_verifier",
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "verifier.txt"),
                user_prompt_template=_read(_USER_DIR / "verifier.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=10,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                output_type_name="verifier_verdict",
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.single_agent_with_verifier — verifier wrapping (Phase 5)
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.single_agent_with_verifier",
            name="Single Agent with Verifier (builtin)",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.SINGLE_AGENT_WITH_VERIFIER,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "single_agent_with_verifier.txt"),
                user_prompt_template=_read(_USER_DIR / "single_agent_with_verifier.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=100,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                subagents=["builtin.verifier"],
                dispatch_fallback="reprompt",
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.classifier — subagent invoked by builtin.classifier_dispatch
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.classifier",
            name="Classifier subagent (builtin)",
            parent_strategy_id="builtin.classifier_dispatch",
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "classifier.txt"),
                user_prompt_template=_read(_USER_DIR / "classifier.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=20,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                output_type_name="classifier_judgement_list",
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.classifier_dispatch — classifier-guided dispatch (Phase 5)
    #
    # Subagents: builtin.classifier + all 16 specialists from per_vuln_class.
    # dispatch_fallback="reprompt": the classifier intentionally skips classes;
    # programmatic fallback would defeat the cost optimisation.
    # ------------------------------------------------------------------
    _cd_specialist_ids = [
        f"builtin.{vc.value}_specialist" for vc in VulnClass
    ]
    registry.register(
        UserStrategy(
            id="builtin.classifier_dispatch",
            name="Classifier Dispatch (builtin)",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.CLASSIFIER_DISPATCH,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "classifier_dispatch.txt"),
                user_prompt_template=_read(_USER_DIR / "classifier_dispatch.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=60,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                subagents=["builtin.classifier"] + _cd_specialist_ids,
                dispatch_fallback="reprompt",
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.source_finder — Stage 1 subagent for builtin.taint_pipeline
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.source_finder",
            name="Source Finder subagent (builtin)",
            parent_strategy_id="builtin.taint_pipeline",
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "source_finder.txt"),
                user_prompt_template=_read(_USER_DIR / "source_finder.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=30,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                output_type_name="source_list",
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.sink_tracer — Stage 2 subagent for builtin.taint_pipeline
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.sink_tracer",
            name="Sink Tracer subagent (builtin)",
            parent_strategy_id="builtin.taint_pipeline",
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "sink_tracer.txt"),
                user_prompt_template=_read(_USER_DIR / "sink_tracer.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=20,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                output_type_name="taint_path_list",
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.sanitization_checker — Stage 3 subagent for builtin.taint_pipeline
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.sanitization_checker",
            name="Sanitization Checker subagent (builtin)",
            parent_strategy_id="builtin.taint_pipeline",
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "sanitization_checker.txt"),
                user_prompt_template=_read(_USER_DIR / "sanitization_checker.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=15,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                output_type_name="sanitization_verdict",
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.taint_pipeline — 3-stage taint analysis (Phase 5)
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.taint_pipeline",
            name="Taint Pipeline (builtin)",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.TAINT_PIPELINE,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "taint_pipeline.txt"),
                user_prompt_template=_read(_USER_DIR / "taint_pipeline.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=80,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                subagents=[
                    "builtin.source_finder",
                    "builtin.sink_tracer",
                    "builtin.sanitization_checker",
                ],
                dispatch_fallback="reprompt",
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.blast_radius_finder — subagent invoked by builtin.diff_blast_radius
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.blast_radius_finder",
            name="Blast Radius Finder subagent (builtin)",
            parent_strategy_id="builtin.diff_blast_radius",
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "blast_radius_finder.txt"),
                user_prompt_template=_read(_USER_DIR / "blast_radius_finder.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=30,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.caller_checker — per-caller subagent for builtin.diff_blast_radius
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.caller_checker",
            name="Caller Checker subagent (builtin)",
            parent_strategy_id="builtin.diff_blast_radius",
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "caller_checker.txt"),
                user_prompt_template=_read(_USER_DIR / "caller_checker.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=20,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.diff_blast_radius — diff review + blast-radius analysis (Phase 5)
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin.diff_blast_radius",
            name="Diff Blast Radius (builtin)",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.DIFF_BLAST_RADIUS,
            default=StrategyBundleDefault(
                system_prompt=_read(_SYSTEM_DIR / "diff_blast_radius.txt"),
                user_prompt_template=_read(_USER_DIR / "diff_blast_radius.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=80,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
                subagents=[
                    "builtin.blast_radius_finder",
                    "builtin.caller_checker",
                ],
                dispatch_fallback="reprompt",
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )


def load_default_registry() -> StrategyRegistry:
    """Return a new StrategyRegistry seeded with the builtin strategies."""
    registry = StrategyRegistry()
    seed_builtins(registry)
    return registry


async def build_registry_from_db(db) -> StrategyRegistry:
    """Return a StrategyRegistry containing builtins and DB-stored user strategies.

    Called by the coordinator before expanding an ExperimentMatrix so that
    user-created strategies are resolvable alongside the builtins.  Builtins
    are seeded first, then user strategies (DB wins on id collision).
    """
    registry = load_default_registry()
    for strategy in await db.list_user_strategies():
        registry.register(strategy)
    return registry
