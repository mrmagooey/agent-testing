"""Integration tests: POST /experiments gates on language_allowlist (Story 59).

Tests:
1. Empty allowlist → dataset with language='java' is accepted (gate disabled).
2. Allowlist mismatch → dataset language='java', allowlist=['python'] → 400 with useful detail.
3. Allowlist match → dataset language='python', allowlist=['python','typescript'] → 200.
4. Dataset missing metadata.language → non-empty allowlist passes through (backward compat).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator
from tests.helpers import make_smoke_strategy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0),
        }
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        config_dir=config_dir,
        default_cap=4,
    )


async def _seed_dataset(db: Database, name: str, metadata: dict) -> None:
    """Insert a minimal dataset row with the given metadata_json."""
    await db.create_dataset(
        {
            "name": name,
            "kind": "git",
            "origin_url": "https://example.com/repo.git",
            "origin_commit": "abc123",
            "metadata_json": json.dumps(metadata),
            "created_at": datetime.now(UTC).isoformat(),
        }
    )


def _submit_payload(
    strategy_id: str,
    dataset_name: str,
    *,
    language_allowlist: list[str] | None = None,
) -> dict:
    payload: dict = {
        "experiment_id": f"test-exp-{dataset_name}",
        "dataset_name": dataset_name,
        "dataset_version": "1.0",
        "strategy_ids": [strategy_id],
        "allow_unavailable_models": True,
    }
    if language_allowlist is not None:
        payload["language_allowlist"] = language_allowlist
    return payload


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def setup(tmp_path: Path):
    """Yield (client, coordinator, db) for language-allowlist submit tests."""
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    strategy = make_smoke_strategy("gpt-4o")
    await db.insert_user_strategy(strategy)

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client, c, db, strategy


# ---------------------------------------------------------------------------
# Test 1: Empty allowlist → gate disabled, language='java' is accepted
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_empty_allowlist_accepts_any_language(setup):
    """An empty language_allowlist disables the gate; any dataset language passes."""
    client, c, db, strategy = setup

    await _seed_dataset(db, "ds-java-open", {"language": "java"})

    with patch.object(c, "submit_experiment", return_value="exp-open"):
        resp = client.post(
            "/experiments",
            json=_submit_payload(strategy.id, "ds-java-open"),
            # no language_allowlist key → defaults to []
        )

    assert resp.status_code == 201, resp.text
    assert resp.json()["experiment_id"] == "exp-open"


# ---------------------------------------------------------------------------
# Test 2: Allowlist mismatches dataset language → 400 with useful detail
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_allowlist_mismatch_returns_400(setup):
    """dataset.language='java' vs allowlist=['python'] → HTTP 400.

    The error detail must name the dataset language ('java') and the allowlist
    ('python') so the caller knows exactly what went wrong.
    """
    client, c, db, strategy = setup

    await _seed_dataset(db, "ds-java-mismatch", {"language": "java"})

    resp = client.post(
        "/experiments",
        json=_submit_payload(
            strategy.id, "ds-java-mismatch", language_allowlist=["python"]
        ),
    )

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    # The error detail is a string containing the dataset language and allowlist.
    assert "java" in detail, f"Expected 'java' in detail: {detail!r}"
    assert "python" in detail, f"Expected 'python' in detail: {detail!r}"


# ---------------------------------------------------------------------------
# Test 3: Allowlist matches dataset language → 200
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_allowlist_match_accepts_experiment(setup):
    """dataset.language='python' is in allowlist=['python','typescript'] → 201."""
    client, c, db, strategy = setup

    await _seed_dataset(db, "ds-python-match", {"language": "python"})

    with patch.object(c, "submit_experiment", return_value="exp-match"):
        resp = client.post(
            "/experiments",
            json=_submit_payload(
                strategy.id,
                "ds-python-match",
                language_allowlist=["python", "typescript"],
            ),
        )

    assert resp.status_code == 201, resp.text
    assert resp.json()["experiment_id"] == "exp-match"


# ---------------------------------------------------------------------------
# Test 4: Dataset missing metadata.language → accepted (backward compat)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_missing_language_metadata_passes_with_warning(setup, caplog):
    """Dataset with no metadata_json.language passes through even with a non-empty allowlist.

    A warning must be logged (backward-compat path).
    """
    client, c, db, strategy = setup

    # metadata intentionally has no 'language' key
    await _seed_dataset(db, "ds-no-lang", {"version": "1.2.3", "some_key": "value"})

    with patch.object(c, "submit_experiment", return_value="exp-nolang"):
        with caplog.at_level(logging.WARNING, logger="sec_review_framework.coordinator"):
            resp = client.post(
                "/experiments",
                json=_submit_payload(
                    strategy.id,
                    "ds-no-lang",
                    language_allowlist=["python"],
                ),
            )

    assert resp.status_code == 201, resp.text
    assert resp.json()["experiment_id"] == "exp-nolang"

    # The gate should have logged a warning about the missing language field.
    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "no metadata_json.language" in str(m) or "skipping language gate" in str(m)
        for m in warning_messages
    ), f"Expected language-gate warning in logs; got: {warning_messages}"
