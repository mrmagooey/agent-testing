"""Live With-Tools Run Test: real LLM call with tool-calling enabled.

Exercises the full ExperimentWorker pipeline with ToolVariant.WITH_TOOLS,
offering read-file/grep/list-directory tools to the model and asserting that
tool_calls.jsonl is written and well-formed.

Environment variables required to run (same pattern as test_live_smoke.py):

    LIVE_TEST_API_BASE   — /v1 endpoint of your OpenAI-compatible server,
                           e.g. http://192.168.7.100:8080/v1
    LIVE_TEST_MODEL_ID   — LiteLLM model string, e.g. openai/my-model
    LIVE_TEST_API_KEY    — optional, defaults to "sk-noop"

If neither LIVE_TEST_API_BASE+LIVE_TEST_MODEL_ID nor OPENROUTER_TEST_KEY is
set the test is skipped automatically.

Run:
    LIVE_TEST_API_BASE=http://192.168.7.100:8080/v1 \\
    LIVE_TEST_MODEL_ID=openai/your-model \\
    pytest tests/e2e/test_live_with_tools_run.py -v -s
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

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
from sec_review_framework.tools.registry import ToolRegistryFactory
from sec_review_framework.worker import ExperimentWorker

# ---------------------------------------------------------------------------
# Backend selection (mirrors test_live_smoke.py exactly)
# ---------------------------------------------------------------------------

OPENROUTER_KEY = os.environ.get("OPENROUTER_TEST_KEY")
LIVE_API_BASE = os.environ.get("LIVE_TEST_API_BASE")
LIVE_MODEL_ID = os.environ.get("LIVE_TEST_MODEL_ID")
LIVE_API_KEY = os.environ.get("LIVE_TEST_API_KEY", "sk-noop")

pytestmark = pytest.mark.skipif(
    not (OPENROUTER_KEY or (LIVE_API_BASE and LIVE_MODEL_ID)),
    reason=(
        "neither OPENROUTER_TEST_KEY nor "
        "(LIVE_TEST_API_BASE + LIVE_TEST_MODEL_ID) is set"
    ),
)

if LIVE_API_BASE and LIVE_MODEL_ID:
    MODEL_ID = LIVE_MODEL_ID
    MODEL_CONFIG: dict = {"api_base": LIVE_API_BASE, "api_key": LIVE_API_KEY}
    # LiteLLM's openai/ provider routes through the OpenAI SDK, which reads
    # the key from OPENAI_API_KEY and the endpoint from OPENAI_BASE_URL.
    os.environ.setdefault("OPENAI_API_KEY", LIVE_API_KEY)
    os.environ["OPENAI_BASE_URL"] = LIVE_API_BASE
else:
    if OPENROUTER_KEY:
        os.environ["OPENROUTER_API_KEY"] = OPENROUTER_KEY
    MODEL_ID = "openrouter/meta-llama/llama-3.1-8b-instruct"
    MODEL_CONFIG = {}

# ---------------------------------------------------------------------------
# Synthetic dataset
# ---------------------------------------------------------------------------
# 2 files: app.py (entry-point, hints model to inspect helpers) and
# helpers.py (the actual CWE-89 sink so the model has something to find
# if it reads the file).

_APP_PY = """\
from helpers import build_query, run_query

def search(request):
    q = request.args.get("q")
    sql = build_query(q)
    return run_query(sql)
"""

_HELPERS_PY = """\
import sqlite3

def build_query(name):
    # BAD: user input concatenated into SQL string
    return "SELECT * FROM users WHERE name = '%s'" % name

def run_query(sql):
    conn = sqlite3.connect('db.sqlite3')
    return conn.execute(sql).fetchall()
"""

_TOOL_CALL_RECORD_KEYS = frozenset(
    {"call_id", "tool_name", "input", "timestamp", "duration_ms", "output_truncated"}
)


@pytest.fixture
def with_tools_dirs(tmp_path: Path):
    dataset_name = "live-with-tools-cwe89"
    dataset_version = "1.0.0"

    repo_dir = tmp_path / "datasets" / "targets" / dataset_name / "repo"
    repo_dir.mkdir(parents=True)

    (repo_dir / "app.py").write_text(_APP_PY)
    (repo_dir / "helpers.py").write_text(_HELPERS_PY)

    labels_path = (
        tmp_path / "datasets" / "targets" / dataset_name / "labels.jsonl"
    )
    positive_labels = [
        {
            "id": "lbl-wt-sqli-01",
            "dataset_version": dataset_version,
            "file_path": "helpers.py",
            "line_start": 5,
            "line_end": 5,
            "cwe_id": "CWE-89",
            "vuln_class": "sqli",
            "severity": "high",
            "description": "SQL injection via % string formatting",
            "source": GroundTruthSource.BENCHMARK.value,
            "confidence": "confirmed",
            "created_at": "2024-01-01T00:00:00+00:00",
        },
    ]
    labels_path.write_text(
        "\n".join(json.dumps(lbl) for lbl in positive_labels) + "\n"
    )

    output_dir = tmp_path / "output" / "with-tools-run"
    return {
        "datasets_dir": tmp_path / "datasets",
        "output_dir": output_dir,
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "repo_dir": repo_dir,
        "positive_labels": positive_labels,
    }


@pytest.fixture
def with_tools_run(with_tools_dirs) -> ExperimentRun:
    return ExperimentRun(
        id="live-with-tools-cwe89_single_agent_with_tools_default_none",
        experiment_id="live-with-tools",
        strategy_id="builtin.single_agent",
        model_id=MODEL_ID,
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name=with_tools_dirs["dataset_name"],
        dataset_version=with_tools_dirs["dataset_version"],
        provider_kwargs=MODEL_CONFIG,
        strategy_config={"max_turns": 6},
        created_at=datetime(2026, 4, 29, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# The with-tools live test
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_live_with_tools_run(with_tools_dirs, with_tools_run):
    """Full ExperimentWorker pipeline with ToolVariant.WITH_TOOLS.

    Uses a synthetic 2-file dataset (app.py entry-point + helpers.py CWE-89
    sink) to exercise the tool-calling round-trip.  _fetch_labels is patched
    to return the single positive label directly; _fetch_negative_labels is
    NOT patched (its default returns [] which is correct — there are no
    negative labels).

    Asserts the WITH_TOOLS plumbing: tools are registered, the model invokes
    them (≥1 call required), and the full round-trip is captured in both
    tool_calls.jsonl and conversation.jsonl (assistant tool_calls block AND
    role==tool result entry).

    Note on status: small/local models (e.g. Gemma 4B) reliably call tools but
    often fail to parse pydantic-ai's structured-output Findings schema at the
    final step.  A FAILED run is therefore acceptable here IFF the error is
    the known output-validation failure — any other failure mode (network error,
    missing tool, crash) is still caught and will fail this test.
    """
    output_dir: Path = with_tools_dirs["output_dir"]
    positive_labels_raw = with_tools_dirs["positive_labels"]

    positive_label_objects = [
        GroundTruthLabel.model_validate(lbl) for lbl in positive_labels_raw
    ]

    # Build the expected registered tool names from the live registry so the
    # assertion doesn't hard-code names that may change.
    class _FakeTarget:
        repo_path = with_tools_dirs["repo_dir"]

    registered_tool_names = set(
        ToolRegistryFactory.create(ToolVariant.WITH_TOOLS, _FakeTarget()).tools.keys()
    )

    worker = ExperimentWorker()

    with patch.object(
        ExperimentWorker,
        "_fetch_labels",
        return_value=positive_label_objects,
    ):
        worker.run(with_tools_run, output_dir, with_tools_dirs["datasets_dir"])

    # run_result.json must exist and parse
    run_result_path = output_dir / "run_result.json"
    assert run_result_path.exists(), "run_result.json must be written"

    result = RunResult.model_validate_json(run_result_path.read_text())

    # A COMPLETED run is ideal.  A FAILED run is acceptable ONLY when the
    # failure is pydantic-ai's structured-output validation (small models
    # reliably call tools but often cannot produce valid Findings JSON).
    # Any other failure (network error, missing tool, crash) is a real bug
    # and must still fail this test.
    if result.status == RunStatus.FAILED:
        assert "output validation" in (result.error or ""), (
            f"Run failed for an unexpected reason (not output validation): "
            f"{result.error!r}"
        )

    # conversation.jsonl must exist and have at least one message line
    conversation_path = output_dir / "conversation.jsonl"
    assert conversation_path.exists(), "conversation.jsonl must be written"
    conversation_lines = [
        ln for ln in conversation_path.read_text().splitlines() if ln.strip()
    ]
    assert len(conversation_lines) >= 1, "conversation.jsonl must have ≥1 line"

    # Verify the full tool-call round-trip in conversation.jsonl:
    # (a) at least one assistant message that contains a tool_calls block, and
    # (b) at least one role==tool entry (framework returned the result).
    conversation_entries = [json.loads(ln) for ln in conversation_lines]
    assistant_with_tool_calls = [
        e for e in conversation_entries if e.get("tool_calls")
    ]
    tool_result_entries = [
        e for e in conversation_entries if e.get("role") == "tool"
    ]
    assert len(assistant_with_tool_calls) >= 1, (
        "conversation.jsonl must contain ≥1 assistant entry with tool_calls "
        "(model must have invoked at least one tool)"
    )
    assert len(tool_result_entries) >= 1, (
        "conversation.jsonl must contain ≥1 role==tool entry "
        "(framework must have returned tool results to the model)"
    )

    # tool_calls.jsonl must exist and have at least one recorded call
    tool_calls_path = output_dir / "tool_calls.jsonl"
    assert tool_calls_path.exists(), "tool_calls.jsonl must be written"

    raw_lines = [
        ln for ln in tool_calls_path.read_text().splitlines() if ln.strip()
    ]

    # Tool calls are required — this is the whole point of the WITH_TOOLS test.
    assert result.tool_call_count >= 1, (
        f"result.tool_call_count must be ≥1; model must invoke tools "
        f"(got {result.tool_call_count})"
    )
    assert len(raw_lines) >= 1, (
        "tool_calls.jsonl must have ≥1 line matching result.tool_call_count"
    )
    assert len(raw_lines) == result.tool_call_count, (
        f"tool_calls.jsonl line count ({len(raw_lines)}) must equal "
        f"result.tool_call_count ({result.tool_call_count})"
    )

    # Every line must be a valid ToolCallRecord-shaped dict with a registered tool_name.
    for i, line in enumerate(raw_lines):
        entry = json.loads(line)
        assert isinstance(entry, dict), f"line {i}: expected dict, got {type(entry)}"
        missing = _TOOL_CALL_RECORD_KEYS - entry.keys()
        assert not missing, (
            f"line {i}: tool_calls.jsonl entry missing keys {missing!r}"
        )
        assert entry["tool_name"] in registered_tool_names, (
            f"line {i}: tool_name {entry['tool_name']!r} not in registered "
            f"tool set {registered_tool_names!r}"
        )

    # Print summary for human inspection when running with -s
    print("\n--- Live With-Tools Run Test Results ---")
    print(f"Model: {MODEL_ID}")
    print(f"Status: {result.status.value}")
    if result.status == RunStatus.FAILED:
        print(f"  (acceptable output-validation failure: {result.error!r})")
    print(f"Duration: {result.duration_seconds:.1f}s")
    print(f"Tokens: {result.total_input_tokens} in / {result.total_output_tokens} out")
    print(f"Findings: {len(result.findings)}")
    print(f"Tool calls recorded: {result.tool_call_count}")
    print(f"Conversation lines: {len(conversation_lines)}")
    print(f"  assistant-with-tool_calls: {len(assistant_with_tool_calls)}")
    print(f"  role==tool results: {len(tool_result_entries)}")
    print(f"Registered tools offered: {sorted(registered_tool_names)}")
    print("--- End ---\n")
