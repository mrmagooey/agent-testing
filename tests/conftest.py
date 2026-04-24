"""Common fixtures shared across unit and integration tests."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from sec_review_framework.data.evaluation import (
    EvaluationResult,
    EvidenceQuality,
    GroundTruthLabel,
    GroundTruthSource,
    MatchStatus,
    VerificationResult,
)
from sec_review_framework.data.experiment import (
    BundleSnapshot,
    ExperimentMatrix,
    ExperimentRun,
    ReviewProfileName,
    RunResult,
    RunStatus,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import (
    DedupEntry,
    Finding,
    Severity,
    StrategyOutput,
    VulnClass,
)
from sec_review_framework.models.base import (
    Message,
    ModelProvider,
    ModelResponse,
    RetryPolicy,
    ToolDefinition,
)


# Re-export the shared helper so conftest-based tests can still call it
from tests.helpers import make_test_bundle_snapshot  # noqa: F401


# ---------------------------------------------------------------------------
# FakeModelProvider — deterministic model for testing
# ---------------------------------------------------------------------------


class FakeModelProvider(ModelProvider):
    """A ModelProvider that returns canned responses from a queue.

    Usage::

        responses = [
            ModelResponse(content="hello", tool_calls=[], input_tokens=10,
                          output_tokens=5, model_id="fake", raw={}),
        ]
        model = FakeModelProvider(responses)
        result = model.complete([Message(role="user", content="hi")])
    """

    def __init__(
        self,
        responses: list[ModelResponse] | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        super().__init__(retry_policy=retry_policy or RetryPolicy(max_retries=0))
        self._responses: deque[ModelResponse] = deque(responses or [])

    def _do_complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        if not self._responses:
            return ModelResponse(
                content="No more canned responses.",
                tool_calls=[],
                input_tokens=10,
                output_tokens=5,
                model_id="fake-model",
                raw={},
            )
        return self._responses.popleft()

    def model_id(self) -> str:
        return "fake-model"


@pytest.fixture
def fake_model_provider() -> FakeModelProvider:
    """A FakeModelProvider with a single no-tool-call response."""
    return FakeModelProvider([
        ModelResponse(
            content='```json\n[]\n```',
            tool_calls=[],
            input_tokens=100,
            output_tokens=50,
            model_id="fake-model",
            raw={},
        ),
    ])


# ---------------------------------------------------------------------------
# Sample Finding
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_finding() -> Finding:
    """A well-formed SQLi finding with line citations."""
    return Finding(
        id="finding-001",
        file_path="myapp/views.py",
        line_start=42,
        line_end=45,
        vuln_class=VulnClass.SQLI,
        cwe_ids=["CWE-89"],
        severity=Severity.HIGH,
        title="SQL Injection in user search",
        description=(
            "myapp/views.py:42 builds a raw SQL query by concatenating user-supplied "
            "input directly into the query string. An attacker can inject arbitrary SQL "
            "via the `q` parameter, bypassing authentication or exfiltrating data."
        ),
        confidence=0.9,
        raw_llm_output="<raw output>",
        produced_by="single_agent",
        experiment_id="test-exp-001",
    )


@pytest.fixture
def sample_finding_fp() -> Finding:
    """A false-positive finding on a different file."""
    return Finding(
        id="finding-fp-001",
        file_path="myapp/other.py",
        line_start=10,
        line_end=12,
        vuln_class=VulnClass.SQLI,
        cwe_ids=["CWE-89"],
        severity=Severity.MEDIUM,
        title="Possible SQL injection",
        description="Might be a SQL injection issue.",
        confidence=0.3,
        raw_llm_output="<raw output>",
        produced_by="single_agent",
        experiment_id="test-exp-001",
    )


# ---------------------------------------------------------------------------
# Sample GroundTruthLabel
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_label() -> GroundTruthLabel:
    """A confirmed SQLi label matching sample_finding."""
    return GroundTruthLabel(
        id="label-001",
        dataset_version="1.0.0",
        file_path="myapp/views.py",
        line_start=40,
        line_end=46,
        cwe_id="CWE-89",
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        description="SQL injection via unsanitized query parameter",
        source=GroundTruthSource.CVE_PATCH,
        source_ref="CVE-2023-00001",
        confidence="confirmed",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Sample ExperimentRun
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_experiment_run() -> ExperimentRun:
    """A minimal experiment run fixture."""
    return ExperimentRun(
        id="experiment-example_builtin.single_agent",
        experiment_id="experiment-example",
        strategy_id="builtin.single_agent",
        model_id="gpt-4o",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="test-dataset",
        dataset_version="1.0.0",
        created_at=datetime(2026, 4, 16, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Sample ExperimentMatrix
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_experiment_matrix() -> ExperimentMatrix:
    """A small experiment matrix: 2 strategies = 2 base runs."""
    return ExperimentMatrix(
        experiment_id="test-experiment",
        dataset_name="test-dataset",
        dataset_version="1.0.0",
        strategy_ids=["builtin.single_agent", "builtin.per_file"],
    )


# ---------------------------------------------------------------------------
# Sample StrategyOutput
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_strategy_output(sample_finding: Finding) -> StrategyOutput:
    """A StrategyOutput with one finding and no dedup."""
    return StrategyOutput(
        findings=[sample_finding],
        pre_dedup_count=1,
        post_dedup_count=1,
        dedup_log=[],
    )


# ---------------------------------------------------------------------------
# Sample RunResult
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_run_result(
    sample_experiment_run: ExperimentRun,
    sample_finding: Finding,
    sample_strategy_output: StrategyOutput,
) -> RunResult:
    """A fully populated RunResult for testing reports and serialization."""
    from sec_review_framework.data.strategy_bundle import (
        OrchestrationShape,
        StrategyBundleDefault,
        UserStrategy,
    )
    _bundle = StrategyBundleDefault(
        system_prompt="You are a security reviewer.",
        user_prompt_template="Review this code.",
        model_id="gpt-4o",
        tools=frozenset(["read_file"]),
        verification="none",
        max_turns=80,
        tool_extensions=frozenset(),
    )
    _strategy = UserStrategy(
        id="builtin.single_agent",
        name="Single Agent (builtin)",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=_bundle,
        overrides=[],
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        is_builtin=True,
    )
    return RunResult(
        experiment=sample_experiment_run,
        status=RunStatus.COMPLETED,
        findings=[sample_finding],
        strategy_output=sample_strategy_output,
        bundle_snapshot=BundleSnapshot.capture(_strategy),
        tool_call_count=5,
        total_input_tokens=5000,
        total_output_tokens=1200,
        verification_tokens=0,
        estimated_cost_usd=0.42,
        duration_seconds=120.5,
        completed_at=datetime(2026, 4, 16, 1, 0, 0, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# Mock TargetCodebase (tmpdir-based)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_target_codebase(tmp_path: Path):
    """Creates a temp repo with known vuln files. Returns a TargetCodebase."""
    from sec_review_framework.ground_truth.models import TargetCodebase

    repo = tmp_path / "repo"
    repo.mkdir()

    (repo / "myapp").mkdir()
    (repo / "myapp" / "views.py").write_text(
        'def search(request):\n'
        '    q = request.GET.get("q")\n'
        '    query = "SELECT * FROM users WHERE name = \'%s\'" % q\n'
        '    cursor.execute(query)\n'
        '    return cursor.fetchall()\n'
    )
    (repo / "myapp" / "templates.py").write_text(
        'def render_profile(request):\n'
        '    name = request.GET.get("name")\n'
        '    return f"<h1>Welcome {name}</h1>"\n'
    )
    (repo / "myapp" / "utils.py").write_text(
        'import hashlib\n'
        'def hash_password(pw):\n'
        '    return hashlib.md5(pw.encode()).hexdigest()\n'
    )
    (repo / "myapp" / "__init__.py").write_text("")

    return TargetCodebase(repo)


@pytest.fixture
def sample_labels_for_mock_target() -> list[GroundTruthLabel]:
    """Labels matching the vulns in mock_target_codebase."""
    return [
        GroundTruthLabel(
            id="label-sqli",
            dataset_version="1.0.0",
            file_path="myapp/views.py",
            line_start=3,
            line_end=4,
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="SQL injection via string formatting",
            source=GroundTruthSource.INJECTED,
            confidence="confirmed",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        GroundTruthLabel(
            id="label-xss",
            dataset_version="1.0.0",
            file_path="myapp/templates.py",
            line_start=3,
            line_end=3,
            cwe_id="CWE-79",
            vuln_class=VulnClass.XSS,
            severity=Severity.MEDIUM,
            description="Reflected XSS via unescaped user input",
            source=GroundTruthSource.INJECTED,
            confidence="confirmed",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
        GroundTruthLabel(
            id="label-crypto",
            dataset_version="1.0.0",
            file_path="myapp/utils.py",
            line_start=3,
            line_end=3,
            cwe_id="CWE-327",
            vuln_class=VulnClass.CRYPTO_MISUSE,
            severity=Severity.MEDIUM,
            description="MD5 used for password hashing",
            source=GroundTruthSource.INJECTED,
            confidence="confirmed",
            created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        ),
    ]


# ---------------------------------------------------------------------------
# Temp Database
# ---------------------------------------------------------------------------


@pytest.fixture
async def temp_database(tmp_path: Path):
    """Creates a Database on a temp SQLite file, initialized with schema."""
    from sec_review_framework.db import Database

    db = Database(tmp_path / "test.db")
    await db.init()
    return db
