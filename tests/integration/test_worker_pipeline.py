"""Integration tests for ExperimentWorker.run().

Uses FakeModelProvider to inject canned LLM responses so no real API calls
are made.  Uses a real temp directory as the datasets root and output root.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tests.conftest import FakeModelProvider
from sec_review_framework.data.experiment import (
    ExperimentRun,
    ReviewProfileName,
    RunStatus,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.models.base import ModelResponse, RetryPolicy
from sec_review_framework.worker import ExperimentWorker


# ---------------------------------------------------------------------------
# Canned finding JSON that the fake model will return
# ---------------------------------------------------------------------------

SQLI_FINDING_JSON = json.dumps([
    {
        "file_path": "myapp/views.py",
        "line_start": 3,
        "line_end": 4,
        "vuln_class": "sqli",
        "cwe_ids": ["CWE-89"],
        "severity": "high",
        "title": "SQL Injection in search",
        "description": "User input concatenated directly into SQL query.",
        "recommendation": "Use parameterized queries.",
        "confidence": 0.95,
    }
])

EMPTY_FINDINGS_JSON = json.dumps([])


def _canned_response(content: str) -> ModelResponse:
    return ModelResponse(
        content=f"Analysis complete.\n\n```json\n{content}\n```",
        tool_calls=[],
        input_tokens=100,
        output_tokens=50,
        model_id="fake-model",
        raw={},
    )


def _no_finding_response() -> ModelResponse:
    return ModelResponse(
        content=f"No issues found.\n\n```json\n{EMPTY_FINDINGS_JSON}\n```",
        tool_calls=[],
        input_tokens=50,
        output_tokens=20,
        model_id="fake-model",
        raw={},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def datasets_dir(tmp_path: Path) -> Path:
    """Create a minimal datasets directory matching LabelStore.load() expectations."""
    ds_root = tmp_path / "datasets"
    repo_dir = ds_root / "targets" / "test-dataset" / "repo"
    repo_dir.mkdir(parents=True)

    # Source files the strategy will read
    (repo_dir / "myapp").mkdir()
    (repo_dir / "myapp" / "views.py").write_text(
        'def search(request):\n'
        '    q = request.GET.get("q")\n'
        '    query = "SELECT * FROM users WHERE name = \'%s\'" % q\n'
        '    cursor.execute(query)\n'
        '    return cursor.fetchall()\n'
    )
    (repo_dir / "myapp" / "__init__.py").write_text("")

    # Labels JSONL — required by LabelStore.load()
    labels_path = ds_root / "targets" / "test-dataset" / "labels.jsonl"
    label = {
        "id": "lbl-sqli",
        "dataset_version": "1.0.0",
        "file_path": "myapp/views.py",
        "line_start": 3,
        "line_end": 4,
        "cwe_id": "CWE-89",
        "vuln_class": "sqli",
        "severity": "high",
        "description": "SQL injection via string formatting",
        "source": "injected",
        "confidence": "confirmed",
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    labels_path.write_text(json.dumps(label) + "\n")

    return ds_root


@pytest.fixture
def base_run() -> ExperimentRun:
    """A minimal ExperimentRun for single_agent / with_tools."""
    return ExperimentRun(
        id="batch-w_fake-model_single_agent_with_tools_default_none",
        batch_id="batch-w",
        model_id="fake-model",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITHOUT_TOOLS,  # avoids real semgrep/file tools
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="test-dataset",
        dataset_version="1.0.0",
        created_at=datetime(2026, 4, 16, tzinfo=timezone.utc),
    )


def _run_worker(
    run: ExperimentRun,
    datasets_dir: Path,
    output_dir: Path,
    responses: list[ModelResponse],
) -> Path:
    """Patch ModelProviderFactory to return our FakeModelProvider, then run worker."""
    from unittest.mock import patch
    from sec_review_framework.worker import ModelProviderFactory

    fake = FakeModelProvider(responses, retry_policy=RetryPolicy(max_retries=0))

    def _fake_create(self, model_id, model_config):  # noqa: ANN001
        return fake

    with patch.object(ModelProviderFactory, "create", _fake_create):
        worker = ExperimentWorker()
        worker.run(run, output_dir, datasets_dir)

    return output_dir


# ---------------------------------------------------------------------------
# Test 1: run() completes and writes run_result.json
# ---------------------------------------------------------------------------

def test_worker_writes_run_result_json(base_run, datasets_dir, tmp_path):
    output_dir = tmp_path / "output" / base_run.id
    _run_worker(base_run, datasets_dir, output_dir, [_canned_response(SQLI_FINDING_JSON)])

    result_file = output_dir / "run_result.json"
    assert result_file.exists(), "run_result.json was not created"

    from sec_review_framework.data.experiment import RunResult
    result = RunResult.model_validate_json(result_file.read_text())
    assert result.status == RunStatus.COMPLETED
    assert result.error is None


# ---------------------------------------------------------------------------
# Test 2: findings.jsonl written with correct format
# ---------------------------------------------------------------------------

def test_worker_writes_findings_jsonl(base_run, datasets_dir, tmp_path):
    output_dir = tmp_path / "output" / base_run.id
    _run_worker(base_run, datasets_dir, output_dir, [_canned_response(SQLI_FINDING_JSON)])

    findings_file = output_dir / "findings.jsonl"
    assert findings_file.exists()

    lines = [l for l in findings_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 1

    finding = json.loads(lines[0])
    assert finding["vuln_class"] == "sqli"
    assert finding["file_path"] == "myapp/views.py"
    assert "title" in finding


# ---------------------------------------------------------------------------
# Test 3: tool_calls.jsonl written (may be empty for WITHOUT_TOOLS)
# ---------------------------------------------------------------------------

def test_worker_writes_tool_calls_jsonl(base_run, datasets_dir, tmp_path):
    output_dir = tmp_path / "output" / base_run.id
    _run_worker(base_run, datasets_dir, output_dir, [_canned_response(EMPTY_FINDINGS_JSON)])

    assert (output_dir / "tool_calls.jsonl").exists()


# ---------------------------------------------------------------------------
# Test 4: conversation.jsonl written
# ---------------------------------------------------------------------------

def test_worker_writes_conversation_jsonl(base_run, datasets_dir, tmp_path):
    output_dir = tmp_path / "output" / base_run.id
    _run_worker(base_run, datasets_dir, output_dir, [_canned_response(EMPTY_FINDINGS_JSON)])

    conv_file = output_dir / "conversation.jsonl"
    assert conv_file.exists()
    # At least one turn must be logged
    lines = [l for l in conv_file.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1


# ---------------------------------------------------------------------------
# Test 5: strategy that raises → status=FAILED, error captured
# ---------------------------------------------------------------------------

def test_worker_failed_strategy_captured(datasets_dir, tmp_path):
    """When the strategy raises, the worker captures the error and writes FAILED status."""
    from unittest.mock import patch, MagicMock
    from sec_review_framework.worker import StrategyFactory, ModelProviderFactory
    from sec_review_framework.models.base import RetryPolicy

    run = ExperimentRun(
        id="batch-fail_single_agent",
        batch_id="batch-fail",
        model_id="fake-model",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITHOUT_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="test-dataset",
        dataset_version="1.0.0",
    )

    fake = FakeModelProvider([], retry_policy=RetryPolicy(max_retries=0))

    def _fake_create_model(self, model_id, model_config):  # noqa: ANN001
        return fake

    def _fake_create_strategy(self, strategy_name):  # noqa: ANN001
        strategy = MagicMock()
        strategy.run.side_effect = RuntimeError("Strategy deliberately failed")
        return strategy

    output_dir = tmp_path / "output" / run.id

    with patch.object(ModelProviderFactory, "create", _fake_create_model):
        with patch.object(StrategyFactory, "create", _fake_create_strategy):
            worker = ExperimentWorker()
            worker.run(run, output_dir, datasets_dir)

    from sec_review_framework.data.experiment import RunResult
    result = RunResult.model_validate_json((output_dir / "run_result.json").read_text())
    assert result.status == RunStatus.FAILED
    assert result.error is not None
    assert "deliberately failed" in result.error


# ---------------------------------------------------------------------------
# Test 6: output directory is created if it doesn't exist
# ---------------------------------------------------------------------------

def test_worker_creates_output_dir(base_run, datasets_dir, tmp_path):
    output_dir = tmp_path / "deeply" / "nested" / "output" / base_run.id
    assert not output_dir.exists()

    _run_worker(base_run, datasets_dir, output_dir, [_canned_response(EMPTY_FINDINGS_JSON)])

    assert output_dir.exists()
    assert (output_dir / "run_result.json").exists()
