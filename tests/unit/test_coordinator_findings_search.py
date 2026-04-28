"""Unit tests for the /findings endpoint (cross-experiment findings search).

Tests cover:
- Response shape (total, limit, offset, facets, items keys present)
- Empty index → empty results, facets present
- Filter by vuln_class, severity, match_status, model_id, strategy
- FTS via q=
- Pagination: limit/offset
- Bad params: limit=0, limit=201, offset=-1
- Sort parameter: known values work, unknown value falls back safely
- SPA routing: GET /findings with text/html → serves SPA shell
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0),
        }
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        config_dir=config_dir,
        default_cap=4,
    )


def _make_finding(
    id: str = "f1",
    title: str = "SQL Injection",
    description: str = "User input unsanitized.",
    vuln_class: str = "sqli",
    severity: str = "high",
    match_status: str = "tp",
    model_id: str = "gpt-4o",
    strategy: str = "single_agent",
) -> dict:
    return {
        "id": id,
        "title": title,
        "description": description,
        "vuln_class": vuln_class,
        "severity": severity,
        "match_status": match_status,
        "confidence": 0.9,
        "file_path": "src/db.py",
        "line_start": 10,
        "line_end": 15,
        "cwe_ids": ["CWE-89"],
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def findings_client(tmp_path: Path):
    """TestClient with a coordinator that has some findings seeded."""
    db = Database(tmp_path / "test.db")
    await db.init()

    c = _make_coordinator(tmp_path, db)

    # Seed two runs with different filters
    await db.upsert_findings_for_run(
        run_id="run-1",
        experiment_id="exp-A",
        findings=[
            _make_finding(id="f1", vuln_class="sqli", severity="high", match_status="tp"),
            _make_finding(id="f2", vuln_class="sqli", severity="medium", match_status="fp",
                          title="SQL FP"),
        ],
        model_id="gpt-4o",
        strategy="single_agent",
        dataset_name="ds-1",
    )
    await db.upsert_findings_for_run(
        run_id="run-2",
        experiment_id="exp-B",
        findings=[
            _make_finding(id="f3", vuln_class="xss", severity="high", match_status="tp",
                          title="XSS"),
        ],
        model_id="claude-3",
        strategy="per_file",
        dataset_name="ds-2",
    )

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<!DOCTYPE html><html><body>SPA</body></html>")

    with patch.object(coord_module, "FRONTEND_DIST_DIR", dist_dir):
        with patch.object(coord_module, "coordinator", c):
            with patch.object(c, "reconcile", return_value=None):
                with TestClient(app, raise_server_exceptions=True) as client:
                    yield client


@pytest_asyncio.fixture
async def empty_client(tmp_path: Path):
    """TestClient with an empty findings index."""
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<!DOCTYPE html><html><body>SPA</body></html>")

    with patch.object(coord_module, "FRONTEND_DIST_DIR", dist_dir):
        with patch.object(coord_module, "coordinator", c):
            with patch.object(c, "reconcile", return_value=None):
                with TestClient(app, raise_server_exceptions=True) as client:
                    yield client


# ---------------------------------------------------------------------------
# Response shape
# ---------------------------------------------------------------------------


def test_findings_endpoint_response_shape(findings_client):
    """GET /findings returns the expected top-level keys."""
    resp = findings_client.get("/api/findings")
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body
    assert "limit" in body
    assert "offset" in body
    assert "facets" in body
    assert "items" in body
    assert isinstance(body["items"], list)
    assert isinstance(body["facets"], dict)


def test_findings_total_matches_all_seeded(findings_client):
    """Without filters, total should equal the number of seeded findings."""
    resp = findings_client.get("/api/findings")
    assert resp.status_code == 200
    assert resp.json()["total"] == 3


def test_findings_empty_index(empty_client):
    """Empty index returns total=0 and empty items, but still valid shape."""
    resp = empty_client.get("/api/findings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 0
    assert body["items"] == []
    assert "facets" in body


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


def test_filter_vuln_class(findings_client):
    resp = findings_client.get("/api/findings?vuln_class=sqli")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    for item in body["items"]:
        assert item["vuln_class"] == "sqli"


def test_filter_severity(findings_client):
    resp = findings_client.get("/api/findings?severity=high")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2


def test_filter_match_status(findings_client):
    resp = findings_client.get("/api/findings?match_status=fp")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_filter_model_id(findings_client):
    resp = findings_client.get("/api/findings?model_id=claude-3")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_filter_strategy(findings_client):
    resp = findings_client.get("/api/findings?strategy=per_file")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


def test_filter_experiment_id(findings_client):
    resp = findings_client.get("/api/findings?experiment_id=exp-A")
    assert resp.status_code == 200
    assert resp.json()["total"] == 2


def test_filter_dataset_name(findings_client):
    resp = findings_client.get("/api/findings?dataset_name=ds-2")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1


# ---------------------------------------------------------------------------
# FTS query
# ---------------------------------------------------------------------------


def test_fts_q_filter(findings_client):
    """q= triggers FTS; should find findings with 'SQL' in title/description."""
    resp = findings_client.get("/api/findings?q=SQL")
    assert resp.status_code == 200
    body = resp.json()
    # Both sqli findings contain "SQL" in title/description
    assert body["total"] >= 1


def test_fts_q_no_match(findings_client):
    resp = findings_client.get("/api/findings?q=zzznosuchthing999")
    assert resp.status_code == 200
    assert resp.json()["total"] == 0


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


def test_pagination_limit(findings_client):
    resp = findings_client.get("/api/findings?limit=2&offset=0")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0


def test_pagination_offset(findings_client):
    resp = findings_client.get("/api/findings?limit=2&offset=2")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["offset"] == 2


def test_pagination_offset_past_end(findings_client):
    resp = findings_client.get("/api/findings?limit=10&offset=100")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert body["items"] == []


# ---------------------------------------------------------------------------
# Bad parameters → 400
# ---------------------------------------------------------------------------


def test_bad_limit_zero(findings_client):
    resp = findings_client.get("/api/findings?limit=0")
    assert resp.status_code == 400


def test_bad_limit_over_max(findings_client):
    resp = findings_client.get("/api/findings?limit=201")
    assert resp.status_code == 400


def test_bad_offset_negative(findings_client):
    resp = findings_client.get("/api/findings?offset=-1")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Sort parameter
# ---------------------------------------------------------------------------


def test_sort_created_at_asc(findings_client):
    """sort=created_at asc must not raise."""
    resp = findings_client.get("/api/findings?sort=created_at+asc")
    assert resp.status_code == 200


def test_sort_unknown_falls_back(findings_client):
    """Unknown sort column must not raise (falls back to created_at desc)."""
    resp = findings_client.get("/api/findings?sort=nonexistent_col+desc")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Facets shape
# ---------------------------------------------------------------------------


def test_facets_contain_expected_keys(findings_client):
    resp = findings_client.get("/api/findings")
    facets = resp.json()["facets"]
    for key in ("vuln_class", "severity", "match_status", "model_id", "strategy", "dataset_name"):
        assert key in facets, f"Missing facet: {key}"


def test_facets_counts_match_filter_off(findings_client):
    """Facets should reflect correct counts when no filters applied."""
    resp = findings_client.get("/api/findings")
    facets = resp.json()["facets"]
    assert facets["vuln_class"].get("sqli", 0) == 2
    assert facets["vuln_class"].get("xss", 0) == 1


# ---------------------------------------------------------------------------
# Multi-value query params (regression test)
# ---------------------------------------------------------------------------


def test_multi_value_vuln_class_filter(findings_client):
    """Repeated query-string params must be honored (Query(default=None) regression)."""
    # Request findings with BOTH sqli AND xss
    resp = findings_client.get("/api/findings?vuln_class=sqli&vuln_class=xss")
    assert resp.status_code == 200
    body = resp.json()
    # Should return 3 findings: 2 sqli + 1 xss
    assert body["total"] == 3
    assert len(body["items"]) == 3
    vuln_classes = {item["vuln_class"] for item in body["items"]}
    assert vuln_classes == {"sqli", "xss"}


# ---------------------------------------------------------------------------
# SPA routing: /findings with text/html → serves SPA
# ---------------------------------------------------------------------------


def test_findings_route_serves_spa_for_browser(findings_client):
    """Browser navigation to /findings must return the SPA shell."""
    resp = findings_client.get(
        "/findings",
        headers={"Accept": "text/html,*/*;q=0.8"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "SPA" in resp.text


# ---------------------------------------------------------------------------
# Severity sort — semantic rank (bug fix tests)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def severity_sort_client(tmp_path: Path):
    """TestClient with all four severity levels seeded for sort tests."""
    db = Database(tmp_path / "test.db")
    await db.init()

    c = _make_coordinator(tmp_path, db)

    # Seed four findings, one per severity level
    await db.upsert_findings_for_run(
        run_id="run-sev",
        experiment_id="exp-sev",
        findings=[
            _make_finding(id="sev-crit", severity="critical", vuln_class="sqli",
                          title="Critical Finding"),
            _make_finding(id="sev-high", severity="high", vuln_class="sqli",
                          title="High Finding"),
            _make_finding(id="sev-med", severity="medium", vuln_class="sqli",
                          title="Medium Finding"),
            _make_finding(id="sev-low", severity="low", vuln_class="sqli",
                          title="Low Finding"),
        ],
        model_id="gpt-4o",
        strategy="single_agent",
        dataset_name="ds-sev",
    )

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<!DOCTYPE html><html><body>SPA</body></html>")

    with patch.object(coord_module, "FRONTEND_DIST_DIR", dist_dir):
        with patch.object(coord_module, "coordinator", c):
            with patch.object(c, "reconcile", return_value=None):
                with TestClient(app, raise_server_exceptions=True) as client:
                    yield client


def test_severity_sort_rank_desc(severity_sort_client):
    """sort=severity desc → critical first, then high, medium, low (semantic order).

    This is the bug-fix test: alphabetic ordering would return medium first.
    The frontend option 'severity desc' is labelled 'Severity (high→low)' so
    the user expects the highest-severity items first.
    """
    resp = severity_sort_client.get("/api/findings?sort=severity+desc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    severities = [item["severity"] for item in body["items"]]
    assert severities == ["critical", "high", "medium", "low"], (
        f"Expected semantic desc order ['critical','high','medium','low'], got {severities}"
    )


def test_severity_sort_rank_asc(severity_sort_client):
    """sort=severity asc → low first, then medium, high, critical (semantic ascending)."""
    resp = severity_sort_client.get("/api/findings?sort=severity+asc")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 4
    severities = [item["severity"] for item in body["items"]]
    assert severities == ["low", "medium", "high", "critical"], (
        f"Expected semantic asc order ['low','medium','high','critical'], got {severities}"
    )


# ---------------------------------------------------------------------------
# Date range filter tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def date_range_client(tmp_path: Path):
    """TestClient with findings at known timestamps for date-range tests."""
    import aiosqlite

    db = Database(tmp_path / "test.db")
    await db.init()

    c = _make_coordinator(tmp_path, db)

    # Seed findings — created_at will be auto-stamped to now.
    # We then UPDATE them directly with deterministic timestamps.
    await db.upsert_findings_for_run(
        run_id="run-dates",
        experiment_id="exp-dates",
        findings=[
            _make_finding(id="date-early", severity="low", title="Early Finding"),
            _make_finding(id="date-in1",   severity="medium", title="In Range 1"),
            _make_finding(id="date-in2",   severity="high",   title="In Range 2"),
            _make_finding(id="date-late",  severity="critical", title="Late Finding"),
        ],
        model_id="gpt-4o",
        strategy="single_agent",
        dataset_name="ds-dates",
    )

    # Override created_at to deterministic values so range tests are stable
    async with aiosqlite.connect(db.db_path) as adb:
        await adb.execute(
            "UPDATE findings SET created_at = ? WHERE id = ?",
            ("2026-04-10T00:00:00+00:00", "date-early"),
        )
        await adb.execute(
            "UPDATE findings SET created_at = ? WHERE id = ?",
            ("2026-04-15T00:00:00+00:00", "date-in1"),
        )
        await adb.execute(
            "UPDATE findings SET created_at = ? WHERE id = ?",
            ("2026-04-20T00:00:00+00:00", "date-in2"),
        )
        await adb.execute(
            "UPDATE findings SET created_at = ? WHERE id = ?",
            ("2026-04-25T00:00:00+00:00", "date-late"),
        )
        await adb.commit()

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text("<!DOCTYPE html><html><body>SPA</body></html>")

    with patch.object(coord_module, "FRONTEND_DIST_DIR", dist_dir):
        with patch.object(coord_module, "coordinator", c):
            with patch.object(c, "reconcile", return_value=None):
                with TestClient(app, raise_server_exceptions=True) as client:
                    yield client


def test_date_range_filter(date_range_client):
    """created_from + created_to returns only findings within that range (inclusive).

    The DB stores full ISO-8601 timestamps (e.g. '2026-04-20T00:00:00+00:00').
    The SQL comparison is lexicographic (TEXT column), so the created_to bound
    must be at least as large as any timestamp on that day.  We use the end-of-day
    timestamp '2026-04-20T23:59:59' to include all findings on 2026-04-20.
    """
    resp = date_range_client.get(
        "/api/findings?created_from=2026-04-15&created_to=2026-04-20T23:59:59"
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    assert ids == {"date-in1", "date-in2"}, (
        f"Expected only in-range findings, got {ids}"
    )
    assert body["total"] == 2


def test_date_range_to_bare_date_inclusive(date_range_client):
    """A bare YYYY-MM-DD created_to value includes findings on that date.

    This guards against the lexicographic-truncation bug: with raw SQL
    `f.created_at <= '2026-04-20'`, a finding stamped '2026-04-20T00:00:00+00:00'
    would be excluded because `'...T00:00:00...' > '2026-04-20'` lexicographically.
    The coordinator normalises bare-date `created_to` to end-of-day so the user's
    intent ("up to and including April 20") is honoured.

    The frontend's <input type="date"> always produces bare YYYY-MM-DD values, so
    this is the actual UX path.
    """
    resp = date_range_client.get(
        "/api/findings?created_from=2026-04-15&created_to=2026-04-20"
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    # date-in2 is at exactly 2026-04-20T00:00:00+00:00 — must be included.
    assert "date-in2" in ids, (
        f"date-in2 (Apr 20 00:00) missing from results — bare-date created_to "
        f"normalisation broken. Got: {ids}"
    )
    # date-late at 2026-04-25 must still be excluded.
    assert "date-late" not in ids
    assert ids == {"date-in1", "date-in2"}


def test_date_range_to_full_timestamp_unchanged(date_range_client):
    """A created_to with explicit time is NOT normalised — strict comparison stands.

    The bare-date normalisation only triggers on YYYY-MM-DD; full timestamps pass
    through verbatim so callers can still do strict sub-day filtering.
    """
    # date-in2 is at 2026-04-20T00:00:00. Asking for created_to before that should exclude it.
    resp = date_range_client.get(
        "/api/findings?created_from=2026-04-10&created_to=2026-04-19T23:59:59"
    )
    assert resp.status_code == 200
    body = resp.json()
    ids = {item["id"] for item in body["items"]}
    # date-early (Apr 10) and date-in1 (Apr 15) included; date-in2 (Apr 20) excluded.
    assert "date-in2" not in ids, (
        f"Strict timestamp filter should exclude Apr 20 finding when end is Apr 19 23:59:59; "
        f"got {ids}"
    )
    assert ids == {"date-early", "date-in1"}


# ---------------------------------------------------------------------------
# Combined-filters AND test
# ---------------------------------------------------------------------------


def test_combined_filters_and(findings_client):
    """severity=high AND vuln_class=sqli → only finding f1 (both conditions)."""
    resp = findings_client.get("/api/findings?severity=high&vuln_class=sqli")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == "f1"
