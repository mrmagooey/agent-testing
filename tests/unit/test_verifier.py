"""Unit tests for LLMVerifier."""

import json
from unittest.mock import MagicMock

import pytest

from sec_review_framework.data.evaluation import VerificationOutcome
from sec_review_framework.verification.verifier import LLMVerifier


@pytest.fixture
def verifier():
    return LLMVerifier()


@pytest.fixture
def mock_tools():
    tools = MagicMock()
    tools.get_tool_definitions.return_value = []
    return tools


@pytest.fixture
def mock_target():
    return MagicMock()


def _make_model_returning(json_response: str) -> MagicMock:
    """Create a mock ModelProvider that returns a fixed response with no tool calls."""
    model = MagicMock()
    response = MagicMock()
    response.content = json_response
    response.tool_calls = []
    response.input_tokens = 100
    response.output_tokens = 50
    model.complete.return_value = response
    model.token_log = [response]
    return model


def test_verifier_verified_response(verifier, sample_finding, mock_target, mock_tools):
    """When the model returns 'verified', the finding is classified as verified."""
    decisions = json.dumps([{
        "finding_id": sample_finding.id,
        "outcome": "verified",
        "evidence": "SQL injection confirmed at line 42.",
        "cited_lines": ["src/views.py:42"],
    }])
    model = _make_model_returning(f"```json\n{decisions}\n```")

    result = verifier.verify([sample_finding], mock_target, model, mock_tools)

    assert len(result.verified) == 1
    assert result.verified[0].outcome == VerificationOutcome.VERIFIED
    assert result.total_candidates == 1


def test_verifier_rejected_response(verifier, sample_finding, mock_target, mock_tools):
    """When the model returns 'rejected', the finding is classified as rejected."""
    decisions = json.dumps([{
        "finding_id": sample_finding.id,
        "outcome": "rejected",
        "evidence": "Input is sanitized by framework middleware.",
    }])
    model = _make_model_returning(f"```json\n{decisions}\n```")

    result = verifier.verify([sample_finding], mock_target, model, mock_tools)

    assert len(result.rejected) == 1
    assert result.rejected[0].outcome == VerificationOutcome.REJECTED
    assert sample_finding.verified is False


def test_verifier_handles_malformed_json(verifier, sample_finding, mock_target, mock_tools):
    """Verifier should not raise on invalid JSON — findings become uncertain."""
    model = _make_model_returning("I cannot determine this.")

    result = verifier.verify([sample_finding], mock_target, model, mock_tools)

    assert len(result.uncertain) == 1
    assert result.uncertain[0].outcome == VerificationOutcome.UNCERTAIN


def test_verifier_empty_candidates(verifier, mock_target, mock_tools):
    """Empty candidate list returns empty result without calling model."""
    model = MagicMock()
    result = verifier.verify([], mock_target, model, mock_tools)

    assert result.total_candidates == 0
    model.complete.assert_not_called()


def test_verifier_unaddressed_finding_is_uncertain(verifier, sample_finding, mock_target, mock_tools):
    """Findings not addressed by the verifier are classified as uncertain."""
    decisions = json.dumps([{
        "finding_id": "some-other-id",
        "outcome": "verified",
        "evidence": "Different finding.",
    }])
    model = _make_model_returning(f"```json\n{decisions}\n```")

    result = verifier.verify([sample_finding], mock_target, model, mock_tools)

    assert len(result.uncertain) == 1
