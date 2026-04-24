"""Integration tests for ExperimentCoordinator.enrich_model_configs().

Phase 3 rewrite: tests the async enrich_model_configs() which now derives
model IDs from strategy bundles (stored in the DB) instead of from
matrix.model_ids.

Verifies that synthesized local-LLM configs have their api_base/api_key
returned in the enrichment dict.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sec_review_framework.coordinator import ExperimentCoordinator
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.experiment import ExperimentMatrix
from sec_review_framework.db import Database
from sec_review_framework.models.catalog import ModelMetadata, ProviderSnapshot
from sec_review_framework.reporting.markdown import MarkdownReportGenerator

from tests.fixtures.provider_snapshots import fake_catalog as _fake_catalog
from tests.helpers import make_smoke_strategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={"gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)},
    )
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        config_dir=None,
        default_cap=4,
    )


def _fresh_local_snap(*model_ids: str) -> ProviderSnapshot:
    return ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(model_ids),
        metadata={mid: ModelMetadata(id=mid, raw_id=mid) for mid in model_ids},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def _coordinator(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)
    yield c, db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enrich_populates_api_base_for_synthesized_model(_coordinator, monkeypatch):
    """Synthesized local-LLM model gets api_base and api_key in the enrichment dict."""
    c, db = _coordinator
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "secret")

    c.catalog = _fake_catalog({"local_llm": _fresh_local_snap("openai/some-model")})

    # Insert a strategy referencing the local LLM model.
    strategy = make_smoke_strategy("openai/some-model")
    await db.insert_user_strategy(strategy)

    matrix = ExperimentMatrix(
        experiment_id="test-enrich",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=[strategy.id],
    )
    enriched = await c.enrich_model_configs(matrix)

    assert enriched.get("openai/some-model") == {
        "api_base": "http://x",
        "api_key": "secret",
    }


@pytest.mark.asyncio
async def test_enrich_is_noop_for_standard_api_key_model(_coordinator, monkeypatch):
    """Standard api-key model has no api_base — not included in enrichment dict."""
    c, db = _coordinator
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
    })

    strategy = make_smoke_strategy("gpt-4o")
    await db.insert_user_strategy(strategy)

    matrix = ExperimentMatrix(
        experiment_id="test-enrich",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=[strategy.id],
    )
    enriched = await c.enrich_model_configs(matrix)

    assert "gpt-4o" not in enriched


@pytest.mark.asyncio
async def test_enrich_respects_caller_overrides(_coordinator, monkeypatch):
    """enrich_model_configs returns api_base/api_key from the catalog for enriched models."""
    c, db = _coordinator
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://authoritative")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "real-key")

    c.catalog = _fake_catalog({"local_llm": _fresh_local_snap("openai/some-model")})

    strategy = make_smoke_strategy("openai/some-model")
    await db.insert_user_strategy(strategy)

    matrix = ExperimentMatrix(
        experiment_id="test-enrich",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=[strategy.id],
    )
    enriched = await c.enrich_model_configs(matrix)

    assert enriched["openai/some-model"]["api_base"] == "http://authoritative"
    assert enriched["openai/some-model"]["api_key"] == "real-key"


@pytest.mark.asyncio
async def test_enrich_enriches_verifier_model_id(_coordinator, monkeypatch):
    """verifier_model_id is also enriched if it resolves to a local-LLM model."""
    c, db = _coordinator
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "key")

    c.catalog = _fake_catalog({"local_llm": _fresh_local_snap("openai/verifier")})

    # The strategy references a different model; verifier comes from matrix.verifier_model_id.
    strategy = make_smoke_strategy("some-other-model")
    await db.insert_user_strategy(strategy)

    matrix = ExperimentMatrix(
        experiment_id="test-enrich",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=[strategy.id],
        verifier_model_id="openai/verifier",
    )
    enriched = await c.enrich_model_configs(matrix)

    assert "openai/verifier" in enriched
    assert enriched["openai/verifier"]["api_base"] == "http://x"
