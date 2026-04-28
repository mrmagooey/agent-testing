"""Integration tests: POST /experiments rejects unavailable models.

Phase 3 rewrite: uses strategy_ids instead of model_ids in matrix.
Models are now derived from strategy bundles at validation time.

Tests:
- Rejects key_missing models (via strategy referencing that model)
- Accepts when allow_unavailable_models=True
- allow_unavailable_models excluded from persisted JSON
- Rejects unknown verifier_model_id
- Accepts when all models are available
- Multiple unavailable models reported at once
- No snapshots → no validation → submit proceeds
- Legacy id aliases are transparently rewritten
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
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
        pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0),
        }
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


def _submit_payload(
    strategy_ids: list[str],
    *,
    verifier_model_id: str | None = None,
    allow_unavailable_models: bool = False,
) -> dict:
    """Build a POST /experiments payload using the new strategy_ids API."""
    payload: dict = {
        "experiment_id": "test-exp",
        "dataset_name": "ds",
        "dataset_version": "1.0",
        "strategy_ids": strategy_ids,
        "allow_unavailable_models": allow_unavailable_models,
    }
    if verifier_model_id is not None:
        payload["verifier_model_id"] = verifier_model_id
    return payload


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def _setup(tmp_path: Path):
    """Yield (client, coordinator, db) for submit tests."""
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client, c, db


# ---------------------------------------------------------------------------
# Reject key_missing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rejects_key_missing_model(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    # Insert a strategy referencing gpt-4o so the coordinator can derive model_ids.
    strategy = make_smoke_strategy("gpt-4o")
    await db.insert_user_strategy(strategy)

    # Catalog has a snapshot with gpt-4o, but no API key in env → key_missing.
    # Note: catalog must be set INSIDE the TestClient context because the lifespan
    # overwrites coordinator.catalog with a real ProviderCatalog after startup.
    key_missing_catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
    })

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                # Set the fake catalog after lifespan startup (which overwrites catalog).
                c.catalog = key_missing_catalog
                resp = client.post("/experiments", json=_submit_payload([strategy.id]))

    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["error"] == "unavailable_models"
    problems = body["detail"]["models"]
    assert any(p["id"] == "gpt-4o" and p["status"] == "key_missing" for p in problems)


# ---------------------------------------------------------------------------
# Allow when allow_unavailable_models=True
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_allows_unavailable_when_override_set(tmp_path: Path, monkeypatch):
    """allow_unavailable_models: true bypasses validation."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    strategy = make_smoke_strategy("gpt-4o")
    await db.insert_user_strategy(strategy)

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
    })

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                # Patch submit_experiment to avoid real K8s calls
                with patch.object(c, "submit_experiment", return_value="exp-123"):
                    resp = client.post(
                        "/experiments",
                        json=_submit_payload([strategy.id], allow_unavailable_models=True),
                    )

    assert resp.status_code == 201
    assert resp.json()["experiment_id"] == "exp-123"


@pytest.mark.asyncio
async def test_allow_unavailable_not_in_persisted_json(tmp_path: Path, monkeypatch):
    """allow_unavailable_models must NOT appear in the persisted experiment
    config JSON.  It is a submit-time flag only and must never reach the DB
    or on-disk worker config files.
    """
    import json

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    strategy = make_smoke_strategy("gpt-4o")
    await db.insert_user_strategy(strategy)

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
    })

    # Capture the matrix passed to submit_experiment so we can inspect its
    # serialised form without touching K8s.
    captured: list = []

    async def _capture(matrix):
        captured.append(matrix)
        return "exp-persist-test"

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                with patch.object(c, "submit_experiment", side_effect=_capture):
                    resp = client.post(
                        "/experiments",
                        json=_submit_payload([strategy.id], allow_unavailable_models=True),
                    )

    assert resp.status_code == 201
    assert len(captured) == 1
    matrix = captured[0]
    assert matrix.allow_unavailable_models is True  # attribute still set
    serialised = json.loads(matrix.model_dump_json())
    assert "allow_unavailable_models" not in serialised


# ---------------------------------------------------------------------------
# Accepts available models
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_accepts_available_model(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    strategy = make_smoke_strategy("gpt-4o")
    await db.insert_user_strategy(strategy)

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
    })

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                with patch.object(c, "submit_experiment", return_value="exp-ok"):
                    resp = client.post("/experiments", json=_submit_payload([strategy.id]))

    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Reject unknown verifier_model_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rejects_unknown_verifier_model_id(tmp_path: Path, monkeypatch):
    """verifier_model_id that is not in the registry → status=unknown error."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    strategy = make_smoke_strategy("gpt-4o")
    await db.insert_user_strategy(strategy)

    # Catalog must be set INSIDE the TestClient context (lifespan overwrites it).
    verifier_catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
    })

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                c.catalog = verifier_catalog
                resp = client.post(
                    "/experiments",
                    json=_submit_payload([strategy.id], verifier_model_id="does-not-exist"),
                )

    assert resp.status_code == 400
    body = resp.json()
    problems = body["detail"]["models"]
    assert any(
        p["id"] == "does-not-exist" and p["status"] == "unknown"
        for p in problems
    )


# ---------------------------------------------------------------------------
# Multiple problems reported at once
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_multiple_unavailable_models_reported(tmp_path: Path, monkeypatch):
    """All unavailable models are returned in a single error response."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    s1 = make_smoke_strategy("gpt-4o")
    s2 = make_smoke_strategy("claude-opus-4")
    await db.insert_user_strategy(s1)
    await db.insert_user_strategy(s2)

    # Catalog must be set INSIDE the TestClient context (lifespan overwrites it).
    multi_catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        ),
        "anthropic": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["claude-opus-4"]),
            metadata={
                "claude-opus-4": ModelMetadata(
                    id="claude-opus-4", raw_id="claude-opus-4"
                )
            },
        ),
    })

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                c.catalog = multi_catalog
                resp = client.post("/experiments", json=_submit_payload([s1.id, s2.id]))

    assert resp.status_code == 400
    problems = resp.json()["detail"]["models"]
    problem_ids = {p["id"] for p in problems}
    assert "gpt-4o" in problem_ids
    assert "claude-opus-4" in problem_ids


# ---------------------------------------------------------------------------
# No snapshots → no validation → submit proceeds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_snapshots_skips_validation(tmp_path: Path):
    """When catalog has no snapshots, validation is skipped (no registry = no constraints)."""
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    strategy = make_smoke_strategy("any-model")
    await db.insert_user_strategy(strategy)

    # Empty snapshot dict → empty registry.
    c.catalog = _fake_catalog({})

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                with patch.object(c, "submit_experiment", return_value="exp-no-config"):
                    resp = client.post("/experiments", json=_submit_payload([strategy.id]))

    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Legacy alias rewriting
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_legacy_alias_rewritten_and_accepted(tmp_path: Path, monkeypatch):
    """Submitting a strategy whose model is a legacy short id is transparently rewritten."""
    from sec_review_framework.models.aliases import _reset_warnings_for_tests
    _reset_warnings_for_tests()

    # The catalog contains the canonical routing string.
    canonical_id = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
    legacy_id = "bedrock-claude-3-5-sonnet"

    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    # Strategy references the legacy short id.
    strategy = make_smoke_strategy(legacy_id)
    await db.insert_user_strategy(strategy)

    c.catalog = _fake_catalog({
        "bedrock": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset([canonical_id]),
            metadata={
                canonical_id: ModelMetadata(
                    id=canonical_id,
                    raw_id=canonical_id,
                    region="us-east-1",
                    provider_key="bedrock",
                )
            },
        )
    })

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                with patch.object(c, "submit_experiment", return_value="exp-alias"):
                    resp = client.post(
                        "/experiments",
                        json=_submit_payload([strategy.id]),
                    )

    assert resp.status_code == 201
