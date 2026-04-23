"""Integration tests for ExperimentCoordinator.enrich_model_configs().

Phase 2 rewrite: uses ProviderCatalog stubs directly instead of writing
models.yaml on disk.

Verifies that synthesized local-LLM configs have their api_base/api_key
forwarded into matrix.model_configs.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

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
    yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_enrich_populates_api_base_for_synthesized_model(_coordinator, monkeypatch):
    """Synthesized local-LLM model gets api_base and api_key forwarded."""
    c = _coordinator
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "secret")

    c.catalog = _fake_catalog({"local_llm": _fresh_local_snap("openai/some-model")})

    # In the new design, model id = raw_id = "openai/some-model" (the full routing string).
    matrix = _minimal_matrix(["openai/some-model"])
    c.enrich_model_configs(matrix)

    assert matrix.model_configs["openai/some-model"] == {
        "api_base": "http://x",
        "api_key": "secret",
    }


def test_enrich_is_noop_for_standard_api_key_model(_coordinator, monkeypatch):
    """Standard api-key model has no api_base — not enriched."""
    c = _coordinator
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
    })

    matrix = _minimal_matrix(["gpt-4o"])
    c.enrich_model_configs(matrix)

    assert "gpt-4o" not in matrix.model_configs


def test_enrich_respects_caller_overrides(_coordinator, monkeypatch):
    """Pre-set keys in model_configs are overwritten by enrich (setdefault then update).

    Chosen behavior: enrich OVERWRITES individual keys that it knows about
    (api_base, api_key) via dict.update(), but preserves any extra caller-supplied
    keys.
    """
    c = _coordinator
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://authoritative")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "real-key")

    c.catalog = _fake_catalog({"local_llm": _fresh_local_snap("openai/some-model")})

    matrix = _minimal_matrix(["openai/some-model"])
    # Caller pre-set a different api_base and an extra key.
    matrix.model_configs["openai/some-model"] = {
        "api_base": "http://override",
        "extra": "preserved",
    }

    c.enrich_model_configs(matrix)

    cfg = matrix.model_configs["openai/some-model"]
    # enrich overwrites api_base with the authoritative value.
    assert cfg["api_base"] == "http://authoritative"
    # caller-supplied extra key is preserved (update only touches its own keys).
    assert cfg["extra"] == "preserved"


def test_enrich_enriches_verifier_model_id(_coordinator, monkeypatch):
    """verifier_model_id is also enriched if it resolves to a local-LLM model."""
    c = _coordinator
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "key")

    c.catalog = _fake_catalog({"local_llm": _fresh_local_snap("openai/verifier")})

    matrix = _minimal_matrix(["some-other-model"], verifier_model_id="openai/verifier")
    c.enrich_model_configs(matrix)

    assert "openai/verifier" in matrix.model_configs
    assert matrix.model_configs["openai/verifier"]["api_base"] == "http://x"
