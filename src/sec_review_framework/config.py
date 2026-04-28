"""Pydantic configuration loaders for all YAML config files."""

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator

# --- Shared ---


def load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# --- Model Provider Config ---


class ModelProviderConfig(BaseModel):
    id: str
    provider_class: str = "LiteLLMProvider"
    model_name: str
    temperature: float = 0.2
    max_tokens: int = 8192
    api_key_env: str | None = None
    auth: Literal["api_key", "aws"] = "api_key"
    region: str | None = None
    api_base: str | None = None
    display_name: str | None = None

    @model_validator(mode="after")
    def _validate_auth(self) -> ModelProviderConfig:
        if self.auth == "api_key" and not self.api_key_env and self.api_base is None:
            raise ValueError(
                f"model {self.id}: api_key_env required when auth='api_key' and api_base is not set"
            )
        if self.auth == "aws" and not self.region:
            raise ValueError(f"model {self.id}: region required when auth='aws'")
        return self




# --- Retry Config ---


class RetryPolicyConfig(BaseModel):
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    jitter: bool = True
    retryable_status_codes: list[int] = [429, 503, 529]


class RetryConfig(BaseModel):
    defaults: RetryPolicyConfig = RetryPolicyConfig()
    providers: dict[str, RetryPolicyConfig] = {}

    @classmethod
    def from_yaml(cls, path: Path) -> RetryConfig:
        return cls.model_validate(load_yaml(path))

    def for_provider(self, provider: str) -> RetryPolicyConfig:
        return self.providers.get(provider, self.defaults)


# --- Concurrency Config ---


class ConcurrencyConfig(BaseModel):
    default_cap: int = 4
    per_model: dict[str, int] = {}

    @classmethod
    def from_yaml(cls, path: Path) -> ConcurrencyConfig:
        return cls.model_validate(load_yaml(path))

    def cap_for(self, model_id: str) -> int:
        return self.per_model.get(model_id, self.default_cap)


# --- Pricing Config ---


class ModelPricingConfig(BaseModel):
    input_per_million: float
    output_per_million: float


class PricingConfig(BaseModel):
    models: dict[str, ModelPricingConfig]

    @classmethod
    def from_yaml(cls, path: Path) -> PricingConfig:
        return cls.model_validate(load_yaml(path))


# --- Evaluation Config ---


class EvaluationConfig(BaseModel):
    evidence_assessor: str = "heuristic"
    evidence_judge_model: str | None = None


# --- Retention Config ---


class RetentionConfig(BaseModel):
    retention_days: int = 30
    cleanup_interval_hours: int = 1


class JobConfig(BaseModel):
    ttl_seconds_after_finished: int = 3600


class CoordinatorConfig(BaseModel):
    retention: RetentionConfig = RetentionConfig()
    jobs: JobConfig = JobConfig()

    @classmethod
    def from_yaml(cls, path: Path) -> CoordinatorConfig:
        return cls.model_validate(load_yaml(path))


# --- Strategy Config ---


class StrategyEntryConfig(BaseModel):
    name: str
    config: dict = {}


# --- Dataset Config ---


class DatasetConfig(BaseModel):
    name: str
    version: str


# --- Top-level Experiment Config ---


class ExperimentFileConfig(BaseModel):
    """Maps the experiments.yaml file structure."""

    experiment_id: str
    dataset: DatasetConfig
    models: list[ModelProviderConfig]
    strategies: list[StrategyEntryConfig]
    tool_variants: list[str] = ["with_tools", "without_tools"]
    review_profiles: list[str] = ["default"]
    verification_variants: list[str] = ["none", "with_verification"]
    parallel_modes: list[bool] = [False]
    num_repetitions: int = 1
    verifier_model_id: str | None = None
    max_experiment_cost_usd: float | None = None
    max_parallel_runs: int = 10
    evaluation: EvaluationConfig = EvaluationConfig()
    output_root: str = "outputs/"

    @classmethod
    def from_yaml(cls, path: Path) -> ExperimentFileConfig:
        return cls.model_validate(load_yaml(path))


# --- Tool Extension Availability ---


class ToolExtensionAvailability:
    """Reads TOOL_EXT_* environment variables set by the coordinator Helm template.

    Chunk 6's /api/tool-extensions route should instantiate this and serialise
    it as JSON — no further wiring required.

    Environment variables:
      TOOL_EXT_DEVDOCS_AVAILABLE      "true"|"false"  (default: "false")
      TOOL_EXT_LSP_AVAILABLE          "true"|"false"  (default: "false")
      TOOL_EXT_SEMGREP_AVAILABLE      "true"|"false"  (default: "false")
      TOOL_EXT_TREE_SITTER_AVAILABLE  "true"|"false"  (default: "false")
    """

    def __init__(self) -> None:
        self.devdocs: bool = os.environ.get("TOOL_EXT_DEVDOCS_AVAILABLE", "false").lower() == "true"
        self.lsp: bool = os.environ.get("TOOL_EXT_LSP_AVAILABLE", "false").lower() == "true"
        self.semgrep: bool = os.environ.get("TOOL_EXT_SEMGREP_AVAILABLE", "false").lower() == "true"
        self.tree_sitter: bool = os.environ.get("TOOL_EXT_TREE_SITTER_AVAILABLE", "false").lower() == "true"

    def as_dict(self) -> dict[str, bool]:
        return {
            "devdocs": self.devdocs,
            "lsp": self.lsp,
            "semgrep": self.semgrep,
            "tree_sitter": self.tree_sitter,
        }


# --- Vuln Class Config ---


class VulnClassDefinition(BaseModel):
    display_name: str
    cwe_ids: list[str]
    owasp_category: str | None = None


class VulnClassesConfig(BaseModel):
    vuln_classes: dict[str, VulnClassDefinition]

    @classmethod
    def from_yaml(cls, path: Path) -> VulnClassesConfig:
        return cls.model_validate(load_yaml(path))
