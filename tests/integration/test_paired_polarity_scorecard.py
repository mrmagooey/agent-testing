"""Integration tests for Story 61: benchmark scorecard surfaces TN/FP from
negative labels, driving scoring through actual DB rows.

Unlike test_benchmark_scoring_integration.py (which passes dicts directly
to compute_benchmark_scorecard), these tests seed both dataset_labels and
dataset_negative_labels via the Database API and read them back through
list_dataset_labels / list_dataset_negative_labels — exercising the full
DB round-trip.

Test cases
----------
1. Healthy paired CWE-89 (30 pos + 30 neg, TP=25 FN=5 FP=3 TN=27).
2. Insufficient-sample CWE-22 (10 pos + 10 neg) — warning, null metrics.
3. owasp_score formula verified for per-CWE and aggregate rows.
"""

from __future__ import annotations

import pytest
from datetime import UTC, datetime
from pathlib import Path

from sec_review_framework.db import Database
from sec_review_framework.evaluation.benchmark_scoring import compute_benchmark_scorecard
from sec_review_framework.data.findings import Finding, Severity, VulnClass

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = "2024-01-01T00:00:00+00:00"


def _pos_row(dataset_name: str, cwe_id: str, file_path: str, idx: int) -> dict:
    # line_start / line_end are NOT NULL in dataset_labels schema; use a
    # synthetic but valid range so CHECK and NOT NULL constraints pass.
    return {
        "id": f"pos-{dataset_name}-{cwe_id}-{idx}",
        "dataset_name": dataset_name,
        "dataset_version": "v1",
        "file_path": file_path,
        "line_start": 1,
        "line_end": 10,
        "cwe_id": cwe_id,
        "vuln_class": "sqli",
        "severity": "high",
        "description": "test vuln",
        "source": "cve_patch",
        "source_ref": None,
        "confidence": "confirmed",
        "created_at": _TS,
        "notes": None,
        "introduced_in_diff": None,
        "patch_lines_changed": None,
    }


def _neg_row(dataset_name: str, cwe_id: str, file_path: str, idx: int) -> dict:
    return {
        "id": f"neg-{dataset_name}-{cwe_id}-{idx}",
        "dataset_name": dataset_name,
        "dataset_version": "v1",
        "file_path": file_path,
        "cwe_id": cwe_id,
        "vuln_class": "sqli",
        "source": "benchmark",
        "source_ref": None,
        "created_at": _TS,
        "notes": None,
    }


def _dataset_row(name: str) -> dict:
    return {
        "name": name,
        "kind": "git",
        "origin_url": "file:///fake",
        "origin_commit": "abc123",
        "created_at": _TS,
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
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


# ---------------------------------------------------------------------------
# Test 1 & 3 — Healthy paired CWE-89 (30 pos, 30 neg, TP=25 FN=5 FP=3 TN=27)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestHealthyPairedCWE89:
    """30 positives + 30 negatives for CWE-89; agent hits 25 pos and 3 neg."""

    DATASET = "bench-cwe89"
    CWE = "CWE-89"
    N_POS = 30
    N_NEG = 30
    N_TP = 25
    N_FP = 3

    async def _seed(self, db: Database) -> None:
        await db.create_dataset(_dataset_row(self.DATASET))

        pos_rows = [
            _pos_row(self.DATASET, self.CWE, f"app/vuln_{i}.py", i)
            for i in range(self.N_POS)
        ]
        await db.append_dataset_labels(pos_rows)

        neg_rows = [
            _neg_row(self.DATASET, self.CWE, f"app/clean_{i}.py", i)
            for i in range(self.N_NEG)
        ]
        await db.append_dataset_negative_labels(neg_rows)

    async def _scorecard(self, db: Database):
        pos_labels = await db.list_dataset_labels(self.DATASET)
        neg_labels = await db.list_dataset_negative_labels(self.DATASET)

        findings = (
            [_finding(self.CWE, f"app/vuln_{i}.py") for i in range(self.N_TP)]
            + [_finding(self.CWE, f"app/clean_{i}.py") for i in range(self.N_FP)]
        )
        return compute_benchmark_scorecard(
            findings=findings,
            positive_labels=pos_labels,
            negative_labels=neg_labels,
            dataset_name=self.DATASET,
        )

    async def test_db_round_trip_label_counts(self, db: Database) -> None:
        """Verify seeded labels are retrievable from DB before scoring."""
        await self._seed(db)
        pos = await db.list_dataset_labels(self.DATASET)
        neg = await db.list_dataset_negative_labels(self.DATASET)
        assert len(pos) == self.N_POS
        assert len(neg) == self.N_NEG

    async def test_per_cwe_counts(self, db: Database) -> None:
        """TP=25 FN=5 FP=3 TN=27 for CWE-89."""
        await self._seed(db)
        result = await self._scorecard(db)

        assert result is not None
        assert len(result.per_cwe) == 1
        sc = result.per_cwe[0]
        assert sc.cwe_id == self.CWE

        assert sc.tp == self.N_TP                          # 25
        assert sc.fn == self.N_POS - self.N_TP             # 5
        assert sc.fp == self.N_FP                          # 3
        assert sc.tn == self.N_NEG - self.N_FP             # 27

    async def test_no_warning_for_adequate_sample(self, db: Database) -> None:
        await self._seed(db)
        result = await self._scorecard(db)
        assert result is not None
        sc = result.per_cwe[0]
        assert sc.warning is None

    async def test_metrics_not_null(self, db: Database) -> None:
        await self._seed(db)
        result = await self._scorecard(db)
        assert result is not None
        sc = result.per_cwe[0]
        assert sc.precision is not None
        assert sc.recall is not None
        assert sc.f1 is not None
        assert sc.fp_rate is not None
        assert sc.owasp_score is not None

    async def test_aggregate_matches_per_cwe(self, db: Database) -> None:
        """Single-CWE dataset: aggregate equals per-CWE counts."""
        await self._seed(db)
        result = await self._scorecard(db)
        assert result is not None
        sc = result.per_cwe[0]
        agg = result.aggregate

        assert agg["tp"] == sc.tp
        assert agg["fn"] == sc.fn
        assert agg["fp"] == sc.fp
        assert agg["tn"] == sc.tn

    # ---- Test 3: owasp_score formula (per-CWE and aggregate) ----

    async def test_owasp_score_per_cwe(self, db: Database) -> None:
        """owasp_score == TPR - FPR to float precision."""
        await self._seed(db)
        result = await self._scorecard(db)
        assert result is not None
        sc = result.per_cwe[0]

        tp, fn, fp, tn = sc.tp, sc.fn, sc.fp, sc.tn
        tpr = tp / (tp + fn)
        fpr = fp / (fp + tn)
        expected = tpr - fpr

        assert abs(sc.owasp_score - expected) < 1e-9

    async def test_owasp_score_aggregate(self, db: Database) -> None:
        """Aggregate owasp_score == TPR - FPR computed from aggregate counts."""
        await self._seed(db)
        result = await self._scorecard(db)
        assert result is not None
        agg = result.aggregate

        tp, fn, fp, tn = agg["tp"], agg["fn"], agg["fp"], agg["tn"]
        tpr = tp / (tp + fn)
        fpr = fp / (fp + tn)
        expected = tpr - fpr

        assert abs(agg["owasp_score"] - expected) < 1e-9


# ---------------------------------------------------------------------------
# Test 2 — Insufficient sample CWE-22 (10 pos, 10 neg)
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestInsufficientSampleCWE22:
    """10 positives + 10 negatives for CWE-22; both below the n<25 threshold."""

    DATASET = "bench-cwe22"
    CWE = "CWE-22"
    N_POS = 10
    N_NEG = 10

    async def _seed(self, db: Database) -> None:
        await db.create_dataset(_dataset_row(self.DATASET))

        pos_rows = [
            _pos_row(self.DATASET, self.CWE, f"app/trav_{i}.py", i)
            for i in range(self.N_POS)
        ]
        await db.append_dataset_labels(pos_rows)

        neg_rows = [
            _neg_row(self.DATASET, self.CWE, f"app/safe_{i}.py", i)
            for i in range(self.N_NEG)
        ]
        await db.append_dataset_negative_labels(neg_rows)

    async def _scorecard(self, db: Database):
        pos_labels = await db.list_dataset_labels(self.DATASET)
        neg_labels = await db.list_dataset_negative_labels(self.DATASET)
        # Agent reports a few findings, but sample size is the real test
        findings = [_finding(self.CWE, f"app/trav_{i}.py") for i in range(5)]
        return compute_benchmark_scorecard(
            findings=findings,
            positive_labels=pos_labels,
            negative_labels=neg_labels,
            dataset_name=self.DATASET,
        )

    async def test_warning_present(self, db: Database) -> None:
        """n<25 per polarity → warning emitted on per-CWE row."""
        await self._seed(db)
        result = await self._scorecard(db)

        assert result is not None
        assert len(result.per_cwe) == 1
        sc = result.per_cwe[0]
        assert sc.warning is not None
        assert "insufficient sample size" in sc.warning

    async def test_metrics_null_due_to_small_sample(self, db: Database) -> None:
        """Derived metrics are suppressed when sample size is inadequate."""
        await self._seed(db)
        result = await self._scorecard(db)

        assert result is not None
        sc = result.per_cwe[0]
        assert sc.precision is None
        assert sc.recall is None
        assert sc.f1 is None
        assert sc.fp_rate is None
        assert sc.owasp_score is None

    async def test_counts_still_correct(self, db: Database) -> None:
        """Raw counts are always populated even with a small sample."""
        await self._seed(db)
        result = await self._scorecard(db)

        assert result is not None
        sc = result.per_cwe[0]
        # 5 TPs, 5 FNs, 0 FPs, 10 TNs (agent only hits positive files)
        assert sc.tp == 5
        assert sc.fn == 5
        assert sc.fp == 0
        assert sc.tn == self.N_NEG

    async def test_aggregate_still_computed(self, db: Database) -> None:
        """Aggregate is always populated regardless of per-CWE sample warning."""
        await self._seed(db)
        result = await self._scorecard(db)

        assert result is not None
        agg = result.aggregate
        assert agg["tp"] == 5
        assert agg["fn"] == 5
        assert agg["fp"] == 0
        assert agg["tn"] == self.N_NEG
        # Aggregate metrics are computed (no per-CWE guard at aggregate level)
        assert isinstance(agg["recall"], float)
