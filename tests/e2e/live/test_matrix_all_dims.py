"""All-dimensions minimum coverage test: diagonal covering array.

Two 1-cell experiments × 2 repetitions each = 4 runs total. Every experiment
matrix dimension is exercised with at least two distinct values by using
opposite corners of the parameter space (a "diagonal covering array"):

  Cell A — cheap/simple: llama-3.1-8b, single_agent, without_tools, default
            profile, no verification, sequential, no extensions, 2 reps.
  Cell B — heavy/full:   llama-3.2-3b, per_file, with_tools, strict profile,
            with_verification, parallel, all extensions, 2 reps.

repetition_index ∈ {0, 1} is covered because num_repetitions=2 for both cells.

Scope note: `verifier_model_id` is intentionally NOT swept. It is an optional
override orthogonal to the matrix; when unset (as here) the verifier reuses
the reviewer model. Sweeping it would add model-availability risk without
exercising new plumbing.

Intended as a periodic plumbing check, NOT a per-PR gate — slower and more
expensive than test_matrix_2x2.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e.live.conftest import K8S_LIVE_MARK, poll_until_done, unique_experiment_id

pytestmark = [
    K8S_LIVE_MARK,
    pytest.mark.skipif(
        not (os.getenv("OPENROUTER_TEST_KEY") or os.getenv("LIVE_TEST_MODEL_ID")),
        reason="neither OPENROUTER_TEST_KEY nor LIVE_TEST_MODEL_ID is set",
    ),
]

# ---------------------------------------------------------------------------
# Model IDs
# ---------------------------------------------------------------------------

# Cell A uses the well-tested 8b model.
MODEL_A = os.environ.get(
    "LIVE_TEST_MODEL_ID", "openrouter/meta-llama/llama-3.1-8b-instruct"
)

# Cell B uses a different model to exercise the model_ids dimension.
# If this model is missing from the test cluster config, swap it locally
# for MODEL_A — the other 7 dimensions still get coverage.
# When LIVE_TEST_MODEL_ID is set both cells collapse to one model; that is
# acceptable — the test's purpose is exercising all dimensions, not distinct models.
MODEL_B = os.environ.get(
    "LIVE_TEST_MODEL_ID", "openrouter/meta-llama/llama-3.2-3b-instruct"
)

# ---------------------------------------------------------------------------
# Experiment payloads — each is a single cell (1 run × 2 reps = 2 runs per experiment)
# ---------------------------------------------------------------------------

CELL_A_PAYLOAD = {
    "dataset_name": "live-e2e",
    "dataset_version": "1.0.0",
    "model_ids": [MODEL_A],
    "strategies": ["single_agent"],
    "tool_variants": ["without_tools"],
    "review_profiles": ["default"],
    "verification_variants": ["none"],
    "parallel_modes": [False],
    "tool_extension_sets": [[]],
    "num_repetitions": 2,
    # Cheap cell: no tools, no verification, no extensions → tight cap.
    "max_experiment_cost_usd": 0.20,
    "strategy_configs": {"single_agent": {"max_turns": 3}},
}

CELL_B_PAYLOAD = {
    "dataset_name": "live-e2e",
    "dataset_version": "1.0.0",
    "model_ids": [MODEL_B],
    "strategies": ["per_file"],
    "tool_variants": ["with_tools"],
    "review_profiles": ["strict"],
    "verification_variants": ["with_verification"],
    "parallel_modes": [True],
    "tool_extension_sets": [["tree_sitter", "lsp", "devdocs"]],
    "num_repetitions": 2,
    # More expensive: verification + 3 extensions + parallel → higher cap.
    "max_experiment_cost_usd": 0.60,
    "strategy_configs": {"per_file": {"max_turns": 3}},
}


def test_matrix_all_dims(live_client, experiment_cleanup):
    # --- Submit Cell A ---
    experiment_id_a = unique_experiment_id("live-e2e-all-dims-a")
    experiment_cleanup.append(experiment_id_a)
    resp_a = live_client.post("/experiments", json={**CELL_A_PAYLOAD, "experiment_id": experiment_id_a})
    assert resp_a.status_code == 201, f"Cell A submit failed {resp_a.status_code}: {resp_a.text}"
    body_a = resp_a.json()
    assert body_a["experiment_id"] == experiment_id_a
    assert body_a["total_runs"] == 2, (
        f"Cell A: expected 2 runs (1 cell × 2 reps), got {body_a['total_runs']}"
    )

    # --- Submit Cell B ---
    experiment_id_b = unique_experiment_id("live-e2e-all-dims-b")
    experiment_cleanup.append(experiment_id_b)
    resp_b = live_client.post("/experiments", json={**CELL_B_PAYLOAD, "experiment_id": experiment_id_b})
    assert resp_b.status_code == 201, f"Cell B submit failed {resp_b.status_code}: {resp_b.text}"
    body_b = resp_b.json()
    assert body_b["experiment_id"] == experiment_id_b
    assert body_b["total_runs"] == 2, (
        f"Cell B: expected 2 runs (1 cell × 2 reps), got {body_b['total_runs']}"
    )

    # --- Poll Cell A to completion ---
    # Both experiments run concurrently on the cluster; polling is sequential.
    # Cell A's timeout is deliberately tight (600 s) — it's the cheap cell,
    # and a hang here must not starve Cell B's poll budget.
    final_a = poll_until_done(live_client, experiment_id_a, timeout_s=600, poll_interval_s=15)
    completed_a = final_a["completed_runs"]
    failed_a = final_a["failed_runs"]
    assert completed_a + failed_a == 2, (
        f"Cell A: expected 2 terminal runs, completed={completed_a} failed={failed_a}"
    )
    assert completed_a >= 1, (
        f"Cell A: at least one run must succeed; completed={completed_a}"
    )

    # --- Poll Cell B to completion ---
    # Cell B has extensions + verification; allow the full 1200 s.
    final_b = poll_until_done(live_client, experiment_id_b, timeout_s=1200, poll_interval_s=15)
    completed_b = final_b["completed_runs"]
    failed_b = final_b["failed_runs"]
    assert completed_b + failed_b == 2, (
        f"Cell B: expected 2 terminal runs, completed={completed_b} failed={failed_b}"
    )
    assert completed_b >= 1, (
        f"Cell B: at least one run must succeed; completed={completed_b}"
    )

    # --- Fetch results for both experiments — plumbing check only ---
    results_a = live_client.get(f"/experiments/{experiment_id_a}/results")
    assert results_a.status_code == 200
    runs_a = results_a.json().get("runs", [])
    assert len(runs_a) == 2, f"Cell A results: expected 2 runs, got {len(runs_a)}"

    results_b = live_client.get(f"/experiments/{experiment_id_b}/results")
    assert results_b.status_code == 200
    runs_b = results_b.json().get("runs", [])
    assert len(runs_b) == 2, f"Cell B results: expected 2 runs, got {len(runs_b)}"
