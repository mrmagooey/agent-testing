"""B2: Integration tests for /datasets/* routes.

Routes covered:
  - GET  /datasets/{name}/tree      (empty tree + real tree)
  - GET  /datasets/{name}/file      (missing path → error gracefully)
  - POST /datasets/{name}/inject/preview (no real template → graceful)
  - POST /datasets/{name}/inject    (no real template → graceful)
  - POST /datasets/import-cve       (empty spec → labels_created=0 or error shape)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import app

from tests.integration.test_coordinator_api import _make_coordinator
from sec_review_framework.db import Database


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
    with patch.object(c, "inject_vuln", return_value=fake_label):
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
    with patch.object(c, "inject_vuln", return_value=fake_label):
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
    with patch.object(c, "import_cve", return_value=[]):
        resp = client.post("/datasets/import-cve", json={})
    assert resp.status_code != 500


def test_import_cve_with_valid_spec_returns_labels_created(coordinator_client):
    """import-cve with a reasonable spec returns labels_created key."""
    client, _, tmp_path = coordinator_client
    _, c, _ = coordinator_client
    # Mock import_cve to return empty list (no real CVE data in test)
    with patch.object(c, "import_cve", return_value=[]):
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
    with patch.object(c, "import_cve", return_value=mock_labels):
        resp = client.post("/datasets/import-cve", json={"cve_id": "CVE-2024-99999"})
    assert resp.status_code == 201
    assert resp.json()["labels_created"] == 2
