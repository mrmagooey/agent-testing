"""Shared fixtures and helpers for the live k8s e2e test suite.

These tests run against a real cluster bootstrapped by
scripts/e2e-live/bootstrap.sh. The coordinator is expected to be reachable
at http://localhost:8080 (or E2E_LIVE_BASE_URL) via port-forward.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

# ---------------------------------------------------------------------------
# Marker constant — import this in spec files
# ---------------------------------------------------------------------------

K8S_LIVE_MARK = pytest.mark.k8s_live

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def live_base_url() -> str:
    return os.getenv("E2E_LIVE_BASE_URL", "http://localhost:8080")


@pytest.fixture
def live_client(live_base_url: str):
    client = httpx.Client(base_url=live_base_url, timeout=60)
    yield client
    client.close()


@pytest.fixture(scope="session", autouse=True)
def require_coordinator(live_base_url: str):
    """Session-scoped guard: skip all tests if coordinator is not reachable."""
    try:
        resp = httpx.get(f"{live_base_url}/health", timeout=10)
        resp.raise_for_status()
    except Exception:
        pytest.skip(
            "coordinator not reachable — run scripts/e2e-live/bootstrap.sh and port-forward"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def unique_batch_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time())}-{os.getpid()}"


def poll_until_done(
    client: httpx.Client,
    batch_id: str,
    timeout_s: int = 600,
    poll_interval_s: int = 5,
) -> dict:
    """Poll GET /batches/{batch_id} until the batch reaches a terminal state.

    Terminal is defined as:
      - completed_runs + failed_runs >= total_runs, OR
      - batch-level status is one of: completed, cancelled, failed

    Returns the final batch status dict. Raises TimeoutError on timeout.
    """
    terminal_statuses = {"completed", "cancelled", "failed"}
    deadline = time.monotonic() + timeout_s

    while time.monotonic() < deadline:
        resp = client.get(f"/batches/{batch_id}")
        resp.raise_for_status()
        data = resp.json()

        total = data.get("total_runs", 0)
        completed = data.get("completed_runs", 0)
        failed = data.get("failed_runs", 0)
        status = data.get("status", "")

        if status in terminal_statuses:
            return data
        if total > 0 and (completed + failed) >= total:
            return data

        time.sleep(poll_interval_s)

    raise TimeoutError(
        f"Batch {batch_id} did not reach terminal state within {timeout_s}s"
    )


# ---------------------------------------------------------------------------
# Cleanup fixture — best-effort DELETE after each test
# ---------------------------------------------------------------------------


@pytest.fixture
def batch_cleanup(live_client: httpx.Client):
    """Yields a list; append batch IDs to it; they will be deleted after the test."""
    batch_ids: list[str] = []
    yield batch_ids
    for bid in batch_ids:
        try:
            live_client.delete(f"/batches/{bid}")
        except Exception:
            pass  # best-effort; never fail the test on cleanup errors
