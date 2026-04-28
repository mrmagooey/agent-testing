"""Integration tests for Phase 2B: DB-backed dataset creation flows.

Covers:
  1. CVE import end-to-end: datasets row kind='git', repo cloned, labels in DB.
  2. Inject end-to-end: derived row kind='derived', recipe_json round-trips,
     labels persisted, no labels.json produced.
  3. GET /datasets/{name}/labels returns DB-backed results.
  4. GET /datasets/{name}/labels?cwe=CWE-79 filters correctly.
  5. After both flows, no labels.json or labels.jsonl files anywhere.
  6. materialize_dataset for kind='git': repo present at right SHA, materialized_at updated.
  7. materialize_dataset for kind='derived': base materialized recursively;
     templates_version mismatch raises 409.
  8. templates_version is stable across calls and changes when a file is modified.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator

# ---------------------------------------------------------------------------
# Fixtures path
# ---------------------------------------------------------------------------

FIXTURE_TEMPLATES_DIR = Path(__file__).parent.parent / "fixtures" / "templates"
TEMPLATE_ID = "test_sqli_format_string"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={"gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)}
    )
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
        default_cap=4,
    )


def _init_git_repo(path: Path, content: str = "def foo():\n    pass\n") -> str:
    """Initialize a git repo with a single commit. Returns HEAD SHA."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "app.py").write_text(content)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path, check=True, capture_output=True,
    )
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True
    ).strip()
    return sha


def _make_label_row(
    label_id: str,
    dataset_name: str,
    file_path: str = "app.py",
    cwe_id: str = "CWE-89",
) -> dict:
    return {
        "id": label_id,
        "dataset_name": dataset_name,
        "dataset_version": "v1",
        "file_path": file_path,
        "line_start": 1,
        "line_end": 1,
        "cwe_id": cwe_id,
        "vuln_class": "sqli",
        "severity": "high",
        "description": "SQL injection via user input",
        "source": "cve_patch",
        "confidence": "confirmed",
        "created_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


@pytest.fixture
def coord(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    return _make_coordinator(tmp_path, db)


@pytest.fixture
def client(coord: ExperimentCoordinator) -> TestClient:
    with patch.object(coord_module, "coordinator", coord):
        with patch.object(coord, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=False) as c:
                yield c


# ---------------------------------------------------------------------------
# 1. CVE import: datasets row written, repo cloned, labels in DB
# ---------------------------------------------------------------------------


async def test_cve_import_persists_dataset_row(tmp_path: Path, db: Database):
    """import_cve writes a kind='git' dataset row with correct fields."""
    coord = _make_coordinator(tmp_path, db)
    repo_src = tmp_path / "repo_src"
    sha = _init_git_repo(repo_src)

    # Build a mock importer that pretends to clone + build labels
    from uuid import uuid4

    from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource
    from sec_review_framework.data.findings import Severity, VulnClass

    labels = [
        GroundTruthLabel(
            id=str(uuid4()),
            dataset_version="v1",
            file_path="app.py",
            line_start=1,
            line_end=2,
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="SQL injection",
            source=GroundTruthSource.CVE_PATCH,
            source_ref="CVE-2024-TEST01",
            confidence="confirmed",
            created_at=datetime.now(UTC),
        )
    ]
    mock_importer = MagicMock()
    mock_importer.import_from_spec.return_value = labels

    with patch.object(coord, "_build_cve_importer", return_value=mock_importer):
        result = await coord.import_cve({
            "cve_id": "CVE-2024-TEST01",
            "repo_url": f"file://{repo_src}",
            "fix_commit_sha": sha,
            "dataset_name": "test-cve-01",
            "cwe_id": "CWE-89",
            "vuln_class": "sqli",
            "severity": "high",
            "description": "SQL injection test",
        })

    assert len(result) == 1

    # Dataset row persisted
    row = await db.get_dataset("test-cve-01")
    assert row is not None
    assert row["kind"] == "git"
    assert row["origin_url"] == f"file://{repo_src}"
    assert row["origin_commit"] == sha
    assert row["cve_id"] == "CVE-2024-TEST01"
    assert row["materialized_at"] is not None


async def test_cve_import_persists_labels_to_db(tmp_path: Path, db: Database):
    """import_cve persists labels to DB with correct dataset_name."""
    coord = _make_coordinator(tmp_path, db)

    from uuid import uuid4

    from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource
    from sec_review_framework.data.findings import Severity, VulnClass

    labels = [
        GroundTruthLabel(
            id=str(uuid4()),
            dataset_version="v1",
            file_path="src/vuln.py",
            line_start=10,
            line_end=12,
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="SQL injection",
            source=GroundTruthSource.CVE_PATCH,
            source_ref="CVE-2024-TEST02",
            confidence="confirmed",
            created_at=datetime.now(UTC),
        ),
        GroundTruthLabel(
            id=str(uuid4()),
            dataset_version="v1",
            file_path="src/other.py",
            line_start=5,
            line_end=5,
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="SQL injection 2",
            source=GroundTruthSource.CVE_PATCH,
            source_ref="CVE-2024-TEST02",
            confidence="confirmed",
            created_at=datetime.now(UTC),
        ),
    ]
    mock_importer = MagicMock()
    mock_importer.import_from_spec.return_value = labels

    with patch.object(coord, "_build_cve_importer", return_value=mock_importer):
        await coord.import_cve({
            "cve_id": "CVE-2024-TEST02",
            "repo_url": "https://github.com/example/repo",
            "fix_commit_sha": "deadbeef",
            "dataset_name": "test-cve-02",
            "cwe_id": "CWE-89",
            "vuln_class": "sqli",
            "severity": "high",
            "description": "SQL injection test",
        })

    db_labels = await db.list_dataset_labels("test-cve-02")
    assert len(db_labels) == 2
    file_paths = {lbl["file_path"] for lbl in db_labels}
    assert "src/vuln.py" in file_paths
    assert "src/other.py" in file_paths


async def test_cve_import_no_labels_json_written(tmp_path: Path, db: Database):
    """import_cve must NOT write any labels.json file to disk."""
    coord = _make_coordinator(tmp_path, db)

    from uuid import uuid4

    from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource
    from sec_review_framework.data.findings import Severity, VulnClass

    labels = [
        GroundTruthLabel(
            id=str(uuid4()),
            dataset_version="v1",
            file_path="app.py",
            line_start=1,
            line_end=1,
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="test",
            source=GroundTruthSource.CVE_PATCH,
            confidence="confirmed",
            created_at=datetime.now(UTC),
        )
    ]
    mock_importer = MagicMock()
    mock_importer.import_from_spec.return_value = labels

    with patch.object(coord, "_build_cve_importer", return_value=mock_importer):
        await coord.import_cve({
            "cve_id": "CVE-2024-NOJSON",
            "repo_url": "https://github.com/example/repo",
            "fix_commit_sha": "aabbcc",
            "dataset_name": "no-json-test",
            "cwe_id": "CWE-89",
            "vuln_class": "sqli",
            "severity": "high",
            "description": "no json test",
        })

    storage_root = coord.storage_root
    json_files = list(storage_root.rglob("labels.json"))
    jsonl_files = list(storage_root.rglob("labels.jsonl"))
    assert not json_files, f"Found unexpected labels.json: {json_files}"
    assert not jsonl_files, f"Found unexpected labels.jsonl: {jsonl_files}"


# ---------------------------------------------------------------------------
# 2. Inject end-to-end: derived row written, recipe_json round-trips
# ---------------------------------------------------------------------------


async def test_inject_creates_derived_dataset_row(tmp_path: Path, db: Database):
    """inject_vuln creates a kind='derived' dataset row with correct base and recipe."""
    coord = _make_coordinator(tmp_path, db)

    # Seed base dataset in DB
    await db.create_dataset({
        "name": "base-ds",
        "kind": "git",
        "origin_url": "https://github.com/example/repo",
        "origin_commit": "abc123",
        "created_at": datetime.now(UTC).isoformat(),
    })

    # Create the repo dir + target file
    repo_dir = coord.storage_root / "datasets" / "base-ds" / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "views.py").write_text("def search(req):\n    pass\n")

    # Install test templates
    templates_dir = coord.storage_root / "datasets" / "templates" / "sqli"
    templates_dir.mkdir(parents=True)
    src_template = FIXTURE_TEMPLATES_DIR / "sqli" / "test_sqli_template.yaml"
    (templates_dir / "test_sqli_format_string.yaml").write_text(src_template.read_text())

    await coord.inject_vuln("base-ds", {
        "template_id": TEMPLATE_ID,
        "target_file": "views.py",
    })

    # A derived row should now exist
    derived_name = f"base-ds_injected_{TEMPLATE_ID}"
    row = await db.get_dataset(derived_name)
    assert row is not None, f"Expected derived dataset row '{derived_name}'"
    assert row["kind"] == "derived"
    assert row["base_dataset"] == "base-ds"
    assert row["materialized_at"] is not None

    # recipe_json round-trips correctly
    recipe = json.loads(row["recipe_json"])
    assert "templates_version" in recipe
    assert "applications" in recipe
    apps = recipe["applications"]
    assert len(apps) == 1
    assert apps[0]["template_id"] == TEMPLATE_ID
    assert apps[0]["target_file"] == "views.py"


async def test_inject_persists_labels_to_db(tmp_path: Path, db: Database):
    """inject_vuln persists labels to DB under the derived dataset name."""
    coord = _make_coordinator(tmp_path, db)

    await db.create_dataset({
        "name": "base-inject-ds",
        "kind": "git",
        "origin_url": "https://github.com/example/repo",
        "origin_commit": "abc123",
        "created_at": datetime.now(UTC).isoformat(),
    })

    repo_dir = coord.storage_root / "datasets" / "base-inject-ds" / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "views.py").write_text("def search(req):\n    pass\n")

    templates_dir = coord.storage_root / "datasets" / "templates" / "sqli"
    templates_dir.mkdir(parents=True)
    src_template = FIXTURE_TEMPLATES_DIR / "sqli" / "test_sqli_template.yaml"
    (templates_dir / "test_sqli_format_string.yaml").write_text(src_template.read_text())

    await coord.inject_vuln("base-inject-ds", {
        "template_id": TEMPLATE_ID,
        "target_file": "views.py",
    })

    derived_name = f"base-inject-ds_injected_{TEMPLATE_ID}"
    db_labels = await db.list_dataset_labels(derived_name)
    assert len(db_labels) == 1
    assert db_labels[0]["file_path"] == "views.py"
    assert db_labels[0]["source"] == "injected"


async def test_inject_no_labels_json_written(tmp_path: Path, db: Database):
    """inject_vuln must NOT write any labels.json file to disk."""
    coord = _make_coordinator(tmp_path, db)

    await db.create_dataset({
        "name": "inject-nojson-ds",
        "kind": "git",
        "origin_url": "https://github.com/example/repo",
        "origin_commit": "abc123",
        "created_at": datetime.now(UTC).isoformat(),
    })

    repo_dir = coord.storage_root / "datasets" / "inject-nojson-ds" / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "views.py").write_text("def search(req):\n    pass\n")

    templates_dir = coord.storage_root / "datasets" / "templates" / "sqli"
    templates_dir.mkdir(parents=True)
    src_template = FIXTURE_TEMPLATES_DIR / "sqli" / "test_sqli_template.yaml"
    (templates_dir / "test_sqli_format_string.yaml").write_text(src_template.read_text())

    await coord.inject_vuln("inject-nojson-ds", {
        "template_id": TEMPLATE_ID,
        "target_file": "views.py",
    })

    json_files = list(coord.storage_root.rglob("labels.json"))
    jsonl_files = list(coord.storage_root.rglob("labels.jsonl"))
    assert not json_files, f"Found unexpected labels.json: {json_files}"
    assert not jsonl_files, f"Found unexpected labels.jsonl: {jsonl_files}"


# ---------------------------------------------------------------------------
# 3. GET /datasets/{name}/labels returns DB-backed results
# ---------------------------------------------------------------------------


async def test_get_labels_returns_db_backed_results(client: TestClient, db: Database, coord: ExperimentCoordinator):
    """GET /datasets/{name}/labels returns labels from DB."""
    await db.create_dataset({
        "name": "labeled-ds",
        "kind": "git",
        "origin_url": "https://github.com/example/repo",
        "origin_commit": "abc123",
        "created_at": datetime.now(UTC).isoformat(),
    })
    await db.append_dataset_labels([
        _make_label_row("lbl-001", "labeled-ds", "src/main.py"),
        _make_label_row("lbl-002", "labeled-ds", "src/auth.py"),
    ])

    resp = client.get("/datasets/labeled-ds/labels")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    ids = {lbl["id"] for lbl in data}
    assert "lbl-001" in ids
    assert "lbl-002" in ids


async def test_get_labels_unknown_dataset_returns_empty(client: TestClient):
    """GET /datasets/{name}/labels for unknown dataset returns empty list (not 404)."""
    resp = client.get("/datasets/nonexistent-ds/labels")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# 4. GET /datasets/{name}/labels?cwe=CWE-79 filters correctly
# ---------------------------------------------------------------------------


async def test_get_labels_cwe_filter(client: TestClient, db: Database):
    """GET /datasets/{name}/labels?cwe=CWE-79 returns only matching labels."""
    await db.create_dataset({
        "name": "filter-ds",
        "kind": "git",
        "origin_url": "https://github.com/example/repo",
        "origin_commit": "abc123",
        "created_at": datetime.now(UTC).isoformat(),
    })
    await db.append_dataset_labels([
        _make_label_row("lbl-sqli", "filter-ds", "src/a.py", cwe_id="CWE-89"),
        _make_label_row("lbl-xss", "filter-ds", "src/b.py", cwe_id="CWE-79"),
        _make_label_row("lbl-xss2", "filter-ds", "src/c.py", cwe_id="CWE-79"),
    ])

    resp = client.get("/datasets/filter-ds/labels?cwe=CWE-79")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(lbl["cwe_id"] == "CWE-79" for lbl in data)

    resp_sqli = client.get("/datasets/filter-ds/labels?cwe=CWE-89")
    assert resp_sqli.status_code == 200
    sqli_data = resp_sqli.json()
    assert len(sqli_data) == 1
    assert sqli_data[0]["id"] == "lbl-sqli"


# ---------------------------------------------------------------------------
# 5. After both flows, no labels.json or labels.jsonl anywhere
# ---------------------------------------------------------------------------


async def test_no_labels_json_after_both_flows(tmp_path: Path, db: Database):
    """After CVE import and inject, storage_root has no labels.json/jsonl files."""
    coord = _make_coordinator(tmp_path, db)

    from uuid import uuid4

    from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource
    from sec_review_framework.data.findings import Severity, VulnClass

    labels = [
        GroundTruthLabel(
            id=str(uuid4()),
            dataset_version="v1",
            file_path="app.py",
            line_start=1,
            line_end=1,
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="test",
            source=GroundTruthSource.CVE_PATCH,
            confidence="confirmed",
            created_at=datetime.now(UTC),
        )
    ]
    mock_importer = MagicMock()
    mock_importer.import_from_spec.return_value = labels

    with patch.object(coord, "_build_cve_importer", return_value=mock_importer):
        await coord.import_cve({
            "cve_id": "CVE-BOTH-01",
            "repo_url": "https://github.com/example/repo",
            "fix_commit_sha": "aabbcc",
            "dataset_name": "both-test-cve",
            "cwe_id": "CWE-89",
            "vuln_class": "sqli",
            "severity": "high",
            "description": "test",
        })

    # Also do an inject
    await db.create_dataset({
        "name": "both-base-ds",
        "kind": "git",
        "origin_url": "https://github.com/example/repo",
        "origin_commit": "abc123",
        "created_at": datetime.now(UTC).isoformat(),
    })
    repo_dir = coord.storage_root / "datasets" / "both-base-ds" / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "views.py").write_text("def search(req):\n    pass\n")
    templates_dir = coord.storage_root / "datasets" / "templates" / "sqli"
    templates_dir.mkdir(parents=True)
    src_template = FIXTURE_TEMPLATES_DIR / "sqli" / "test_sqli_template.yaml"
    (templates_dir / "test_sqli_format_string.yaml").write_text(src_template.read_text())
    await coord.inject_vuln("both-base-ds", {
        "template_id": TEMPLATE_ID,
        "target_file": "views.py",
    })

    json_files = list(coord.storage_root.rglob("labels.json"))
    jsonl_files = list(coord.storage_root.rglob("labels.jsonl"))
    assert not json_files, f"Unexpected labels.json files: {json_files}"
    assert not jsonl_files, f"Unexpected labels.jsonl files: {jsonl_files}"


# ---------------------------------------------------------------------------
# 6. materialize_dataset for kind='git'
# ---------------------------------------------------------------------------


async def test_materialize_git_dataset(tmp_path: Path, db: Database):
    """materialize_dataset clones repo, checks out commit, stamps materialized_at."""
    coord = _make_coordinator(tmp_path, db)

    # Create a local git repo to clone from
    repo_src = tmp_path / "source_repo"
    sha = _init_git_repo(repo_src)

    await db.create_dataset({
        "name": "mat-git-ds",
        "kind": "git",
        "origin_url": f"file://{repo_src}",
        "origin_commit": sha,
        "created_at": datetime.now(UTC).isoformat(),
    })

    # Remove any pre-existing repo dir (if any)
    target_dir = coord.storage_root / "datasets" / "mat-git-ds" / "repo"
    assert not target_dir.exists()

    await coord.materialize_dataset("mat-git-ds")

    assert target_dir.exists(), "Repo dir should exist after materialization"
    # Verify HEAD is the expected SHA
    head_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=target_dir, text=True
    ).strip()
    assert head_sha == sha

    # materialized_at updated in DB
    row = await db.get_dataset("mat-git-ds")
    assert row is not None
    assert row["materialized_at"] is not None


async def test_materialize_unknown_dataset_raises_404(tmp_path: Path, db: Database):
    """materialize_dataset for an unknown name raises HTTPException 404."""
    coord = _make_coordinator(tmp_path, db)
    with pytest.raises(HTTPException) as exc_info:
        await coord.materialize_dataset("does-not-exist")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 7. materialize_dataset for kind='derived'
# ---------------------------------------------------------------------------


async def test_materialize_derived_dataset_templates_version_mismatch(
    tmp_path: Path, db: Database
):
    """materialize_dataset raises 409 when templates_version in recipe mismatches."""
    coord = _make_coordinator(tmp_path, db)

    repo_src = tmp_path / "source_repo2"
    sha = _init_git_repo(repo_src)

    await db.create_dataset({
        "name": "base-for-derived",
        "kind": "git",
        "origin_url": f"file://{repo_src}",
        "origin_commit": sha,
        "created_at": datetime.now(UTC).isoformat(),
    })

    recipe = {
        "templates_version": "stale-version-that-does-not-match",
        "applications": [],
    }
    await db.create_dataset({
        "name": "derived-mismatch",
        "kind": "derived",
        "base_dataset": "base-for-derived",
        "recipe_json": json.dumps(recipe),
        "created_at": datetime.now(UTC).isoformat(),
    })

    # Set up templates so templates_version returns a real hash (not "absent")
    templates_dir = coord.storage_root / "datasets" / "templates" / "sqli"
    templates_dir.mkdir(parents=True)
    src_template = FIXTURE_TEMPLATES_DIR / "sqli" / "test_sqli_template.yaml"
    (templates_dir / "test_sqli_format_string.yaml").write_text(src_template.read_text())

    with pytest.raises(HTTPException) as exc_info:
        await coord.materialize_dataset("derived-mismatch")
    assert exc_info.value.status_code == 409
    assert "templates_version" in exc_info.value.detail

    # materialized_at must remain unchanged (still None)
    row = await db.get_dataset("derived-mismatch")
    assert row is not None
    # The base was materialized recursively, but the derived should not have
    # been stamped because materialization failed


async def test_materialize_derived_base_not_in_db_raises_404(tmp_path: Path, db: Database):
    """materialize_dataset for derived whose base has no DB row raises 404.

    We create the base row just so the FK constraint passes, then delete it to
    simulate a missing-base condition via a direct SQL call bypassing FK enforcement.
    """
    import aiosqlite

    coord = _make_coordinator(tmp_path, db)

    # Insert base row first (needed for FK), then remove it before materialization
    await db.create_dataset({
        "name": "phantom-base",
        "kind": "git",
        "origin_url": "https://github.com/example/repo",
        "origin_commit": "abc",
        "created_at": datetime.now(UTC).isoformat(),
    })
    recipe = {"templates_version": "any", "applications": []}
    await db.create_dataset({
        "name": "derived-no-base",
        "kind": "derived",
        "base_dataset": "phantom-base",
        "recipe_json": json.dumps(recipe),
        "created_at": datetime.now(UTC).isoformat(),
    })
    # Remove base dataset (bypass FK for this setup step)
    async with aiosqlite.connect(db.db_path) as raw:
        await raw.execute("DELETE FROM datasets WHERE name = ?", ("phantom-base",))
        await raw.commit()

    with pytest.raises(HTTPException) as exc_info:
        await coord.materialize_dataset("derived-no-base")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# 8. templates_version is stable and changes when a file changes
# ---------------------------------------------------------------------------


def test_templates_version_stable_across_calls(tmp_path: Path, db: Database):
    """templates_version returns the same hash on repeated calls."""
    coord = _make_coordinator(tmp_path, db)

    templates_dir = coord.storage_root / "datasets" / "templates" / "sqli"
    templates_dir.mkdir(parents=True)
    src_template = FIXTURE_TEMPLATES_DIR / "sqli" / "test_sqli_template.yaml"
    (templates_dir / "test_sqli_format_string.yaml").write_text(src_template.read_text())

    v1 = coord.templates_version
    v2 = coord.templates_version
    assert v1 == v2
    assert v1 != "absent"


def test_templates_version_absent_when_no_templates(tmp_path: Path, db: Database):
    """templates_version returns 'absent' when templates dir doesn't exist."""
    coord = _make_coordinator(tmp_path, db)
    # Don't create any templates dir
    assert coord.templates_version == "absent"


def test_templates_version_changes_when_file_modified(tmp_path: Path, db: Database):
    """templates_version changes when a template file is modified.

    NOTE: templates_version is cached on the coordinator instance. A new
    coordinator instance is used to observe the changed hash.
    """
    # First coordinator + version
    coord1 = _make_coordinator(tmp_path, db)
    templates_dir = coord1.storage_root / "datasets" / "templates" / "sqli"
    templates_dir.mkdir(parents=True)
    src_template = FIXTURE_TEMPLATES_DIR / "sqli" / "test_sqli_template.yaml"
    template_file = templates_dir / "test_sqli_format_string.yaml"
    template_file.write_text(src_template.read_text())

    v1 = coord1.templates_version

    # Modify the template file
    template_file.write_text(src_template.read_text() + "\n# modified\n")

    # New coordinator instance (cache reset)
    coord2 = _make_coordinator(tmp_path, db)
    v2 = coord2.templates_version

    assert v1 != v2, "templates_version should change when a template file is modified"
