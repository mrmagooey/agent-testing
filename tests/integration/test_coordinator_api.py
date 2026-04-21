"""Integration tests for the FastAPI coordinator API.

Tests all endpoints that don't require a live K8s cluster by monkey-patching
the global ``coordinator`` object with a real ExperimentCoordinator backed by a
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
    ExperimentCoordinator,
    ExperimentCostTracker,
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

def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    """Build an ExperimentCoordinator wired to a temp DB and storage root."""
    cost_calc = CostCalculator(
        pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0),
            "claude-opus-4": ModelPricing(input_per_million=15.0, output_per_million=75.0),
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


def _minimal_matrix() -> dict:
    """Minimal ExperimentMatrix payload for API calls."""
    return {
        "experiment_id": "test-experiment",
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
# GET /experiments — empty initially
# ---------------------------------------------------------------------------

def test_list_experiments_empty(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/experiments")
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
# POST /experiments/estimate
# ---------------------------------------------------------------------------

def test_estimate_experiment_returns_cost(coordinator_client):
    client, *_ = coordinator_client
    payload = {
        "matrix": _minimal_matrix(),
        "target_kloc": 10.0,
    }
    resp = client.post("/experiments/estimate", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "total_runs" in data
    assert "estimated_cost_usd" in data
    assert "by_model" in data
    assert "warning" in data
    assert data["total_runs"] == 1  # 1 model * 1 strategy * 1 tool * 1 profile * 1 verif


def test_estimate_experiment_cost_is_positive_for_known_model(coordinator_client):
    client, *_ = coordinator_client
    payload = {
        "matrix": _minimal_matrix(),
        "target_kloc": 5.0,
    }
    data = client.post("/experiments/estimate", json=payload).json()
    assert data["estimated_cost_usd"] >= 0.0
    assert "gpt-4o" in data["by_model"]


def test_estimate_experiment_larger_matrix(coordinator_client):
    client, *_ = coordinator_client
    payload = {
        "matrix": {
            **_minimal_matrix(),
            "experiment_id": "big-experiment",
            "model_ids": ["gpt-4o", "claude-opus-4"],
            "strategies": ["single_agent", "per_file"],
        },
        "target_kloc": 2.0,
    }
    data = client.post("/experiments/estimate", json=payload).json()
    # 2 models * 2 strategies = 4 runs (1 tool * 1 profile * 1 verif)
    assert data["total_runs"] == 4


# ---------------------------------------------------------------------------
# GET /experiments/{id} — non-existent → 404
# ---------------------------------------------------------------------------

def test_get_nonexistent_experiment_returns_404(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/experiments/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /experiments/{id}/runs — non-existent → empty list (no rows)
# ---------------------------------------------------------------------------

def test_list_runs_nonexistent_experiment_returns_empty(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/experiments/does-not-exist/runs")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /experiments/{id}/runs/{run_id} — non-existent → 404
# ---------------------------------------------------------------------------

def test_get_nonexistent_experiment_run_returns_404(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/experiments/does-not-exist/runs/also-missing")
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

def test_feedback_compare_with_empty_experiments(coordinator_client):
    """compare_experiments returns a valid structure even when both experiments are empty."""
    client, *_ = coordinator_client
    resp = client.post(
        "/feedback/compare",
        json={"experiment_a_id": "experiment-x", "experiment_b_id": "experiment-y"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "experiment_a_id" in data
    assert "experiment_b_id" in data
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
# GET /experiments/{id}/results — 404 if report file missing
# ---------------------------------------------------------------------------

def test_get_results_json_404_when_not_finalized(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/experiments/missing-experiment/results")
    assert resp.status_code == 404


def test_get_results_markdown_404_when_not_finalized(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/experiments/missing-experiment/results/markdown")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /experiments — submit creates experiment in DB (K8s job skipped because k8s=None)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_experiment_creates_db_record(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    matrix = ExperimentMatrix(
        experiment_id="submit-test",
        dataset_name="ds",
        dataset_version="1.0",
        model_ids=["gpt-4o"],
        strategies=[StrategyName.SINGLE_AGENT],
        tool_variants=[ToolVariant.WITH_TOOLS],
        review_profiles=[ReviewProfileName.DEFAULT],
        verification_variants=[VerificationVariant.NONE],
        parallel_modes=[False],
    )

    experiment_id = await c.submit_experiment(matrix)
    assert experiment_id == "submit-test"

    experiment_row = await db.get_experiment("submit-test")
    assert experiment_row is not None
    assert experiment_row["total_runs"] == 1

    runs = await db.list_runs("submit-test")
    assert len(runs) == 1


# ---------------------------------------------------------------------------
# Experiment Frontend Contract — enforce shape of serialized experiments
# ---------------------------------------------------------------------------

# Expected frontend Experiment interface from frontend/src/api/client.ts:7-20
FRONTEND_EXPERIMENT_REQUIRED_KEYS = {
    "experiment_id",
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

FRONTEND_EXPERIMENT_OPTIONAL_KEYS = {
    "completed_at",
    "spend_cap_usd",
}

ALLOWED_STATUS_VALUES = {"pending", "running", "completed", "failed", "cancelled"}


class TestExperimentFrontendContract:
    """Test that experiment endpoints return correct shape for frontend consumption."""

    @pytest.mark.asyncio
    async def test_list_experiments_contract(self, coordinator_client):
        """GET /experiments returns array of experiments matching frontend Experiment interface."""
        client, c, tmp_path = coordinator_client

        # Submit an experiment
        resp = client.post("/experiments", json=_minimal_matrix())
        assert resp.status_code == 201

        # List experiments
        resp = client.get("/experiments")
        assert resp.status_code == 200
        experiments = resp.json()
        assert isinstance(experiments, list)
        assert len(experiments) > 0

        # Check first experiment conforms to frontend contract
        experiment = experiments[0]
        self._assert_experiment_contract(experiment)

    @pytest.mark.asyncio
    async def test_get_experiment_detail_contract(self, coordinator_client):
        """GET /experiments/{id} returns single experiment matching frontend Experiment interface."""
        client, c, tmp_path = coordinator_client

        # Submit an experiment
        resp = client.post("/experiments", json=_minimal_matrix())
        assert resp.status_code == 201
        experiment_id = resp.json()["experiment_id"]

        # Get experiment detail
        resp = client.get(f"/experiments/{experiment_id}")
        assert resp.status_code == 200
        experiment = resp.json()

        # Check experiment conforms to frontend contract
        self._assert_experiment_contract(experiment)

    def _assert_experiment_contract(self, experiment: dict) -> None:
        """Assert experiment dict has exactly the keys and types the frontend expects."""
        # Check all required keys present
        experiment_keys = set(experiment.keys())
        missing = FRONTEND_EXPERIMENT_REQUIRED_KEYS - experiment_keys
        assert not missing, f"Missing required keys: {missing}"

        # Check no unexpected keys (only required + optional allowed)
        allowed_keys = FRONTEND_EXPERIMENT_REQUIRED_KEYS | FRONTEND_EXPERIMENT_OPTIONAL_KEYS
        unexpected = experiment_keys - allowed_keys
        assert not unexpected, f"Unexpected keys: {unexpected}"

        # Check types
        assert isinstance(experiment["experiment_id"], str)
        assert experiment["status"] in ALLOWED_STATUS_VALUES
        assert isinstance(experiment["dataset"], str)
        assert isinstance(experiment["created_at"], str)
        assert isinstance(experiment["total_runs"], int)
        assert isinstance(experiment["completed_runs"], int)
        assert isinstance(experiment["running_runs"], int)
        assert isinstance(experiment["pending_runs"], int)
        assert isinstance(experiment["failed_runs"], int)
        assert isinstance(experiment["total_cost_usd"], (int, float))

        # Optional fields may be present as null or their type
        if experiment.get("completed_at") is not None:
            assert isinstance(experiment["completed_at"], str)
        if experiment.get("spend_cap_usd") is not None:
            assert isinstance(experiment["spend_cap_usd"], (int, float))


# ---------------------------------------------------------------------------
# GET /experiments/{id}/findings/search — no crash when experiment has no results
# ---------------------------------------------------------------------------

def test_search_findings_empty_experiment(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/experiments/empty-experiment/findings/search?q=injection")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# GET /experiments/{id}/runs/{run_id}/tool-audit — returns empty audit when no file
# ---------------------------------------------------------------------------

def test_tool_audit_no_file(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/experiments/b/runs/r/tool-audit")
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
    run_dir = storage / "outputs" / "experiment-x" / "run-1"
    run_dir.mkdir(parents=True)
    tool_calls = run_dir / "tool_calls.jsonl"
    tool_calls.write_text(
        _json.dumps({
            "tool_name": "doc_fetch",
            "inputs": {"path": "library/urllib.request#urllib.request.urlopen"},
        }) + "\n"
    )

    resp = client.get("/experiments/experiment-x/runs/run-1/tool-audit")
    assert resp.status_code == 200
    data = resp.json()
    assert data["suspicious_calls"] == []


def test_audit_unknown_tool_with_url_flagged(coordinator_client, tmp_path):
    """Non-exempt tools with https:// in inputs must be flagged."""
    import json as _json
    client, c, tp = coordinator_client
    storage = tp / "storage"
    run_dir = storage / "outputs" / "experiment-y" / "run-2"
    run_dir.mkdir(parents=True)
    tool_calls = run_dir / "tool_calls.jsonl"
    tool_calls.write_text(
        _json.dumps({
            "tool_name": "fetch_something",
            "inputs": {"url": "https://evil.example.com/exfil"},
        }) + "\n"
    )

    resp = client.get("/experiments/experiment-y/runs/run-2/tool-audit")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["suspicious_calls"]) == 1
    assert data["suspicious_calls"][0]["tool_name"] == "fetch_something"


# ---------------------------------------------------------------------------
# Background task lifecycle — must not leak across TestClient lifecycles
# ---------------------------------------------------------------------------

def test_startup_tracks_retention_task_in_default_env(coordinator_client):
    """Regression: startup must register retention_cleanup_loop in
    _background_tasks so the shutdown handler can cancel it. Pre-fix, the
    task was un-tracked and accumulated across ~110 TestClient lifecycles
    (each holding a closure over the test's tmp_path), OOM-killing pytest.
    """
    coro_names = {t.get_coro().cr_code.co_name for t in coord_module._background_tasks}
    assert "retention_cleanup_loop" in coro_names


async def test_shutdown_cancels_background_tasks_when_gated_on(tmp_path, monkeypatch):
    """When the reconcile loop is gated on, TestClient teardown must cancel
    both it and the retention loop."""
    monkeypatch.setenv("ENABLE_RECONCILE_LOOP", "1")

    db = Database(tmp_path / "test.db")
    await db.init()

    c = _make_coordinator(tmp_path, db)
    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                # Startup ran — retention + reconcile loops tracked.
                assert len(coord_module._background_tasks) == 2
                # Exercise the client to prove the app is live.
                assert client.get("/health").status_code == 200
    # TestClient.__exit__ triggers the shutdown handler, which cancels + awaits.
    assert coord_module._background_tasks == set()
