"""Integration tests for POST /datasets/discover-cves and GET /datasets/resolve-cve."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import app
from sec_review_framework.db import Database
from sec_review_framework.ground_truth.cve_importer import (
    CVECandidate as _CVECandidate,
    ResolvedCVE,
)
from sec_review_framework.data.findings import Severity, VulnClass

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
                yield client, c


def _make_resolved_cve(cve_id: str = "CVE-2024-11111") -> ResolvedCVE:
    return ResolvedCVE(
        cve_id=cve_id,
        ghsa_id=None,
        description="Test SQL injection vulnerability",
        cwe_ids=["CWE-89"],
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        cvss_score=8.1,
        repo_url="https://github.com/example/repo",
        fix_commit_sha="abc123def456",
        affected_files=["src/db.py", "src/models.py"],
        lines_changed=42,
        language="python",
        repo_kloc=15.0,
        published_date="2024-01-15",
        source="ghsa",
    )


def _make_candidate(resolved: ResolvedCVE | None = None) -> _CVECandidate:
    if resolved is None:
        resolved = _make_resolved_cve()
    return _CVECandidate(
        resolved=resolved,
        score=0.82,
        score_breakdown={"patch_size": 0.9, "severity": 0.8, "language": 1.0},
        importable=True,
    )


# ---------------------------------------------------------------------------
# POST /datasets/discover-cves — happy path
# ---------------------------------------------------------------------------


def test_discover_cves_returns_200_with_candidate_list(coordinator_client):
    """Happy path: mocked discovery returns candidates in the flat frontend shape."""
    client, c = coordinator_client
    candidate = _make_candidate()
    with patch.object(c, "discover_cves", return_value=[
        coord_module.CVECandidateResponse.from_candidate(candidate)
    ]):
        resp = client.post("/datasets/discover-cves", json={"languages": ["python"]})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    item = data[0]
    # Check all required frontend fields are present
    assert item["cve_id"] == "CVE-2024-11111"
    assert item["score"] == pytest.approx(0.82)
    assert item["vuln_class"] == "sqli"
    assert item["severity"] == "high"
    assert item["language"] == "python"
    assert item["repo"] == "https://github.com/example/repo"
    assert item["files_changed"] == 2
    assert item["lines_changed"] == 42
    assert item["importable"] is True
    assert "description" in item


def test_discover_cves_empty_body_returns_200(coordinator_client):
    """Empty body (all criteria optional) is valid and returns list."""
    client, c = coordinator_client
    with patch.object(c, "discover_cves", return_value=[]):
        resp = client.post("/datasets/discover-cves", json={})
    assert resp.status_code == 200
    assert resp.json() == []


def test_discover_cves_with_all_criteria_fields(coordinator_client):
    """All optional criteria fields are accepted without error."""
    client, c = coordinator_client
    with patch.object(c, "discover_cves", return_value=[]):
        resp = client.post("/datasets/discover-cves", json={
            "languages": ["python", "go"],
            "vuln_classes": ["sqli", "xss"],
            "severities": ["high", "critical"],
            "patch_size_min": 5,
            "patch_size_max": 100,
            "date_from": "2024-01-01",
            "date_to": "2024-12-31",
            "max_results": 20,
        })
    assert resp.status_code == 200


def test_discover_cves_fix_commit_url_in_advisory_url(coordinator_client):
    """advisory_url is synthesized as repo_url/commit/sha when both are present."""
    client, c = coordinator_client
    resolved = _make_resolved_cve()
    candidate = _make_candidate(resolved)
    with patch.object(c, "discover_cves", return_value=[
        coord_module.CVECandidateResponse.from_candidate(candidate)
    ]):
        resp = client.post("/datasets/discover-cves", json={})
    assert resp.status_code == 200
    item = resp.json()[0]
    assert item["advisory_url"] == "https://github.com/example/repo/commit/abc123def456"
    assert item["fix_commit"] == "abc123def456"


# ---------------------------------------------------------------------------
# POST /datasets/discover-cves — bad request
# ---------------------------------------------------------------------------


def test_discover_cves_invalid_json_type_returns_422(coordinator_client):
    """Sending a string instead of an object returns 422 from Pydantic validation."""
    client, _ = coordinator_client
    resp = client.post(
        "/datasets/discover-cves",
        content='"not an object"',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 422


def test_discover_cves_wrong_field_type_returns_422(coordinator_client):
    """patch_size_min must be an int; sending a string triggers 422."""
    client, _ = coordinator_client
    resp = client.post(
        "/datasets/discover-cves",
        json={"patch_size_min": "not-an-int"},
    )
    assert resp.status_code == 422


def test_discover_cves_no_body_returns_422(coordinator_client):
    """Missing body triggers 422."""
    client, _ = coordinator_client
    resp = client.post("/datasets/discover-cves")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /datasets/resolve-cve — happy path
# ---------------------------------------------------------------------------


def test_resolve_cve_returns_200_with_candidate(coordinator_client):
    """Happy path: mocked resolver returns a single flat CVECandidateResponse."""
    client, c = coordinator_client
    resolved = _make_resolved_cve("CVE-2024-99999")
    candidate = _make_candidate(resolved)
    with patch.object(c, "resolve_cve", return_value=
            coord_module.CVECandidateResponse.from_candidate(candidate)):
        resp = client.get("/datasets/resolve-cve?id=CVE-2024-99999")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cve_id"] == "CVE-2024-99999"
    assert data["importable"] is True
    assert data["severity"] == "high"


def test_resolve_cve_not_found_returns_404(coordinator_client):
    """When CVE cannot be resolved, the route returns 404."""
    from fastapi import HTTPException
    client, c = coordinator_client
    with patch.object(c, "resolve_cve", side_effect=HTTPException(
        status_code=404, detail="Could not resolve CVE-2099-00001 via GHSA/OSV/NVD"
    )):
        resp = client.get("/datasets/resolve-cve?id=CVE-2099-00001")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Integration: CVECandidateResponse.from_candidate mapping correctness
# ---------------------------------------------------------------------------


def test_cve_candidate_response_maps_nested_resolved_fields():
    """Unit-level: from_candidate() correctly flattens nested ResolvedCVE fields."""
    resolved = _make_resolved_cve()
    candidate = _make_candidate(resolved)
    resp = coord_module.CVECandidateResponse.from_candidate(candidate)

    assert resp.cve_id == resolved.cve_id
    assert resp.score == candidate.score
    assert resp.vuln_class == resolved.vuln_class.value
    assert resp.severity == resolved.severity.value
    assert resp.language == resolved.language
    assert resp.repo == resolved.repo_url
    assert resp.files_changed == len(resolved.affected_files)
    assert resp.lines_changed == resolved.lines_changed
    assert resp.importable == candidate.importable
    assert resp.fix_commit == resolved.fix_commit_sha
