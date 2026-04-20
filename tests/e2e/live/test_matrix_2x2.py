"""Layer 3: 2×2 matrix — 2 strategies × 2 tool_variants = 4 runs.

Tests the scheduling / fan-out plumbing with one cheap model. We do NOT
assert on findings or metric values; we only verify that all 4 cells appear
in the matrix report and at least one run finishes successfully.
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

MATRIX_PAYLOAD = {
    "dataset_name": "live-e2e",
    "dataset_version": "1.0.0",
    "model_ids": [MODEL_ID],
    "strategies": ["single_agent", "per_file"],
    "tool_variants": ["with_tools", "without_tools"],
    "review_profiles": ["default"],
    "verification_variants": ["none"],
    "parallel_modes": [False],
    "tool_extension_sets": [[]],
    "num_repetitions": 1,
    "max_batch_cost_usd": 0.30,
    "strategy_configs": {"single_agent": {"max_turns": 3}, "per_file": {"max_turns": 3}},
}


def test_matrix_2x2(live_client, batch_cleanup):
    batch_id = unique_batch_id("live-e2e-2x2")
    payload = {**MATRIX_PAYLOAD, "batch_id": batch_id}
    batch_cleanup.append(batch_id)

    # --- Submit ---
    resp = live_client.post("/batches", json=payload)
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    submit_body = resp.json()
    assert submit_body["batch_id"] == batch_id
    assert submit_body["total_runs"] == 4, (
        f"Expected 4 runs (2 strategies × 2 tool_variants), got {submit_body['total_runs']}"
    )

    # --- Poll to completion (15 min max) ---
    final = poll_until_done(live_client, batch_id, timeout_s=900, poll_interval_s=10)

    completed = final["completed_runs"]
    failed = final["failed_runs"]
    assert completed + failed == 4, (
        f"Expected all 4 runs to reach terminal state, "
        f"completed={completed} failed={failed}. State: {final}"
    )
    assert completed >= 1, (
        f"At least one run must complete successfully; completed={completed}"
    )

    # --- Matrix report has all 4 cells ---
    results_resp = live_client.get(f"/batches/{batch_id}/results")
    assert results_resp.status_code == 200
    results = results_resp.json()
    runs = results.get("runs", [])
    assert len(runs) == 4, f"Expected 4 runs in matrix report, got {len(runs)}"

    # Verify cartesian coverage: all (strategy, tool_variant) combos present
    expected_combos = {
        ("single_agent", "with_tools"),
        ("single_agent", "without_tools"),
        ("per_file", "with_tools"),
        ("per_file", "without_tools"),
    }
    actual_combos = {
        (r.get("strategy") or r.get("experiment", {}).get("strategy"),
         r.get("tool_variant") or r.get("experiment", {}).get("tool_variant"))
        for r in runs
    }
    assert actual_combos == expected_combos, (
        f"Matrix cells mismatch.\nExpected: {expected_combos}\nActual:   {actual_combos}"
    )
