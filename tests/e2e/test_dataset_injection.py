"""Backend e2e tests for dataset injection endpoints (gap #6 of 13).

Exercises:
  1. POST /datasets/{name}/inject/preview  → returns diff fields + language.
  2. POST /datasets/{name}/inject          → appends label to labels.json on disk.

Uses TestClient + minimal dataset fixture, following the pattern from
test_coordinator_smoke.py. No Kubernetes, no real LLM calls.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.db import Database

# ---------------------------------------------------------------------------
# Async helper (mirrors test_coordinator_smoke.py)
# ---------------------------------------------------------------------------


def _run_async(coro):  # noqa: ANN001
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATASET_NAME = "inject-test-dataset"
TEMPLATE_ID = "test_sqli_format_string"  # from tests/fixtures/templates/sqli/


# ---------------------------------------------------------------------------
# Dataset fixture: minimal repo + labels.json + injection template
# ---------------------------------------------------------------------------


def _make_inject_dataset(storage_root: Path) -> None:
    """
    Create a minimal dataset layout under storage_root/datasets/<DATASET_NAME>/:

        datasets/
          <DATASET_NAME>/
            repo/
              app.py          ← Python file with a function def anchor
            labels.json       ← one pre-existing label (JSON array)
          templates/
            sqli/
              test_sqli_format_string.yaml  ← injection template
    """
    dataset_dir = storage_root / "datasets" / DATASET_NAME
    repo_dir = dataset_dir / "repo"
    repo_dir.mkdir(parents=True)

    # Target Python file with a function definition anchor for the template
    (repo_dir / "app.py").write_text(
        "def get_user(name):\n"
        '    return db.execute("SELECT * FROM users WHERE name = ?", [name])\n',
        encoding="utf-8",
    )

    # One pre-existing label (JSON array format as _append_labels writes)
    existing_label = GroundTruthLabel(
        id="label-pre-001",
        dataset_version="1.0.0",
        file_path="app.py",
        line_start=1,
        line_end=2,
        cwe_id="CWE-89",
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        description="Pre-existing SQL injection label",
        source=GroundTruthSource.INJECTED,
        confidence="confirmed",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    labels_file = dataset_dir / "labels.json"
    labels_file.write_text(
        json.dumps([existing_label.model_dump(mode="json")], indent=2),
        encoding="utf-8",
    )

    # Injection template — copy the test fixture YAML into the templates dir
    # that the coordinator expects: storage_root/datasets/templates/<subdir>/
    templates_dir = storage_root / "datasets" / "templates" / "sqli"
    templates_dir.mkdir(parents=True)
    template_src = (
        Path(__file__).parent.parent
        / "fixtures"
        / "templates"
        / "sqli"
        / "test_sqli_template.yaml"
    )
    (templates_dir / "test_sqli_format_string.yaml").write_text(
        template_src.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture()
def cost_calculator() -> CostCalculator:
    return CostCalculator(
        pricing={
            "fake-model": ModelPricing(input_per_million=0.0, output_per_million=0.0)
        }
    )


@pytest.fixture()
def coordinator_instance(
    tmp_path: Path, storage_root: Path, cost_calculator: CostCalculator
) -> ExperimentCoordinator:
    db = Database(tmp_path / "test.db")
    _run_async(db.init())

    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage_root,
        concurrency_caps={},
        worker_image="unused-in-test",
        namespace="default",
        db=db,
        reporter=None,  # type: ignore[arg-type]
        cost_calculator=cost_calculator,
        default_cap=4,
    )


@pytest.fixture()
def test_client(
    coordinator_instance: ExperimentCoordinator, storage_root: Path
) -> TestClient:
    """TestClient with the module-level coordinator patched to our instance."""
    _make_inject_dataset(storage_root)

    import sec_review_framework.coordinator as coord_module

    original = coord_module.coordinator
    coord_module.coordinator = coordinator_instance
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client
    finally:
        coord_module.coordinator = original


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInjectPreviewReturnsDiff:
    """POST /datasets/{name}/inject/preview returns a diff of the injection."""

    def test_inject_preview_returns_diff(
        self, test_client: TestClient, storage_root: Path
    ) -> None:
        payload = {
            "template_id": TEMPLATE_ID,
            "file_path": "app.py",
            "substitutions": {},
        }

        resp = test_client.post(
            f"/datasets/{DATASET_NAME}/inject/preview",
            json=payload,
        )
        assert resp.status_code == 200, resp.text

        data = resp.json()

        # The InjectionPreview model exposes before_snippet, after_snippet,
        # unified_diff, template_id, target_file, anchor_line, label_preview.
        assert "before_snippet" in data, f"Missing 'before_snippet' in: {list(data)}"
        assert "after_snippet" in data, f"Missing 'after_snippet' in: {list(data)}"
        assert "unified_diff" in data, f"Missing 'unified_diff' in: {list(data)}"

        # The injection must actually change the file (before != after)
        assert data["before_snippet"] != data["after_snippet"], (
            "Preview before_snippet and after_snippet must differ — "
            "injection produced no change"
        )

        # unified_diff must be non-empty (indicates a real change)
        assert data["unified_diff"].strip(), "unified_diff must be non-empty"

        # template_id round-trips correctly
        assert data["template_id"] == TEMPLATE_ID

        # label_preview contains expected vulnerability metadata
        label_preview = data.get("label_preview", {})
        assert label_preview.get("vuln_class") == "sqli"


class TestInjectAppendsLabel:
    """POST /datasets/{name}/inject appends a new label to labels.json on disk."""

    def test_inject_appends_label(
        self, test_client: TestClient, storage_root: Path
    ) -> None:
        labels_file = storage_root / "datasets" / DATASET_NAME / "labels.json"

        # Snapshot the existing label count before injection
        before_labels = json.loads(labels_file.read_text())
        assert isinstance(before_labels, list)
        count_before = len(before_labels)

        payload = {
            "template_id": TEMPLATE_ID,
            "file_path": "app.py",
            "substitutions": {},
        }

        resp = test_client.post(
            f"/datasets/{DATASET_NAME}/inject",
            json=payload,
        )
        assert resp.status_code == 201, resp.text

        data = resp.json()

        # Response contains label_id
        assert "label_id" in data, f"Missing 'label_id' in response: {data}"
        assert data["label_id"], "label_id must be non-empty"

        # labels.json now has exactly one more entry
        after_labels = json.loads(labels_file.read_text())
        assert isinstance(after_labels, list)
        count_after = len(after_labels)
        assert count_after == count_before + 1, (
            f"Expected {count_before + 1} labels after inject, got {count_after}"
        )

        # The new entry references the injected file
        new_label = after_labels[-1]
        assert new_label.get("file_path") == "app.py", (
            f"New label file_path should be 'app.py', got {new_label.get('file_path')!r}"
        )

        # The new entry contains the injected vulnerability class
        assert new_label.get("vuln_class") == "sqli", (
            f"New label vuln_class should be 'sqli', got {new_label.get('vuln_class')!r}"
        )

        # The label_id in the response matches the record on disk
        label_ids_on_disk = {lbl.get("id") for lbl in after_labels}
        assert data["label_id"] in label_ids_on_disk, (
            f"Returned label_id {data['label_id']!r} not found in labels.json on disk"
        )
