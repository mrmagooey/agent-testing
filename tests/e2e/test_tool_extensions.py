"""E2E tests for the tool-extensions feature.

Invariants tested (from CLAUDE.md):
  - Run IDs gain `_ext-<sorted>` suffix for non-empty extension sets.
  - Legacy empty-extension runs stay byte-identical (no suffix).
  - Suffix ordering is always alphabetical, not insertion order.
  - The `tool_extensions` DB column round-trips through frozenset correctly.

All tests use TestClient + FakeModelProvider so no K8s or LLM API is needed.
Peak memory is minimal — one small dataset file per test.

Note on extension builders: the real extension builders (tree_sitter_ext,
lsp_ext) launch MCP server subprocesses, which are unavailable in the test
environment. Tests that need the *run_id suffix* and *DB column* invariants do
not require the extension tools to function — they only need the run to be
submitted, written to the DB, and for the worker to complete (with extensions
stubbed out). We therefore patch ``_EXTENSION_BUILDERS`` to no-op stubs in
those tests.
"""

from __future__ import annotations

import asyncio
import sqlite3
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.tools.registry as _registry_module
from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.evaluation import (
    GroundTruthLabel,
    GroundTruthSource,
)
from sec_review_framework.data.experiment import (
    ExperimentMatrix,
    ReviewProfileName,
    StrategyName,
    ToolExtension,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.db import Database
from sec_review_framework.models.base import ModelResponse, RetryPolicy
from sec_review_framework.reporting.generator import ReportGenerator
from sec_review_framework.worker import ExperimentWorker, ModelProviderFactory

sys.path.insert(0, str(Path(__file__).parent.parent))
from conftest import FakeModelProvider  # noqa: E402

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

MODEL_ID = "fake-model-ext"
DATASET_NAME = "ext-smoke-dataset"
DATASET_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# No-op extension builder context manager
# ---------------------------------------------------------------------------

def _noop_builder(registry: Any, target: Any) -> None:
    """Extension builder stub that does nothing (no MCP subprocess launched)."""


@contextmanager
def _stub_extension_builders(*extensions: ToolExtension):
    """Temporarily register no-op builders for the given extensions.

    This lets us test run_id suffix and DB column invariants without needing
    real MCP server subprocesses.
    """
    original = dict(_registry_module._EXTENSION_BUILDERS)
    for ext in extensions:
        _registry_module._EXTENSION_BUILDERS[ext] = _noop_builder
    try:
        yield
    finally:
        _registry_module._EXTENSION_BUILDERS.clear()
        _registry_module._EXTENSION_BUILDERS.update(original)


# ---------------------------------------------------------------------------
# Minimal dataset helper (tiny fixture — well under 2 GB)
# ---------------------------------------------------------------------------

def _make_minimal_dataset(datasets_dir: Path) -> None:
    """Create the minimal dataset layout that ExperimentWorker expects."""
    target_dir = datasets_dir / "targets" / DATASET_NAME
    repo_dir = target_dir / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    (repo_dir / "app.py").write_text(
        'def login(username, password):\n'
        '    query = "SELECT * FROM users WHERE name = \'%s\'" % username\n'
        '    return db.execute(query)\n',
        encoding="utf-8",
    )

    label = GroundTruthLabel(
        id="label-ext-sqli-001",
        dataset_version=DATASET_VERSION,
        file_path="app.py",
        line_start=2,
        line_end=2,
        cwe_id="CWE-89",
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        description="SQL injection via string formatting",
        source=GroundTruthSource.INJECTED,
        confidence="confirmed",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    (target_dir / "labels.jsonl").write_text(
        label.model_dump_json() + "\n", encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Fake model response — valid JSON findings block
# ---------------------------------------------------------------------------

_FAKE_RESPONSE = ModelResponse(
    content=(
        '```json\n'
        '[\n'
        '  {\n'
        '    "file_path": "app.py",\n'
        '    "line_start": 2,\n'
        '    "line_end": 2,\n'
        '    "vuln_class": "sqli",\n'
        '    "cwe_ids": ["CWE-89"],\n'
        '    "severity": "high",\n'
        '    "title": "SQL Injection",\n'
        '    "description": "String formatting in SQL query.",\n'
        '    "recommendation": "Use parameterised queries.",\n'
        '    "confidence": 0.95\n'
        '  }\n'
        ']\n'
        '```'
    ),
    tool_calls=[],
    input_tokens=50,
    output_tokens=60,
    model_id=MODEL_ID,
    raw={},
)


def _make_fake_provider() -> FakeModelProvider:
    return FakeModelProvider(
        responses=[_FAKE_RESPONSE] * 5,
        retry_policy=RetryPolicy(max_retries=0),
    )


# ---------------------------------------------------------------------------
# Null reporter (we do not need matrix reports in these tests)
# ---------------------------------------------------------------------------

class _NullReporter(ReportGenerator):
    def render_run(self, result, output_dir: Path) -> None:  # type: ignore[override]
        pass

    def render_matrix(self, results, output_dir: Path) -> None:  # type: ignore[override]
        pass


# ---------------------------------------------------------------------------
# Shared async helper
# ---------------------------------------------------------------------------

def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---------------------------------------------------------------------------
# Per-test fixture helpers
# ---------------------------------------------------------------------------

def _build_coordinator(tmp_path: Path, storage_root: Path) -> ExperimentCoordinator:
    db = Database(tmp_path / "test.db")
    _run_async(db.init())
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage_root,
        concurrency_caps={},
        worker_image="unused",
        namespace="default",
        db=db,
        reporter=_NullReporter(),
        cost_calculator=CostCalculator(
            pricing={MODEL_ID: ModelPricing(input_per_million=0.0, output_per_million=0.0)}
        ),
        default_cap=4,
    )


def _build_matrix(
    experiment_id: str,
    extension_sets: list[frozenset[ToolExtension]],
) -> ExperimentMatrix:
    return ExperimentMatrix(
        experiment_id=experiment_id,
        dataset_name=DATASET_NAME,
        dataset_version=DATASET_VERSION,
        model_ids=[MODEL_ID],
        strategies=[StrategyName.SINGLE_AGENT],
        tool_variants=[ToolVariant.WITH_TOOLS],
        review_profiles=[ReviewProfileName.DEFAULT],
        verification_variants=[VerificationVariant.NONE],
        parallel_modes=[False],
        num_repetitions=1,
        tool_extension_sets=extension_sets,
    )


def _submit_and_run(
    coordinator_inst: ExperimentCoordinator,
    matrix: ExperimentMatrix,
    datasets_dir: Path,
    test_client: TestClient,
) -> None:
    """Submit via TestClient then drive workers directly (no K8s)."""
    resp = test_client.post("/experiments", json=matrix.model_dump())
    assert resp.status_code == 201, resp.text

    config_dir = coordinator_inst.storage_root / "config" / "runs"
    if not config_dir.exists():
        return

    from sec_review_framework.data.experiment import ExperimentRun

    for config_file in config_dir.glob("*.json"):
        run = ExperimentRun.model_validate_json(config_file.read_text())
        output_dir = coordinator_inst.storage_root / "outputs" / run.experiment_id / run.id
        with patch.object(ModelProviderFactory, "create", return_value=_make_fake_provider()):
            worker = ExperimentWorker()
            worker.run(run, output_dir, datasets_dir)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture()
def datasets_dir(storage_root: Path) -> Path:
    ds_dir = storage_root / "datasets"
    _make_minimal_dataset(ds_dir)
    return ds_dir


@pytest.fixture()
def coordinator_instance(tmp_path: Path, storage_root: Path) -> ExperimentCoordinator:
    return _build_coordinator(tmp_path, storage_root)


@pytest.fixture()
def test_client(coordinator_instance: ExperimentCoordinator, datasets_dir: Path):
    import sec_review_framework.coordinator as coord_module

    original = coord_module.coordinator
    coord_module.coordinator = coordinator_instance
    try:
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client
    finally:
        coord_module.coordinator = original


# ---------------------------------------------------------------------------
# Test 1: single extension → _ext-<name> suffix
# ---------------------------------------------------------------------------

class TestSingleExtensionSuffix:
    """Run IDs must end with _ext-tree_sitter when only TREE_SITTER is selected."""

    def test_single_extension_appends_sorted_suffix(
        self,
        test_client: TestClient,
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        experiment_id = "ext-test-single-001"
        matrix = _build_matrix(
            experiment_id=experiment_id,
            extension_sets=[frozenset({ToolExtension.TREE_SITTER})],
        )

        with _stub_extension_builders(ToolExtension.TREE_SITTER):
            _submit_and_run(coordinator_instance, matrix, datasets_dir, test_client)

        runs_resp = test_client.get(f"/experiments/{experiment_id}/runs")
        assert runs_resp.status_code == 200, runs_resp.text
        runs = runs_resp.json()
        assert len(runs) >= 1, "Expected at least one run"

        for run in runs:
            run_id = run["id"]
            assert run_id.endswith("_ext-tree_sitter"), (
                f"Run ID '{run_id}' does not end with '_ext-tree_sitter'. "
                "This is the CLAUDE.md backwards-compatibility invariant."
            )


# ---------------------------------------------------------------------------
# Test 2: empty extensions → no _ext- suffix (legacy byte-identical path)
# ---------------------------------------------------------------------------

class TestEmptyExtensionsNoSuffix:
    """Legacy path: empty extension set must produce no _ext- suffix at all."""

    def test_empty_extensions_has_no_suffix(
        self,
        test_client: TestClient,
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        experiment_id = "ext-test-empty-001"
        matrix = _build_matrix(
            experiment_id=experiment_id,
            extension_sets=[frozenset()],  # empty set — legacy path
        )

        # No extension stubs needed — empty frozenset never invokes builders
        _submit_and_run(coordinator_instance, matrix, datasets_dir, test_client)

        runs_resp = test_client.get(f"/experiments/{experiment_id}/runs")
        assert runs_resp.status_code == 200, runs_resp.text
        runs = runs_resp.json()
        assert len(runs) >= 1, "Expected at least one run"

        for run in runs:
            run_id = run["id"]
            assert "_ext-" not in run_id, (
                f"Run ID '{run_id}' contains '_ext-' but no extensions were selected. "
                "CLAUDE.md invariant: legacy empty-extension runs stay byte-identical."
            )


# ---------------------------------------------------------------------------
# Test 3: multiple extensions sorted alphabetically in suffix
# ---------------------------------------------------------------------------

class TestMultipleExtensionsSortAlphabetically:
    """_ext-lsp+tree_sitter, not _ext-tree_sitter+lsp (alphabetical, not insertion)."""

    def test_multiple_extensions_sort_alphabetically(
        self,
        test_client: TestClient,
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        experiment_id = "ext-test-multi-001"
        # Note: frozenset insertion order is irrelevant — sorting is enforced in expand()
        matrix = _build_matrix(
            experiment_id=experiment_id,
            extension_sets=[frozenset({ToolExtension.TREE_SITTER, ToolExtension.LSP})],
        )

        with _stub_extension_builders(ToolExtension.TREE_SITTER, ToolExtension.LSP):
            _submit_and_run(coordinator_instance, matrix, datasets_dir, test_client)

        runs_resp = test_client.get(f"/experiments/{experiment_id}/runs")
        assert runs_resp.status_code == 200, runs_resp.text
        runs = runs_resp.json()
        assert len(runs) >= 1, "Expected at least one run"

        for run in runs:
            run_id = run["id"]
            # Must end with alphabetically sorted suffix: lsp comes before tree_sitter
            assert run_id.endswith("_ext-lsp+tree_sitter"), (
                f"Run ID '{run_id}' does not end with '_ext-lsp+tree_sitter'. "
                "Extensions must be sorted alphabetically in the suffix, not by insertion order."
            )


# ---------------------------------------------------------------------------
# Test 4: DB column round-trips as frozenset
# ---------------------------------------------------------------------------

class TestDbColumnRoundtrips:
    """The `tool_extensions` DB column must round-trip to frozenset[ToolExtension]."""

    def test_db_column_roundtrips_frozenset(
        self,
        test_client: TestClient,
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        experiment_id = "ext-test-db-001"
        matrix = _build_matrix(
            experiment_id=experiment_id,
            extension_sets=[frozenset({ToolExtension.TREE_SITTER, ToolExtension.LSP})],
        )

        with _stub_extension_builders(ToolExtension.TREE_SITTER, ToolExtension.LSP):
            _submit_and_run(coordinator_instance, matrix, datasets_dir, test_client)

        # Open the SQLite DB directly and query the raw column value
        db_path = coordinator_instance.db.db_path
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.execute(
                "SELECT id, tool_extensions FROM runs WHERE experiment_id = ?",
                (experiment_id,),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()

        assert len(rows) >= 1, "Expected at least one run row in the DB"

        for run_id, raw_ext in rows:
            # The column is stored as a comma-separated sorted string.
            # E.g. "lsp,tree_sitter" (sorted alphabetically by the DB layer)
            assert raw_ext is not None, f"tool_extensions column is NULL for run {run_id}"

            # Parse back to frozenset[ToolExtension] using the same logic as the DB layer
            if raw_ext == "":
                parsed: frozenset[ToolExtension] = frozenset()
            else:
                parsed = frozenset(ToolExtension(e) for e in raw_ext.split(",") if e)

            expected = frozenset({ToolExtension.LSP, ToolExtension.TREE_SITTER})
            assert parsed == expected, (
                f"Run {run_id}: stored '{raw_ext}' parsed to {parsed!r}, "
                f"expected {expected!r}. The DB column must round-trip correctly."
            )
