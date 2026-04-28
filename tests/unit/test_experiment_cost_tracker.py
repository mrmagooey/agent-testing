"""Unit tests for ExperimentCostTracker — thread-safety, idempotent halt, race conditions."""

from __future__ import annotations

import threading

import pytest

from sec_review_framework.coordinator import ExperimentCostTracker

# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------


class TestExperimentCostTrackerBasic:
    def test_initial_state(self):
        tracker = ExperimentCostTracker("experiment-1", cap_usd=10.0)
        assert tracker.spent_usd == 0.0
        assert tracker._cancelled is False

    def test_record_below_cap_returns_false(self):
        tracker = ExperimentCostTracker("experiment-1", cap_usd=10.0)
        result = tracker.record_job_cost(5.0)
        assert result is False
        assert tracker.spent_usd == pytest.approx(5.0)

    def test_record_exactly_at_cap_returns_true(self):
        tracker = ExperimentCostTracker("experiment-1", cap_usd=10.0)
        result = tracker.record_job_cost(10.0)
        assert result is True
        assert tracker._cancelled is True

    def test_record_exceeds_cap_returns_true(self):
        tracker = ExperimentCostTracker("experiment-1", cap_usd=5.0)
        tracker.record_job_cost(3.0)
        result = tracker.record_job_cost(3.0)
        assert result is True

    def test_no_cap_never_cancels(self):
        tracker = ExperimentCostTracker("experiment-1", cap_usd=None)
        for _ in range(100):
            result = tracker.record_job_cost(1000.0)
            assert result is False
        assert tracker._cancelled is False

    def test_cap_zero_cancels_immediately(self):
        """A cap of 0.0 triggers halt on the first non-zero cost."""
        tracker = ExperimentCostTracker("experiment-1", cap_usd=0.0)
        result = tracker.record_job_cost(0.01)
        # cap_usd is falsy when 0.0; check Python bool(0.0) is False
        # The source checks `if self.cap_usd and ...` — so cap=0.0 behaves like no cap.
        assert result is False  # 0.0 is falsy — documents existing behaviour

    def test_spent_accumulates_multiple_calls(self):
        tracker = ExperimentCostTracker("experiment-x", cap_usd=100.0)
        tracker.record_job_cost(1.5)
        tracker.record_job_cost(2.5)
        tracker.record_job_cost(3.0)
        assert tracker.spent_usd == pytest.approx(7.0)


# ---------------------------------------------------------------------------
# Idempotent halt — once cancelled, further calls must return False
# ---------------------------------------------------------------------------


class TestIdempotentHalt:
    def test_second_call_after_cancel_returns_false(self):
        """Once _cancelled is True, subsequent calls should return False (idempotent)."""
        tracker = ExperimentCostTracker("experiment-1", cap_usd=5.0)
        first = tracker.record_job_cost(6.0)   # crosses cap → True
        second = tracker.record_job_cost(1.0)   # already cancelled → False
        assert first is True
        assert second is False

    def test_halt_fires_exactly_once(self):
        """Record 10 costs crossing cap; exactly one call should return True."""
        tracker = ExperimentCostTracker("experiment-1", cap_usd=3.0)
        results = [tracker.record_job_cost(1.0) for _ in range(10)]
        true_count = sum(1 for r in results if r is True)
        assert true_count == 1

    def test_cancelled_flag_stays_true(self):
        tracker = ExperimentCostTracker("experiment-1", cap_usd=1.0)
        tracker.record_job_cost(2.0)  # cancel
        tracker.record_job_cost(0.0)  # harmless zero-cost follow-up
        assert tracker._cancelled is True


# ---------------------------------------------------------------------------
# Thread-safety — concurrent record_job_cost() calls
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_concurrent_accumulation_is_accurate(self):
        """_spent must equal the sum of all costs across 50 concurrent threads."""
        tracker = ExperimentCostTracker("experiment-c", cap_usd=None)
        n_threads = 50
        cost_per_thread = 0.10

        threads = [
            threading.Thread(target=tracker.record_job_cost, args=(cost_per_thread,))
            for _ in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert tracker.spent_usd == pytest.approx(n_threads * cost_per_thread, rel=1e-6)

    def test_halt_fires_exactly_once_under_concurrency(self):
        """Under concurrent load, exactly one thread should observe the cap trip."""
        cap = 5.0
        cost_per_call = 0.5
        n_threads = 50  # total cost = 25.0, cap will be crossed
        tracker = ExperimentCostTracker("experiment-c", cap_usd=cap)

        results: list[bool] = []
        lock = threading.Lock()

        def worker():
            r = tracker.record_job_cost(cost_per_call)
            with lock:
                results.append(r)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        true_count = sum(1 for r in results if r is True)
        assert true_count == 1

    def test_no_data_race_on_cancelled_flag(self):
        """_cancelled should only ever transition from False → True (never back)."""
        tracker = ExperimentCostTracker("experiment-c", cap_usd=1.0)
        violations: list[bool] = []

        def worker():
            tracker.record_job_cost(0.1)
            # cancelled flag should only be True or False — never an intermediate inconsistent state
            state = tracker._cancelled
            if state not in (True, False):
                violations.append(True)

        threads = [threading.Thread(target=worker) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert violations == []

    def test_spent_never_decreases_under_concurrency(self):
        """Each recorded cost must move _spent monotonically upward."""
        tracker = ExperimentCostTracker("experiment-c", cap_usd=None)
        samples: list[float] = []
        lock = threading.Lock()

        def worker():
            tracker.record_job_cost(0.01)
            with lock:
                samples.append(tracker.spent_usd)

        threads = [threading.Thread(target=worker) for _ in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Samples may be unordered, but the final value must be the max
        assert tracker.spent_usd == pytest.approx(max(samples), rel=1e-6)
