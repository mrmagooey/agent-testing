"""Layer 2: Single-run batch end-to-end with a real LLM.

Submits a minimal 1×1×1 matrix (one model, one strategy, no tools) against
the live-e2e fixture dataset and waits for it to complete.  Asserts on run
status and response shape — NOT on finding counts or metric values, because
the cheap 8B model may or may not detect the planted SQLi.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e.live.conftest import K8S_LIVE_MARK, poll_until_done, unique_batch_id

pytestmark = [
    K8S_LIVE_MARK,
    pytest.mark.skipif(
        not (os.getenv("OPENROUTER_TEST_KEY") or os.getenv("LIVE_TEST_MODEL_ID")),
        reason="neither OPENROUTER_TEST_KEY nor LIVE_TEST_MODEL_ID is set",
    ),
]

MODEL_ID = os.environ.get(
    "LIVE_TEST_MODEL_ID", "openrouter/meta-llama/llama-3.1-8b-instruct"
)

# ExperimentMatrix payload sent to POST /batches
MATRIX_PAYLOAD = {
    "dataset_name": "live-e2e",
    "dataset_version": "1.0.0",
    "model_ids": [MODEL_ID],
    "strategies": ["single_agent"],
    "tool_variants": ["without_tools"],
    "review_profiles": ["default"],
    "verification_variants": ["none"],
    "parallel_modes": [False],
    "tool_extension_sets": [[]],
    "num_repetitions": 1,
    "max_batch_cost_usd": 0.10,
    "strategy_configs": {"single_agent": {"max_turns": 3}},
}


def test_single_run_batch(live_client, batch_cleanup):
    batch_id = unique_batch_id("live-e2e-single")
    payload = {**MATRIX_PAYLOAD, "batch_id": batch_id}
    batch_cleanup.append(batch_id)

    # --- Submit ---
    resp = live_client.post("/batches", json=payload)
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    submit_body = resp.json()
    assert submit_body["batch_id"] == batch_id
    assert submit_body["total_runs"] == 1, (
        f"Expected exactly 1 run, got {submit_body['total_runs']}"
    )

    # --- Poll to completion (10 min max) ---
    final = poll_until_done(live_client, batch_id, timeout_s=600, poll_interval_s=5)

    assert final["failed_runs"] == 0, (
        f"Expected 0 failed runs, got {final['failed_runs']}. Batch state: {final}"
    )
    assert final["completed_runs"] == 1, (
        f"Expected 1 completed run, got {final['completed_runs']}. Batch state: {final}"
    )
    # Regression test: the periodic reconcile loop must flip the batch to
    # "completed" (not stay "running" forever — the pre-fix bug).
    assert final["status"] == "completed", (
        f"Expected batch status 'completed' but got '{final['status']}'. "
        "The reconcile loop may not be running or finalize_batch was not called."
    )

    # --- Results shape ---
    results_resp = live_client.get(f"/batches/{batch_id}/results")
    assert results_resp.status_code == 200, (
        f"GET /batches/{batch_id}/results returned {results_resp.status_code}"
    )
    results = results_resp.json()
    runs = results.get("runs", [])
    assert len(runs) == 1, f"Expected 1 run in results, got {len(runs)}"

    run = runs[0]
    assert run.get("status") == "completed", (
        f"Run status should be 'completed', got {run.get('status')}"
    )

    # Metrics keys must be present (values may be 0 for a cheap model)
    metrics = run.get("metrics") or run.get("evaluation") or {}
    assert "precision" in metrics or "recall" in metrics or "f1" in metrics, (
        f"Expected precision/recall/f1 in run metrics. Got keys: {list(metrics.keys())}"
    )

    # --- Per-run token counts ---
    runs_list_resp = live_client.get(f"/batches/{batch_id}/runs")
    assert runs_list_resp.status_code == 200
    run_list = runs_list_resp.json()
    assert len(run_list) == 1
    run_id = run_list[0]["id"]

    run_resp = live_client.get(f"/batches/{batch_id}/runs/{run_id}")
    assert run_resp.status_code == 200, (
        f"GET /batches/{batch_id}/runs/{run_id} returned {run_resp.status_code}"
    )
    run_detail = run_resp.json()
    assert run_detail.get("total_input_tokens", 0) > 0, (
        "Expected total_input_tokens > 0"
    )
    assert run_detail.get("total_output_tokens", 0) > 0, (
        "Expected total_output_tokens > 0"
    )
