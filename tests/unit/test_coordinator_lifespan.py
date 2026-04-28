"""Unit tests for the lifespan() startup error paths in coordinator.py.

Covers three failure modes that occur before the ``yield``:
  1. Fernet import failure (missing / malformed LLM_PROVIDER_ENCRYPTION_KEY).
  2. ProviderCatalog.start() exception when background tasks are enabled.
  3. coordinator.reconcile() exception during startup.

All tests invoke the lifespan async-context-manager directly via a minimal
FastAPI stub so they remain independent of a live K8s cluster or real DB.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import ExperimentCoordinator, lifespan
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="unused",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(
            pricing={"fake-model": ModelPricing(input_per_million=0.0, output_per_million=0.0)}
        ),
        default_cap=4,
    )


class _FakeApp:
    """Minimal stand-in for the FastAPI app object passed to lifespan."""


# ---------------------------------------------------------------------------
# Test 1: fernet import failure surfaces as startup error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_fernet_import_failure_raises(tmp_path: Path):
    db = Database(tmp_path / "coordinator.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)
    coord.storage_root.mkdir(parents=True, exist_ok=True)

    fernet_key = "sec_review_framework.secrets.fernet"

    with patch.object(coord_module, "coordinator", coord):
        with patch.object(coord, "reconcile", new=AsyncMock(return_value=None)):
            with patch("sec_review_framework.coordinator._seed_builtin_strategies", new=AsyncMock()):
                saved = sys.modules.pop(fernet_key, None)
                sys.modules[fernet_key] = None  # type: ignore[assignment]
                try:
                    with pytest.raises((ImportError, AttributeError)):
                        async with lifespan(_FakeApp()):
                            pass
                finally:
                    if saved is not None:
                        sys.modules[fernet_key] = saved
                    else:
                        sys.modules.pop(fernet_key, None)


# ---------------------------------------------------------------------------
# Test 2: catalog.start() exception propagates as startup error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_catalog_start_exception_propagates(tmp_path: Path):
    db = Database(tmp_path / "coordinator.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)
    coord.storage_root.mkdir(parents=True, exist_ok=True)

    fake_catalog = MagicMock()
    fake_catalog.start = AsyncMock(side_effect=RuntimeError("probe backend unavailable"))
    fake_catalog.stop = AsyncMock(return_value=None)

    with patch.object(coord_module, "coordinator", coord):
        with patch.object(coord, "reconcile", new=AsyncMock(return_value=None)):
            with patch("sec_review_framework.coordinator._seed_builtin_strategies", new=AsyncMock()):
                with patch("sec_review_framework.coordinator.ProviderCatalog", return_value=fake_catalog):
                    with pytest.raises(RuntimeError, match="probe backend unavailable"):
                        with patch.dict(
                            "os.environ",
                            {"ENABLE_BACKGROUND_TASKS": "1"},
                            clear=False,
                        ):
                            async with lifespan(_FakeApp()):
                                pass


# ---------------------------------------------------------------------------
# Test 3: reconcile() exception during startup propagates as startup error
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_reconcile_exception_propagates(tmp_path: Path):
    db = Database(tmp_path / "coordinator.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)
    coord.storage_root.mkdir(parents=True, exist_ok=True)

    with patch.object(coord_module, "coordinator", coord):
        with patch.object(
            coord,
            "reconcile",
            new=AsyncMock(side_effect=RuntimeError("DB connection lost")),
        ):
            with patch("sec_review_framework.coordinator._seed_builtin_strategies", new=AsyncMock()):
                with pytest.raises(RuntimeError, match="DB connection lost"):
                    async with lifespan(_FakeApp()):
                        pass
