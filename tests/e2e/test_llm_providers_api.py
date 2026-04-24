"""Integration tests for the user-configurable LLM providers API.

Uses FastAPI TestClient with a real SQLite database and a stub probe that
always returns success, so tests run without network access.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
from sec_review_framework.reporting.generator import ReportGenerator


# ---------------------------------------------------------------------------
# Encryption key setup helpers
# ---------------------------------------------------------------------------

def _generate_fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


_TEST_KEY: str = _generate_fernet_key()


def _ensure_fernet_env() -> None:
    """Set the encryption env var and reload the fernet module."""
    os.environ["LLM_PROVIDER_ENCRYPTION_KEY"] = _TEST_KEY
    mod_name = "sec_review_framework.secrets.fernet"
    if mod_name in sys.modules:
        del sys.modules[mod_name]


def _clear_fernet_env() -> None:
    os.environ.pop("LLM_PROVIDER_ENCRYPTION_KEY", None)
    mod_name = "sec_review_framework.secrets.fernet"
    if mod_name in sys.modules:
        del sys.modules[mod_name]


# ---------------------------------------------------------------------------
# Minimal reporter stub
# ---------------------------------------------------------------------------

class _NoopReporter(ReportGenerator):
    def render_run(self, result, output_dir: Path) -> None:
        pass

    def render_matrix(self, results, output_dir: Path) -> None:
        pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture()
def coordinator_with_db(tmp_path: Path):
    _ensure_fernet_env()
    db = Database(tmp_path / "test.db")
    _run_async(db.init())

    coord = ExperimentCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="unused",
        namespace="default",
        db=db,
        reporter=_NoopReporter(),
        cost_calculator=CostCalculator(pricing={}),
        default_cap=4,
    )
    yield coord
    _clear_fernet_env()


@pytest.fixture()
def client(coordinator_with_db: ExperimentCoordinator):
    """TestClient with coordinator global injected and probe patched to succeed."""
    import sec_review_framework.coordinator as coord_module
    import sec_review_framework.llm_providers as providers_module

    original = coord_module.coordinator
    coord_module.coordinator = coordinator_with_db

    # Patch _probe_custom_provider to return "fresh" without hitting network
    async def _fake_probe(row: dict) -> dict:
        from datetime import UTC, datetime
        return {
            "last_probe_at": datetime.now(UTC).isoformat(),
            "last_probe_status": "fresh",
            "last_probe_error": None,
        }

    with patch.object(providers_module, "_probe_custom_provider", side_effect=_fake_probe):
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    coord_module.coordinator = original


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _create_provider(client: TestClient, **kwargs) -> dict:
    defaults = {
        "name": "test-provider",
        "display_name": "Test Provider",
        "adapter": "openai_compat",
        "model_id": "gpt-4-test",
        "auth_type": "api_key",
        "api_key": "sk-test-1234",
    }
    defaults.update(kwargs)
    resp = client.post("/api/llm-providers", json=defaults)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_list_providers_empty(client: TestClient):
    resp = client.get("/api/llm-providers")
    assert resp.status_code == 200
    data = resp.json()
    assert "builtin" in data
    assert "custom" in data
    assert data["custom"] == []


def test_create_provider_returns_dto(client: TestClient):
    dto = _create_provider(client, name="my-openai", display_name="My OpenAI")
    assert dto["name"] == "my-openai"
    assert dto["display_name"] == "My OpenAI"
    assert dto["adapter"] == "openai_compat"
    assert dto["source"] == "custom"
    assert dto["api_key_masked"] is not None
    assert "sk-test-1234" not in dto["api_key_masked"]
    assert dto["last_probe_status"] == "fresh"


def test_create_provider_api_key_not_in_db_plaintext(
    client: TestClient, coordinator_with_db: ExperimentCoordinator
):
    """The raw API key must not be stored as plaintext in the database."""
    _create_provider(client, name="enc-check", api_key="plaintext-key-9999")

    # Read raw DB row
    rows = _run_async(coordinator_with_db.db.list_llm_providers())
    assert len(rows) == 1
    ciphertext = rows[0]["api_key_ciphertext"]
    assert ciphertext is not None
    assert b"plaintext-key-9999" not in ciphertext


def test_create_duplicate_name_returns_409(client: TestClient):
    _create_provider(client, name="dupe-test")
    resp = client.post("/api/llm-providers", json={
        "name": "dupe-test",
        "display_name": "Dupe",
        "adapter": "litellm",
        "model_id": "m",
        "auth_type": "none",
    })
    assert resp.status_code == 409


def test_list_providers_after_create(client: TestClient):
    _create_provider(client, name="visible-one")
    resp = client.get("/api/llm-providers")
    assert resp.status_code == 200
    customs = resp.json()["custom"]
    assert len(customs) == 1
    assert customs[0]["name"] == "visible-one"


def test_patch_provider(client: TestClient):
    dto = _create_provider(client, name="patch-me")
    provider_id = dto["id"]

    resp = client.patch(f"/api/llm-providers/{provider_id}", json={
        "display_name": "Patched Name",
        "enabled": False,
    })
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["display_name"] == "Patched Name"
    assert updated["enabled"] is False


def test_patch_provider_not_found(client: TestClient):
    resp = client.patch("/api/llm-providers/does-not-exist", json={"display_name": "X"})
    assert resp.status_code == 404


def test_delete_provider(client: TestClient):
    dto = _create_provider(client, name="delete-me")
    provider_id = dto["id"]

    resp = client.delete(f"/api/llm-providers/{provider_id}")
    assert resp.status_code == 204

    # Gone from list
    resp = client.get("/api/llm-providers")
    customs = resp.json()["custom"]
    ids = [c["id"] for c in customs]
    assert provider_id not in ids


def test_delete_provider_not_found(client: TestClient):
    resp = client.delete("/api/llm-providers/does-not-exist")
    assert resp.status_code == 404


def test_probe_endpoint(client: TestClient):
    dto = _create_provider(client, name="probe-me")
    provider_id = dto["id"]

    resp = client.post(f"/api/llm-providers/{provider_id}/probe")
    assert resp.status_code == 200
    result = resp.json()
    assert result["last_probe_status"] == "fresh"
    assert result["last_probe_at"] is not None


def test_probe_status_reflected_in_get(client: TestClient):
    """After POST creating a provider, GET shows the probe status."""
    dto = _create_provider(client, name="probe-visible")

    resp = client.get("/api/llm-providers")
    customs = resp.json()["custom"]
    assert len(customs) == 1
    assert customs[0]["last_probe_status"] == "fresh"


def test_custom_provider_appears_in_models(
    client: TestClient, coordinator_with_db: ExperimentCoordinator
):
    """Creating a custom provider causes it to appear in GET /api/models."""
    _create_provider(client, name="catalog-test", model_id="my-model")

    resp = client.get("/api/models")
    assert resp.status_code == 200
    data = resp.json()
    # The provider should appear as a group named "custom:catalog-test"
    provider_names = [g["provider"] for g in data]
    assert any("custom:catalog-test" in p for p in provider_names), (
        f"Expected custom:catalog-test in providers, got: {provider_names}"
    )


def test_app_settings_get(client: TestClient):
    resp = client.get("/api/settings/defaults")
    assert resp.status_code == 200
    data = resp.json()
    assert data["evidence_assessor"] == "heuristic"
    assert data["allow_unavailable_models"] is False
    assert "evidence_judge_model" in data


def test_app_settings_patch(client: TestClient):
    resp = client.patch("/api/settings/defaults", json={
        "allow_unavailable_models": True,
        "evidence_assessor": "llm_judge",
        "evidence_judge_model": "gpt-4o",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["allow_unavailable_models"] is True
    assert data["evidence_assessor"] == "llm_judge"
    assert data["evidence_judge_model"] == "gpt-4o"


def test_app_settings_patch_roundtrip(client: TestClient):
    """PATCH then GET returns the updated values."""
    client.patch("/api/settings/defaults", json={"evidence_assessor": "llm_judge"})
    resp = client.get("/api/settings/defaults")
    assert resp.json()["evidence_assessor"] == "llm_judge"


def test_create_duplicate_name_integrity_error_returns_409(
    client: TestClient, coordinator_with_db: ExperimentCoordinator
):
    """Concurrent duplicate-name POST must return 409, not 500.

    The pre-check was removed; the DB unique constraint (IntegrityError) is
    the authoritative guard. This test exercises the IntegrityError→409
    conversion by calling POST twice sequentially with the same name.
    """
    _create_provider(client, name="race-condition-test")

    # Second POST with same name: the DB UNIQUE index raises IntegrityError
    # which the endpoint converts to 409.
    resp = client.post("/api/llm-providers", json={
        "name": "race-condition-test",
        "display_name": "Duplicate",
        "adapter": "litellm",
        "model_id": "m",
        "auth_type": "none",
    })
    assert resp.status_code == 409, f"Expected 409, got {resp.status_code}: {resp.text}"
    assert "race-condition-test" in resp.json()["detail"]


def test_create_provider_invalid_slug_rejected(client: TestClient):
    resp = client.post("/api/llm-providers", json={
        "name": "Bad Name",
        "display_name": "Bad",
        "adapter": "litellm",
        "model_id": "m",
        "auth_type": "none",
    })
    assert resp.status_code == 422


def test_full_crud_roundtrip(client: TestClient):
    """Create → list → patch → probe → delete."""
    # Create
    dto = _create_provider(client, name="full-crud", display_name="Full CRUD")
    pid = dto["id"]

    # List
    resp = client.get("/api/llm-providers")
    assert any(p["id"] == pid for p in resp.json()["custom"])

    # Patch
    resp = client.patch(f"/api/llm-providers/{pid}", json={"display_name": "Updated"})
    assert resp.json()["display_name"] == "Updated"

    # Probe
    resp = client.post(f"/api/llm-providers/{pid}/probe")
    assert resp.status_code == 200

    # Delete
    resp = client.delete(f"/api/llm-providers/{pid}")
    assert resp.status_code == 204

    # Confirm gone
    resp = client.get("/api/llm-providers")
    assert not any(p["id"] == pid for p in resp.json()["custom"])
