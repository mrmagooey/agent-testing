"""Unit tests for the /findings endpoint (cross-experiment findings search).

Tests cover:
- Response shape (total, limit, offset, facets, items keys present)
- Empty index → empty results, facets present
- Filter by vuln_class, severity, match_status, model_id, strategy
- FTS via q=
- Pagination: limit/offset
- Bad params: limit=0, limit=201, offset=-1
- Sort parameter: known values work, unknown value falls back safely
- SPA routing: GET /findings with text/html → serves SPA shell
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import ExperimentCoordinator, app
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
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        config_dir=config_dir,
        default_cap=4,
    )


def _make_finding(
    id: str = "f1",
    title: str = "SQL Injection",
    description: str = "User input unsanitized.",
    vuln_class: str = "sqli",
    severity: str = "high",
    match_status: str = "tp",
    model_id: str = "gpt-4o",
    strategy: str = "single_agent",
) -> dict:
    return {
        "id": id,
        "title": title,
        "description": description,
        "vuln_class": vuln_class,
        "severity": severity,
        "match_status": match_status,
        "confidence": 0.9,
        "file_path": "src/db.py",
        "line_start": 10,
        "line_end": 15,
        "cwe_ids": ["CWE-89"],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def findings_client(tmp_path: Path):
    """TestClient with a coordinator that has some findings seeded."""
    db = Database(tmp_path / "test.db")
    await db.init()

    c = _make_coordinator(tmp_path, db)

    # Seed two runs with different filters
    await db.upsert_findings_for_run(
        run_id="run-1",
        experiment_id="exp-A",
        findings=[
            _make_finding(id="f1", vuln_class="sqli", severity="high", match_status="tp"),
            _make_finding(id="f2", vuln_class="sqli", severity="medium", match_status="fp",
                          title="SQL FP"),
        ],
        model_id="gpt-4o",
        strategy="single_agent",
        dataset_name="ds-1",
    )
    await db.upsert_findings_for_run(
        run_id="run-2",
        experiment_id="exp-B",
        findings=[
            _make_finding(id="f3", vuln_class="xss", severity="high", match_status="tp",
                          title="XSS"),
        ],
        model_id="claude-3",
        strategy="per_file",
        dataset_name="ds-2",
    )

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<!DOCTYPE html><html><body>SPA</body></html>")

    with patch.object(coord_module, "FRONTEND_DIST_DIR", dist_dir):
        with patch.object(coord_module, "coordinator", c):
            with patch.object(c, "reconcile", return_value=None):
                with TestClient(app, raise_server_exceptions=True) as client:
                    yield client


@pytest_asyncio.fixture
async def empty_client(tmp_path: Path):
    """TestClient with an empty findings index."""
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<!DOCTYPE html><html><body>SPA</body></html>")

    with patch.object(coord_module, "FRONTEND_DIST_DIR", dist_dir):
        with patch.object(coord_module, "coordinator", c):
            with patch.object(c, "reconcile", return_value=None):
                with TestClient(app, raise_server_exceptions=True) as client:
                    yield client


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def test_findings_endpoint_response_shape(findings_client):
    """GET /findings returns the expected top-level keys."""
    resp = findings_client.get("/api/findings")
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "limit" in body
    assert "offset" in body
    assert "facets" in body
    assert "items" in body
    assert isinstance(body["items"], list)
    assert isinstance(body["facets"], dict)


def test_findings_total_matches_all_seeded(findings_client):
    """Without filters, total should equal the number of seeded findings."""
    resp = findings_client.get("/api/findings")
    assert resp.status_code == 200
    assert resp.json()["total"] == 3


def test_findings_empty_index(empty_client):
    """Empty index returns total=0 and empty items, but still valid shape."""
    resp = empty_client.get("/api/findings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert "facets" in body


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_filter_vuln_class(findings_client):
    resp = findings_client.get("/api/findings?vuln_class=sqli")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    for item in body["items"]:
        assert item["vuln_class"] == "sqli"


def test_filter_severity(findings_client):
    resp = findings_client.get("/api/findings?severity=high")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2


def test_filter_match_status(findings_client):
    resp = findings_client.get("/api/findings?match_status=fp")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_filter_model_id(findings_client):
    resp = findings_client.get("/api/findings?model_id=claude-3")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_filter_strategy(findings_client):
    resp = findings_client.get("/api/findings?strategy=per_file")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_filter_experiment_id(findings_client):
    resp = findings_client.get("/api/findings?experiment_id=exp-A")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_filter_dataset_name(findings_client):
    resp = findings_client.get("/api/findings?dataset_name=ds-2")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


# ---------------------------------------------------------------------------
# FTS query
# ---------------------------------------------------------------------------


def test_fts_q_filter(findings_client):
    """q= triggers FTS; should find findings with 'SQL' in title/description."""
    resp = findings_client.get("/api/findings?q=SQL")
    assert resp.status_code == 200
    body = resp.json()
    # Both sqli findings contain "SQL" in title/description
    assert body["total"] >= 1


def test_fts_q_no_match(findings_client):
    resp = findings_client.get("/api/findings?q=zzznosuchthing999")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_pagination_limit(findings_client):
    resp = findings_client.get("/api/findings?limit=2&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0


def test_pagination_offset(findings_client):
    resp = findings_client.get("/api/findings?limit=2&offset=2")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["offset"] == 2


def test_pagination_offset_past_end(findings_client):
    resp = findings_client.get("/api/findings?limit=10&offset=100")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Bad parameters → 400
# ---------------------------------------------------------------------------


def test_bad_limit_zero(findings_client):
    resp = findings_client.get("/api/findings?limit=0")
    assert resp.status_code == 400


def test_bad_limit_over_max(findings_client):
    resp = findings_client.get("/api/findings?limit=201")
    assert resp.status_code == 400


def test_bad_offset_negative(findings_client):
    resp = findings_client.get("/api/findings?offset=-1")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Sort parameter
# ---------------------------------------------------------------------------


def test_sort_created_at_asc(findings_client):
    """sort=created_at asc must not raise."""
    resp = findings_client.get("/api/findings?sort=created_at+asc")
    assert resp.status_code == 200


def test_sort_unknown_falls_back(findings_client):
    """Unknown sort column must not raise (falls back to created_at desc)."""
    resp = findings_client.get("/api/findings?sort=nonexistent_col+desc")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Facets shape
# ---------------------------------------------------------------------------


def test_facets_contain_expected_keys(findings_client):
    resp = findings_client.get("/api/findings")
    facets = resp.json()["facets"]
    for key in ("vuln_class", "severity", "match_status", "model_id", "strategy", "dataset_name"):
        assert key in facets, f"Missing facet: {key}"


def test_facets_counts_match_filter_off(findings_client):
    """Facets should reflect correct counts when no filters applied."""
    resp = findings_client.get("/api/findings")
    facets = resp.json()["facets"]
    assert facets["vuln_class"].get("sqli", 0) == 2
    assert facets["vuln_class"].get("xss", 0) == 1


# ---------------------------------------------------------------------------
# Multi-value query params (regression test)
# ---------------------------------------------------------------------------


def test_multi_value_vuln_class_filter(findings_client):
    """Repeated query-string params must be honored (Query(default=None) regression)."""
    # Request findings with BOTH sqli AND xss
    resp = findings_client.get("/api/findings?vuln_class=sqli&vuln_class=xss")
    assert resp.status_code == 200
    body = resp.json()
    # Should return 3 findings: 2 sqli + 1 xss
    assert body["total"] == 3
    assert len(body["items"]) == 3
    vuln_classes = {item["vuln_class"] for item in body["items"]}
    assert vuln_classes == {"sqli", "xss"}


# ---------------------------------------------------------------------------
# SPA routing: /findings with text/html → serves SPA
# ---------------------------------------------------------------------------


def test_findings_route_serves_spa_for_browser(findings_client):
    """Browser navigation to /findings must return the SPA shell."""
    resp = findings_client.get(
        "/findings",
        headers={"Accept": "text/html,*/*;q=0.8"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "SPA" in resp.text
