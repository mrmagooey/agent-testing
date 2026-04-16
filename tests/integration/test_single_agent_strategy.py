"""Integration tests for all scan strategies using FakeModelProvider.

Replaces the original placeholder. Each test wires a real strategy class to
FakeModelProvider + a real ToolRegistry + a real TargetCodebase in a tmp dir.
No real LLM or K8s calls are made.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import FakeModelProvider
from sec_review_framework.data.experiment import ToolVariant
from sec_review_framework.data.findings import StrategyOutput, VulnClass
from sec_review_framework.models.base import ModelResponse, RetryPolicy
from sec_review_framework.ground_truth.models import TargetCodebase
from sec_review_framework.strategies.single_agent import SingleAgentStrategy
from sec_review_framework.strategies.per_file import PerFileStrategy
from sec_review_framework.strategies.per_vuln_class import PerVulnClassStrategy
from sec_review_framework.strategies.sast_first import SASTFirstStrategy
from sec_review_framework.strategies.diff_review import DiffReviewStrategy
from sec_review_framework.tools.registry import ToolRegistryFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _finding_response(findings: list[dict] | None = None) -> ModelResponse:
    """Build a ModelResponse that embeds a JSON findings block."""
    payload = findings or []
    content = f"Here is my analysis.\n\n```json\n{json.dumps(payload)}\n```"
    return ModelResponse(
        content=content,
        tool_calls=[],
        input_tokens=100,
        output_tokens=50,
        model_id="fake-model",
        raw={},
    )


def _empty_response() -> ModelResponse:
    return _finding_response([])


_SQLI_FINDING = {
    "file_path": "myapp/views.py",
    "line_start": 3,
    "line_end": 4,
    "vuln_class": "sqli",
    "cwe_ids": ["CWE-89"],
    "severity": "high",
    "title": "SQL Injection in search handler",
    "description": "User input concatenated directly into SQL query.",
    "recommendation": "Use parameterized queries.",
    "confidence": 0.95,
}

_RCE_FINDING = {
    "file_path": "main.py",
    "line_start": 3,
    "line_end": 3,
    "vuln_class": "rce",
    "cwe_ids": ["CWE-78"],
    "severity": "critical",
    "title": "Shell injection via subprocess",
    "description": "Subprocess call with user-supplied input allows command injection.",
    "recommendation": "Validate and sanitize all shell command arguments.",
    "confidence": 0.9,
}


def _make_target(tmp_path: Path) -> TargetCodebase:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "myapp").mkdir()
    (repo / "myapp" / "views.py").write_text(
        'def search(request):\n'
        '    q = request.GET.get("q")\n'
        '    query = "SELECT * FROM users WHERE name = \'%s\'" % q\n'
        '    cursor.execute(query)\n'
    )
    (repo / "myapp" / "__init__.py").write_text("")
    return TargetCodebase(repo)


def _make_tools(target: TargetCodebase, with_tools: bool = False) -> object:
    variant = ToolVariant.WITH_TOOLS if with_tools else ToolVariant.WITHOUT_TOOLS
    return ToolRegistryFactory.create(variant, target)


def _base_config() -> dict:
    return {"experiment_id": "test-exp", "parallel": False}


# ---------------------------------------------------------------------------
# Test 1: SingleAgent with FakeModelProvider produces parseable StrategyOutput
# ---------------------------------------------------------------------------

def test_single_agent_produces_strategy_output(tmp_path):
    target = _make_target(tmp_path)
    tools = _make_tools(target)
    model = FakeModelProvider(
        [_finding_response([_SQLI_FINDING])],
        retry_policy=RetryPolicy(max_retries=0),
    )

    output = SingleAgentStrategy().run(target, model, tools, _base_config())

    assert isinstance(output, StrategyOutput)
    assert output.post_dedup_count == 1
    assert output.pre_dedup_count == 1
    assert len(output.findings) == 1
    finding = output.findings[0]
    assert finding.vuln_class == VulnClass.SQLI
    assert finding.file_path == "myapp/views.py"
    assert finding.confidence == pytest.approx(0.95)


def test_single_agent_empty_response_yields_no_findings(tmp_path):
    target = _make_target(tmp_path)
    tools = _make_tools(target)
    model = FakeModelProvider(
        [_empty_response()],
        retry_policy=RetryPolicy(max_retries=0),
    )

    output = SingleAgentStrategy().run(target, model, tools, _base_config())

    assert isinstance(output, StrategyOutput)
    assert output.findings == []


# ---------------------------------------------------------------------------
# Test 2: PerFile invokes model once per source file
# ---------------------------------------------------------------------------

def test_per_file_invokes_model_per_file(tmp_path):
    target = _make_target(tmp_path)
    source_files = target.list_source_files()
    n_files = len(source_files)

    # One response per file — all empty
    responses = [_empty_response() for _ in range(n_files)]
    model = FakeModelProvider(responses, retry_policy=RetryPolicy(max_retries=0))
    tools = _make_tools(target)

    output = PerFileStrategy().run(target, model, tools, _base_config())

    assert isinstance(output, StrategyOutput)
    # All canned responses consumed means model was called once per file
    assert len(model._responses) == 0, "Model was not called for every source file"


def test_per_file_collects_findings_from_each_file(tmp_path):
    target = _make_target(tmp_path)
    source_files = target.list_source_files()
    n_files = len(source_files)

    # Return a finding from the first file, empty from others
    responses = [_finding_response([_SQLI_FINDING])] + [
        _empty_response() for _ in range(n_files - 1)
    ]
    model = FakeModelProvider(responses, retry_policy=RetryPolicy(max_retries=0))
    tools = _make_tools(target)

    output = PerFileStrategy().run(target, model, tools, _base_config())

    assert isinstance(output, StrategyOutput)
    assert len(output.findings) >= 1


# ---------------------------------------------------------------------------
# Test 3: PerVulnClass invokes model once per vulnerability class
# ---------------------------------------------------------------------------

def test_per_vuln_class_invokes_model_per_class(tmp_path):
    from sec_review_framework.data.findings import VulnClass as VC

    target = _make_target(tmp_path)
    active_classes = list(VC)
    n_classes = len(active_classes)

    responses = [_empty_response() for _ in range(n_classes)]
    model = FakeModelProvider(responses, retry_policy=RetryPolicy(max_retries=0))
    tools = _make_tools(target)

    config = {**_base_config(), "vuln_classes": active_classes}
    output = PerVulnClassStrategy().run(target, model, tools, config)

    assert isinstance(output, StrategyOutput)
    assert len(model._responses) == 0, "Model was not called for every vuln class"


def test_per_vuln_class_restricted_to_subset(tmp_path):
    """With vuln_classes=[sqli], only one model call is made."""
    target = _make_target(tmp_path)
    model = FakeModelProvider(
        [_finding_response([_SQLI_FINDING])],
        retry_policy=RetryPolicy(max_retries=0),
    )
    tools = _make_tools(target)

    config = {**_base_config(), "vuln_classes": [VulnClass.SQLI]}
    output = PerVulnClassStrategy().run(target, model, tools, config)

    assert isinstance(output, StrategyOutput)
    assert len(output.findings) == 1
    assert output.findings[0].vuln_class == VulnClass.SQLI
    # No extra responses consumed
    assert len(model._responses) == 0


# ---------------------------------------------------------------------------
# Test 4: SASTFirst with no semgrep results → empty StrategyOutput (phase 2 skipped)
# ---------------------------------------------------------------------------

def test_sast_first_no_semgrep_results_returns_empty(tmp_path):
    """When Semgrep returns no matches, SASTFirst returns empty without calling model."""
    from unittest.mock import patch
    from sec_review_framework.tools.semgrep import SemgrepTool

    target = _make_target(tmp_path)
    model = FakeModelProvider([], retry_policy=RetryPolicy(max_retries=0))
    tools = _make_tools(target)

    # Patch SemgrepTool so it returns no matches (no semgrep binary needed)
    with patch.object(SemgrepTool, "run_full_scan", return_value=[]):
        output = SASTFirstStrategy().run(target, model, tools, _base_config())

    assert isinstance(output, StrategyOutput)
    assert output.findings == []
    assert output.pre_dedup_count == 0
    # Model should NOT have been called at all
    assert len(model._responses) == 0, "Model called despite no Semgrep results"


def test_sast_first_with_semgrep_results_invokes_model(tmp_path):
    """When Semgrep returns matches, SASTFirst calls model once per flagged file."""
    from unittest.mock import patch, MagicMock
    from sec_review_framework.tools.semgrep import SemgrepTool

    target = _make_target(tmp_path)

    # Fake one Semgrep match on views.py
    mock_match = MagicMock()
    mock_match.file_path = "myapp/views.py"
    mock_match.rule_id = "python.django.sqli"
    mock_match.message = "Possible SQL injection"
    mock_match.line_start = 3
    mock_match.line_end = 4
    mock_match.severity = "ERROR"

    model = FakeModelProvider(
        [_finding_response([_SQLI_FINDING])],
        retry_policy=RetryPolicy(max_retries=0),
    )
    tools = _make_tools(target)

    with patch.object(SemgrepTool, "run_full_scan", return_value=[mock_match]):
        output = SASTFirstStrategy().run(target, model, tools, _base_config())

    assert isinstance(output, StrategyOutput)
    assert len(output.findings) == 1


# ---------------------------------------------------------------------------
# Test 5: DiffReview with a real git-initialized tmp repo
# ---------------------------------------------------------------------------

def _init_git_repo_with_diff(repo_path: Path) -> tuple[str, str]:
    """
    Create a git repo with two commits so we have a real diff.
    Returns (base_ref, head_ref) as commit SHA strings.
    """
    env_extras = {
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@test.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@test.com",
    }

    import os
    env = {**os.environ, **env_extras}

    def run(*args: str) -> None:
        subprocess.run(list(args), cwd=repo_path, check=True, capture_output=True, env=env)

    run("git", "init")
    run("git", "config", "user.email", "test@test.com")
    run("git", "config", "user.name", "Test")

    # Base commit — clean file
    (repo_path / "main.py").write_text('print("hello")\n')
    run("git", "add", "main.py")
    run("git", "commit", "-m", "initial")

    base_ref = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_path, env=env
    ).decode().strip()

    # Head commit — introduce a code change (content is test data, not executable)
    new_content = (
        "import subprocess\n"
        "def run_cmd(args):\n"
        "    return subprocess.call(args)\n"
    )
    (repo_path / "main.py").write_text(new_content)
    run("git", "add", "main.py")
    run("git", "commit", "-m", "add run helper")

    head_ref = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo_path, env=env
    ).decode().strip()

    return base_ref, head_ref


def test_diff_review_with_git_repo(tmp_path):
    """DiffReview strategy reads diff_spec.yaml, runs git diff, and calls model."""
    import yaml

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    base_ref, head_ref = _init_git_repo_with_diff(repo_path)

    # diff_spec.yaml lives in repo_path.parent (= tmp_path)
    diff_spec = {"base_ref": base_ref, "head_ref": head_ref}
    (tmp_path / "diff_spec.yaml").write_text(yaml.dump(diff_spec))

    target = TargetCodebase(repo_path)

    model = FakeModelProvider(
        [_finding_response([_RCE_FINDING])],
        retry_policy=RetryPolicy(max_retries=0),
    )
    tools = _make_tools(target)

    output = DiffReviewStrategy().run(target, model, tools, _base_config())

    assert isinstance(output, StrategyOutput)
    assert len(output.findings) == 1
    assert output.findings[0].vuln_class == VulnClass.RCE
