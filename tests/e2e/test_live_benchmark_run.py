"""Live Benchmark Run Test: real LLM call producing a benchmark scorecard.

Exercises the full ExperimentWorker pipeline with a benchmark-shaped dataset
(positive + negative labels for CWE-89) and asserts that a benchmark
scorecard is produced.

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
    pytest tests/e2e/test_live_benchmark_run.py -v -s
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

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
# Synthetic benchmark dataset
# ---------------------------------------------------------------------------
# 2 positive files: real-looking SQL injection in Python
# 2 negative files: parameterized queries (same shape, safe)
#
# All 4 files are CWE-89 labelled so aggregate tp+fp+tn+fn == 4.

_POSITIVE_APP_PY = """\
import sqlite3

def search_users(request):
    q = request.args.get("q")
    # BAD: user input directly interpolated into SQL string
    query = "SELECT * FROM users WHERE name = '%s'" % q
    conn = sqlite3.connect('db.sqlite3')
    return conn.execute(query).fetchall()
"""

_POSITIVE_ADMIN_PY = """\
import sqlite3

def get_order(order_id):
    # BAD: f-string with untrusted order_id
    sql = f"SELECT * FROM orders WHERE id = {order_id}"
    conn = sqlite3.connect('shop.db')
    return conn.execute(sql).fetchone()
"""

_NEGATIVE_SAFE_APP_PY = """\
import sqlite3

def search_users_safe(request):
    q = request.args.get("q")
    # GOOD: parameterized query
    conn = sqlite3.connect('db.sqlite3')
    return conn.execute("SELECT * FROM users WHERE name = ?", (q,)).fetchall()
"""

_NEGATIVE_SAFE_ADMIN_PY = """\
import sqlite3

def get_order_safe(order_id):
    # GOOD: parameterized query
    conn = sqlite3.connect('shop.db')
    return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
"""


@pytest.fixture
def bench_dirs(tmp_path: Path):
    dataset_name = "live-bench-cwe89"
    dataset_version = "1.0.0"

    repo_dir = tmp_path / "datasets" / "targets" / dataset_name / "repo"
    repo_dir.mkdir(parents=True)

    (repo_dir / "app.py").write_text(_POSITIVE_APP_PY)
    (repo_dir / "admin.py").write_text(_POSITIVE_ADMIN_PY)
    (repo_dir / "safe_app.py").write_text(_NEGATIVE_SAFE_APP_PY)
    (repo_dir / "safe_admin.py").write_text(_NEGATIVE_SAFE_ADMIN_PY)

    # Positive labels (dataset_labels): 2 files
    labels_path = (
        tmp_path / "datasets" / "targets" / dataset_name / "labels.jsonl"
    )
    positive_labels = [
        {
            "id": "lbl-bench-sqli-01",
            "dataset_version": dataset_version,
            "file_path": "app.py",
            "line_start": 6,
            "line_end": 6,
            "cwe_id": "CWE-89",
            "vuln_class": "sqli",
            "severity": "high",
            "description": "SQL injection via % formatting",
            "source": GroundTruthSource.BENCHMARK.value,
            "confidence": "confirmed",
            "created_at": "2024-01-01T00:00:00+00:00",
        },
        {
            "id": "lbl-bench-sqli-02",
            "dataset_version": dataset_version,
            "file_path": "admin.py",
            "line_start": 5,
            "line_end": 5,
            "cwe_id": "CWE-89",
            "vuln_class": "sqli",
            "severity": "high",
            "description": "SQL injection via f-string",
            "source": GroundTruthSource.BENCHMARK.value,
            "confidence": "confirmed",
            "created_at": "2024-01-01T00:00:00+00:00",
        },
    ]
    labels_path.write_text(
        "\n".join(json.dumps(lbl) for lbl in positive_labels) + "\n"
    )

    # Negative labels (dataset_negative_labels): 2 safe files
    negative_labels = [
        {
            "id": "neg-bench-sqli-01",
            "dataset_version": dataset_version,
            "file_path": "safe_app.py",
            "line_start": 6,
            "line_end": 6,
            "cwe_id": "CWE-89",
            "vuln_class": "sqli",
            "severity": "high",
            "description": "Parameterized query — safe",
            "source": GroundTruthSource.BENCHMARK.value,
            "confidence": "confirmed",
            "created_at": "2024-01-01T00:00:00+00:00",
        },
        {
            "id": "neg-bench-sqli-02",
            "dataset_version": dataset_version,
            "file_path": "safe_admin.py",
            "line_start": 4,
            "line_end": 4,
            "cwe_id": "CWE-89",
            "vuln_class": "sqli",
            "severity": "high",
            "description": "Parameterized query — safe",
            "source": GroundTruthSource.BENCHMARK.value,
            "confidence": "confirmed",
            "created_at": "2024-01-01T00:00:00+00:00",
        },
    ]

    output_dir = tmp_path / "output" / "bench-run"
    return {
        "datasets_dir": tmp_path / "datasets",
        "output_dir": output_dir,
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "positive_labels": positive_labels,
        "negative_labels": negative_labels,
    }


@pytest.fixture
def bench_run(bench_dirs) -> ExperimentRun:
    return ExperimentRun(
        id="live-bench-cwe89_single_agent_without_tools_default_none",
        experiment_id="live-bench",
        strategy_id="builtin.single_agent",
        model_id=MODEL_ID,
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITHOUT_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name=bench_dirs["dataset_name"],
        dataset_version=bench_dirs["dataset_version"],
        provider_kwargs=MODEL_CONFIG,
        strategy_config={"max_turns": 5},
        created_at=datetime(2026, 4, 29, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# The benchmark live test
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_live_benchmark_scorecard(bench_dirs, bench_run):
    """Full ExperimentWorker pipeline producing a benchmark scorecard.

    Uses a synthetic 4-file dataset (2 positive SQL-injection, 2 negative
    parameterized-query) labelled under CWE-89.  _fetch_negative_labels is
    patched to inject the synthetic negatives directly, bypassing the HTTP
    coordinator call.  _fetch_labels is similarly patched so positive labels
    are returned without a DB round-trip.

    Asserts:
    - run completes successfully
    - benchmark_scorecard is populated (not None)
    - at least one CWE row exists in per_cwe
    - aggregate tp+fp+tn+fn == 4 (total file count)
    - tp=0 is tolerated (model may miss everything)
    """
    from sec_review_framework.data.evaluation import GroundTruthLabel

    output_dir: Path = bench_dirs["output_dir"]
    positive_labels_raw = bench_dirs["positive_labels"]
    negative_labels_raw = bench_dirs["negative_labels"]

    # Build GroundTruthLabel objects for the positive side (what _fetch_labels returns)
    positive_label_objects = [
        GroundTruthLabel.model_validate(lbl) for lbl in positive_labels_raw
    ]

    worker = ExperimentWorker()

    with (
        patch.object(
            ExperimentWorker,
            "_fetch_labels",
            return_value=positive_label_objects,
        ),
        patch.object(
            ExperimentWorker,
            "_fetch_negative_labels",
            return_value=negative_labels_raw,
        ),
    ):
        worker.run(bench_run, output_dir, bench_dirs["datasets_dir"])

    # run_result.json must exist and parse
    run_result_path = output_dir / "run_result.json"
    assert run_result_path.exists(), "run_result.json must be written"

    result = RunResult.model_validate_json(run_result_path.read_text())

    assert result.status == RunStatus.COMPLETED, (
        f"Run should complete, got {result.status}: {result.error}"
    )
    assert result.error is None

    # Token counts must be present
    assert result.total_input_tokens > 0, "Should have consumed input tokens"
    assert result.total_output_tokens > 0, "Should have produced output tokens"

    # benchmark_scorecard must be populated (not None) because we injected negatives
    assert result.benchmark_scorecard is not None, (
        "benchmark_scorecard must be populated when negative labels are present"
    )

    scorecard = result.benchmark_scorecard  # a dict from BenchmarkScorecard.to_dict()

    # At least one CWE row
    assert "per_cwe" in scorecard, "scorecard must have per_cwe key"
    assert len(scorecard["per_cwe"]) >= 1, "at least one CWE row expected"

    # Aggregate counts must cover all 4 files (tp+fp+tn+fn == 4)
    agg = scorecard["aggregate"]
    total = agg["tp"] + agg["fp"] + agg["tn"] + agg["fn"]
    assert total == 4, (
        f"aggregate tp+fp+tn+fn should equal 4 (2 pos + 2 neg files), got {total}. "
        f"agg={agg}"
    )

    # tp=0 is fine (model may miss everything); we do NOT assert precision/recall.
    # Only check that counts are non-negative.
    assert agg["tp"] >= 0
    assert agg["fp"] >= 0
    assert agg["tn"] >= 0
    assert agg["fn"] >= 0

    # Print summary for human inspection when running with -s
    print("\n--- Live Benchmark Scorecard Test Results ---")
    print(f"Model: {MODEL_ID}")
    print(f"Status: {result.status.value}")
    print(f"Duration: {result.duration_seconds:.1f}s")
    print(f"Tokens: {result.total_input_tokens} in / {result.total_output_tokens} out")
    print(f"Findings: {len(result.findings)}")
    print(f"Aggregate: TP={agg['tp']} FP={agg['fp']} TN={agg['tn']} FN={agg['fn']}")
    for cwe_row in scorecard["per_cwe"]:
        print(
            f"  {cwe_row['cwe_id']}: TP={cwe_row['tp']} FP={cwe_row['fp']} "
            f"TN={cwe_row['tn']} FN={cwe_row['fn']}"
            + (f" [WARNING: {cwe_row['warning']}]" if cwe_row.get("warning") else "")
        )
    print("--- End ---\n")
