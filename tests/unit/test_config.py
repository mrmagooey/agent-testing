"""Tests for all config loaders in sec_review_framework/config.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sec_review_framework.config import (
    ConcurrencyConfig,
    ExperimentFileConfig,
    ModelsConfig,
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
# ModelsConfig
# ---------------------------------------------------------------------------


def test_models_config_from_yaml_loads_correctly(tmp_path: Path):
    cfg_path = _write_yaml(
        tmp_path / "models.yaml",
        {
            "providers": {
                "gpt4o": {
                    "id": "gpt-4o",
                    "model_name": "gpt-4o",
                    "temperature": 0.2,
                    "max_tokens": 4096,
                }
            }
        },
    )
    cfg = ModelsConfig.from_yaml(cfg_path)
    assert "gpt4o" in cfg.providers
    assert cfg.providers["gpt4o"].model_name == "gpt-4o"


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
            "batch_id": "test-batch",
            "dataset": {"name": "mydata", "version": "1.0.0"},
            "models": [
                {"id": "gpt-4o", "model_name": "gpt-4o"}
            ],
            "strategies": [
                {"name": "single_agent"}
            ],
        },
    )
    cfg = ExperimentFileConfig.from_yaml(cfg_path)
    assert cfg.batch_id == "test-batch"
    assert cfg.dataset.name == "mydata"
    assert len(cfg.models) == 1
    assert len(cfg.strategies) == 1


# ---------------------------------------------------------------------------
# File not found
# ---------------------------------------------------------------------------


def test_config_file_not_found_raises_error(tmp_path: Path):
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises((FileNotFoundError, OSError)):
        ModelsConfig.from_yaml(missing)
