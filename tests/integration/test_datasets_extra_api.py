"""Phase 5: Integration tests for dataset API endpoints.

Routes covered:
  - POST /datasets/discover-cves      (happy path, validation, edge cases)
  - POST /datasets/import-cve         (happy path, 400, mocked labels count)
  - GET  /datasets/resolve-cve        (happy path, 404, missing param)
  - POST /datasets/{name}/inject/preview  (happy, 404 dataset, 400 missing fields)
  - POST /datasets/{name}/inject      (happy, 404 dataset, 404 template)
  - GET  /datasets/{name}/file        (happy, 404, path traversal, start/end params)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import CVECandidateResponse, app
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.db import Database
from sec_review_framework.ground_truth.cve_importer import (
    CVECandidate as _CVECandidate,
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
                yield client, c, tmp_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolved_cve(cve_id: str = "CVE-2024-11111") -> ResolvedCVE:
    return ResolvedCVE(
        cve_id=cve_id,
        ghsa_id=None,
        description="SQL injection in login handler",
        cwe_ids=["CWE-89"],
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        cvss_score=8.1,
        repo_url="https://github.com/example/vuln-repo",
        fix_commit_sha="deadbeef1234",
        affected_files=["app/db.py"],
        lines_changed=15,
        language="python",
        repo_kloc=12.5,
        published_date="2024-03-01",
        source="ghsa",
    )


def _candidate(resolved: ResolvedCVE | None = None) -> _CVECandidate:
    r = resolved or _resolved_cve()
    return _CVECandidate(
        resolved=r,
        score=0.75,
        score_breakdown={"patch_size": 0.8, "severity": 0.7},
        importable=bool(r.fix_commit_sha),
    )


# ---------------------------------------------------------------------------
# POST /datasets/discover-cves — happy path
# ---------------------------------------------------------------------------


def test_discover_cves_happy_path_returns_list(coordinator_client):
    client, c, _ = coordinator_client
    r = _resolved_cve()
    cand = _candidate(r)
    with patch.object(c, "discover_cves", return_value=[CVECandidateResponse.from_candidate(cand)]):
        resp = client.post("/datasets/discover-cves", json={"languages": ["python"]})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    item = data[0]
    assert item["cve_id"] == "CVE-2024-11111"
    assert item["vuln_class"] == "sqli"
    assert item["severity"] == "high"
    assert item["importable"] is True


def test_discover_cves_empty_criteria_returns_empty_list(coordinator_client):
    client, c, _ = coordinator_client
    with patch.object(c, "discover_cves", return_value=[]):
        resp = client.post("/datasets/discover-cves", json={})
    assert resp.status_code == 200
    assert resp.json() == []


def test_discover_cves_all_optional_fields_accepted(coordinator_client):
    client, c, _ = coordinator_client
    with patch.object(c, "discover_cves", return_value=[]):
        resp = client.post(
            "/datasets/discover-cves",
            json={
                "languages": ["python", "javascript"],
                "vuln_classes": ["sqli", "xss"],
                "severities": ["high", "critical"],
                "patch_size_min": 1,
                "patch_size_max": 200,
                "date_from": "2024-01-01",
                "date_to": "2024-12-31",
                "max_results": 10,
            },
        )
    assert resp.status_code == 200


def test_discover_cves_schema_has_required_fields(coordinator_client):
    client, c, _ = coordinator_client
    r = _resolved_cve()
    cand = _candidate(r)
    with patch.object(c, "discover_cves", return_value=[CVECandidateResponse.from_candidate(cand)]):
        resp = client.post("/datasets/discover-cves", json={})
    assert resp.status_code == 200
    item = resp.json()[0]
    for field in ("cve_id", "score", "vuln_class", "severity", "language", "repo",
                  "files_changed", "lines_changed", "importable"):
        assert field in item, f"Missing field: {field}"


def test_discover_cves_wrong_field_type_returns_422(coordinator_client):
    client, _, _ = coordinator_client
    resp = client.post("/datasets/discover-cves", json={"patch_size_min": "not-an-int"})
    assert resp.status_code == 422


def test_discover_cves_missing_body_returns_422(coordinator_client):
    client, _, _ = coordinator_client
    resp = client.post("/datasets/discover-cves")
    assert resp.status_code == 422


def test_discover_cves_multiple_candidates_returned(coordinator_client):
    client, c, _ = coordinator_client
    candidates = [
        CVECandidateResponse.from_candidate(_candidate(_resolved_cve("CVE-2024-0001"))),
        CVECandidateResponse.from_candidate(_candidate(_resolved_cve("CVE-2024-0002"))),
        CVECandidateResponse.from_candidate(_candidate(_resolved_cve("CVE-2024-0003"))),
    ]
    with patch.object(c, "discover_cves", return_value=candidates):
        resp = client.post("/datasets/discover-cves", json={"max_results": 3})
    assert resp.status_code == 200
    assert len(resp.json()) == 3


# ---------------------------------------------------------------------------
# POST /datasets/import-cve — happy path + errors
# ---------------------------------------------------------------------------


def test_import_cve_returns_201_with_labels_created(coordinator_client):
    client, c, _ = coordinator_client

    from sec_review_framework.data.evaluation import GroundTruthLabel

    fake_label = MagicMock(spec=GroundTruthLabel)
    with patch.object(c, "import_cve", return_value=[fake_label, fake_label]):
        resp = client.post("/datasets/import-cve", json={"cve_id": "CVE-2024-99999"})
    assert resp.status_code == 201
    assert resp.json()["labels_created"] == 2


def test_import_cve_zero_labels_when_mocked_empty(coordinator_client):
    client, c, _ = coordinator_client
    with patch.object(c, "import_cve", return_value=[]):
        resp = client.post("/datasets/import-cve", json={"cve_id": "CVE-2024-00001"})
    assert resp.status_code == 201
    assert resp.json()["labels_created"] == 0


def test_import_cve_bad_spec_returns_400(coordinator_client):
    client, c, _ = coordinator_client
    with patch.object(c, "import_cve", side_effect=HTTPException(status_code=400, detail="bad spec")):
        resp = client.post("/datasets/import-cve", json={})
    assert resp.status_code == 400


def test_import_cve_git_failure_returns_502(coordinator_client):
    client, c, _ = coordinator_client
    with patch.object(c, "import_cve", side_effect=HTTPException(status_code=502, detail="git clone failed")):
        resp = client.post("/datasets/import-cve", json={"cve_id": "CVE-2024-BAD"})
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# GET /datasets/resolve-cve — happy path + 404 + missing param
# ---------------------------------------------------------------------------


def test_resolve_cve_happy_path_returns_candidate(coordinator_client):
    client, c, _ = coordinator_client
    r = _resolved_cve("CVE-2024-55555")
    with patch.object(c, "resolve_cve", return_value=CVECandidateResponse.from_candidate(_candidate(r))):
        resp = client.get("/datasets/resolve-cve?id=CVE-2024-55555")
    assert resp.status_code == 200
    data = resp.json()
    assert data["cve_id"] == "CVE-2024-55555"
    assert data["importable"] is True


def test_resolve_cve_not_found_returns_404(coordinator_client):
    client, c, _ = coordinator_client
    with patch.object(c, "resolve_cve", side_effect=HTTPException(status_code=404, detail="not found")):
        resp = client.get("/datasets/resolve-cve?id=CVE-NOTEXIST")
    assert resp.status_code == 404


def test_resolve_cve_missing_id_param_returns_422(coordinator_client):
    client, _, _ = coordinator_client
    resp = client.get("/datasets/resolve-cve")
    assert resp.status_code == 422


def test_resolve_cve_response_has_correct_shape(coordinator_client):
    client, c, _ = coordinator_client
    r = _resolved_cve("CVE-2024-77777")
    with patch.object(c, "resolve_cve", return_value=CVECandidateResponse.from_candidate(_candidate(r))):
        resp = client.get("/datasets/resolve-cve?id=CVE-2024-77777")
    assert resp.status_code == 200
    data = resp.json()
    for field in ("cve_id", "score", "vuln_class", "severity", "language", "repo", "importable"):
        assert field in data, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# POST /datasets/{name}/inject/preview
# ---------------------------------------------------------------------------


def test_inject_preview_missing_dataset_returns_404(coordinator_client):
    client, _, _ = coordinator_client
    resp = client.post(
        "/datasets/nonexistent-dataset/inject/preview",
        json={"template_id": "sqli", "target_file": "src/app.py"},
    )
    assert resp.status_code == 404


def test_inject_preview_mocked_happy_path(coordinator_client):
    client, c, tmp_path = coordinator_client
    # Create a dataset with a repo dir so _parse_injection_request passes
    repo_dir = tmp_path / "storage" / "datasets" / "preview-ds" / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "app.py").write_text("x = 1\n")

    preview_result = {"diff": "--- a/app.py\n+++ b/app.py\n", "line_start": 1, "line_end": 3}
    with patch.object(c, "preview_injection", return_value=preview_result):
        resp = client.post(
            "/datasets/preview-ds/inject/preview",
            json={"template_id": "sqli", "target_file": "app.py"},
        )
    assert resp.status_code == 200
    assert "diff" in resp.json()


def test_inject_preview_missing_required_fields_returns_400(coordinator_client):
    client, _, tmp_path = coordinator_client
    repo_dir = tmp_path / "storage" / "datasets" / "badfields-ds" / "repo"
    repo_dir.mkdir(parents=True)
    # Missing template_id and target_file triggers 400 from _parse_injection_request
    resp = client.post(
        "/datasets/badfields-ds/inject/preview",
        json={},
    )
    assert resp.status_code == 400


def test_inject_preview_path_traversal_rejected(coordinator_client):
    client, _, tmp_path = coordinator_client
    repo_dir = tmp_path / "storage" / "datasets" / "traverse-ds" / "repo"
    repo_dir.mkdir(parents=True)
    resp = client.post(
        "/datasets/traverse-ds/inject/preview",
        json={"template_id": "sqli", "target_file": "../../etc/passwd"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /datasets/{name}/inject
# ---------------------------------------------------------------------------


def test_inject_missing_dataset_returns_404(coordinator_client):
    client, _, _ = coordinator_client
    resp = client.post(
        "/datasets/no-such-ds/inject",
        json={"template_id": "sqli", "target_file": "app.py"},
    )
    assert resp.status_code == 404


def test_inject_mocked_happy_path_returns_201(coordinator_client):
    client, c, tmp_path = coordinator_client
    repo_dir = tmp_path / "storage" / "datasets" / "inject-ds" / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "main.py").write_text("x = 1\n")

    fake_label = MagicMock()
    fake_label.id = "lbl-injected-001"
    fake_label.file_path = "main.py"
    with patch.object(c, "inject_vuln", return_value=fake_label):
        resp = client.post(
            "/datasets/inject-ds/inject",
            json={"template_id": "sqli", "target_file": "main.py"},
        )
    assert resp.status_code == 201
    data = resp.json()
    assert data["label_id"] == "lbl-injected-001"
    assert data["file_path"] == "main.py"


def test_inject_unknown_template_returns_404(coordinator_client):
    client, c, tmp_path = coordinator_client
    repo_dir = tmp_path / "storage" / "datasets" / "tmpl-ds" / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "app.py").write_text("x = 1\n")

    with patch.object(c, "inject_vuln", side_effect=HTTPException(status_code=404, detail="Template not found")):
        resp = client.post(
            "/datasets/tmpl-ds/inject",
            json={"template_id": "nonexistent-template", "target_file": "app.py"},
        )
    assert resp.status_code == 404


def test_inject_missing_fields_returns_400(coordinator_client):
    client, _, tmp_path = coordinator_client
    repo_dir = tmp_path / "storage" / "datasets" / "missing-ds" / "repo"
    repo_dir.mkdir(parents=True)
    resp = client.post(
        "/datasets/missing-ds/inject",
        json={},  # Missing template_id and target_file
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /datasets/{name}/file
# ---------------------------------------------------------------------------


def test_get_file_happy_path_returns_content(coordinator_client):
    client, _, tmp_path = coordinator_client
    ds_dir = tmp_path / "storage" / "datasets" / "read-ds"
    ds_dir.mkdir(parents=True)
    (ds_dir / "hello.py").write_text("print('hello world')\n")

    resp = client.get("/datasets/read-ds/file?path=hello.py")
    assert resp.status_code == 200
    data = resp.json()
    assert "content" in data
    assert "hello world" in data["content"]
    assert data["path"] == "hello.py"


def test_get_file_returns_metadata_fields(coordinator_client):
    client, _, tmp_path = coordinator_client
    ds_dir = tmp_path / "storage" / "datasets" / "meta-ds"
    ds_dir.mkdir(parents=True)
    (ds_dir / "code.py").write_text("x = 1\n")

    resp = client.get("/datasets/meta-ds/file?path=code.py")
    assert resp.status_code == 200
    data = resp.json()
    for field in ("path", "content", "language", "line_count", "size_bytes"):
        assert field in data, f"Missing field: {field}"
    assert data["language"] == "python"


def test_get_file_nonexistent_file_returns_404(coordinator_client):
    client, _, tmp_path = coordinator_client
    ds_dir = tmp_path / "storage" / "datasets" / "nf-ds"
    ds_dir.mkdir(parents=True)

    resp = client.get("/datasets/nf-ds/file?path=does-not-exist.py")
    assert resp.status_code == 404


def test_get_file_path_traversal_returns_400(coordinator_client):
    client, _, tmp_path = coordinator_client
    ds_dir = tmp_path / "storage" / "datasets" / "trav-ds"
    ds_dir.mkdir(parents=True)

    resp = client.get("/datasets/trav-ds/file?path=../../etc/passwd")
    assert resp.status_code == 400


def test_get_file_missing_path_param_returns_422(coordinator_client):
    client, _, _ = coordinator_client
    resp = client.get("/datasets/some-ds/file")
    assert resp.status_code == 422


def test_get_file_with_start_end_returns_highlight_fields(coordinator_client):
    client, _, tmp_path = coordinator_client
    ds_dir = tmp_path / "storage" / "datasets" / "hl-ds"
    ds_dir.mkdir(parents=True)
    (ds_dir / "target.py").write_text("line1\nline2\nline3\n")

    resp = client.get("/datasets/hl-ds/file?path=target.py&start=1&end=2")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("highlight_start") == 1
    assert data.get("highlight_end") == 2


def test_get_file_absolute_path_rejected(coordinator_client):
    client, _, _ = coordinator_client
    resp = client.get("/datasets/some-ds/file?path=/etc/passwd")
    assert resp.status_code == 400
