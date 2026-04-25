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
    ["read_file", "list_directory", "grep", "semgrep", "doc_lookup"]
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
    """Construct the 5 builtin UserStrategy objects and register them."""

    # ------------------------------------------------------------------
    # builtin.single_agent
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
    # builtin.per_file
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
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.per_vuln_class
    #
    # No top-level system/per_vuln_class.txt exists; each subagent's
    # system_prompt comes from system/per_vuln_class/{vuln_class}.txt.
    # The default system_prompt is left empty (the per-class overrides
    # carry the actual prompts).
    # ------------------------------------------------------------------
    pvc_dir = _SYSTEM_DIR / "per_vuln_class"
    pvc_overrides: list[OverrideRule] = []
    for txt_file in sorted(pvc_dir.glob("*.txt")):
        vuln_class_name = txt_file.stem  # e.g. "sqli", "xss"
        pvc_overrides.append(
            OverrideRule(
                key=vuln_class_name,
                override=StrategyBundleOverride(
                    system_prompt=txt_file.read_text(encoding="utf-8").strip(),
                ),
            )
        )

    registry.register(
        UserStrategy(
            id="builtin.per_vuln_class",
            name="Per Vuln Class (builtin)",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.PER_VULN_CLASS,
            default=StrategyBundleDefault(
                system_prompt="",
                user_prompt_template=_read(_USER_DIR / "per_vuln_class.txt"),
                profile_modifier="",
                model_id=_DEFAULT_MODEL_ID,
                tools=_DEFAULT_TOOLS,
                verification=_DEFAULT_VERIFICATION,
                max_turns=40,
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
            ),
            overrides=pvc_overrides,
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.sast_first
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
                tool_extensions=_DEFAULT_TOOL_EXTENSIONS,
            ),
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin.diff_review
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
    # builtin_v2.single_agent — parent-agent runner (Phase 3)
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin_v2.single_agent",
            name="Single Agent v2 (builtin)",
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
            use_new_runner=True,
        )
    )

    # ------------------------------------------------------------------
    # builtin_v2.diff_review — parent-agent runner (Phase 3)
    # ------------------------------------------------------------------
    registry.register(
        UserStrategy(
            id="builtin_v2.diff_review",
            name="Diff Review v2 (builtin)",
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
            use_new_runner=True,
        )
    )


def load_default_registry() -> StrategyRegistry:
    """Return a new StrategyRegistry seeded with the 5 builtin strategies."""
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
