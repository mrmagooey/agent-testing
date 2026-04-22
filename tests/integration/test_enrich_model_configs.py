"""Integration tests for ExperimentCoordinator.enrich_model_configs().

Verifies that synthesized local-LLM configs (and static YAML entries with
api_base) have their api_base/api_key forwarded into matrix.model_configs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from sec_review_framework.coordinator import ExperimentCoordinator
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.experiment import ExperimentMatrix
from sec_review_framework.db import Database
from sec_review_framework.models.catalog import ModelMetadata, ProviderCatalog, ProviderSnapshot
from sec_review_framework.reporting.markdown import MarkdownReportGenerator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={"gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)},
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        config_dir=config_dir,
        default_cap=4,
    )


def _write_models_yaml(config_dir: Path, providers: dict) -> None:
    data = {
        "defaults": {"temperature": 0.2, "max_tokens": 8192},
        "providers": providers,
    }
    (config_dir / "models.yaml").write_text(yaml.dump(data))


def _fake_catalog(snapshots: dict[str, ProviderSnapshot]) -> ProviderCatalog:
    catalog = MagicMock(spec=ProviderCatalog)
    catalog.snapshot.return_value = snapshots
    return catalog


def _minimal_matrix(model_ids: list[str], **kwargs) -> ExperimentMatrix:
    return ExperimentMatrix(
        experiment_id="test-enrich",
        dataset_name="ds",
        dataset_version="1.0",
        model_ids=model_ids,
        strategies=["single_agent"],
        tool_variants=["with_tools"],
        review_profiles=["default"],
        verification_variants=["none"],
        parallel_modes=[False],
        **kwargs,
    )


def _fresh_local_snap(*model_ids: str) -> ProviderSnapshot:
    return ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(model_ids),
        metadata={mid: ModelMetadata(id=mid) for mid in model_ids},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def _coordinator(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)
    yield c, tmp_path / "config"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_enrich_populates_api_base_for_synthesized_model(_coordinator, monkeypatch):
    """Synthesized local-LLM model gets api_base and api_key forwarded."""
    c, config_dir = _coordinator
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "secret")

    _write_models_yaml(config_dir, {})  # empty static registry
    c.catalog = _fake_catalog({"local_llm": _fresh_local_snap("openai/some-model")})

    matrix = _minimal_matrix(["local_llm-some-model"])
    c.enrich_model_configs(matrix)

    assert matrix.model_configs["local_llm-some-model"] == {
        "api_base": "http://x",
        "api_key": "secret",
    }


def test_enrich_is_noop_for_registry_entry_without_api_base(_coordinator, monkeypatch):
    """Static YAML model without api_base is not touched."""
    c, config_dir = _coordinator
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _write_models_yaml(config_dir, {
        "gpt-4o": {
            "model_name": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "display_name": "GPT-4o",
        }
    })
    c.catalog = _fake_catalog({})

    matrix = _minimal_matrix(["gpt-4o"])
    c.enrich_model_configs(matrix)

    assert "gpt-4o" not in matrix.model_configs


def test_enrich_respects_caller_overrides(_coordinator, monkeypatch):
    """Pre-set keys in model_configs are overwritten by enrich (setdefault then update).

    Chosen behavior: enrich OVERWRITES individual keys that it knows about
    (api_base, api_key) via dict.update(), but preserves any extra caller-supplied
    keys. This is the natural result of setdefault({}).update({...}): the dict
    object is preserved if it exists, but the keys enrich touches are replaced.
    Rationale: the coordinator is the authoritative source for where the endpoint
    lives; allowing a stale caller override to silently win would route traffic
    to the wrong host.
    """
    c, config_dir = _coordinator
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://authoritative")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "real-key")

    _write_models_yaml(config_dir, {})
    c.catalog = _fake_catalog({"local_llm": _fresh_local_snap("openai/some-model")})

    matrix = _minimal_matrix(["local_llm-some-model"])
    # Caller pre-set a different api_base and an extra key.
    matrix.model_configs["local_llm-some-model"] = {
        "api_base": "http://override",
        "extra": "preserved",
    }

    c.enrich_model_configs(matrix)

    cfg = matrix.model_configs["local_llm-some-model"]
    # enrich overwrites api_base with the authoritative value.
    assert cfg["api_base"] == "http://authoritative"
    # caller-supplied extra key is preserved (update only touches its own keys).
    assert cfg["extra"] == "preserved"


def test_enrich_enriches_verifier_model_id(_coordinator, monkeypatch):
    """verifier_model_id is also enriched if it resolves to a local-LLM model."""
    c, config_dir = _coordinator
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "key")

    _write_models_yaml(config_dir, {})
    c.catalog = _fake_catalog({"local_llm": _fresh_local_snap("openai/verifier")})

    matrix = _minimal_matrix(["some-other-model"], verifier_model_id="local_llm-verifier")
    c.enrich_model_configs(matrix)

    assert "local_llm-verifier" in matrix.model_configs
    assert matrix.model_configs["local_llm-verifier"]["api_base"] == "http://x"
