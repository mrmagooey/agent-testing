"""E2E tests for the tool-extensions feature.

After the ExperimentMatrix collapse, ``tool_extensions`` is no longer a matrix
axis — it is baked into each strategy's bundle and is immutable per strategy_id.
The ``_ext-<sorted>`` run-id suffix was explicitly dropped (see
``ExperimentMatrix.expand`` docstring). These tests now assert the post-collapse
invariants:

  - Run IDs are ``{experiment_id}_{strategy_id}[_rep{N}]`` with NO ``_ext-``
    suffix, regardless of the strategy's tool_extensions.
  - The ``tool_extensions`` DB column still round-trips through frozenset
    correctly when the strategy's bundle declares extensions.

All tests use TestClient + FakeModelProvider so no K8s or LLM API is needed.
Peak memory is minimal — one small dataset file per test.

Note on extension builders: the real extension builders (tree_sitter_ext,
lsp_ext) launch MCP server subprocesses, which are unavailable in the test
environment. We therefore patch ``_EXTENSION_BUILDERS`` to no-op stubs so the
worker completes without needing real MCP subprocesses.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

if TYPE_CHECKING:
    from sec_review_framework.data.strategy_bundle import UserStrategy

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
    ToolExtension,
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

MODEL_ID = "claude-opus-4-5"
DATASET_NAME = "ext-smoke-dataset"
DATASET_VERSION = "1.0.0"


def _make_ext_strategy(strategy_id: str, extensions: frozenset[ToolExtension]) -> UserStrategy:
    """Build a UserStrategy whose default bundle carries the requested tool_extensions."""
    from sec_review_framework.data.strategy_bundle import (
        OrchestrationShape,
        StrategyBundleDefault,
        UserStrategy,
    )

    return UserStrategy(
        id=strategy_id,
        name=strategy_id,
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="test",
            user_prompt_template="test",
            profile_modifier="",
            model_id=MODEL_ID,
            tools=frozenset(["read_file"]),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(e.value for e in extensions),
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None),
        is_builtin=False,
    )


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

    Also temporarily sets the TOOL_EXT_*_AVAILABLE env vars so the coordinator's
    POST /experiments availability check treats the stubbed extensions as enabled.
    The env vars use the pattern TOOL_EXT_{value.upper()}_AVAILABLE.
    """
    original_builders = dict(_registry_module._EXTENSION_BUILDERS)
    # Compute env var names for each extension (e.g. "lsp" -> "TOOL_EXT_LSP_AVAILABLE")
    env_var_names = [f"TOOL_EXT_{ext.value.upper()}_AVAILABLE" for ext in extensions]
    original_env: dict[str, str | None] = {
        name: os.environ.get(name) for name in env_var_names
    }
    for ext in extensions:
        _registry_module._EXTENSION_BUILDERS[ext] = _noop_builder
    for name in env_var_names:
        os.environ[name] = "true"
    try:
        yield
    finally:
        _registry_module._EXTENSION_BUILDERS.clear()
        _registry_module._EXTENSION_BUILDERS.update(original_builders)
        for name, old_val in original_env.items():
            if old_val is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_val


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


def _register_ext_strategy_and_build_matrix(
    coordinator_inst: ExperimentCoordinator,
    experiment_id: str,
    extensions: frozenset[ToolExtension],
    strategy_suffix: str = "default",
) -> ExperimentMatrix:
    """Register a per-test user strategy whose bundle carries *extensions*,
    then return a matrix that targets it.

    tool_extensions is no longer a matrix axis (it's baked into the strategy).
    """
    strategy_id = f"test.ext.{strategy_suffix}"
    strategy = _make_ext_strategy(strategy_id, extensions)
    _run_async(coordinator_inst.db.insert_user_strategy(strategy))
    return ExperimentMatrix(
        experiment_id=experiment_id,
        dataset_name=DATASET_NAME,
        dataset_version=DATASET_VERSION,
        strategy_ids=[strategy_id],
        num_repetitions=1,
        allow_unavailable_models=True,
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
# Test 1: single extension → run ID has no _ext- suffix
# ---------------------------------------------------------------------------

class TestSingleExtensionSuffix:
    """Run IDs must be {experiment_id}_{strategy_id} with no _ext- suffix,
    even when the strategy bundle declares tool_extensions."""

    # Feature change: the _ext-<sorted> suffix was dropped when tool_extensions
    # was baked into the strategy bundle (no longer a matrix axis).
    def test_single_extension_appends_sorted_suffix(
        self,
        test_client: TestClient,
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        experiment_id = "ext-test-single-001"
        matrix = _register_ext_strategy_and_build_matrix(
            coordinator_instance,
            experiment_id=experiment_id,
            extensions=frozenset({ToolExtension.TREE_SITTER}),
            strategy_suffix="single",
        )

        with _stub_extension_builders(ToolExtension.TREE_SITTER):
            _submit_and_run(coordinator_instance, matrix, datasets_dir, test_client)

        runs_resp = test_client.get(f"/experiments/{experiment_id}/runs")
        assert runs_resp.status_code == 200, runs_resp.text
        runs = runs_resp.json()
        assert len(runs) >= 1, "Expected at least one run"

        for run in runs:
            run_id = run["id"]
            assert "_ext-" not in run_id, (
                f"Run ID '{run_id}' must not contain '_ext-'; tool_extensions "
                f"are now strategy-scoped, not a run-id axis."
            )


# ---------------------------------------------------------------------------
# Test 2: empty extensions → no _ext- suffix
# ---------------------------------------------------------------------------

class TestEmptyExtensionsNoSuffix:
    """Empty extension set must produce no _ext- suffix."""

    def test_empty_extensions_has_no_suffix(
        self,
        test_client: TestClient,
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        experiment_id = "ext-test-empty-001"
        matrix = _register_ext_strategy_and_build_matrix(
            coordinator_instance,
            experiment_id=experiment_id,
            extensions=frozenset(),
            strategy_suffix="empty",
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
                f"Run ID '{run_id}' contains '_ext-' but no extensions were selected."
            )


# ---------------------------------------------------------------------------
# Test 3: multiple extensions → still no _ext- suffix
# ---------------------------------------------------------------------------

class TestMultipleExtensionsSortAlphabetically:
    """Multiple extensions on a strategy still produce a run ID with no suffix."""

    # Feature change: previously asserted _ext-lsp+tree_sitter ordering in the
    # run-id suffix; the suffix was dropped entirely, so the closest current
    # equivalent is to assert its absence regardless of extension count.
    def test_multiple_extensions_sort_alphabetically(
        self,
        test_client: TestClient,
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        experiment_id = "ext-test-multi-001"
        matrix = _register_ext_strategy_and_build_matrix(
            coordinator_instance,
            experiment_id=experiment_id,
            extensions=frozenset({ToolExtension.TREE_SITTER, ToolExtension.LSP}),
            strategy_suffix="multi",
        )

        with _stub_extension_builders(ToolExtension.TREE_SITTER, ToolExtension.LSP):
            _submit_and_run(coordinator_instance, matrix, datasets_dir, test_client)

        runs_resp = test_client.get(f"/experiments/{experiment_id}/runs")
        assert runs_resp.status_code == 200, runs_resp.text
        runs = runs_resp.json()
        assert len(runs) >= 1, "Expected at least one run"

        for run in runs:
            run_id = run["id"]
            assert "_ext-" not in run_id, (
                f"Run ID '{run_id}' must not contain '_ext-'; tool_extensions "
                f"are now strategy-scoped, not a run-id axis."
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
        matrix = _register_ext_strategy_and_build_matrix(
            coordinator_instance,
            experiment_id=experiment_id,
            extensions=frozenset({ToolExtension.TREE_SITTER, ToolExtension.LSP}),
            strategy_suffix="db",
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
