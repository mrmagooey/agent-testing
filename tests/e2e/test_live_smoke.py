"""Live Smoke Test: real LLM call.

Exercises the full ExperimentWorker pipeline against a real model provider.

Two ways to point it at a backend:

1. OpenRouter (the original configuration): set ``OPENROUTER_TEST_KEY``.
   Uses the cheap llama-3.1-8b-instruct model via OpenRouter.

2. Any OpenAI-compatible server (e.g. llama.cpp, vLLM, local gateway):
   set ``LIVE_TEST_API_BASE`` to the server's ``/v1`` endpoint, and
   ``LIVE_TEST_MODEL_ID`` to a LiteLLM-compatible model string — typically
   prefixed ``openai/`` so LiteLLM routes it to the OpenAI SDK path.
   Optional: ``LIVE_TEST_API_KEY`` (defaults to ``"sk-noop"`` for servers
   that don't enforce auth).

If both are set, (2) wins. If neither is set, the test is skipped.

Run:
    pytest tests/e2e/test_live_smoke.py -v -s
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sec_review_framework.data.evaluation import GroundTruthSource
from sec_review_framework.data.experiment import (
    ExperimentRun,
    ReviewProfileName,
    RunResult,
    RunStatus,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.worker import ExperimentWorker


# ---------------------------------------------------------------------------
# Backend selection
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
    # the key from OPENAI_API_KEY and the endpoint from OPENAI_BASE_URL
    # (module-level litellm.api_key / litellm.api_base are not consulted).
    os.environ.setdefault("OPENAI_API_KEY", LIVE_API_KEY)
    os.environ["OPENAI_BASE_URL"] = LIVE_API_BASE
else:
    # LiteLLM routes to OpenRouter when OPENROUTER_API_KEY is set
    if OPENROUTER_KEY:
        os.environ["OPENROUTER_API_KEY"] = OPENROUTER_KEY
    MODEL_ID = "openrouter/meta-llama/llama-3.1-8b-instruct"
    MODEL_CONFIG = {}


# ---------------------------------------------------------------------------
# Fixture: dataset directory with a known vuln
# ---------------------------------------------------------------------------

@pytest.fixture
def live_dirs(tmp_path: Path):
    dataset_name = "live-smoke"
    dataset_version = "1.0.0"

    repo_dir = tmp_path / "datasets" / "targets" / dataset_name / "repo"
    repo_dir.mkdir(parents=True)
    (repo_dir / "app.py").write_text(
        "import sqlite3\n"
        "\n"
        "def search(request):\n"
        '    q = request.args.get("q")\n'
        '    query = "SELECT * FROM users WHERE name = \'%s\'" % q\n'
        "    conn = sqlite3.connect('db.sqlite3')\n"
        "    return conn.execute(query).fetchall()\n"
    )

    labels_path = tmp_path / "datasets" / "targets" / dataset_name / "labels.jsonl"
    label = {
        "id": "lbl-sqli-live",
        "dataset_version": dataset_version,
        "file_path": "app.py",
        "line_start": 5,
        "line_end": 5,
        "cwe_id": "CWE-89",
        "vuln_class": "sqli",
        "severity": "high",
        "description": "SQL injection via string formatting",
        "source": GroundTruthSource.INJECTED.value,
        "confidence": "confirmed",
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    labels_path.write_text(json.dumps(label) + "\n")

    output_dir = tmp_path / "output" / "live-run"
    return {
        "datasets_dir": tmp_path / "datasets",
        "output_dir": output_dir,
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
    }


# ---------------------------------------------------------------------------
# Fixture: ExperimentRun targeting the live model
# ---------------------------------------------------------------------------

@pytest.fixture
def live_run(live_dirs) -> ExperimentRun:
    return ExperimentRun(
        id="live-smoke_openrouter_single_agent_without_tools_default_none",
        batch_id="live-smoke",
        model_id=MODEL_ID,
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITHOUT_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name=live_dirs["dataset_name"],
        dataset_version=live_dirs["dataset_version"],
        model_config=MODEL_CONFIG,
        strategy_config={"max_turns": 5},
        created_at=datetime(2026, 4, 17, tzinfo=timezone.utc),
    )


# ---------------------------------------------------------------------------
# The live smoke test
# ---------------------------------------------------------------------------

def test_live_pipeline_smoke(live_dirs, live_run):
    """Full ExperimentWorker pipeline with a real OpenRouter LLM call."""
    output_dir: Path = live_dirs["output_dir"]

    worker = ExperimentWorker()
    worker.run(live_run, output_dir, live_dirs["datasets_dir"])

    # --- run_result.json must exist and parse ---
    run_result_path = output_dir / "run_result.json"
    assert run_result_path.exists(), "run_result.json must be written"

    result = RunResult.model_validate_json(run_result_path.read_text())

    assert result.status == RunStatus.COMPLETED, (
        f"Run should complete, got {result.status}: {result.error}"
    )
    assert result.error is None

    # --- We got a real LLM response with token counts ---
    assert result.total_input_tokens > 0, "Should have consumed input tokens"
    assert result.total_output_tokens > 0, "Should have produced output tokens"

    # --- Output artifacts exist ---
    assert (output_dir / "findings.jsonl").exists()
    assert (output_dir / "tool_calls.jsonl").exists()
    assert (output_dir / "conversation.jsonl").exists()
    assert (output_dir / "report.md").exists()

    # --- Conversation log should have at least one entry ---
    conv_lines = [
        ln for ln in (output_dir / "conversation.jsonl").read_text().splitlines()
        if ln.strip()
    ]
    assert len(conv_lines) >= 1, "conversation.jsonl must have entries"

    # --- Evaluation should be present ---
    assert result.evaluation is not None, "Evaluation must be present for completed run"

    # --- Duration is realistic ---
    assert result.duration_seconds > 0

    # Print summary for visual inspection
    print(f"\n--- Live Smoke Test Results ---")
    print(f"Model: {MODEL_ID}")
    print(f"Status: {result.status.value}")
    print(f"Duration: {result.duration_seconds:.1f}s")
    print(f"Tokens: {result.total_input_tokens} in / {result.total_output_tokens} out")
    print(f"Findings: {len(result.findings)}")
    if result.evaluation:
        print(f"TP={result.evaluation.true_positives}, "
              f"FP={result.evaluation.false_positives}, "
              f"FN={result.evaluation.false_negatives}")
        print(f"Precision={result.evaluation.precision:.2f}, "
              f"Recall={result.evaluation.recall:.2f}")
    print(f"--- End ---\n")
