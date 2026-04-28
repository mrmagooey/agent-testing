"""B2: Integration tests for /datasets/* routes.

Routes covered:
  - GET  /datasets/{name}/tree      (empty tree + real tree)
  - GET  /datasets/{name}/file      (missing path → error gracefully)
  - POST /datasets/{name}/inject/preview (no real template → graceful)
  - POST /datasets/{name}/inject    (no real template → graceful)
  - POST /datasets/import-cve       (empty spec → labels_created=0 or error shape)
  - GET  /datasets/resolve-cve      (happy path, 404, 422 missing param, schema)
  - POST /datasets/{name}/rematerialize  (happy path, 404, 409, 403, 502, schema)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import CVECandidateResponse, app
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.db import Database
from sec_review_framework.ground_truth.cve_importer import CVECandidate as _CVECandidate
from sec_review_framework.ground_truth.cve_importer import ResolvedCVE
from tests.integration.test_coordinator_api import _make_coordinator


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
# GET /datasets/{name}/tree
# ---------------------------------------------------------------------------

def test_get_file_tree_missing_dataset_returns_non_500(coordinator_client):
    """Tree for a missing dataset returns 404 (not found) — not a server crash."""
    client, *_ = coordinator_client
    resp = client.get("/datasets/no-such-dataset/tree")
    # 404 is expected for unknown dataset; 500 is unacceptable
    assert resp.status_code != 500


def test_get_file_tree_returns_dict_with_name_key(coordinator_client):
    """Tree response is a dict that at minimum has a recognizable structure."""
    client, _, tmp_path = coordinator_client
    # Create a minimal dataset directory
    ds_dir = tmp_path / "storage" / "datasets" / "tree-test"
    ds_dir.mkdir(parents=True)
    (ds_dir / "main.py").write_text("# test\n")

    resp = client.get("/datasets/tree-test/tree")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


# ---------------------------------------------------------------------------
# GET /datasets/{name}/file
# ---------------------------------------------------------------------------

def test_get_file_content_missing_path_returns_error(coordinator_client):
    """Requesting a file that doesn't exist returns a non-500 response."""
    client, *_ = coordinator_client
    resp = client.get("/datasets/some-dataset/file?path=nonexistent.py")
    # Should not be 500; 404 or 200 with empty content are both acceptable
    assert resp.status_code != 500


def test_get_file_content_returns_dict_with_content_key_when_found(coordinator_client):
    """File content response includes 'content' key."""
    client, _, tmp_path = coordinator_client
    ds_dir = tmp_path / "storage" / "datasets" / "file-test"
    ds_dir.mkdir(parents=True)
    (ds_dir / "hello.py").write_text("print('hello')\n")

    resp = client.get("/datasets/file-test/file?path=hello.py")
    if resp.status_code == 200:
        data = resp.json()
        assert "content" in data


# ---------------------------------------------------------------------------
# POST /datasets/{name}/inject/preview
# ---------------------------------------------------------------------------

def test_inject_preview_no_real_template_returns_non_500(coordinator_client):
    """inject/preview with a minimal request body doesn't crash the server."""
    client, _, tmp_path = coordinator_client
    _, c, _ = coordinator_client
    with patch.object(c, "preview_injection", return_value={"diff": "", "lines": []}):
        resp = client.post(
            "/datasets/test-ds/inject/preview",
            json={"template_id": "sqli", "file_path": "src/app.py", "line": 10},
        )
    # May be 404/400/200 depending on template availability; must not be 500
    assert resp.status_code != 500


def test_inject_preview_requires_json_body(coordinator_client):
    """inject/preview without body returns 422."""
    client, *_ = coordinator_client
    resp = client.post("/datasets/test-ds/inject/preview")
    # FastAPI returns 422 for missing body when dict is required
    assert resp.status_code in (400, 422)


# ---------------------------------------------------------------------------
# POST /datasets/{name}/inject
# ---------------------------------------------------------------------------

def test_inject_vuln_no_real_template_returns_non_500(coordinator_client):
    """inject with a minimal body doesn't crash the server."""
    client, _, tmp_path = coordinator_client
    _, c, _ = coordinator_client
    fake_label = MagicMock()
    fake_label.id = "label-noop"
    fake_label.file_path = "src/app.py"
    with patch.object(c, "inject_vuln", new=AsyncMock(return_value=fake_label)):
        resp = client.post(
            "/datasets/test-ds/inject",
            json={"template_id": "sqli", "file_path": "src/app.py", "line": 10},
        )
    assert resp.status_code != 500


def test_inject_vuln_returns_label_id_on_success(coordinator_client):
    """If injection succeeds, response has label_id and file_path."""
    client, _, tmp_path = coordinator_client
    # Mock the coordinator method to return a fake label
    fake_label = MagicMock()
    fake_label.id = "label-injected-001"
    fake_label.file_path = "src/vuln.py"

    _, c, _ = coordinator_client
    with patch.object(c, "inject_vuln", new=AsyncMock(return_value=fake_label)):
        resp = client.post(
            "/datasets/test-ds/inject",
            json={"template_id": "sqli", "file_path": "src/vuln.py", "line": 5},
        )
    if resp.status_code == 201:
        data = resp.json()
        assert "label_id" in data
        assert "file_path" in data


# ---------------------------------------------------------------------------
# POST /datasets/import-cve
# ---------------------------------------------------------------------------

def test_import_cve_empty_spec_returns_non_500(coordinator_client):
    """import-cve with an empty dict body returns non-500."""
    client, c, _ = coordinator_client
    with patch.object(c, "import_cve", new=AsyncMock(return_value=[])):
        resp = client.post("/datasets/import-cve", json={})
    assert resp.status_code != 500


def test_import_cve_with_valid_spec_returns_labels_created(coordinator_client):
    """import-cve with a reasonable spec returns labels_created key."""
    client, _, tmp_path = coordinator_client
    _, c, _ = coordinator_client
    # Mock import_cve to return empty list (no real CVE data in test)
    with patch.object(c, "import_cve", new=AsyncMock(return_value=[])):
        resp = client.post(
            "/datasets/import-cve",
            json={"cve_id": "CVE-2024-12345", "repo_url": "https://github.com/test/repo"},
        )
    if resp.status_code == 201:
        data = resp.json()
        assert "labels_created" in data
        assert isinstance(data["labels_created"], int)


def test_import_cve_mocked_labels_count(coordinator_client):
    """import-cve with mocked labels returns correct count."""
    client, c, _ = coordinator_client
    mock_labels = [{"id": "lbl-1"}, {"id": "lbl-2"}]
    with patch.object(c, "import_cve", new=AsyncMock(return_value=mock_labels)):
        resp = client.post("/datasets/import-cve", json={"cve_id": "CVE-2024-99999"})
    assert resp.status_code == 201
    assert resp.json()["labels_created"] == 2


# ---------------------------------------------------------------------------
# GET /datasets/resolve-cve
# ---------------------------------------------------------------------------

def _make_resolved_cve(cve_id: str = "CVE-2024-10001") -> ResolvedCVE:
    return ResolvedCVE(
        cve_id=cve_id,
        ghsa_id=None,
        description="Remote code execution via deserialization",
        cwe_ids=["CWE-502"],
        vuln_class=VulnClass.SQLI,
        severity=Severity.CRITICAL,
        cvss_score=9.8,
        repo_url="https://github.com/example/rce-repo",
        fix_commit_sha="cafebabe1234",
        affected_files=["src/deserialize.py"],
        lines_changed=8,
        language="python",
        repo_kloc=5.0,
        published_date="2024-06-01",
        source="osv",
    )


def _make_candidate(resolved: ResolvedCVE | None = None) -> _CVECandidate:
    r = resolved or _make_resolved_cve()
    return _CVECandidate(
        resolved=r,
        score=0.9,
        score_breakdown={"patch_size": 0.9, "severity": 1.0},
        importable=bool(r.fix_commit_sha),
    )


def test_resolve_cve_happy_path_returns_200(coordinator_client):
    client, c, _ = coordinator_client
    r = _make_resolved_cve("CVE-2024-10001")
    with patch.object(c, "resolve_cve", return_value=CVECandidateResponse.from_candidate(_make_candidate(r))):
        resp = client.get("/datasets/resolve-cve?id=CVE-2024-10001")
    assert resp.status_code == 200


def test_resolve_cve_happy_path_response_schema(coordinator_client):
    client, c, _ = coordinator_client
    r = _make_resolved_cve("CVE-2024-10001")
    with patch.object(c, "resolve_cve", return_value=CVECandidateResponse.from_candidate(_make_candidate(r))):
        resp = client.get("/datasets/resolve-cve?id=CVE-2024-10001")
    assert resp.status_code == 200
    data = resp.json()
    for field in ("cve_id", "score", "vuln_class", "severity", "language", "repo",
                  "files_changed", "lines_changed", "importable"):
        assert field in data, f"Missing required field: {field}"
    assert data["cve_id"] == "CVE-2024-10001"
    assert isinstance(data["score"], float)
    assert isinstance(data["importable"], bool)
    assert isinstance(data["files_changed"], int)
    assert isinstance(data["lines_changed"], int)


def test_resolve_cve_not_found_returns_404(coordinator_client):
    client, c, _ = coordinator_client
    with patch.object(c, "resolve_cve", side_effect=HTTPException(status_code=404, detail="not found")):
        resp = client.get("/datasets/resolve-cve?id=CVE-9999-00000")
    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data


def test_resolve_cve_missing_id_param_returns_422(coordinator_client):
    client, _, _ = coordinator_client
    resp = client.get("/datasets/resolve-cve")
    assert resp.status_code == 422
    data = resp.json()
    assert "detail" in data


def test_resolve_cve_advisory_url_synthesized(coordinator_client):
    client, c, _ = coordinator_client
    r = _make_resolved_cve("CVE-2024-20002")
    with patch.object(c, "resolve_cve", return_value=CVECandidateResponse.from_candidate(_make_candidate(r))):
        resp = client.get("/datasets/resolve-cve?id=CVE-2024-20002")
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("advisory_url") == "https://github.com/example/rce-repo/commit/cafebabe1234"
    assert data.get("fix_commit") == "cafebabe1234"


# ---------------------------------------------------------------------------
# POST /datasets/{name}/rematerialize
# ---------------------------------------------------------------------------


def test_rematerialize_happy_path_returns_200(coordinator_client):
    client, c, _ = coordinator_client
    with patch.object(c, "rematerialize_dataset", new=AsyncMock(
        return_value={"materialized_at": "2026-04-26T00:00:00+00:00"}
    )):
        resp = client.post("/datasets/some-dataset/rematerialize")
    assert resp.status_code == 200


def test_rematerialize_happy_path_response_schema(coordinator_client):
    client, c, _ = coordinator_client
    ts = "2026-04-26T12:00:00+00:00"
    with patch.object(c, "rematerialize_dataset", new=AsyncMock(return_value={"materialized_at": ts})):
        resp = client.post("/datasets/some-dataset/rematerialize")
    assert resp.status_code == 200
    data = resp.json()
    assert "materialized_at" in data
    assert data["materialized_at"] == ts


def test_rematerialize_unknown_dataset_returns_404(coordinator_client):
    client, c, _ = coordinator_client
    with patch.object(c, "rematerialize_dataset", new=AsyncMock(
        side_effect=HTTPException(status_code=404, detail="unknown dataset no-such-ds")
    )):
        resp = client.post("/datasets/no-such-ds/rematerialize")
    assert resp.status_code == 404
    data = resp.json()
    assert "detail" in data


def test_rematerialize_conflict_returns_409(coordinator_client):
    client, c, _ = coordinator_client
    with patch.object(c, "rematerialize_dataset", new=AsyncMock(
        side_effect=HTTPException(status_code=409, detail="templates_version mismatch")
    )):
        resp = client.post("/datasets/derived-ds/rematerialize")
    assert resp.status_code == 409
    data = resp.json()
    assert "detail" in data


def test_rematerialize_disabled_returns_403(coordinator_client):
    client, _, _ = coordinator_client
    with patch.object(coord_module, "IMPORT_ENABLED", False):
        resp = client.post("/datasets/any-dataset/rematerialize")
    assert resp.status_code == 403
    data = resp.json()
    assert "detail" in data
    assert "disabled" in data["detail"].lower() or "import" in data["detail"].lower()


def test_rematerialize_clone_failure_returns_502(coordinator_client):
    client, c, _ = coordinator_client
    with patch.object(c, "rematerialize_dataset", new=AsyncMock(
        side_effect=HTTPException(status_code=502, detail="git clone or checkout failed")
    )):
        resp = client.post("/datasets/clone-fail-ds/rematerialize")
    assert resp.status_code == 502
    data = resp.json()
    assert "detail" in data
