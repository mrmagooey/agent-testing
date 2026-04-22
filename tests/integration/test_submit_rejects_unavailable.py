"""Integration tests: POST /experiments rejects unavailable models.

Tests:
- Rejects key_missing models
- Rejects not_listed models
- Accepts when allow_unavailable_models=True
- Rejects unknown verifier_model_id
- Accepts when all models are available
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
from sec_review_framework.models.catalog import ModelMetadata, ProviderCatalog, ProviderSnapshot
from sec_review_framework.reporting.markdown import MarkdownReportGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0),
        }
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
    """Write a minimal models.yaml to config_dir."""
    data = {
        "defaults": {"temperature": 0.2, "max_tokens": 8192},
        "providers": providers,
    }
    (config_dir / "models.yaml").write_text(yaml.dump(data))


def _fake_catalog(snapshots: dict[str, ProviderSnapshot]) -> ProviderCatalog:
    """Build a ProviderCatalog with a fake snapshot() return."""
    catalog = MagicMock(spec=ProviderCatalog)
    catalog.snapshot.return_value = snapshots
    return catalog


def _submit_payload(
    model_ids: list[str],
    *,
    verifier_model_id: str | None = None,
    allow_unavailable_models: bool = False,
) -> dict:
    payload: dict = {
        "experiment_id": "test-exp",
        "dataset_name": "ds",
        "dataset_version": "1.0",
        "model_ids": model_ids,
        "strategies": ["single_agent"],
        "tool_variants": ["with_tools"],
        "review_profiles": ["default"],
        "verification_variants": ["none"],
        "parallel_modes": [False],
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
    """Yield (client, coordinator, config_dir) for submit tests."""
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client, c, tmp_path / "config"


# ---------------------------------------------------------------------------
# Reject key_missing
# ---------------------------------------------------------------------------

def test_rejects_key_missing_model(_setup, monkeypatch):
    client, c, config_dir = _setup
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    _write_models_yaml(config_dir, {
        "gpt-4o": {
            "model_name": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "display_name": "GPT-4o",
        }
    })
    # Catalog has no snapshot for openai (disabled)
    c.catalog = _fake_catalog({})

    resp = client.post("/experiments", json=_submit_payload(["gpt-4o"]))
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"]["error"] == "unavailable_models"
    problems = body["detail"]["models"]
    assert any(p["id"] == "gpt-4o" and p["status"] == "key_missing" for p in problems)


# ---------------------------------------------------------------------------
# Reject not_listed
# ---------------------------------------------------------------------------

def test_rejects_not_listed_model(_setup, monkeypatch):
    """Model id present in registry but not in provider snapshot."""
    client, c, config_dir = _setup
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _write_models_yaml(config_dir, {
        "gpt-4o-ultra": {
            "model_name": "gpt-4o-ultra",
            "api_key_env": "OPENAI_API_KEY",
            "display_name": "GPT-4o Ultra",
        }
    })
    # Snapshot only contains gpt-4o, not gpt-4o-ultra
    snap = ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(["gpt-4o"]),
        metadata={"gpt-4o": ModelMetadata(id="gpt-4o")},
    )
    c.catalog = _fake_catalog({"openai": snap})

    resp = client.post("/experiments", json=_submit_payload(["gpt-4o-ultra"]))
    assert resp.status_code == 400
    body = resp.json()
    problems = body["detail"]["models"]
    assert any(p["id"] == "gpt-4o-ultra" and p["status"] == "not_listed" for p in problems)


# ---------------------------------------------------------------------------
# Reject probe_failed
# ---------------------------------------------------------------------------

def test_rejects_probe_failed_model(_setup, monkeypatch):
    client, c, config_dir = _setup
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _write_models_yaml(config_dir, {
        "gpt-4o": {
            "model_name": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "display_name": "GPT-4o",
        }
    })
    snap = ProviderSnapshot(probe_status="failed", last_error="timeout")
    c.catalog = _fake_catalog({"openai": snap})

    resp = client.post("/experiments", json=_submit_payload(["gpt-4o"]))
    assert resp.status_code == 400
    body = resp.json()
    problems = body["detail"]["models"]
    assert any(p["id"] == "gpt-4o" and p["status"] == "probe_failed" for p in problems)


# ---------------------------------------------------------------------------
# Allow when allow_unavailable_models=True
# ---------------------------------------------------------------------------

def test_allows_unavailable_when_override_set(_setup, monkeypatch):
    """allow_unavailable_models: true bypasses validation."""
    client, c, config_dir = _setup
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    _write_models_yaml(config_dir, {
        "gpt-4o": {
            "model_name": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "display_name": "GPT-4o",
        }
    })
    c.catalog = _fake_catalog({})

    # Patch submit_experiment to avoid real K8s calls
    with patch.object(c, "submit_experiment", return_value="exp-123"):
        resp = client.post(
            "/experiments",
            json=_submit_payload(["gpt-4o"], allow_unavailable_models=True),
        )
    assert resp.status_code == 201
    assert resp.json()["experiment_id"] == "exp-123"


def test_allow_unavailable_not_in_persisted_json(_setup, monkeypatch):
    """allow_unavailable_models must NOT appear in the persisted experiment
    config JSON.  It is a submit-time flag only and must never reach the DB
    or on-disk worker config files.
    """
    import json

    client, c, config_dir = _setup
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _write_models_yaml(config_dir, {
        "gpt-4o": {
            "model_name": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "display_name": "GPT-4o",
        }
    })
    snap = ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(["gpt-4o"]),
        metadata={"gpt-4o": ModelMetadata(id="gpt-4o")},
    )
    c.catalog = _fake_catalog({"openai": snap})

    # Capture the matrix passed to submit_experiment so we can inspect its
    # serialised form without touching K8s.
    captured: list = []

    async def _capture(matrix):
        captured.append(matrix)
        return "exp-persist-test"

    with patch.object(c, "submit_experiment", side_effect=_capture):
        resp = client.post(
            "/experiments",
            json=_submit_payload(["gpt-4o"], allow_unavailable_models=True),
        )
    assert resp.status_code == 201

    # The matrix was received and the flag is accessible as attribute (used by
    # the validation bypass), but must not appear in serialised output.
    assert len(captured) == 1
    matrix = captured[0]
    assert matrix.allow_unavailable_models is True  # attribute still set
    serialised = json.loads(matrix.model_dump_json())
    assert "allow_unavailable_models" not in serialised


# ---------------------------------------------------------------------------
# Accepts available models
# ---------------------------------------------------------------------------

def test_accepts_available_model(_setup, monkeypatch):
    client, c, config_dir = _setup
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _write_models_yaml(config_dir, {
        "gpt-4o": {
            "model_name": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "display_name": "GPT-4o",
        }
    })
    snap = ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(["gpt-4o"]),
        metadata={"gpt-4o": ModelMetadata(id="gpt-4o")},
    )
    c.catalog = _fake_catalog({"openai": snap})

    with patch.object(c, "submit_experiment", return_value="exp-ok"):
        resp = client.post("/experiments", json=_submit_payload(["gpt-4o"]))
    assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Reject unknown verifier_model_id
# ---------------------------------------------------------------------------

def test_rejects_unknown_verifier_model_id(_setup, monkeypatch):
    """verifier_model_id that is not in the registry → status=unknown error."""
    client, c, config_dir = _setup
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _write_models_yaml(config_dir, {
        "gpt-4o": {
            "model_name": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "display_name": "GPT-4o",
        }
    })
    snap = ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(["gpt-4o"]),
        metadata={"gpt-4o": ModelMetadata(id="gpt-4o")},
    )
    c.catalog = _fake_catalog({"openai": snap})

    resp = client.post(
        "/experiments",
        json=_submit_payload(["gpt-4o"], verifier_model_id="does-not-exist"),
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

def test_multiple_unavailable_models_reported(_setup, monkeypatch):
    """All unavailable models are returned in a single error response."""
    client, c, config_dir = _setup
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    _write_models_yaml(config_dir, {
        "gpt-4o": {
            "model_name": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "display_name": "GPT-4o",
        },
        "claude-opus": {
            "model_name": "claude-opus-4",
            "api_key_env": "ANTHROPIC_API_KEY",
            "display_name": "Claude Opus",
        },
    })
    c.catalog = _fake_catalog({})

    resp = client.post("/experiments", json=_submit_payload(["gpt-4o", "claude-opus"]))
    assert resp.status_code == 400
    problems = resp.json()["detail"]["models"]
    problem_ids = {p["id"] for p in problems}
    assert "gpt-4o" in problem_ids
    assert "claude-opus" in problem_ids


# ---------------------------------------------------------------------------
# No models.yaml → no validation → submit proceeds
# ---------------------------------------------------------------------------

def test_no_models_yaml_skips_validation(_setup):
    """When models.yaml is absent, validation is skipped (no config = no constraints)."""
    client, c, config_dir = _setup
    # Do not create models.yaml
    c.catalog = _fake_catalog({})

    with patch.object(c, "submit_experiment", return_value="exp-no-config"):
        resp = client.post("/experiments", json=_submit_payload(["any-model"]))
    assert resp.status_code == 201
