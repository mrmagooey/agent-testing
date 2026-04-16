"""Experiment configuration and run result data models."""

import hashlib
from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from sec_review_framework.data.evaluation import EvaluationResult, VerificationResult
from sec_review_framework.data.findings import Finding, StrategyOutput


class ToolVariant(str, Enum):
    WITH_TOOLS = "with_tools"
    WITHOUT_TOOLS = "without_tools"


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

    @classmethod
    def capture(cls, **kwargs: str | None) -> "PromptSnapshot":
        blob = "".join(str(v) for v in kwargs.values() if v)
        snap_id = hashlib.sha256(blob.encode()).hexdigest()[:16]
        return cls(snapshot_id=snap_id, captured_at=datetime.utcnow(), **kwargs)


class ExperimentRun(BaseModel):
    """One cell in the experiment matrix."""

    id: str
    batch_id: str
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

    batch_id: str
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

    strategy_configs: dict[str, dict] = {}
    model_configs: dict[str, dict] = {}

    num_repetitions: int = 1
    max_batch_cost_usd: float | None = None
    verifier_model_id: str | None = None

    def expand(self) -> list[ExperimentRun]:
        """Cartesian product of all active dimensions x num_repetitions."""
        runs: list[ExperimentRun] = []
        for model_id in self.model_ids:
            for strategy in self.strategies:
                for tool_variant in self.tool_variants:
                    for profile in self.review_profiles:
                        for verif in self.verification_variants:
                            for parallel in self.parallel_modes:
                                for rep in range(self.num_repetitions):
                                    rep_suffix = f"_rep{rep}" if self.num_repetitions > 1 else ""
                                    run_id = (
                                        f"{self.batch_id}_{model_id}_{strategy.value}"
                                        f"_{tool_variant.value}_{profile.value}_{verif.value}{rep_suffix}"
                                    )
                                    runs.append(
                                        ExperimentRun(
                                            id=run_id,
                                            batch_id=self.batch_id,
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
                                        )
                                    )
        return runs


class BatchStatus(BaseModel):
    """Current status of a batch."""

    batch_id: str
    total: int
    completed: int
    failed: int
    running: int
    pending: int
    estimated_cost_so_far: float = 0.0
