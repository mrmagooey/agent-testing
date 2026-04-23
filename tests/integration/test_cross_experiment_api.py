"""Phase 5: Integration tests for cross-experiment endpoints.

Routes covered:
  - GET  /findings                (happy path, filters, pagination, validation)
  - POST /feedback/compare        (happy path, mocked, schema)
  - GET  /trends                  (happy path, 400 missing dataset, filter params)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import app
from sec_review_framework.db import Database
from tests.integration.test_coordinator_api import _make_coordinator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def coordinator_client(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)
    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client, c, tmp_path


# ---------------------------------------------------------------------------
# GET /findings
# ---------------------------------------------------------------------------


def test_findings_empty_returns_200_with_correct_shape(coordinator_client):
    """GET /findings with no params returns 200 and the expected envelope."""
    client, *_ = coordinator_client
    resp = client.get("/findings")
    assert resp.status_code == 200
    data = resp.json()
    assert "total" in data
    assert "limit" in data
    assert "offset" in data
    assert "items" in data
    assert "facets" in data
    assert isinstance(data["items"], list)


def test_findings_with_text_query(coordinator_client):
    """GET /findings?q=injection returns 200."""
    client, *_ = coordinator_client
    resp = client.get("/findings?q=injection")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["items"], list)


def test_findings_pagination_limit_and_offset(coordinator_client):
    """limit and offset query params are accepted and reflected in the response."""
    client, *_ = coordinator_client
    resp = client.get("/findings?limit=5&offset=0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["limit"] == 5
    assert data["offset"] == 0


def test_findings_limit_too_large_returns_400(coordinator_client):
    """limit > 200 is rejected with 400."""
    client, *_ = coordinator_client
    resp = client.get("/findings?limit=999")
    assert resp.status_code == 400


def test_findings_filter_by_vuln_class(coordinator_client):
    """vuln_class filter is accepted."""
    client, *_ = coordinator_client
    resp = client.get("/findings?vuln_class=sqli")
    assert resp.status_code == 200


def test_findings_filter_by_severity(coordinator_client):
    """severity filter is accepted."""
    client, *_ = coordinator_client
    resp = client.get("/findings?severity=high")
    assert resp.status_code == 200


def test_findings_filter_by_model_id(coordinator_client):
    """model_id filter is accepted."""
    client, *_ = coordinator_client
    resp = client.get("/findings?model_id=gpt-4o")
    assert resp.status_code == 200


def test_findings_filter_by_experiment_id(coordinator_client):
    """experiment_id filter is accepted."""
    client, *_ = coordinator_client
    resp = client.get("/findings?experiment_id=exp-001")
    assert resp.status_code == 200


def test_findings_combined_filters(coordinator_client):
    """Multiple filters can be combined in a single request."""
    client, *_ = coordinator_client
    resp = client.get("/findings?q=sql&vuln_class=sqli&severity=high&limit=10")
    assert resp.status_code == 200


def test_findings_date_range_filter(coordinator_client):
    """created_from and created_to filters are accepted."""
    client, *_ = coordinator_client
    resp = client.get("/findings?created_from=2024-01-01&created_to=2024-12-31")
    assert resp.status_code == 200


def test_findings_total_is_integer(coordinator_client):
    """total in the response is always an integer."""
    client, *_ = coordinator_client
    resp = client.get("/findings")
    assert resp.status_code == 200
    assert isinstance(resp.json()["total"], int)


def test_findings_offset_zero_same_as_no_offset(coordinator_client):
    """offset=0 is equivalent to not providing an offset."""
    client, *_ = coordinator_client
    resp_no_offset = client.get("/findings?limit=10")
    resp_zero_offset = client.get("/findings?limit=10&offset=0")
    assert resp_no_offset.status_code == 200
    assert resp_zero_offset.status_code == 200
    assert resp_no_offset.json()["items"] == resp_zero_offset.json()["items"]


# ---------------------------------------------------------------------------
# POST /feedback/compare
# ---------------------------------------------------------------------------


def test_feedback_compare_happy_path_returns_dict(coordinator_client):
    """POST /feedback/compare with two experiment IDs returns a comparison dict."""
    client, c, _ = coordinator_client
    mock_comparison = {
        "experiment_a_id": "exp-a",
        "experiment_b_id": "exp-b",
        "metric_deltas": {},
        "persistent_false_positives": [],
        "persistent_misses": [],
        "improvements": [],
        "regressions": [],
    }
    with patch.object(c, "compare_experiments", return_value=mock_comparison):
        resp = client.post(
            "/feedback/compare",
            json={"experiment_a_id": "exp-a", "experiment_b_id": "exp-b"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "experiment_a_id" in data
    assert "experiment_b_id" in data


def test_feedback_compare_schema_has_required_keys(coordinator_client):
    """Mocked compare returns all expected top-level keys."""
    client, c, _ = coordinator_client
    mock_comparison = {
        "experiment_a_id": "x",
        "experiment_b_id": "y",
        "metric_deltas": {"precision": 0.05, "recall": -0.02},
        "persistent_false_positives": [],
        "persistent_misses": ["label-001"],
        "improvements": ["label-002"],
        "regressions": [],
    }
    with patch.object(c, "compare_experiments", return_value=mock_comparison):
        resp = client.post(
            "/feedback/compare",
            json={"experiment_a_id": "x", "experiment_b_id": "y"},
        )
    assert resp.status_code == 200
    data = resp.json()
    for key in ("metric_deltas", "persistent_false_positives", "persistent_misses",
                "improvements", "regressions"):
        assert key in data, f"Missing key: {key}"


def test_feedback_compare_missing_experiment_a_returns_422(coordinator_client):
    """Missing experiment_a_id returns 422 from Pydantic validation."""
    client, *_ = coordinator_client
    resp = client.post("/feedback/compare", json={"experiment_b_id": "exp-b"})
    assert resp.status_code == 422


def test_feedback_compare_missing_experiment_b_returns_422(coordinator_client):
    """Missing experiment_b_id returns 422 from Pydantic validation."""
    client, *_ = coordinator_client
    resp = client.post("/feedback/compare", json={"experiment_a_id": "exp-a"})
    assert resp.status_code == 422


def test_feedback_compare_empty_experiments_returns_200(coordinator_client):
    """Comparing two experiments with no results returns a valid (empty) comparison."""
    client, c, _ = coordinator_client
    mock_comparison = {
        "experiment_a_id": "empty-a",
        "experiment_b_id": "empty-b",
        "metric_deltas": {},
        "persistent_false_positives": [],
        "persistent_misses": [],
        "improvements": [],
        "regressions": [],
    }
    with patch.object(c, "compare_experiments", return_value=mock_comparison):
        resp = client.post(
            "/feedback/compare",
            json={"experiment_a_id": "empty-a", "experiment_b_id": "empty-b"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["persistent_false_positives"] == []
    assert data["improvements"] == []


def test_feedback_compare_same_experiment_is_accepted(coordinator_client):
    """Comparing an experiment to itself is not a validation error."""
    client, c, _ = coordinator_client
    mock_comparison = {
        "experiment_a_id": "exp",
        "experiment_b_id": "exp",
        "metric_deltas": {},
        "persistent_false_positives": [],
        "persistent_misses": [],
        "improvements": [],
        "regressions": [],
    }
    with patch.object(c, "compare_experiments", return_value=mock_comparison):
        resp = client.post(
            "/feedback/compare",
            json={"experiment_a_id": "exp", "experiment_b_id": "exp"},
        )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /trends
# ---------------------------------------------------------------------------


def test_trends_missing_dataset_returns_400(coordinator_client):
    """GET /trends without dataset query param returns 400."""
    client, *_ = coordinator_client
    resp = client.get("/trends")
    assert resp.status_code == 400
    assert "dataset" in resp.json().get("detail", "").lower()


def test_trends_with_dataset_returns_200(coordinator_client):
    """GET /trends?dataset=my-ds returns 200 with trend data shape."""
    client, c, _ = coordinator_client
    mock_trends = {"dataset": "my-ds", "series": [], "total_experiments": 0}
    with patch.object(c, "get_trends", return_value=mock_trends):
        resp = client.get("/trends?dataset=my-ds")
    assert resp.status_code == 200


def test_trends_response_shape(coordinator_client):
    """Trends response has dataset and series keys."""
    client, c, _ = coordinator_client
    mock_trends = {
        "dataset": "my-dataset",
        "series": [
            {
                "model_id": "gpt-4o",
                "strategy": "single_agent",
                "tool_variant": "with_tools",
                "tool_extensions": "",
                "data": [{"completed_at": "2026-01-01", "f1": 0.75}],
            }
        ],
        "total_experiments": 1,
    }
    with patch.object(c, "get_trends", return_value=mock_trends):
        resp = client.get("/trends?dataset=my-dataset")
    assert resp.status_code == 200
    data = resp.json()
    assert "series" in data


def test_trends_invalid_since_date_returns_400(coordinator_client):
    """Passing an invalid since date returns 400."""
    client, *_ = coordinator_client
    resp = client.get("/trends?dataset=my-ds&since=not-a-date")
    assert resp.status_code == 400


def test_trends_invalid_until_date_returns_400(coordinator_client):
    """Passing an invalid until date returns 400."""
    client, *_ = coordinator_client
    resp = client.get("/trends?dataset=my-ds&until=bad-date")
    assert resp.status_code == 400


def test_trends_valid_since_and_until(coordinator_client):
    """Valid ISO dates for since and until are accepted."""
    client, c, _ = coordinator_client
    mock_trends = {"dataset": "my-ds", "series": [], "total_experiments": 0}
    with patch.object(c, "get_trends", return_value=mock_trends):
        resp = client.get("/trends?dataset=my-ds&since=2024-01-01&until=2024-12-31")
    assert resp.status_code == 200


def test_trends_limit_param_accepted(coordinator_client):
    """limit query param in valid range is accepted."""
    client, c, _ = coordinator_client
    mock_trends = {"dataset": "ds", "series": [], "total_experiments": 0}
    with patch.object(c, "get_trends", return_value=mock_trends):
        resp = client.get("/trends?dataset=ds&limit=20")
    assert resp.status_code == 200


def test_trends_limit_too_large_returns_422(coordinator_client):
    """limit > 200 is rejected (FastAPI Query constraint)."""
    client, *_ = coordinator_client
    resp = client.get("/trends?dataset=ds&limit=999")
    assert resp.status_code == 422


def test_trends_tool_ext_filter_accepted(coordinator_client):
    """tool_ext filter is accepted without error."""
    client, c, _ = coordinator_client
    mock_trends = {"dataset": "ds", "series": [], "total_experiments": 0}
    with patch.object(c, "get_trends", return_value=mock_trends):
        resp = client.get("/trends?dataset=ds&tool_ext=tree_sitter")
    assert resp.status_code == 200


def test_trends_empty_series_when_no_completed_experiments(coordinator_client):
    """With no completed experiments, series is empty."""
    client, c, _ = coordinator_client
    mock_trends = {"dataset": "empty-ds", "series": [], "total_experiments": 0}
    with patch.object(c, "get_trends", return_value=mock_trends):
        resp = client.get("/trends?dataset=empty-ds")
    assert resp.status_code == 200
    assert resp.json()["series"] == []
