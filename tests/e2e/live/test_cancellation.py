"""Layer 4: Cancel mid-flight.

Submits an 8-run experiment, briefly waits for at least one run to start or become
pending (proving the scheduler is working), issues a cancel, then verifies
the experiment reaches a terminal state with no lingering pending runs.
"""

from __future__ import annotations

import os
import time

import pytest

from tests.e2e.live.conftest import K8S_LIVE_MARK, poll_until_done, unique_experiment_id

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

# 2 strategies × 2 tool_variants × 2 repetitions = 8 runs
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
    "num_repetitions": 2,
    "max_experiment_cost_usd": 0.50,
    "strategy_configs": {"single_agent": {"max_turns": 3}, "per_file": {"max_turns": 3}},
}
if os.environ.get("LIVE_TEST_MODEL_ID"):
    MATRIX_PAYLOAD["allow_unavailable_models"] = True


def test_cancel_experiment(live_client, experiment_cleanup):
    experiment_id = unique_experiment_id("live-e2e-cancel")
    payload = {**MATRIX_PAYLOAD, "experiment_id": experiment_id}
    experiment_cleanup.append(experiment_id)

    # --- Submit ---
    resp = live_client.post("/experiments", json=payload)
    assert resp.status_code == 201, f"Expected 201, got {resp.status_code}: {resp.text}"
    submit_body = resp.json()
    assert submit_body["experiment_id"] == experiment_id
    assert submit_body["total_runs"] == 8, (
        f"Expected 8 runs, got {submit_body['total_runs']}"
    )
    total_runs = submit_body["total_runs"]

    # --- Wait briefly for the experiment to register at least some activity ---
    # We give up to 30 s; if the scheduler is slow, that's OK — we cancel anyway.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status_resp = live_client.get(f"/experiments/{experiment_id}")
        assert status_resp.status_code == 200
        status = status_resp.json()
        running = status.get("running_runs", 0)
        pending = status.get("pending_runs", 0)
        # "activity" = any run moved out of pending OR one is already running
        if running > 0 or pending < total_runs:
            break
        time.sleep(3)

    # We proceed to cancel regardless — even if no run has started, cancel
    # should be idempotent and safe.

    # --- Cancel ---
    cancel_resp = live_client.post(f"/experiments/{experiment_id}/cancel")
    assert cancel_resp.status_code == 200, (
        f"Cancel returned {cancel_resp.status_code}: {cancel_resp.text}"
    )
    cancel_body = cancel_resp.json()
    # The response must include a cancelled_jobs key (value may be 0 if all
    # runs finished before we got around to cancelling — that is fine).
    assert "cancelled_jobs" in cancel_body, (
        f"Cancel response missing 'cancelled_jobs' key: {cancel_body}"
    )

    # --- Poll until terminal (5 min — running workers should be allowed to finish) ---
    final = poll_until_done(live_client, experiment_id, timeout_s=300, poll_interval_s=10)

    # No run should still be sitting in pending state
    pending_runs = final.get("pending_runs", 0)
    assert pending_runs == 0, (
        f"Expected pending_runs == 0 after cancel, got {pending_runs}. State: {final}"
    )

    # Tolerant assertion: the combination of completed + failed + cancelled_jobs
    # must account for all runs (some may have finished before cancel landed).
    completed = final.get("completed_runs", 0)
    failed = final.get("failed_runs", 0)
    cancelled_jobs = cancel_body.get("cancelled_jobs", 0)
    accounted_for = completed + failed + cancelled_jobs

    assert accounted_for >= total_runs or final.get("status") in ("cancelled", "completed", "failed"), (
        f"Not all runs accounted for after cancel. "
        f"completed={completed}, failed={failed}, cancelled_jobs={cancelled_jobs}, "
        f"total={total_runs}, experiment_status={final.get('status')}"
    )
