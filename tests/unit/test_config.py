"""Tests for all config loaders in sec_review_framework/config.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from sec_review_framework.config import (
    ConcurrencyConfig,
    ExperimentFileConfig,
    ModelProviderConfig,
    PricingConfig,
    RetryConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_yaml(path: Path, data: dict) -> Path:
    path.write_text(yaml.dump(data, default_flow_style=False))
    return path


# ---------------------------------------------------------------------------
# ModelProviderConfig validators
# ---------------------------------------------------------------------------


def test_model_provider_config_api_key_auth_valid():
    """auth=api_key + api_key_env is accepted."""
    cfg = ModelProviderConfig(
        id="m1", model_name="gpt-4o", auth="api_key", api_key_env="OPENAI_API_KEY"
    )
    assert cfg.api_key_env == "OPENAI_API_KEY"


def test_model_provider_config_aws_auth_valid():
    """auth=aws + region is accepted."""
    cfg = ModelProviderConfig(
        id="m2", model_name="bedrock-claude", auth="aws", region="us-east-1"
    )
    assert cfg.region == "us-east-1"


def test_model_provider_config_api_key_auth_missing_env_raises():
    """auth=api_key without api_key_env raises ValueError."""
    with pytest.raises(ValidationError, match="api_key_env required"):
        ModelProviderConfig(id="m3", model_name="gpt-4o", auth="api_key")


def test_model_provider_config_aws_auth_missing_region_raises():
    """auth=aws without region raises ValueError."""
    with pytest.raises(ValidationError, match="region required"):
        ModelProviderConfig(id="m4", model_name="bedrock-claude", auth="aws")


def test_model_provider_config_api_base_allows_no_api_key_env():
    cfg = ModelProviderConfig(
        id="local-model",
        model_name="openai/local-model",
        auth="api_key",
        api_base="http://localhost:8080",
    )
    assert cfg.api_base == "http://localhost:8080"
    assert cfg.api_key_env is None


def test_model_provider_config_api_base_defaults_to_none():
    cfg = ModelProviderConfig(
        id="m5", model_name="gpt-4o", auth="api_key", api_key_env="OPENAI_API_KEY"
    )
    assert cfg.api_base is None


# ---------------------------------------------------------------------------
# RetryConfig
# ---------------------------------------------------------------------------


def test_retry_config_for_provider_returns_provider_specific_config(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "retry.yaml",
        {
            "defaults": {"max_retries": 3, "base_delay": 1.0, "max_delay": 60.0},
            "providers": {
                "openai": {"max_retries": 5, "base_delay": 2.0, "max_delay": 30.0}
            },
        },
    )
    cfg = RetryConfig.from_yaml(cfg_path)
    openai_policy = cfg.for_provider("openai")
    assert openai_policy.max_retries == 5
    assert openai_policy.base_delay == 2.0


def test_retry_config_for_provider_unknown_returns_default(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "retry.yaml",
        {"defaults": {"max_retries": 3, "base_delay": 1.0, "max_delay": 60.0}},
    )
    cfg = RetryConfig.from_yaml(cfg_path)
    policy = cfg.for_provider("anthropic")  # not in providers
    assert policy.max_retries == 3


# ---------------------------------------------------------------------------
# ConcurrencyConfig
# ---------------------------------------------------------------------------


def test_concurrency_config_cap_for_known_model(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "concurrency.yaml",
        {"default_cap": 4, "per_model": {"gpt-4o": 2, "claude-opus": 1}},
    )
    cfg = ConcurrencyConfig.from_yaml(cfg_path)
    assert cfg.cap_for("gpt-4o") == 2
    assert cfg.cap_for("claude-opus") == 1


def test_concurrency_config_cap_for_unknown_model_returns_default_cap(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "concurrency.yaml",
        {"default_cap": 8, "per_model": {}},
    )
    cfg = ConcurrencyConfig.from_yaml(cfg_path)
    assert cfg.cap_for("unknown-model") == 8


# ---------------------------------------------------------------------------
# PricingConfig
# ---------------------------------------------------------------------------


def test_pricing_config_from_yaml_loads(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "pricing.yaml",
        {
            "models": {
                "gpt-4o": {"input_per_million": 5.0, "output_per_million": 15.0},
                "claude-3-5-sonnet": {"input_per_million": 3.0, "output_per_million": 15.0},
            }
        },
    )
    cfg = PricingConfig.from_yaml(cfg_path)
    assert "gpt-4o" in cfg.models
    assert cfg.models["gpt-4o"].input_per_million == 5.0


# ---------------------------------------------------------------------------
# ExperimentFileConfig
# ---------------------------------------------------------------------------


def test_experiment_file_config_from_yaml_loads(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "experiment.yaml",
        {
            "experiment_id": "test-experiment",
            "dataset": {"name": "mydata", "version": "1.0.0"},
            "models": [
                {"id": "gpt-4o", "model_name": "gpt-4o", "api_key_env": "OPENAI_API_KEY"}
            ],
            "strategies": [
                {"name": "single_agent"}
            ],
        },
    )
    cfg = ExperimentFileConfig.from_yaml(cfg_path)
    assert cfg.experiment_id == "test-experiment"
    assert cfg.dataset.name == "mydata"
    assert len(cfg.models) == 1
    assert len(cfg.strategies) == 1


# ---------------------------------------------------------------------------
# _validate_auth — additional error paths
# ---------------------------------------------------------------------------


def test_model_provider_config_invalid_auth_literal_raises():
    with pytest.raises(ValidationError, match="'api_key' or 'aws'"):
        ModelProviderConfig(id="m-bad", model_name="gpt-4o", auth="oauth")  # type: ignore[arg-type]


def test_model_provider_config_aws_auth_empty_region_raises():
    with pytest.raises(ValidationError, match="region required"):
        ModelProviderConfig(id="m-empty-region", model_name="bedrock-model", auth="aws", region=None)


def test_model_provider_config_api_key_no_env_no_base_raises():
    with pytest.raises(ValidationError, match="api_key_env required"):
        ModelProviderConfig(id="m-nok", model_name="gpt-4o", auth="api_key", api_key_env=None, api_base=None)


# ---------------------------------------------------------------------------
# RetryConfig — error paths
# ---------------------------------------------------------------------------


def test_retry_config_invalid_max_retries_type_raises(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "retry.yaml",
        {"defaults": {"max_retries": "not-an-int", "base_delay": 1.0, "max_delay": 60.0}},
    )
    with pytest.raises(ValidationError, match="valid integer"):
        RetryConfig.from_yaml(cfg_path)


def test_retry_config_providers_not_a_dict_raises(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "retry.yaml",
        {"providers": "should-be-a-dict"},
    )
    with pytest.raises(ValidationError, match="valid dictionary"):
        RetryConfig.from_yaml(cfg_path)


def test_retry_config_provider_policy_invalid_base_delay_raises(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "retry.yaml",
        {"providers": {"openai": {"max_retries": 3, "base_delay": "bad", "max_delay": 30.0}}},
    )
    with pytest.raises(ValidationError, match="valid number"):
        RetryConfig.from_yaml(cfg_path)


# ---------------------------------------------------------------------------
# ConcurrencyConfig — error paths
# ---------------------------------------------------------------------------


def test_concurrency_config_invalid_default_cap_type_raises(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "concurrency.yaml",
        {"default_cap": "not-an-int"},
    )
    with pytest.raises(ValidationError, match="valid integer"):
        ConcurrencyConfig.from_yaml(cfg_path)


def test_concurrency_config_invalid_per_model_cap_type_raises(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "concurrency.yaml",
        {"default_cap": 4, "per_model": {"gpt-4o": "bad-value"}},
    )
    with pytest.raises(ValidationError, match="valid integer"):
        ConcurrencyConfig.from_yaml(cfg_path)


def test_concurrency_config_per_model_not_a_dict_raises(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "concurrency.yaml",
        {"default_cap": 4, "per_model": "not-a-dict"},
    )
    with pytest.raises(ValidationError, match="valid dictionary"):
        ConcurrencyConfig.from_yaml(cfg_path)


# ---------------------------------------------------------------------------
# PricingConfig — error paths
# ---------------------------------------------------------------------------


def test_pricing_config_missing_models_key_raises(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "pricing.yaml",
        {"not_models": {}},
    )
    with pytest.raises(ValidationError, match="Field required"):
        PricingConfig.from_yaml(cfg_path)


def test_pricing_config_model_entry_missing_output_raises(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "pricing.yaml",
        {"models": {"gpt-4o": {"input_per_million": 5.0}}},
    )
    with pytest.raises(ValidationError, match="Field required"):
        PricingConfig.from_yaml(cfg_path)


def test_pricing_config_model_entry_missing_input_raises(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "pricing.yaml",
        {"models": {"gpt-4o": {"output_per_million": 15.0}}},
    )
    with pytest.raises(ValidationError, match="Field required"):
        PricingConfig.from_yaml(cfg_path)


def test_pricing_config_non_numeric_price_raises(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "pricing.yaml",
        {"models": {"gpt-4o": {"input_per_million": "bad", "output_per_million": 15.0}}},
    )
    with pytest.raises(ValidationError, match="valid number"):
        PricingConfig.from_yaml(cfg_path)


def test_pricing_config_models_not_a_dict_raises(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "pricing.yaml",
        {"models": ["gpt-4o", "claude"]},
    )
    with pytest.raises(ValidationError, match="valid dictionary"):
        PricingConfig.from_yaml(cfg_path)


