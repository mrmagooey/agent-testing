"""Unit tests for the SPA middleware and /api prefix stripping in coordinator.py.

These tests verify:
  - GET /batches/new with Accept: text/html returns index.html (SPA route).
  - GET /batches/new with Accept: application/json falls through to the API.
  - GET /api/batches strips the /api prefix and routes to the API handler.
  - GET /api/health strips the /api prefix and returns healthy.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import BatchCoordinator, BatchCostTracker, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator


# ---------------------------------------------------------------------------
# Helpers — mirrors the pattern in tests/integration/test_coordinator_api.py
# ---------------------------------------------------------------------------


def _make_coordinator(tmp_path: Path, db: Database) -> BatchCoordinator:
    cost_calc = CostCalculator(
        pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0),
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dist_dir(tmp_path: Path) -> Path:
    """Create a minimal frontend/dist with index.html."""
    d = tmp_path / "frontend" / "dist"
    d.mkdir(parents=True)
    (d / "index.html").write_text("<!DOCTYPE html><html><body>SPA</body></html>")
    return d


@pytest.fixture
async def spa_client(tmp_path: Path, dist_dir: Path):
    """TestClient with a real coordinator, patched reconcile, and a real index.html."""
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    with patch.object(coord_module, "FRONTEND_DIST_DIR", dist_dir):
        with patch.object(coord_module, "coordinator", c):
            with patch.object(c, "reconcile", return_value=None):
                with TestClient(app, raise_server_exceptions=True) as client:
                    yield client


# ---------------------------------------------------------------------------
# SPA fallback — Accept: text/html
# ---------------------------------------------------------------------------


def test_spa_deep_link_batches_new_returns_html(spa_client):
    """Browser navigation to /batches/new must return the React shell."""
    resp = spa_client.get(
        "/batches/new",
        headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_spa_deep_link_batches_trailing_slash_returns_html(spa_client):
    """Browser navigation to /batches/ must return the React shell."""
    resp = spa_client.get(
        "/batches/",
        headers={"Accept": "text/html,*/*;q=0.8"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_spa_deep_link_datasets_discover_returns_html(spa_client):
    """Browser navigation to /datasets/discover must return the React shell."""
    resp = spa_client.get(
        "/datasets/discover",
        headers={"Accept": "text/html,*/*;q=0.8"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "SPA" in resp.text


def test_spa_returns_html_content_type(spa_client):
    """The SPA fallback response must declare text/html content-type."""
    resp = spa_client.get(
        "/batches/new",
        headers={"Accept": "text/html"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Non-HTML accept header — fall through to the API (no SPA interception)
# ---------------------------------------------------------------------------


def test_json_accept_does_not_serve_spa(spa_client):
    """httpx / curl without text/html accept must NOT get the SPA shell."""
    resp = spa_client.get(
        "/batches/new",
        headers={"Accept": "application/json"},
    )
    # The API route will 404/422 because "new" is not a real batch_id.
    # Crucially it must NOT return the SPA HTML page.
    assert resp.status_code != 200 or "SPA" not in resp.text


def test_no_accept_header_does_not_serve_spa(spa_client):
    """A GET with no Accept header must NOT serve the SPA index.html."""
    resp = spa_client.get("/batches/new")
    assert "SPA" not in resp.text


# ---------------------------------------------------------------------------
# /api prefix stripping
# ---------------------------------------------------------------------------


def test_api_prefix_stripped_for_health(spa_client):
    """/api/health must route to /health and return 200 ok."""
    resp = spa_client.get("/api/health", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_api_prefix_stripped_for_batches(spa_client):
    """/api/batches must route to /batches (the list endpoint) and return a list."""
    resp = spa_client.get("/api/batches", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_api_prefix_stripped_for_strategies(spa_client):
    """/api/strategies must route to /strategies and return a list."""
    resp = spa_client.get("/api/strategies", headers={"Accept": "application/json"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# SPA fallback absent when index.html does not exist
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spa_fallback_absent_when_no_index_html(tmp_path: Path):
    """When frontend is not built, missing index.html must NOT crash the app."""
    empty_dist = tmp_path / "empty_dist"
    empty_dist.mkdir()

    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    with patch.object(coord_module, "FRONTEND_DIST_DIR", empty_dist):
        with patch.object(coord_module, "coordinator", c):
            with patch.object(c, "reconcile", return_value=None):
                with TestClient(app, raise_server_exceptions=True) as client:
                    # Falls through to the API; "new" is not a valid batch_id → 404/422
                    resp = client.get(
                        "/batches/new",
                        headers={"Accept": "text/html"},
                        follow_redirects=True,
                    )
                    # Must not 500; 404 or 422 are both acceptable fallthrough responses
                    assert resp.status_code in (404, 422)


def test_root_get_serves_spa_when_index_exists(spa_client):
    """GET / with a browser Accept header must serve index.html. Regression:
    the middleware and StaticFiles mount both dereference FRONTEND_DIST_DIR,
    so when pip-install relocated __file__ into site-packages (making the
    default path point at a non-existent directory), GET / returned 404."""
    resp = spa_client.get(
        "/",
        headers={"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "SPA" in resp.text
