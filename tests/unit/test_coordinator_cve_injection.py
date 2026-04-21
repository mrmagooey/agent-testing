"""Unit tests for Coordinator CVE import and vulnerability injection methods."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import app, ExperimentCoordinator
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator

# Path to test fixture templates shipped with the test suite
FIXTURE_TEMPLATES_DIR = Path(__file__).parent.parent / "fixtures" / "templates"
FIXTURE_TEMPLATE_ID = "test_sqli_format_string"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0),
        }
    )
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        default_cap=4,
    )


def _make_label(idx: int = 0) -> GroundTruthLabel:
    return GroundTruthLabel(
        id=f"lbl-{idx:04d}",
        dataset_version="v1",
        file_path="app/views.py",
        line_start=10,
        line_end=12,
        cwe_id="CWE-89",
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        description="SQL injection via user input",
        source=GroundTruthSource.CVE_PATCH,
        source_ref="CVE-2023-00001",
        confidence="confirmed",
        created_at=datetime.utcnow(),
    )


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
# import_cve tests
# ---------------------------------------------------------------------------


def test_import_cve_persists_labels_json(coordinator_client):
    """import_cve writes two GroundTruthLabel records to labels.json."""
    client, c, tmp_path = coordinator_client
    labels = [_make_label(0), _make_label(1)]
    with patch.object(c, "_build_cve_importer") as mock_builder:
        mock_importer = MagicMock()
        mock_importer.import_from_spec.return_value = labels
        mock_builder.return_value = mock_importer

        valid_spec = {
            "cve_id": "CVE-2023-00001",
            "repo_url": "https://github.com/owner/repo",
            "fix_commit_sha": "abc123",
            "dataset_name": "test-persist",
            "cwe_id": "CWE-89",
            "vuln_class": "sqli",
            "severity": "high",
            "description": "SQL injection test",
        }
        resp = client.post("/datasets/import-cve", json=valid_spec)

    assert resp.status_code == 201
    labels_file = tmp_path / "storage" / "datasets" / "test-persist" / "labels.json"
    assert labels_file.exists(), "labels.json was not created"
    stored = json.loads(labels_file.read_text())
    assert isinstance(stored, list)
    assert len(stored) == 2


def test_import_cve_appends_to_existing_labels(coordinator_client):
    """import_cve appends to an existing labels.json (3 total from 1 + 2)."""
    client, c, tmp_path = coordinator_client

    # Pre-seed one label
    ds_dir = tmp_path / "storage" / "datasets" / "test-append"
    ds_dir.mkdir(parents=True)
    existing_label = _make_label(99)
    (ds_dir / "labels.json").write_text(
        json.dumps([existing_label.model_dump(mode="json")], default=str)
    )

    new_labels = [_make_label(0), _make_label(1)]
    with patch.object(c, "_build_cve_importer") as mock_builder:
        mock_importer = MagicMock()
        mock_importer.import_from_spec.return_value = new_labels
        mock_builder.return_value = mock_importer

        valid_spec = {
            "cve_id": "CVE-2023-00002",
            "repo_url": "https://github.com/owner/repo",
            "fix_commit_sha": "abc123",
            "dataset_name": "test-append",
            "cwe_id": "CWE-89",
            "vuln_class": "sqli",
            "severity": "high",
            "description": "SQL injection test",
        }
        resp = client.post("/datasets/import-cve", json=valid_spec)

    assert resp.status_code == 201
    stored = json.loads((ds_dir / "labels.json").read_text())
    assert len(stored) == 3


def test_import_cve_invalid_spec_raises_400(coordinator_client):
    """import_cve with an incomplete spec returns 400."""
    client, *_ = coordinator_client
    # Missing repo_url, fix_commit_sha, dataset_name, etc.
    resp = client.post("/datasets/import-cve", json={"cve_id": "CVE-2023-99999"})
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# inject_vuln tests
# ---------------------------------------------------------------------------


def test_inject_vuln_unknown_dataset_raises_404(coordinator_client):
    """inject_vuln for a non-existent dataset returns 404."""
    client, *_ = coordinator_client
    resp = client.post(
        "/datasets/no-such-dataset/inject",
        json={"template_id": FIXTURE_TEMPLATE_ID, "target_file": "app.py"},
    )
    assert resp.status_code == 404


def test_inject_vuln_unknown_template_raises_404(coordinator_client):
    """inject_vuln with an unknown template_id returns 404."""
    client, c, tmp_path = coordinator_client

    # Create the dataset repo dir so the dataset-existence check passes
    repo_dir = tmp_path / "storage" / "datasets" / "ds-tmpl-test" / "repo"
    repo_dir.mkdir(parents=True)

    # Point VulnInjector at fixture templates (has FIXTURE_TEMPLATE_ID)
    from sec_review_framework.ground_truth.vuln_injector import VulnInjector
    injector = VulnInjector(templates_root=FIXTURE_TEMPLATES_DIR)
    c._vuln_injector = injector

    resp = client.post(
        "/datasets/ds-tmpl-test/inject",
        json={"template_id": "nonexistent_template_xyz", "target_file": "app.py"},
    )
    assert resp.status_code == 404


def test_inject_path_traversal_rejected(coordinator_client):
    """target_file resolving outside the dataset repo returns 400."""
    client, c, tmp_path = coordinator_client

    repo_dir = tmp_path / "storage" / "datasets" / "ds-traversal" / "repo"
    repo_dir.mkdir(parents=True)

    from sec_review_framework.ground_truth.vuln_injector import VulnInjector
    c._vuln_injector = VulnInjector(templates_root=FIXTURE_TEMPLATES_DIR)

    for endpoint in ("/datasets/ds-traversal/inject", "/datasets/ds-traversal/inject/preview"):
        resp = client.post(
            endpoint,
            json={"template_id": FIXTURE_TEMPLATE_ID, "target_file": "../../../../etc/passwd"},
        )
        assert resp.status_code == 400, f"{endpoint} did not reject path traversal"


# ---------------------------------------------------------------------------
# preview_injection tests
# ---------------------------------------------------------------------------


def test_preview_injection_does_not_modify_files(coordinator_client):
    """preview_injection leaves the target file unchanged on disk."""
    client, c, tmp_path = coordinator_client

    repo_dir = tmp_path / "storage" / "datasets" / "ds-preview" / "repo"
    repo_dir.mkdir(parents=True)
    target = repo_dir / "views.py"
    original_content = "def search(request):\n    pass\n"
    target.write_text(original_content)

    from sec_review_framework.ground_truth.vuln_injector import VulnInjector
    injector = VulnInjector(templates_root=FIXTURE_TEMPLATES_DIR)
    c._vuln_injector = injector

    resp = client.post(
        "/datasets/ds-preview/inject/preview",
        json={"template_id": FIXTURE_TEMPLATE_ID, "target_file": "views.py"},
    )
    assert resp.status_code == 200
    # File must be unmodified
    assert target.read_text() == original_content


def test_preview_injection_returns_diff(coordinator_client):
    """preview_injection response includes a non-empty unified_diff."""
    client, c, tmp_path = coordinator_client

    repo_dir = tmp_path / "storage" / "datasets" / "ds-preview-diff" / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "views.py").write_text("def search(request):\n    pass\n")

    from sec_review_framework.ground_truth.vuln_injector import VulnInjector
    injector = VulnInjector(templates_root=FIXTURE_TEMPLATES_DIR)
    c._vuln_injector = injector

    resp = client.post(
        "/datasets/ds-preview-diff/inject/preview",
        json={"template_id": FIXTURE_TEMPLATE_ID, "target_file": "views.py"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "unified_diff" in data
    assert len(data["unified_diff"]) > 0
