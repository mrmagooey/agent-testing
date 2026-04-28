"""Integration tests for POST /datasets/discover-cves and GET /datasets/resolve-cve."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import app
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.db import Database
from sec_review_framework.ground_truth.cve_importer import (
    CVECandidate as _CVECandidate,
    DiscoveryIssue,
    DiscoveryResult,
    DiscoveryStats,
)
from sec_review_framework.ground_truth.cve_importer import (
    ResolvedCVE,
)
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


def _empty_discovery_result() -> DiscoveryResult:
    return DiscoveryResult(
        candidates=[],
        issues=[],
        stats=DiscoveryStats(scanned=0, resolved=0, rejected=0, returned=0),
    )


def _discovery_result_from_candidates(
    candidates: list[_CVECandidate],
) -> DiscoveryResult:
    return DiscoveryResult(
        candidates=candidates,
        issues=[],
        stats=DiscoveryStats(
            scanned=len(candidates),
            resolved=len(candidates),
            rejected=0,
            returned=len(candidates),
        ),
    )


# ---------------------------------------------------------------------------
# POST /datasets/discover-cves — happy path
# ---------------------------------------------------------------------------


def test_discover_cves_returns_200_with_candidate_list(coordinator_client):
    """Happy path: mocked discovery returns candidates in the new envelope shape."""
    client, c = coordinator_client
    candidate = _make_candidate()
    with patch.object(c, "discover_cves", return_value=coord_module.DiscoverCVEsResponse(
        candidates=[coord_module.CVECandidateResponse.from_candidate(candidate)],
        page=1,
        page_size=25,
        total=1,
        stats=coord_module.DiscoveryStatsResponse(scanned=1, resolved=1, rejected=0, returned=1),
        issues=[],
    )):
        resp = client.post("/datasets/discover-cves", json={"languages": ["python"]})
    assert resp.status_code == 200
    data = resp.json()
    # Check envelope shape
    assert "candidates" in data
    assert "page" in data
    assert "page_size" in data
    assert "total" in data
    assert "stats" in data
    assert "issues" in data
    assert data["page"] == 1
    assert data["total"] == 1
    assert isinstance(data["candidates"], list)
    assert len(data["candidates"]) == 1
    item = data["candidates"][0]
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
    """Empty body (all criteria optional) is valid and returns envelope with empty list."""
    client, c = coordinator_client
    with patch.object(c, "discover_cves", return_value=coord_module.DiscoverCVEsResponse(
        candidates=[],
        page=1,
        page_size=25,
        total=0,
        stats=coord_module.DiscoveryStatsResponse(scanned=0, resolved=0, rejected=0, returned=0),
        issues=[],
    )):
        resp = client.post("/datasets/discover-cves", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["candidates"] == []
    assert data["total"] == 0
    assert data["page"] == 1


def test_discover_cves_with_all_criteria_fields(coordinator_client):
    """All optional criteria fields are accepted without error."""
    client, c = coordinator_client
    with patch.object(c, "discover_cves", return_value=coord_module.DiscoverCVEsResponse(
        candidates=[],
        page=1,
        page_size=20,
        total=0,
        stats=coord_module.DiscoveryStatsResponse(scanned=0, resolved=0, rejected=0, returned=0),
        issues=[],
    )):
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
    with patch.object(c, "discover_cves", return_value=coord_module.DiscoverCVEsResponse(
        candidates=[coord_module.CVECandidateResponse.from_candidate(candidate)],
        page=1,
        page_size=25,
        total=1,
        stats=coord_module.DiscoveryStatsResponse(scanned=1, resolved=1, rejected=0, returned=1),
        issues=[],
    )):
        resp = client.post("/datasets/discover-cves", json={})
    assert resp.status_code == 200
    item = resp.json()["candidates"][0]
    assert item["advisory_url"] == "https://github.com/example/repo/commit/abc123def456"
    assert item["fix_commit"] == "abc123def456"


# ---------------------------------------------------------------------------
# POST /datasets/discover-cves — pagination
# ---------------------------------------------------------------------------


def test_discover_cves_pagination_slice(coordinator_client):
    """page=2, page_size=2 against 5 candidates returns the correct slice."""
    client, c = coordinator_client

    # 5 candidates with distinct CVE IDs
    candidates = [
        _make_candidate(_make_resolved_cve(f"CVE-2024-{i:05d}"))
        for i in range(1, 6)
    ]

    def fake_discover_cves(req: coord_module.DiscoverCVEsRequest):
        # Simulate what the real method does: discover returns all, then paginate
        total = len(candidates)
        start = (req.page - 1) * req.page_size
        page_slice = candidates[start : start + req.page_size]
        return coord_module.DiscoverCVEsResponse(
            candidates=[coord_module.CVECandidateResponse.from_candidate(c) for c in page_slice],
            page=req.page,
            page_size=req.page_size,
            total=total,
            stats=coord_module.DiscoveryStatsResponse(scanned=5, resolved=5, rejected=0, returned=len(page_slice)),
            issues=[],
        )

    with patch.object(c, "discover_cves", side_effect=fake_discover_cves):
        resp = client.post("/datasets/discover-cves", json={"page": 2, "page_size": 2})

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert data["page"] == 2
    assert data["page_size"] == 2
    assert len(data["candidates"]) == 2
    # Page 2 is candidates[2] and candidates[3]
    assert data["candidates"][0]["cve_id"] == "CVE-2024-00003"
    assert data["candidates"][1]["cve_id"] == "CVE-2024-00004"


def test_discover_cves_stats_returned_reflects_page_not_total(coordinator_client):
    """stats.returned mirrors the slice actually sent back, not the pre-pagination cap.

    Patches CVEDiscovery.discover() (not the coordinator method) so the real
    discover_cves() path runs and the page slicing is exercised end-to-end.
    """
    from sec_review_framework.ground_truth import cve_importer as ci

    client, _ = coordinator_client
    candidates = [
        _make_candidate(_make_resolved_cve(f"CVE-2024-{i:05d}"))
        for i in range(1, 6)
    ]
    full_result = DiscoveryResult(
        candidates=candidates,
        issues=[],
        stats=DiscoveryStats(scanned=10, resolved=5, rejected=5, returned=5),
    )
    with patch.object(ci.CVEDiscovery, "discover", return_value=full_result):
        resp = client.post(
            "/datasets/discover-cves", json={"page": 2, "page_size": 2}
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert len(data["candidates"]) == 2
    # The fix: stats.returned reflects the page slice (2), not the importer's 5.
    assert data["stats"]["returned"] == 2
    # And the other stats fields pass through unchanged.
    assert data["stats"]["scanned"] == 10
    assert data["stats"]["resolved"] == 5
    assert data["stats"]["rejected"] == 5


# ---------------------------------------------------------------------------
# POST /datasets/discover-cves — issues propagation
# ---------------------------------------------------------------------------


def test_discover_cves_propagates_issues(coordinator_client):
    """Route propagates non-empty issues list from discovery."""
    client, c = coordinator_client
    with patch.object(c, "discover_cves", return_value=coord_module.DiscoverCVEsResponse(
        candidates=[],
        page=1,
        page_size=25,
        total=0,
        stats=coord_module.DiscoveryStatsResponse(scanned=3, resolved=0, rejected=0, returned=0),
        issues=[
            coord_module.DiscoveryIssueResponse(
                level="warning",
                message="Advisory query failed for ecosystem 'pip'",
                detail="HTTPStatusError: 401 Unauthorized",
            ),
        ],
    )):
        resp = client.post("/datasets/discover-cves", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["issues"]) == 1
    issue = data["issues"][0]
    assert issue["level"] == "warning"
    assert "pip" in issue["message"]
    assert issue["detail"] == "HTTPStatusError: 401 Unauthorized"


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


def test_discover_cves_invalid_page_returns_400(coordinator_client):
    """page < 1 returns 400."""
    client, _ = coordinator_client
    resp = client.post("/datasets/discover-cves", json={"page": 0})
    assert resp.status_code == 400


def test_discover_cves_invalid_page_size_returns_400(coordinator_client):
    """page_size > 100 returns 400."""
    client, _ = coordinator_client
    resp = client.post("/datasets/discover-cves", json={"page_size": 101})
    assert resp.status_code == 400


def test_discover_cves_page_size_zero_returns_400(coordinator_client):
    """page_size = 0 returns 400."""
    client, _ = coordinator_client
    resp = client.post("/datasets/discover-cves", json={"page_size": 0})
    assert resp.status_code == 400


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
