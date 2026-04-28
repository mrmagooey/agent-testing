"""Unit tests for the per-experiment language allowlist gate.

Tests cover:
- Allowlist ["python"] + dataset language="python" → dispatches (HTTP 201).
- Allowlist ["python"] + dataset language="c"      → raises ValueError-style
  400 naming the dataset, language, and allowlist.
- Allowlist ["python"] + dataset has no language    → dispatches with a warning.
- Empty allowlist                                    → all datasets dispatch.
- Error occurs before any worker invocation (coordinator.submit_experiment
  is never called when the gate fires).
- Default allowlist is ["python", "java"].
- Round-trip serialization preserves language_allowlist.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.experiment import ExperimentMatrix
from sec_review_framework.db import Database
from sec_review_framework.reporting.generator import ReportGenerator


# ---------------------------------------------------------------------------
# Minimal reporter (no-op)
# ---------------------------------------------------------------------------

class _NullReporter(ReportGenerator):
    def render_run(self, result, output_dir: Path) -> None:  # type: ignore[override]
        pass

    def render_matrix(self, results, output_dir: Path) -> None:  # type: ignore[override]
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODEL_ID = "claude-opus-4-5"
_DATASET_VERSION = "1.0.0"


def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_coordinator(tmp_path: Path) -> ExperimentCoordinator:
    """Build a minimal ExperimentCoordinator with a temp SQLite DB (no K8s)."""
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
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


def _seed_dataset(coordinator: ExperimentCoordinator, name: str, language: str | None) -> None:
    """Insert a dataset row with the given language (or no language) into the DB."""
    meta: dict = {}
    if language is not None:
        meta["language"] = language
    row: dict = {
        "name": name,
        "kind": "git",
        "origin_url": "https://github.com/test/repo",
        "origin_commit": "abc123",
        "metadata_json": json.dumps(meta),
        "created_at": "2026-01-01T00:00:00",
    }
    _run_async(coordinator.db.create_dataset(row))


def _make_matrix(
    dataset_name: str,
    allowlist: list[str],
    experiment_id: str = "test-exp-001",
) -> ExperimentMatrix:
    return ExperimentMatrix(
        experiment_id=experiment_id,
        dataset_name=dataset_name,
        dataset_version=_DATASET_VERSION,
        strategy_ids=["builtin.single_agent"],
        language_allowlist=allowlist,
        allow_unavailable_models=True,
    )


# ---------------------------------------------------------------------------
# TestClient fixture factory
# ---------------------------------------------------------------------------

def _make_test_client(coordinator: ExperimentCoordinator):
    import sec_review_framework.coordinator as coord_module
    original = coord_module.coordinator
    coord_module.coordinator = coordinator
    client = TestClient(app, raise_server_exceptions=False)
    client.__enter__()

    class _Ctx:
        def __init__(self, c, orig, mod):
            self.client = c
            self._orig = orig
            self._mod = mod

        def __enter__(self):
            return self.client

        def __exit__(self, *args):
            self._mod.coordinator = self._orig

    return _Ctx(client, original, coord_module)


# ---------------------------------------------------------------------------
# Tests: ExperimentMatrix model
# ---------------------------------------------------------------------------


class TestLanguageAllowlistField:
    """The language_allowlist field on ExperimentMatrix."""

    def test_default_allowlist_is_python_and_java(self):
        """Default language_allowlist must be ['python', 'java']."""
        matrix = ExperimentMatrix(
            experiment_id="exp",
            dataset_name="ds",
            dataset_version="1.0",
            strategy_ids=["builtin.single_agent"],
        )
        assert matrix.language_allowlist == ["python", "java"]

    def test_custom_allowlist_set_correctly(self):
        """A custom allowlist is stored verbatim."""
        matrix = ExperimentMatrix(
            experiment_id="exp",
            dataset_name="ds",
            dataset_version="1.0",
            strategy_ids=["builtin.single_agent"],
            language_allowlist=["python", "go"],
        )
        assert matrix.language_allowlist == ["python", "go"]

    def test_empty_allowlist_is_preserved(self):
        """An empty list disables the gate and must round-trip correctly."""
        matrix = ExperimentMatrix(
            experiment_id="exp",
            dataset_name="ds",
            dataset_version="1.0",
            strategy_ids=["builtin.single_agent"],
            language_allowlist=[],
        )
        assert matrix.language_allowlist == []

    def test_round_trip_serialization_preserves_allowlist(self):
        """language_allowlist must survive a model_dump_json / model_validate_json round-trip."""
        original = ExperimentMatrix(
            experiment_id="exp",
            dataset_name="ds",
            dataset_version="1.0",
            strategy_ids=["builtin.single_agent"],
            language_allowlist=["python", "java", "go"],
        )
        serialized = original.model_dump_json()
        restored = ExperimentMatrix.model_validate_json(serialized)
        assert restored.language_allowlist == ["python", "java", "go"]

    def test_round_trip_preserves_default_allowlist(self):
        """Default allowlist also survives serialization (not excluded from JSON)."""
        original = ExperimentMatrix(
            experiment_id="exp",
            dataset_name="ds",
            dataset_version="1.0",
            strategy_ids=["builtin.single_agent"],
        )
        serialized = original.model_dump_json()
        data = json.loads(serialized)
        # Field must be present in serialized output (exclude=False is the default)
        assert "language_allowlist" in data
        restored = ExperimentMatrix.model_validate_json(serialized)
        assert restored.language_allowlist == ["python", "java"]

    def test_old_experiment_without_allowlist_gets_default_on_load(self):
        """Experiments serialized without language_allowlist get the default on load."""
        old_json = json.dumps({
            "experiment_id": "old-exp",
            "dataset_name": "ds",
            "dataset_version": "1.0",
            "strategy_ids": ["builtin.single_agent"],
            # language_allowlist intentionally absent — simulates pre-this-change serialized data
        })
        restored = ExperimentMatrix.model_validate_json(old_json)
        assert restored.language_allowlist == ["python", "java"]


# ---------------------------------------------------------------------------
# Tests: Dispatch gate via TestClient
# ---------------------------------------------------------------------------


class TestLanguageGate:
    """The language allowlist gate in the POST /experiments endpoint."""

    def test_matching_language_dispatches(self, tmp_path: Path):
        """Allowlist ['python'] + dataset language='python' → 201."""
        coord = _make_coordinator(tmp_path)
        _seed_dataset(coord, "ds-py", language="python")

        matrix = _make_matrix("ds-py", allowlist=["python"])

        import sec_review_framework.coordinator as coord_module
        original = coord_module.coordinator
        coord_module.coordinator = coord
        try:
            with patch.object(coord, "submit_experiment", new_callable=AsyncMock) as mock_submit:
                mock_submit.return_value = matrix.experiment_id
                with patch.object(coord, "enrich_model_configs", new_callable=AsyncMock):
                    with TestClient(app, raise_server_exceptions=True) as client:
                        resp = client.post("/experiments", json=matrix.model_dump())
            assert resp.status_code == 201, resp.text
            mock_submit.assert_awaited_once()
        finally:
            coord_module.coordinator = original

    def test_wrong_language_raises_400(self, tmp_path: Path):
        """Allowlist ['python'] + dataset language='c' → 400 mentioning dataset, language, allowlist."""
        coord = _make_coordinator(tmp_path)
        _seed_dataset(coord, "ds-c", language="c")

        matrix = _make_matrix("ds-c", allowlist=["python"])

        import sec_review_framework.coordinator as coord_module
        original = coord_module.coordinator
        coord_module.coordinator = coord
        try:
            with patch.object(coord, "submit_experiment", new_callable=AsyncMock) as mock_submit:
                with patch.object(coord, "enrich_model_configs", new_callable=AsyncMock):
                    with TestClient(app, raise_server_exceptions=False) as client:
                        resp = client.post("/experiments", json=matrix.model_dump())
            assert resp.status_code == 400, resp.text
            detail = resp.json().get("detail", "")
            assert "ds-c" in detail, f"Dataset name missing from error: {detail}"
            assert "'c'" in detail or '"c"' in detail, f"Language missing from error: {detail}"
            assert "python" in detail, f"Allowlist missing from error: {detail}"
            # Worker must never have been called
            mock_submit.assert_not_awaited()
        finally:
            coord_module.coordinator = original

    def test_no_language_in_metadata_dispatches_with_warning(self, tmp_path: Path, caplog):
        """Allowlist ['python'] + dataset with no language → 201 with a warning logged."""
        import logging

        coord = _make_coordinator(tmp_path)
        _seed_dataset(coord, "ds-nolang", language=None)

        matrix = _make_matrix("ds-nolang", allowlist=["python"])

        import sec_review_framework.coordinator as coord_module
        original = coord_module.coordinator
        coord_module.coordinator = coord
        try:
            with patch.object(coord, "submit_experiment", new_callable=AsyncMock) as mock_submit:
                mock_submit.return_value = matrix.experiment_id
                with patch.object(coord, "enrich_model_configs", new_callable=AsyncMock):
                    with caplog.at_level(logging.WARNING, logger="sec_review_framework.coordinator"):
                        with TestClient(app, raise_server_exceptions=True) as client:
                            resp = client.post("/experiments", json=matrix.model_dump())
            assert resp.status_code == 201, resp.text
            mock_submit.assert_awaited_once()
            # Warning must have been emitted
            assert any("language" in r.message.lower() for r in caplog.records), (
                f"Expected a warning about missing language. Records: {[r.message for r in caplog.records]}"
            )
        finally:
            coord_module.coordinator = original

    def test_empty_allowlist_dispatches_all(self, tmp_path: Path):
        """Empty allowlist disables the gate — any dataset language dispatches."""
        coord = _make_coordinator(tmp_path)
        _seed_dataset(coord, "ds-cpp", language="c++")

        matrix = _make_matrix("ds-cpp", allowlist=[])

        import sec_review_framework.coordinator as coord_module
        original = coord_module.coordinator
        coord_module.coordinator = coord
        try:
            with patch.object(coord, "submit_experiment", new_callable=AsyncMock) as mock_submit:
                mock_submit.return_value = matrix.experiment_id
                with patch.object(coord, "enrich_model_configs", new_callable=AsyncMock):
                    with TestClient(app, raise_server_exceptions=True) as client:
                        resp = client.post("/experiments", json=matrix.model_dump())
            assert resp.status_code == 201, resp.text
            mock_submit.assert_awaited_once()
        finally:
            coord_module.coordinator = original

    def test_error_fires_before_worker_invocation(self, tmp_path: Path):
        """When the gate raises, coordinator.submit_experiment is never awaited."""
        coord = _make_coordinator(tmp_path)
        _seed_dataset(coord, "ds-java", language="java")

        # Allowlist does NOT include java
        matrix = _make_matrix("ds-java", allowlist=["python"])

        import sec_review_framework.coordinator as coord_module
        original = coord_module.coordinator
        coord_module.coordinator = coord
        try:
            with patch.object(coord, "submit_experiment", new_callable=AsyncMock) as mock_submit:
                with patch.object(coord, "enrich_model_configs", new_callable=AsyncMock):
                    with TestClient(app, raise_server_exceptions=False) as client:
                        resp = client.post("/experiments", json=matrix.model_dump())
            assert resp.status_code == 400
            mock_submit.assert_not_awaited()
        finally:
            coord_module.coordinator = original

    def test_dataset_without_row_passes_gate(self, tmp_path: Path):
        """If the dataset is not in the DB, the gate does not block dispatch
        (unknown dataset will be caught later by other validation)."""
        coord = _make_coordinator(tmp_path)
        # Do NOT seed any dataset row

        matrix = _make_matrix("ds-unknown", allowlist=["python"])

        import sec_review_framework.coordinator as coord_module
        original = coord_module.coordinator
        coord_module.coordinator = coord
        try:
            with patch.object(coord, "submit_experiment", new_callable=AsyncMock) as mock_submit:
                mock_submit.return_value = matrix.experiment_id
                with patch.object(coord, "enrich_model_configs", new_callable=AsyncMock):
                    with TestClient(app, raise_server_exceptions=True) as client:
                        resp = client.post("/experiments", json=matrix.model_dump())
            # submit_experiment was reached (gate did not block)
            mock_submit.assert_awaited_once()
        finally:
            coord_module.coordinator = original
