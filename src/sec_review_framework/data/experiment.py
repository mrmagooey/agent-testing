"""Experiment configuration and run result data models."""

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field, field_serializer

from sec_review_framework.data.evaluation import EvaluationResult, VerificationResult
from sec_review_framework.data.findings import Finding, StrategyOutput

if TYPE_CHECKING:
    from sec_review_framework.data.strategy_bundle import UserStrategy
    from sec_review_framework.strategies.strategy_registry import StrategyRegistry


class ToolVariant(StrEnum):
    WITH_TOOLS = "with_tools"
    WITHOUT_TOOLS = "without_tools"


class ToolExtension(StrEnum):
    DEVDOCS = "devdocs"
    LSP = "lsp"
    SEMGREP = "semgrep"
    TREE_SITTER = "tree_sitter"


class VerificationVariant(StrEnum):
    NONE = "none"
    WITH_VERIFICATION = "with_verification"


class StrategyName(StrEnum):
    SINGLE_AGENT = "single_agent"
    PER_FILE = "per_file"
    PER_VULN_CLASS = "per_vuln_class"
    SAST_FIRST = "sast_first"
    DIFF_REVIEW = "diff_review"


class ReviewProfileName(StrEnum):
    DEFAULT = "default"
    STRICT = "strict"
    COMPREHENSIVE = "comprehensive"
    OWASP_FOCUSED = "owasp_focused"
    QUICK_SCAN = "quick_scan"


class BundleSnapshot(BaseModel):
    """Immutable content-addressed snapshot of a UserStrategy bundle.

    Hashes the canonical JSON of the entire UserStrategy (default bundle +
    overrides + orchestration_shape), so any prompt or config change is
    reflected in the snapshot_id.
    """

    snapshot_id: str
    strategy_id: str
    captured_at: datetime
    bundle_json: str  # canonical JSON of the full UserStrategy

    @classmethod
    def capture(cls, strategy: UserStrategy) -> BundleSnapshot:
        """Build a BundleSnapshot from a UserStrategy.

        Parameters
        ----------
        strategy:
            The fully-resolved UserStrategy whose canonical JSON will be hashed.
        """
        from sec_review_framework.data.strategy_bundle import canonical_json

        cjson = canonical_json(strategy)
        snap_id = hashlib.sha256(cjson.encode()).hexdigest()[:16]
        return cls(
            snapshot_id=snap_id,
            strategy_id=strategy.id,
            captured_at=datetime.now(UTC).replace(tzinfo=None),
            bundle_json=cjson,
        )


# Backwards-compatibility alias so existing code can still import PromptSnapshot.
# New code should use BundleSnapshot directly.
# TODO: Remove once all callers have been migrated.
PromptSnapshot = BundleSnapshot


class ExperimentRun(BaseModel):
    """One cell in the experiment matrix."""

    id: str
    experiment_id: str
    strategy_id: str
    dataset_name: str
    dataset_version: str
    tool_extensions: frozenset[ToolExtension] = frozenset()

    # Canonical JSON of the full UserStrategy, populated by
    # ExperimentMatrix.expand().  Workers reconstruct the strategy from this
    # field so user-created strategies (stored only in the coordinator's DB)
    # are visible without a DB round-trip.  Empty string means "fall back to
    # the builtin registry" — used by legacy tests and builtin-only flows.
    bundle_json: str = ""

    # Legacy / derived fields kept for worker compatibility while strategy
    # files still read from ExperimentRun directly.  They are populated by
    # ExperimentMatrix.expand() from the resolved strategy's default bundle.
    model_id: str = ""
    strategy: StrategyName = StrategyName.SINGLE_AGENT
    tool_variant: ToolVariant = ToolVariant.WITH_TOOLS
    review_profile: ReviewProfileName = ReviewProfileName.DEFAULT
    verification_variant: VerificationVariant = VerificationVariant.NONE
    verifier_model_id: str | None = None
    strategy_config: dict = {}
    provider_kwargs: dict = {}
    parallel: bool = False
    repetition_index: int = 0
    created_at: datetime | None = None

    # Per-test-file iteration: when set, the worker scopes its review to this
    # single file path relative to the dataset repo root.  None means the
    # whole repo is reviewed (default / existing behaviour).
    target_file: str | None = None

    # HTTP result transport fields.  Populated at config-write time when
    # result_transport == "http"; excluded from DB config_json persistence via
    # Field(exclude=True).  upload_url and upload_token are None for "pvc" runs.
    result_transport: Literal["pvc", "http"] = "pvc"
    upload_url: str | None = None
    upload_token: str | None = Field(default=None, exclude=True)

    @field_serializer("tool_extensions")
    def _serialize_tool_extensions(self, value: frozenset[ToolExtension]) -> list[str]:
        return sorted(v.value for v in value)

    @property
    def effective_verifier_model(self) -> str:
        return self.verifier_model_id or self.model_id


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RunResult(BaseModel):
    """Complete result of a single experiment run."""

    experiment: ExperimentRun
    status: RunStatus
    findings: list[Finding]
    findings_pre_verification: list[Finding] | None = None
    verification_result: VerificationResult | None = None
    evaluation: EvaluationResult | None = None
    # Populated only for benchmark datasets that have negative labels.
    # None for all existing CVE datasets (backward-compat).
    benchmark_scorecard: dict | None = None
    strategy_output: StrategyOutput
    bundle_snapshot: BundleSnapshot

    tool_call_count: int
    total_input_tokens: int
    total_output_tokens: int
    verification_tokens: int
    estimated_cost_usd: float

    duration_seconds: float
    error: str | None = None
    completed_at: datetime | None = None


class ExperimentMatrix(BaseModel):
    """Defines the set of strategies to run in an experiment.

    The matrix collapses to a single ``strategy_ids`` axis: each strategy
    encapsulates model, tools, profile, verification, and tool_extensions, so
    those are no longer separate axes.
    """

    experiment_id: str
    dataset_name: str
    dataset_version: str

    strategy_ids: list[str]

    num_repetitions: int = 1
    max_experiment_cost_usd: float | None = None
    verifier_model_id: str | None = None

    language_allowlist: list[str] = Field(
        default_factory=list,
        description=(
            "Languages this experiment is prepared to evaluate. Datasets whose "
            "metadata_json.language is not in this list are refused at dispatch "
            "with a clear error. Empty list (the default) disables the gate so "
            "all languages are allowed; populate explicitly to restrict."
        ),
    )

    # Submit-time override: when True, skip availability validation.
    # Excluded from serialisation so it never reaches the DB or on-disk config JSON.
    allow_unavailable_models: bool = Field(default=False, exclude=True)

    # Cost-gate: must be explicitly set to True to permit per-test-file
    # iteration for datasets that declare ``iteration: "per-test-file"`` in
    # their metadata_json.  Excluded from serialisation for the same reason as
    # allow_unavailable_models — it is a submit-time guard, not a run config.
    allow_benchmark_iteration: bool = Field(default=False, exclude=True)

    def expand(
        self,
        registry: StrategyRegistry | None = None,
    ) -> list[ExperimentRun]:
        """Expand the matrix into a flat list of ExperimentRun objects.

        Run ID format: ``{experiment_id}_{strategy_id}`` with ``_rep{N}``
        appended when ``num_repetitions > 1``.

        The ``_ext-<sorted>`` suffix is intentionally dropped — tool_extensions
        are now baked into each strategy and are immutable per strategy_id.

        Parameters
        ----------
        registry:
            Optional :class:`StrategyRegistry` to resolve strategies from.
            When *None*, ``load_default_registry()`` is called.  Pass an
            explicit registry in tests to avoid filesystem side-effects.
        """
        from sec_review_framework.strategies.strategy_registry import load_default_registry

        if registry is None:
            registry = load_default_registry()

        from sec_review_framework.data.strategy_bundle import canonical_json

        runs: list[ExperimentRun] = []
        for strategy_id in self.strategy_ids:
            strategy = registry.get(strategy_id)
            default_bundle = strategy.default
            # Derive tool_extensions from the strategy default bundle plus the
            # union of all override tool_extensions so the worker pod check can
            # gate on the full requirement set.
            ext_union: frozenset[str] = default_bundle.tool_extensions
            for rule in strategy.overrides:
                if rule.override.tool_extensions is not None:
                    ext_union = ext_union | rule.override.tool_extensions
            tool_extensions = frozenset(ToolExtension(e) for e in ext_union)
            bundle_json = canonical_json(strategy)

            for rep in range(self.num_repetitions):
                rep_suffix = f"_rep{rep}" if self.num_repetitions > 1 else ""
                run_id = f"{self.experiment_id}_{strategy_id}{rep_suffix}"
                runs.append(
                    ExperimentRun(
                        id=run_id,
                        experiment_id=self.experiment_id,
                        strategy_id=strategy_id,
                        dataset_name=self.dataset_name,
                        dataset_version=self.dataset_version,
                        tool_extensions=tool_extensions,
                        bundle_json=bundle_json,
                        # Populate legacy fields from the strategy default bundle
                        model_id=default_bundle.model_id,
                        verification_variant=VerificationVariant(default_bundle.verification),
                        verifier_model_id=self.verifier_model_id,
                        repetition_index=rep,
                    )
                )
        return runs


class ExperimentStatus(BaseModel):
    """Current status of an experiment."""

    experiment_id: str
    total: int
    completed: int
    failed: int
    running: int
    pending: int
    estimated_cost_so_far: float = 0.0
