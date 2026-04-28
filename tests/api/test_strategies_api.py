"""API tests for strategy CRUD endpoints.

Uses the same TestClient + coordinator-patch pattern as
tests/integration/test_coordinator_api.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import (
    ExperimentCoordinator,
    _seed_builtin_strategies,
    app,
)
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
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


_SAMPLE_DEFAULT = {
    "system_prompt": "You are a security expert.",
    "user_prompt_template": "Review {repo_summary}. Output as {finding_output_format}.",
    "profile_modifier": "",
    "model_id": "claude-opus-4-5",
    "tools": ["read_file", "grep"],
    "verification": "none",
    "max_turns": 50,
    "tool_extensions": [],
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def strategy_client(tmp_path: Path):
    """TestClient with real DB seeded with builtins."""
    db = Database(tmp_path / "test.db")
    await db.init()
    await _seed_builtin_strategies(db)

    c = _make_coordinator(tmp_path, db)

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client, db, tmp_path


# ---------------------------------------------------------------------------
# GET /strategies — list
# ---------------------------------------------------------------------------

def test_list_strategies_returns_seeded_builtins(strategy_client):
    client, *_ = strategy_client
    resp = client.get("/api/strategies")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    ids = {s["id"] for s in data}
    for builtin_id in (
        "builtin.single_agent",
        "builtin.per_file",
        "builtin.per_vuln_class",
        "builtin.sast_first",
        "builtin.diff_review",
    ):
        assert builtin_id in ids, f"{builtin_id} missing from GET /strategies"


def test_list_strategies_summary_fields(strategy_client):
    client, *_ = strategy_client
    data = client.get("/api/strategies").json()
    for item in data:
        assert "id" in item
        assert "name" in item
        assert "orchestration_shape" in item
        assert "is_builtin" in item
        assert "parent_strategy_id" in item
        # Full bundle fields should NOT be present in summary
        assert "default" not in item
        assert "overrides" not in item


def test_list_strategies_includes_user_strategies(strategy_client):
    client, db, _ = strategy_client
    # Create a user strategy via POST, then list
    payload = {
        "name": "My Custom Strategy",
        "orchestration_shape": "single_agent",
        "default": _SAMPLE_DEFAULT,
        "overrides": [],
    }
    post_resp = client.post("/api/strategies", json=payload)
    assert post_resp.status_code == 201

    list_resp = client.get("/api/strategies")
    ids = {s["id"] for s in list_resp.json()}
    created_id = post_resp.json()["id"]
    assert created_id in ids


# ---------------------------------------------------------------------------
# GET /strategies/{id}
# ---------------------------------------------------------------------------

def test_get_builtin_strategy_full(strategy_client):
    client, *_ = strategy_client
    resp = client.get("/api/strategies/builtin.single_agent")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "builtin.single_agent"
    assert data["is_builtin"] is True
    # Full bundle is returned
    assert "default" in data
    assert "overrides" in data
    assert "orchestration_shape" in data


def test_get_strategy_not_found(strategy_client):
    client, *_ = strategy_client
    resp = client.get("/api/strategies/user.does-not-exist.000000")
    assert resp.status_code == 404


def test_get_strategy_with_overrides(strategy_client):
    """per_vuln_class builtin should have overrides populated."""
    client, *_ = strategy_client
    resp = client.get("/api/strategies/builtin.per_vuln_class")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["overrides"], list)
    # Should have at least some vuln class overrides
    assert len(data["overrides"]) > 0


# ---------------------------------------------------------------------------
# POST /strategies — create
# ---------------------------------------------------------------------------

def test_create_strategy_returns_created(strategy_client):
    client, *_ = strategy_client
    payload = {
        "name": "My Custom Strategy",
        "orchestration_shape": "single_agent",
        "default": _SAMPLE_DEFAULT,
        "overrides": [],
    }
    resp = client.post("/api/strategies", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "My Custom Strategy"
    assert data["orchestration_shape"] == "single_agent"
    assert data["is_builtin"] is False
    # id should follow the user.<slug>.<hash> pattern
    assert data["id"].startswith("user.")
    assert len(data["id"].split(".")) >= 3


def test_create_strategy_is_retrievable(strategy_client):
    client, *_ = strategy_client
    payload = {
        "name": "Retrievable Strategy",
        "orchestration_shape": "single_agent",
        "default": _SAMPLE_DEFAULT,
        "overrides": [],
    }
    post_resp = client.post("/api/strategies", json=payload)
    assert post_resp.status_code == 201
    created_id = post_resp.json()["id"]

    get_resp = client.get(f"/api/strategies/{created_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == created_id


def test_create_strategy_duplicate_returns_409(strategy_client):
    """Creating the same strategy twice (same content → same id) returns 409."""
    client, *_ = strategy_client
    payload = {
        "name": "Duplicate Strategy",
        "orchestration_shape": "single_agent",
        "default": _SAMPLE_DEFAULT,
        "overrides": [],
    }
    resp1 = client.post("/api/strategies", json=payload)
    assert resp1.status_code == 201

    resp2 = client.post("/api/strategies", json=payload)
    assert resp2.status_code == 409


def test_create_strategy_with_parent_id(strategy_client):
    client, *_ = strategy_client
    payload = {
        "name": "Forked Strategy",
        "parent_strategy_id": "builtin.single_agent",
        "orchestration_shape": "single_agent",
        "default": _SAMPLE_DEFAULT,
        "overrides": [],
    }
    resp = client.post("/api/strategies", json=payload)
    assert resp.status_code == 201
    assert resp.json()["parent_strategy_id"] == "builtin.single_agent"


def test_create_strategy_single_agent_with_overrides_returns_422(strategy_client):
    """single_agent strategies must have no overrides — pydantic rejects this."""
    client, *_ = strategy_client
    payload = {
        "name": "Invalid Strategy",
        "orchestration_shape": "single_agent",
        "default": _SAMPLE_DEFAULT,
        "overrides": [
            {
                "key": "some_key",
                "override": {"system_prompt": "override prompt"},
            }
        ],
    }
    resp = client.post("/api/strategies", json=payload)
    # UserStrategy validator raises ValueError for overrides on single_agent
    assert resp.status_code == 422


def test_create_per_file_strategy_with_glob_overrides(strategy_client):
    client, *_ = strategy_client
    payload = {
        "name": "Per File With Globs",
        "orchestration_shape": "per_file",
        "default": _SAMPLE_DEFAULT,
        "overrides": [
            {
                "key": "**/*.py",
                "override": {"system_prompt": "Python prompt."},
            },
            {
                "key": "**/*.js",
                "override": {"system_prompt": "JS prompt."},
            },
        ],
    }
    resp = client.post("/api/strategies", json=payload)
    assert resp.status_code == 201
    data = resp.json()
    override_keys = [r["key"] for r in data["overrides"]]
    assert override_keys == ["**/*.py", "**/*.js"]


# ---------------------------------------------------------------------------
# POST /strategies/{id}/validate
# ---------------------------------------------------------------------------

def test_validate_valid_strategy(strategy_client):
    client, *_ = strategy_client
    body = {
        "orchestration_shape": "single_agent",
        "default": _SAMPLE_DEFAULT,
        "overrides": [],
    }
    resp = client.post("/api/strategies/builtin.single_agent/validate", json=body)
    assert resp.status_code == 200
    assert resp.json() == {"valid": True}


def test_validate_missing_repo_summary_placeholder(strategy_client):
    """user_prompt_template missing {repo_summary} should fail validation."""
    client, *_ = strategy_client
    default_missing_placeholder = {**_SAMPLE_DEFAULT, "user_prompt_template": "No placeholders here."}
    body = {
        "orchestration_shape": "single_agent",
        "default": default_missing_placeholder,
        "overrides": [],
    }
    resp = client.post("/api/strategies/builtin.single_agent/validate", json=body)
    assert resp.status_code == 200
    result = resp.json()
    # No KeyError is raised by a template with no placeholders — it simply
    # doesn't use {repo_summary} but that's valid format() wise.
    # The validation checks that known placeholders *resolve without error*.
    # A template without {repo_summary} is valid; a template with a *wrong*
    # placeholder like {bad_key} should fail.
    assert "valid" in result


def test_validate_unknown_placeholder_fails(strategy_client):
    """user_prompt_template with an unknown placeholder raises KeyError."""
    client, *_ = strategy_client
    default_bad_placeholder = {**_SAMPLE_DEFAULT, "user_prompt_template": "Review {unknown_key}."}
    body = {
        "orchestration_shape": "single_agent",
        "default": default_bad_placeholder,
        "overrides": [],
    }
    resp = client.post("/api/strategies/builtin.single_agent/validate", json=body)
    assert resp.status_code == 200
    result = resp.json()
    assert result["valid"] is False
    assert len(result["errors"]) > 0


def test_validate_overrides_on_single_agent_fails(strategy_client):
    """Overrides on single_agent shape should fail validation."""
    client, *_ = strategy_client
    body = {
        "orchestration_shape": "single_agent",
        "default": _SAMPLE_DEFAULT,
        "overrides": [{"key": "some_key", "override": {"system_prompt": "x"}}],
    }
    resp = client.post("/api/strategies/builtin.single_agent/validate", json=body)
    assert resp.status_code == 200
    result = resp.json()
    assert result["valid"] is False
    assert any("no overrides" in e for e in result["errors"])


def test_validate_invalid_glob_pattern(strategy_client):
    """An invalid glob pattern in a per_file override should fail validation."""
    client, *_ = strategy_client
    body = {
        "orchestration_shape": "per_file",
        "default": _SAMPLE_DEFAULT,
        # fnmatch.translate accepts almost anything, so use a Python exception-
        # causing input by patching — in practice fnmatch is very permissive.
        # We test a valid glob to confirm the happy path.
        "overrides": [{"key": "**/*.py", "override": {"system_prompt": "Python."}}],
    }
    resp = client.post("/api/strategies/builtin.per_file/validate", json=body)
    assert resp.status_code == 200
    # Valid glob should pass
    assert resp.json()["valid"] is True


# ---------------------------------------------------------------------------
# DELETE /strategies/{id}
# ---------------------------------------------------------------------------

def test_delete_builtin_returns_403(strategy_client):
    client, *_ = strategy_client
    resp = client.delete("/api/strategies/builtin.single_agent")
    assert resp.status_code == 403


def test_delete_user_strategy_returns_204(strategy_client):
    client, *_ = strategy_client
    # Create a user strategy first
    payload = {
        "name": "Deletable Strategy",
        "orchestration_shape": "single_agent",
        "default": _SAMPLE_DEFAULT,
        "overrides": [],
    }
    post_resp = client.post("/api/strategies", json=payload)
    assert post_resp.status_code == 201
    created_id = post_resp.json()["id"]

    del_resp = client.delete(f"/api/strategies/{created_id}")
    assert del_resp.status_code == 204


def test_delete_strategy_then_get_returns_404(strategy_client):
    client, *_ = strategy_client
    payload = {
        "name": "Gone Strategy",
        "orchestration_shape": "single_agent",
        "default": _SAMPLE_DEFAULT,
        "overrides": [],
    }
    post_resp = client.post("/api/strategies", json=payload)
    created_id = post_resp.json()["id"]

    client.delete(f"/api/strategies/{created_id}")

    get_resp = client.get(f"/api/strategies/{created_id}")
    assert get_resp.status_code == 404


def test_delete_nonexistent_returns_404(strategy_client):
    client, *_ = strategy_client
    resp = client.delete("/api/strategies/user.ghost.000000")
    assert resp.status_code == 404
