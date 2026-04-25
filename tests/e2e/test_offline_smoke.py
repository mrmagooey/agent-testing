"""Smoke Test Layer 1: Offline Pipeline Smoke

Exercises the full ExperimentWorker pipeline with a FakeModelProvider.
No LLM API calls, no K8s. Should complete in < 5 seconds.

Scenario:
- Target repo has two vulns: SQLi in views.py and hardcoded secret in auth.py
- Ground truth has labels for both
- FakeModelProvider returns one TP (SQLi) + one FP (wrong file)
- hardcoded_secret label is missed → FN

Expected metrics: TP=1, FP=1, FN=1, precision=0.5, recall=0.5
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import FakeModelProvider
from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource
from sec_review_framework.data.experiment import (
    ExperimentRun,
    ReviewProfileName,
    RunResult,
    RunStatus,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.models.base import ModelResponse, RetryPolicy
from sec_review_framework.worker import ExperimentWorker, ModelProviderFactory


# ---------------------------------------------------------------------------
# Canned LLM response: 1 TP (SQLi in views.py) + 1 FP (wrong file)
# ---------------------------------------------------------------------------

_FAKE_RESPONSE_CONTENT = """\
I have analysed the codebase. Here are my findings:

```json
[
  {
    "file_path": "myapp/views.py",
    "line_start": 3,
    "line_end": 4,
    "vuln_class": "sqli",
    "cwe_ids": ["CWE-89"],
    "severity": "high",
    "title": "SQL Injection in search view",
    "description": "User-supplied input is concatenated directly into a SQL query string, allowing an attacker to inject arbitrary SQL.",
    "recommendation": "Use parameterised queries or an ORM.",
    "confidence": 0.95
  },
  {
    "file_path": "myapp/nonexistent.py",
    "line_start": 10,
    "line_end": 12,
    "vuln_class": "sqli",
    "cwe_ids": ["CWE-89"],
    "severity": "medium",
    "title": "Possible SQL injection (false positive)",
    "description": "This file does not actually exist in the repo.",
    "recommendation": "Investigate further.",
    "confidence": 0.3
  }
]
```
"""


# ---------------------------------------------------------------------------
# Fixture: full datasets directory + output directory
# ---------------------------------------------------------------------------


@pytest.fixture
def smoke_labels() -> list[GroundTruthLabel]:
    """Ground-truth labels for the smoke dataset.

    These are injected into the worker via a mock on ``_fetch_labels`` rather
    than written to a labels.jsonl file.  Labels are stored in the coordinator
    DB (Phase 2B) and fetched via HTTP in production.
    """
    dataset_version = "1.0.0"
    return [
        GroundTruthLabel(
            id="lbl-sqli-001",
            dataset_version=dataset_version,
            file_path="myapp/views.py",
            line_start=3,
            line_end=4,
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="SQL injection via string formatting in query parameter",
            source=GroundTruthSource.INJECTED,
            confidence="confirmed",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        GroundTruthLabel(
            id="lbl-secret-001",
            dataset_version=dataset_version,
            file_path="myapp/auth.py",
            line_start=1,
            line_end=1,
            cwe_id="CWE-798",
            vuln_class=VulnClass.HARDCODED_SECRET,
            severity=Severity.CRITICAL,
            description="Hardcoded secret key in auth module",
            source=GroundTruthSource.INJECTED,
            confidence="confirmed",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
    ]


@pytest.fixture
def smoke_dirs(tmp_path: Path):
    """Build the datasets_dir and output_dir expected by ExperimentWorker.

    Directory layout produced:
        tmp_path/
          datasets/
            targets/
              smoke-dataset/
                repo/
                  myapp/
                    __init__.py
                    views.py   ← SQLi vuln (TP target)
                    auth.py    ← hardcoded secret (will be missed → FN)
          output/

    Ground truth labels are supplied via the ``smoke_labels`` fixture and
    injected into the worker via ``ExperimentWorker._fetch_labels``.  No
    labels.jsonl file is written — labels now live in the coordinator DB
    (Phase 2B) and workers fetch them over HTTP.
    """
    dataset_name = "smoke-dataset"
    dataset_version = "1.0.0"

    # --- repo files ---
    repo_dir = tmp_path / "datasets" / "targets" / dataset_name / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "myapp").mkdir()
    (repo_dir / "myapp" / "__init__.py").write_text("")

    (repo_dir / "myapp" / "views.py").write_text(
        "def search(request):\n"
        '    q = request.GET.get("q")\n'
        '    query = "SELECT * FROM users WHERE name = \'%s\'" % q\n'
        "    cursor.execute(query)\n"
        "    return cursor.fetchall()\n"
    )

    (repo_dir / "myapp" / "auth.py").write_text(
        "SECRET_KEY = 'super-secret-hardcoded-value'\n"
        "\n"
        "def get_token():\n"
        "    return SECRET_KEY\n"
    )

    output_dir = tmp_path / "output" / "smoke-run"

    return {
        "datasets_dir": tmp_path / "datasets",
        "output_dir": output_dir,
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
    }


# ---------------------------------------------------------------------------
# Fixture: ExperimentRun
# ---------------------------------------------------------------------------


@pytest.fixture
def smoke_run(smoke_dirs) -> ExperimentRun:
    return ExperimentRun(
        id="smoke-experiment_fake-model_single_agent_with_tools_default_none",
        experiment_id="smoke-experiment",
        strategy_id="builtin.single_agent",
        model_id="fake-model",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name=smoke_dirs["dataset_name"],
        dataset_version=smoke_dirs["dataset_version"],
        created_at=datetime(2026, 4, 16, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Fixture: FakeModelProvider with a single canned response
# ---------------------------------------------------------------------------


@pytest.fixture
def smoke_model() -> FakeModelProvider:
    return FakeModelProvider(
        responses=[
            ModelResponse(
                content=_FAKE_RESPONSE_CONTENT,
                tool_calls=[],          # no tool calls → agentic loop terminates in one turn
                input_tokens=500,
                output_tokens=200,
                model_id="fake-model",
                raw={},
            )
        ],
        retry_policy=RetryPolicy(max_retries=0),
    )


# ---------------------------------------------------------------------------
# Helper: run the worker under the patch
# ---------------------------------------------------------------------------


def _run_smoke(
    run: ExperimentRun,
    smoke_dirs: dict,
    fake_model: FakeModelProvider,
    labels: list | None = None,
) -> RunResult:
    """Patch ModelProviderFactory.create to return fake_model, execute worker.

    ``labels`` is injected via ``ExperimentWorker._fetch_labels`` so tests do
    not need a live coordinator HTTP endpoint.  Pass None to skip evaluation.
    """
    _labels = labels or []

    def _fake_create(self, model_id, model_config):  # noqa: ANN001
        return fake_model

    def _fake_fetch_labels(self, run, datasets_dir):  # noqa: ANN001
        return _labels

    with patch.object(ModelProviderFactory, "create", _fake_create):
        with patch.object(ExperimentWorker, "_fetch_labels", _fake_fetch_labels):
            ExperimentWorker().run(run, smoke_dirs["output_dir"], smoke_dirs["datasets_dir"])

    result_json = (smoke_dirs["output_dir"] / "run_result.json").read_text()
    return RunResult.model_validate_json(result_json)


# ---------------------------------------------------------------------------
# The smoke test
# ---------------------------------------------------------------------------


def test_offline_pipeline_smoke(smoke_dirs, smoke_run, smoke_model, smoke_labels):
    """Full ExperimentWorker pipeline smoke test — no real LLM calls, no K8s."""
    output_dir: Path = smoke_dirs["output_dir"]

    result = _run_smoke(smoke_run, smoke_dirs, smoke_model, labels=smoke_labels)

    # --- run_result.json ---
    run_result_path = output_dir / "run_result.json"
    assert run_result_path.exists(), "run_result.json must be written"

    raw = json.loads(run_result_path.read_text())
    assert raw["status"] == "completed", f"Expected 'completed', got {raw['status']!r}"

    assert result.status == RunStatus.COMPLETED
    assert result.error is None

    # --- findings.jsonl: exactly 2 findings ---
    findings_path = output_dir / "findings.jsonl"
    assert findings_path.exists(), "findings.jsonl must be written"

    finding_lines = [ln for ln in findings_path.read_text().splitlines() if ln.strip()]
    assert len(finding_lines) == 2, (
        f"Expected 2 findings (1 TP + 1 FP), got {len(finding_lines)}"
    )

    # --- tool_calls.jsonl: file must exist (may be empty) ---
    tool_calls_path = output_dir / "tool_calls.jsonl"
    assert tool_calls_path.exists(), "tool_calls.jsonl must be written"

    # --- conversation.jsonl: must exist and have at least one entry ---
    conversation_path = output_dir / "conversation.jsonl"
    assert conversation_path.exists(), "conversation.jsonl must be written"

    conv_lines = [ln for ln in conversation_path.read_text().splitlines() if ln.strip()]
    assert len(conv_lines) >= 1, "conversation.jsonl must contain at least one entry"

    # --- report.md: must exist and be non-empty ---
    report_path = output_dir / "report.md"
    assert report_path.exists(), "report.md must be written"
    assert report_path.stat().st_size > 0, "report.md must be non-empty"

    # --- Evaluation metrics ---
    assert result.evaluation is not None, "Evaluation must be present for a completed run"

    eval_ = result.evaluation
    assert eval_.true_positives == 1, (
        f"Expected 1 TP (SQLi in views.py), got {eval_.true_positives}"
    )
    assert eval_.false_positives == 1, (
        f"Expected 1 FP (nonexistent.py finding), got {eval_.false_positives}"
    )
    assert eval_.false_negatives == 1, (
        f"Expected 1 FN (hardcoded_secret in auth.py was missed), got {eval_.false_negatives}"
    )

    assert eval_.precision == pytest.approx(0.5, abs=1e-6), (
        f"Expected precision=0.5 (1 TP / 2 findings), got {eval_.precision}"
    )
    assert eval_.recall == pytest.approx(0.5, abs=1e-6), (
        f"Expected recall=0.5 (1 TP / 2 labels), got {eval_.recall}"
    )

    # --- Cost and duration ---
    assert result.estimated_cost_usd >= 0, "estimated_cost_usd must be >= 0 (0 for unknown model)"
    assert result.duration_seconds > 0, "duration_seconds must be > 0"
