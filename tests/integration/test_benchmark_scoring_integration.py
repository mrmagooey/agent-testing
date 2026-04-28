"""Integration tests for benchmark scoring with negative labels.

Covers:
  1. Small fixture (5 pos + 5 neg, 2 CWEs): sample-size warning emitted.
  2. Large fixture (≥25 per polarity per CWE): metrics computed, no warning.
  3. CVE-only dataset (positives only, no negatives): RunResult.benchmark_scorecard is None
     and existing EvaluationResult fields are unchanged (backward-compat).
  4. Worker._fetch_negative_labels uses the correct /negative-labels endpoint.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sec_review_framework.data.experiment import ExperimentRun
from sec_review_framework.evaluation.benchmark_scoring import compute_benchmark_scorecard
from sec_review_framework.data.findings import Finding, Severity, VulnClass
from sec_review_framework.worker import ExperimentWorker


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _pos_label(cwe_id: str, file_path: str, line_start: int | None = None, line_end: int | None = None) -> dict:
    row: dict = {
        "id": f"pos-{cwe_id}-{file_path}",
        "dataset_name": "bench",
        "dataset_version": "v1",
        "file_path": file_path,
        "cwe_id": cwe_id,
        "vuln_class": "sqli",
        "severity": "high",
        "description": "test",
        "source": "benchmark",
        "confidence": "confirmed",
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    if line_start is not None:
        row["line_start"] = line_start
        row["line_end"] = line_end
    return row


def _neg_label(cwe_id: str, file_path: str) -> dict:
    return {
        "id": f"neg-{cwe_id}-{file_path}",
        "dataset_name": "bench",
        "dataset_version": "v1",
        "file_path": file_path,
        "cwe_id": cwe_id,
        "vuln_class": "sqli",
        "source": "benchmark",
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _finding(cwe_id: str, file_path: str) -> Finding:
    return Finding(
        id=f"f-{cwe_id}-{file_path}",
        file_path=file_path,
        vuln_class=VulnClass.SQLI,
        cwe_ids=[cwe_id],
        severity=Severity.HIGH,
        title="test",
        description="desc",
        confidence=0.8,
    )


# ---------------------------------------------------------------------------
# 1. Small fixture — sample-size guard
# ---------------------------------------------------------------------------

class TestSmallFixture:
    """5 positive + 5 negative labels across 2 CWEs → sample-size warning."""

    def setup_method(self):
        # CWE-89: 3 pos, 2 neg
        # CWE-79: 2 pos, 3 neg
        self.pos = (
            [_pos_label("CWE-89", f"app/sqli_{i}.py") for i in range(3)]
            + [_pos_label("CWE-79", f"app/xss_{i}.py") for i in range(2)]
        )
        self.neg = (
            [_neg_label("CWE-89", f"app/clean_sqli_{i}.py") for i in range(2)]
            + [_neg_label("CWE-79", f"app/clean_xss_{i}.py") for i in range(3)]
        )

    def test_sample_size_warning_emitted_for_all_cwes(self):
        result = compute_benchmark_scorecard(
            findings=[],
            positive_labels=self.pos,
            negative_labels=self.neg,
            dataset_name="small-bench",
        )
        assert result is not None
        assert len(result.per_cwe) == 2
        for sc in result.per_cwe:
            assert sc.warning is not None, f"Expected warning for {sc.cwe_id}"
            assert "insufficient sample size" in sc.warning
            assert sc.precision is None
            assert sc.recall is None
            assert sc.f1 is None
            assert sc.fp_rate is None
            assert sc.owasp_score is None

    def test_counts_correct_with_tp_fp(self):
        findings = [
            _finding("CWE-89", "app/sqli_0.py"),       # TP
            _finding("CWE-89", "app/clean_sqli_0.py"), # FP
        ]
        result = compute_benchmark_scorecard(
            findings=findings,
            positive_labels=self.pos,
            negative_labels=self.neg,
            dataset_name="small-bench",
        )
        assert result is not None
        cwe89 = next(c for c in result.per_cwe if c.cwe_id == "CWE-89")
        assert cwe89.tp == 1
        assert cwe89.fp == 1
        assert cwe89.fn == 2
        assert cwe89.tn == 1

    def test_aggregate_computed_despite_small_sample(self):
        """Aggregate is always computed even when per-CWE metrics are suppressed."""
        findings = [_finding("CWE-89", "app/sqli_0.py")]  # one TP
        result = compute_benchmark_scorecard(
            findings=findings,
            positive_labels=self.pos,
            negative_labels=self.neg,
            dataset_name="small-bench",
        )
        assert result is not None
        agg = result.aggregate
        assert isinstance(agg["precision"], float)
        assert agg["tp"] == 1

    def test_to_dict_has_required_keys(self):
        result = compute_benchmark_scorecard(
            findings=[],
            positive_labels=self.pos,
            negative_labels=self.neg,
            dataset_name="small-bench",
        )
        assert result is not None
        d = result.to_dict()
        assert "dataset_name" in d
        assert "per_cwe" in d
        assert "aggregate" in d
        required_cwe_keys = {
            "cwe_id", "tp", "fp", "tn", "fn",
            "precision", "recall", "f1", "fp_rate", "owasp_score", "warning",
        }
        for entry in d["per_cwe"]:
            assert required_cwe_keys == set(entry.keys())


# ---------------------------------------------------------------------------
# 2. Large fixture — metrics computed when sample size adequate
# ---------------------------------------------------------------------------

class TestLargeFixture:
    """≥25 per polarity per CWE → metrics are not None."""

    N = 30

    def setup_method(self):
        self.pos = [_pos_label("CWE-89", f"app/vuln_{i}.py") for i in range(self.N)]
        self.neg = [_neg_label("CWE-89", f"app/clean_{i}.py") for i in range(self.N)]

    def _run(self, n_tp: int, n_fp: int):
        findings = (
            [_finding("CWE-89", f"app/vuln_{i}.py") for i in range(n_tp)]
            + [_finding("CWE-89", f"app/clean_{i}.py") for i in range(n_fp)]
        )
        return compute_benchmark_scorecard(
            findings=findings,
            positive_labels=self.pos,
            negative_labels=self.neg,
            dataset_name="large-bench",
        )

    def test_metrics_not_none(self):
        result = self._run(20, 5)
        assert result is not None
        sc = result.per_cwe[0]
        assert sc.warning is None
        assert sc.precision is not None
        assert sc.recall is not None
        assert sc.f1 is not None
        assert sc.fp_rate is not None
        assert sc.owasp_score is not None

    def test_precision_recall_correct(self):
        result = self._run(n_tp=20, n_fp=5)
        assert result is not None
        sc = result.per_cwe[0]
        assert sc.tp == 20
        assert sc.fp == 5
        assert sc.fn == 10
        assert sc.tn == 25
        assert sc.precision == pytest.approx(20 / 25)
        assert sc.recall == pytest.approx(20 / 30)

    def test_owasp_score_is_tpr_minus_fpr(self):
        result = self._run(n_tp=20, n_fp=5)
        assert result is not None
        sc = result.per_cwe[0]
        expected_owasp = (20 / 30) - (5 / 30)
        assert sc.owasp_score == pytest.approx(expected_owasp)

    def test_perfect_score(self):
        """Agent finds all TPs and no FPs → precision=recall=1, owasp=1."""
        result = self._run(n_tp=self.N, n_fp=0)
        assert result is not None
        sc = result.per_cwe[0]
        assert sc.tp == self.N
        assert sc.fp == 0
        assert sc.fn == 0
        assert sc.tn == self.N
        assert sc.precision == pytest.approx(1.0)
        assert sc.recall == pytest.approx(1.0)
        assert sc.f1 == pytest.approx(1.0)
        assert sc.fp_rate == pytest.approx(0.0)
        assert sc.owasp_score == pytest.approx(1.0)

    def test_worst_score(self):
        """Agent misses all TPs and hits all FPs → owasp score is negative."""
        result = self._run(n_tp=0, n_fp=self.N)
        assert result is not None
        sc = result.per_cwe[0]
        assert sc.tp == 0
        assert sc.fp == self.N
        assert sc.fn == self.N
        assert sc.tn == 0
        assert sc.recall == pytest.approx(0.0)
        assert sc.fp_rate == pytest.approx(1.0)
        assert sc.owasp_score == pytest.approx(-1.0)


# ---------------------------------------------------------------------------
# 3. Backward-compat: CVE-only dataset (no negative labels)
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    """Datasets without negative labels return None → existing metrics unchanged."""

    def test_no_negatives_returns_none(self):
        pos = [_pos_label("CWE-89", "app/vuln.py")]
        result = compute_benchmark_scorecard(
            findings=[_finding("CWE-89", "app/vuln.py")],
            positive_labels=pos,
            negative_labels=[],
            dataset_name="cve-dataset",
        )
        assert result is None

    def test_empty_everything_returns_none(self):
        result = compute_benchmark_scorecard(
            findings=[],
            positive_labels=[],
            negative_labels=[],
            dataset_name="cve-dataset",
        )
        assert result is None


# ---------------------------------------------------------------------------
# 4. Worker._fetch_negative_labels uses the correct endpoint
# ---------------------------------------------------------------------------

def _make_run(upload_url: str | None = None) -> ExperimentRun:
    return ExperimentRun(
        id="run-001",
        experiment_id="exp-001",
        strategy_id="builtin.single_agent",
        dataset_name="bench-dataset",
        dataset_version="v1",
        model_id="fake-model",
        upload_url=upload_url,
        result_transport="http" if upload_url else "pvc",  # type: ignore[arg-type]
    )


_NEG_LABEL_JSON = {
    "id": "neg-001",
    "dataset_name": "bench-dataset",
    "dataset_version": "v1",
    "file_path": "app/clean.py",
    "cwe_id": "CWE-89",
    "vuln_class": "sqli",
    "source": "benchmark",
    "created_at": "2024-01-01T00:00:00+00:00",
}


class TestFetchNegativeLabels:
    """ExperimentWorker._fetch_negative_labels calls the correct coordinator endpoint."""

    def test_fetch_negative_labels_correct_url(self, tmp_path: Path):
        run = _make_run(
            upload_url="http://coordinator:8080/api/internal/runs/run-001/result"
        )
        worker = ExperimentWorker()

        mock_response = MagicMock()
        mock_response.json.return_value = [_NEG_LABEL_JSON]
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("httpx.Client", return_value=mock_client):
            labels = worker._fetch_negative_labels(run, tmp_path)

        called_url = mock_client.get.call_args[0][0]
        assert called_url == "http://coordinator:8080/datasets/bench-dataset/negative-labels"
        assert len(labels) == 1
        assert labels[0]["cwe_id"] == "CWE-89"

    def test_fetch_negative_labels_passes_version(self, tmp_path: Path):
        run = _make_run(
            upload_url="http://coordinator:8080/api/internal/runs/run-001/result"
        )
        worker = ExperimentWorker()

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("httpx.Client", return_value=mock_client):
            worker._fetch_negative_labels(run, tmp_path)

        call_kwargs = mock_client.get.call_args[1]
        params = call_kwargs.get("params", {})
        assert params.get("version") == "v1"

    def test_fetch_negative_labels_empty_when_no_coordinator(self, tmp_path: Path, monkeypatch):
        run = _make_run()  # no upload_url
        monkeypatch.delenv("COORDINATOR_INTERNAL_URL", raising=False)
        worker = ExperimentWorker()

        with patch("httpx.Client") as mock_httpx:
            labels = worker._fetch_negative_labels(run, tmp_path)

        mock_httpx.assert_not_called()
        assert labels == []

    def test_fetch_negative_labels_http_error_raises(self, tmp_path: Path):
        import httpx

        run = _make_run(
            upload_url="http://coordinator:8080/api/internal/runs/run-001/result"
        )
        worker = ExperimentWorker()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=MagicMock(),
                response=MagicMock(status_code=500),
            )
        )

        with patch("httpx.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Failed to fetch negative labels"):
                worker._fetch_negative_labels(run, tmp_path)
