"""Experiment configuration and run result data models."""

import hashlib
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field, field_serializer, field_validator

from sec_review_framework.data.evaluation import EvaluationResult, VerificationResult
from sec_review_framework.data.findings import Finding, StrategyOutput


class ToolVariant(str, Enum):
    WITH_TOOLS = "with_tools"
    WITHOUT_TOOLS = "without_tools"


class ToolExtension(str, Enum):
    TREE_SITTER = "tree_sitter"
    LSP = "lsp"
    DEVDOCS = "devdocs"


class VerificationVariant(str, Enum):
    NONE = "none"
    WITH_VERIFICATION = "with_verification"


class StrategyName(str, Enum):
    SINGLE_AGENT = "single_agent"
    PER_FILE = "per_file"
    PER_VULN_CLASS = "per_vuln_class"
    SAST_FIRST = "sast_first"
    DIFF_REVIEW = "diff_review"


class ReviewProfileName(str, Enum):
    DEFAULT = "default"
    STRICT = "strict"
    COMPREHENSIVE = "comprehensive"
    OWASP_FOCUSED = "owasp_focused"
    QUICK_SCAN = "quick_scan"


class PromptSnapshot(BaseModel):
    """Immutable record of every prompt surface used in a single experiment run."""

    snapshot_id: str
    captured_at: datetime
    system_prompt: str
    user_message_template: str
    review_profile_modifier: str | None = None
    finding_output_format: str
    verification_prompt: str | None = None
    clean_prompt: str | None = None
    injected_prompt: str | None = None
    injection_template_id: str | None = None

    @classmethod
    def capture(cls, **kwargs: str | None) -> "PromptSnapshot":
        blob = "".join(str(v) for v in kwargs.values() if v)
        snap_id = hashlib.sha256(blob.encode()).hexdigest()[:16]
        return cls(snapshot_id=snap_id, captured_at=datetime.utcnow(), **kwargs)


class ExperimentRun(BaseModel):
    """One cell in the experiment matrix."""

    id: str
    experiment_id: str
    model_id: str
    strategy: StrategyName
    tool_variant: ToolVariant
    review_profile: ReviewProfileName
    verification_variant: VerificationVariant
    verifier_model_id: str | None = None
    dataset_name: str
    dataset_version: str
    strategy_config: dict = {}
    model_config: dict = {}
    parallel: bool = False
    repetition_index: int = 0
    created_at: datetime | None = None
    tool_extensions: frozenset[ToolExtension] = frozenset()

    @field_serializer("tool_extensions")
    def _serialize_tool_extensions(self, value: frozenset[ToolExtension]) -> list[str]:
        return sorted(v.value for v in value)

    @property
    def effective_verifier_model(self) -> str:
        return self.verifier_model_id or self.model_id


class RunStatus(str, Enum):
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
    strategy_output: StrategyOutput
    prompt_snapshot: PromptSnapshot

    tool_call_count: int
    total_input_tokens: int
    total_output_tokens: int
    verification_tokens: int
    estimated_cost_usd: float

    duration_seconds: float
    error: str | None = None
    completed_at: datetime | None = None


class ExperimentMatrix(BaseModel):
    """Defines the cartesian product of experiment dimensions."""

    experiment_id: str
    dataset_name: str
    dataset_version: str

    model_ids: list[str]
    strategies: list[StrategyName]
    tool_variants: list[ToolVariant] = [ToolVariant.WITH_TOOLS, ToolVariant.WITHOUT_TOOLS]
    review_profiles: list[ReviewProfileName] = [ReviewProfileName.DEFAULT]
    verification_variants: list[VerificationVariant] = [
        VerificationVariant.NONE,
        VerificationVariant.WITH_VERIFICATION,
    ]
    parallel_modes: list[bool] = [False]
    tool_extension_sets: list[frozenset[ToolExtension]] = Field(
        default_factory=lambda: [frozenset()]
    )

    @field_validator("tool_extension_sets", mode="before")
    @classmethod
    def _coerce_extension_sets(cls, v: object) -> list[frozenset[ToolExtension]]:
        # Accept list[list[str]] (e.g., from JSON round-trip) in addition to
        # list[frozenset[ToolExtension]] (in-process usage).
        if not isinstance(v, list):
            raise ValueError("tool_extension_sets must be a list")
        result: list[frozenset[ToolExtension]] = []
        for item in v:
            if isinstance(item, frozenset):
                result.append(item)
            else:
                result.append(frozenset(ToolExtension(e) for e in item))
        return result

    @field_serializer("tool_extension_sets")
    def _serialize_tool_extension_sets(
        self, value: list[frozenset[ToolExtension]]
    ) -> list[list[str]]:
        return [sorted(e.value for e in ext_set) for ext_set in value]

    strategy_configs: dict[str, dict] = {}
    model_configs: dict[str, dict] = {}

    num_repetitions: int = 1
    max_experiment_cost_usd: float | None = None
    verifier_model_id: str | None = None

    # Submit-time override: when True, skip availability validation for models
    # that are key_missing, not_listed, or probe_failed.  Excluded from
    # serialisation so it never reaches the DB or on-disk config JSON.
    allow_unavailable_models: bool = Field(default=False, exclude=True)

    @classmethod
    def tool_extension_sets_powerset(
        cls, enabled: set[ToolExtension]
    ) -> list[frozenset[ToolExtension]]:
        """Return all 2^N subsets of *enabled* as a list of frozensets."""
        items = sorted(enabled, key=lambda e: e.value)
        result: list[frozenset[ToolExtension]] = []
        for mask in range(1 << len(items)):
            subset = frozenset(items[i] for i in range(len(items)) if mask & (1 << i))
            result.append(subset)
        return result

    def expand(self) -> list[ExperimentRun]:
        """Cartesian product of all active dimensions x num_repetitions."""
        runs: list[ExperimentRun] = []
        for model_id in self.model_ids:
            for strategy in self.strategies:
                for tool_variant in self.tool_variants:
                    for profile in self.review_profiles:
                        for verif in self.verification_variants:
                            for parallel in self.parallel_modes:
                                for ext_set in self.tool_extension_sets:
                                    for rep in range(self.num_repetitions):
                                        rep_suffix = f"_rep{rep}" if self.num_repetitions > 1 else ""
                                        if ext_set:
                                            ext_suffix = "_ext-" + "+".join(
                                                sorted(e.value for e in ext_set)
                                            )
                                        else:
                                            ext_suffix = ""
                                        # Sanitize model_id: LiteLLM IDs like
                                        # "openrouter/meta-llama/llama-3.1-8b-instruct"
                                        # contain "/" which leaks into filesystem paths.
                                        safe_model_id = model_id.replace("/", "--")
                                        run_id = (
                                            f"{self.experiment_id}_{safe_model_id}_{strategy.value}"
                                            f"_{tool_variant.value}_{profile.value}_{verif.value}"
                                            f"{rep_suffix}{ext_suffix}"
                                        )
                                        runs.append(
                                            ExperimentRun(
                                                id=run_id,
                                                experiment_id=self.experiment_id,
                                                model_id=model_id,
                                                strategy=strategy,
                                                tool_variant=tool_variant,
                                                review_profile=profile,
                                                verification_variant=verif,
                                                verifier_model_id=self.verifier_model_id,
                                                parallel=parallel,
                                                repetition_index=rep,
                                                dataset_name=self.dataset_name,
                                                dataset_version=self.dataset_version,
                                                strategy_config=self.strategy_configs.get(strategy.value, {}),
                                                model_config=self.model_configs.get(model_id, {}),
                                                tool_extensions=ext_set,
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
