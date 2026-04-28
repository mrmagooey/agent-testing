"""Unit tests for the findings index in Database.

Tests cover:
- Schema migration idempotency (multiple init() calls)
- upsert_findings_for_run idempotency (double-insert same run → no dupes)
- query_findings by each individual filter
- FTS 'q' parameter
- Pagination (limit / offset)
- Facet correctness — facet excludes its own filter
- update_finding_match_status
- count_all_findings
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from sec_review_framework.db import Database, _escape_fts_query

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    """Fresh in-process SQLite database for each test."""
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


def _make_finding(
    id: str = "f1",
    title: str = "SQL Injection",
    description: str = "Unsanitized user input used in query.",
    vuln_class: str = "sqli",
    severity: str = "high",
    confidence: float = 0.9,
    file_path: str = "app/db.py",
    line_start: int = 42,
    line_end: int = 45,
    cwe_ids: list[str] | None = None,
    match_status: str | None = "tp",
) -> dict:
    return {
        "id": id,
        "title": title,
        "description": description,
        "vuln_class": vuln_class,
        "severity": severity,
        "confidence": confidence,
        "file_path": file_path,
        "line_start": line_start,
        "line_end": line_end,
        "cwe_ids": cwe_ids or ["CWE-89"],
        "match_status": match_status,
    }


async def _seed(
    db: Database,
    run_id: str = "run-1",
    experiment_id: str = "exp-1",
    findings: list[dict] | None = None,
    model_id: str = "gpt-4o",
    strategy: str = "single_agent",
    dataset_name: str = "dataset-a",
) -> None:
    if findings is None:
        findings = [_make_finding()]
    await db.upsert_findings_for_run(
        run_id=run_id,
        experiment_id=experiment_id,
        findings=findings,
        model_id=model_id,
        strategy=strategy,
        dataset_name=dataset_name,
    )


# ---------------------------------------------------------------------------
# Schema idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_double_init_is_idempotent(db: Database) -> None:
    """Calling init() twice must not raise or corrupt existing data."""
    await db.init()  # second call
    await _seed(db)
    total, _ = await db.query_findings({})
    assert total == 1


# ---------------------------------------------------------------------------
# upsert_findings_for_run — idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_idempotent_no_dupes(db: Database) -> None:
    """Upserting the same run twice yields the same row count, not doubles."""
    await _seed(db)
    await _seed(db)  # second upsert of same run
    total, _ = await db.query_findings({})
    assert total == 1


@pytest.mark.asyncio
async def test_upsert_two_findings_same_run(db: Database) -> None:
    """Two findings in a single upsert both appear, no duplication on re-upsert."""
    findings = [
        _make_finding(id="f1", vuln_class="sqli"),
        _make_finding(id="f2", vuln_class="xss", title="XSS"),
    ]
    await _seed(db, findings=findings)
    total, _ = await db.query_findings({})
    assert total == 2

    # Re-seed (idempotent) — must still be 2
    await _seed(db, findings=findings)
    total, _ = await db.query_findings({})
    assert total == 2


@pytest.mark.asyncio
async def test_upsert_replaces_findings_on_reindex(db: Database) -> None:
    """Re-upserting a run with different findings replaces old rows."""
    await _seed(db, findings=[_make_finding(id="old-f", title="Old Finding")])
    total, _ = await db.query_findings({})
    assert total == 1

    # Re-seed with two different findings
    await _seed(db, findings=[
        _make_finding(id="new-f1", title="New 1"),
        _make_finding(id="new-f2", title="New 2"),
    ])
    total, items = await db.query_findings({})
    assert total == 2
    ids = {item["id"] for item in items}
    assert "old-f" not in ids
    assert "new-f1" in ids
    assert "new-f2" in ids


# ---------------------------------------------------------------------------
# query_findings — filter by each column
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_filter_by_vuln_class(db: Database) -> None:
    await _seed(db, run_id="r1", findings=[_make_finding(id="f1", vuln_class="sqli")])
    await _seed(db, run_id="r2", findings=[_make_finding(id="f2", vuln_class="xss", title="XSS")])

    total, items = await db.query_findings({"vuln_class": ["sqli"]})
    assert total == 1
    assert items[0]["id"] == "f1"


@pytest.mark.asyncio
async def test_filter_by_severity(db: Database) -> None:
    await _seed(db, run_id="r1", findings=[_make_finding(id="f1", severity="critical")])
    await _seed(db, run_id="r2", findings=[_make_finding(id="f2", severity="low", title="Low")])

    total, items = await db.query_findings({"severity": ["critical"]})
    assert total == 1
    assert items[0]["severity"] == "critical"


@pytest.mark.asyncio
async def test_filter_by_match_status(db: Database) -> None:
    await _seed(db, run_id="r1", findings=[_make_finding(id="f1", match_status="tp")])
    await _seed(db, run_id="r2", findings=[_make_finding(id="f2", match_status="fp", title="FP")])

    total, _ = await db.query_findings({"match_status": ["tp"]})
    assert total == 1


@pytest.mark.asyncio
async def test_filter_by_model_id(db: Database) -> None:
    await _seed(db, run_id="r1", model_id="gpt-4o", findings=[_make_finding(id="f1")])
    await _seed(db, run_id="r2", model_id="claude-3", findings=[_make_finding(id="f2", title="F2")])

    total, _ = await db.query_findings({"model_id": ["gpt-4o"]})
    assert total == 1


@pytest.mark.asyncio
async def test_filter_by_strategy(db: Database) -> None:
    await _seed(db, run_id="r1", strategy="single_agent", findings=[_make_finding(id="f1")])
    await _seed(db, run_id="r2", strategy="per_file", findings=[_make_finding(id="f2", title="F2")])

    total, _ = await db.query_findings({"strategy": ["per_file"]})
    assert total == 1


@pytest.mark.asyncio
async def test_filter_by_experiment_id(db: Database) -> None:
    await _seed(db, run_id="r1", experiment_id="exp-A", findings=[_make_finding(id="f1")])
    await _seed(db, run_id="r2", experiment_id="exp-B", findings=[_make_finding(id="f2", title="F2")])

    total, _ = await db.query_findings({"experiment_id": ["exp-A"]})
    assert total == 1


@pytest.mark.asyncio
async def test_filter_by_dataset_name(db: Database) -> None:
    await _seed(db, run_id="r1", dataset_name="ds-1", findings=[_make_finding(id="f1")])
    await _seed(db, run_id="r2", dataset_name="ds-2", findings=[_make_finding(id="f2", title="F2")])

    total, _ = await db.query_findings({"dataset_name": ["ds-1"]})
    assert total == 1


@pytest.mark.asyncio
async def test_multi_value_filter(db: Database) -> None:
    """Multi-value list filter uses IN logic (OR within facet)."""
    await _seed(db, run_id="r1", findings=[_make_finding(id="f1", severity="critical")])
    await _seed(db, run_id="r2", findings=[_make_finding(id="f2", severity="low", title="F2")])
    await _seed(db, run_id="r3", findings=[_make_finding(id="f3", severity="info", title="F3")])

    total, _ = await db.query_findings({"severity": ["critical", "low"]})
    assert total == 2


# ---------------------------------------------------------------------------
# FTS query
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fts_query_matches_title(db: Database) -> None:
    await _seed(db, run_id="r1", findings=[
        _make_finding(id="f1", title="SQL Injection in login"),
    ])
    await _seed(db, run_id="r2", findings=[
        _make_finding(id="f2", title="Cross-site scripting", description="XSS attack"),
    ])

    total, items = await db.query_findings({"q": "SQL Injection"})
    assert total == 1
    assert items[0]["id"] == "f1"


@pytest.mark.asyncio
async def test_fts_query_matches_description(db: Database) -> None:
    await _seed(db, findings=[
        _make_finding(id="f1", description="Heap buffer overflow in parser"),
    ])
    total, _ = await db.query_findings({"q": "overflow"})
    assert total == 1


@pytest.mark.asyncio
async def test_fts_no_match_returns_empty(db: Database) -> None:
    await _seed(db)
    total, items = await db.query_findings({"q": "zzznomatch999"})
    assert total == 0
    assert items == []


@pytest.mark.asyncio
async def test_fts_with_double_quotes_escaped(db: Database) -> None:
    """FTS with embedded double-quotes must not raise."""
    await _seed(db)
    # Should not raise even with tricky characters
    total, _ = await db.query_findings({"q": 'he said "hello"'})
    # Result can be 0 or 1; key invariant is no exception


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pagination_limit(db: Database) -> None:
    for i in range(10):
        await _seed(db, run_id=f"r{i}", findings=[_make_finding(id=f"f{i}", title=f"F{i}")])

    total, items = await db.query_findings({}, limit=3, offset=0)
    assert total == 10
    assert len(items) == 3


@pytest.mark.asyncio
async def test_pagination_offset(db: Database) -> None:
    for i in range(5):
        await _seed(db, run_id=f"r{i}", findings=[_make_finding(id=f"f{i}", title=f"T{i}")])

    _, page1 = await db.query_findings({}, limit=2, offset=0)
    _, page2 = await db.query_findings({}, limit=2, offset=2)
    _, page3 = await db.query_findings({}, limit=2, offset=4)

    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1  # last page

    # No overlap between pages
    ids_p1 = {r["id"] for r in page1}
    ids_p2 = {r["id"] for r in page2}
    assert ids_p1.isdisjoint(ids_p2)


@pytest.mark.asyncio
async def test_pagination_past_end_returns_empty(db: Database) -> None:
    await _seed(db)
    total, items = await db.query_findings({}, limit=10, offset=999)
    assert total == 1
    assert items == []


# ---------------------------------------------------------------------------
# Facet correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_facet_excludes_own_filter(db: Database) -> None:
    """When filtering by vuln_class=sqli, the vuln_class facet shows all classes."""
    await _seed(db, run_id="r1", findings=[_make_finding(id="f1", vuln_class="sqli")])
    await _seed(db, run_id="r2", findings=[_make_finding(id="f2", vuln_class="xss", title="XSS")])

    facets = await db.facet_findings({"vuln_class": ["sqli"]})
    # The vuln_class facet should reflect counts without the vuln_class filter
    assert "sqli" in facets["vuln_class"]
    assert "xss" in facets["vuln_class"]


@pytest.mark.asyncio
async def test_facet_respects_other_filters(db: Database) -> None:
    """Non-excluded filters still narrow facet counts."""
    await _seed(
        db, run_id="r1", model_id="gpt-4o",
        findings=[_make_finding(id="f1", severity="high")]
    )
    await _seed(
        db, run_id="r2", model_id="claude-3",
        findings=[_make_finding(id="f2", severity="low", title="F2")]
    )

    # Filter by model_id=gpt-4o; severity facet should only see "high"
    facets = await db.facet_findings({"model_id": ["gpt-4o"]})
    assert "high" in facets["severity"]
    assert "low" not in facets["severity"]


@pytest.mark.asyncio
async def test_facet_all_dimensions_returned(db: Database) -> None:
    """facet_findings returns all expected facet keys."""
    await _seed(db)
    facets = await db.facet_findings({})
    expected_keys = {"vuln_class", "severity", "match_status", "model_id", "strategy", "dataset_name"}
    assert expected_keys <= set(facets.keys())


# ---------------------------------------------------------------------------
# update_finding_match_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_finding_match_status(db: Database) -> None:
    await _seed(db, findings=[_make_finding(id="f1", match_status="fp")])
    _, items = await db.query_findings({"match_status": ["fp"]})
    assert len(items) == 1

    await db.update_finding_match_status("f1", "unlabeled_real")
    _, items_after = await db.query_findings({"match_status": ["unlabeled_real"]})
    assert len(items_after) == 1
    assert items_after[0]["id"] == "f1"

    _, old_fp = await db.query_findings({"match_status": ["fp"]})
    assert len(old_fp) == 0


# ---------------------------------------------------------------------------
# count_all_findings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_all_findings_empty(db: Database) -> None:
    n = await db.count_all_findings()
    assert n == 0


@pytest.mark.asyncio
async def test_count_all_findings_after_insert(db: Database) -> None:
    await _seed(db, run_id="r1", findings=[_make_finding(id="f1"), _make_finding(id="f2", title="F2")])
    n = await db.count_all_findings()
    assert n == 2


# ---------------------------------------------------------------------------
# cwe_ids round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cwe_ids_round_trip(db: Database) -> None:
    """cwe_ids should be stored as JSON and returned as a list."""
    await _seed(db, findings=[_make_finding(id="f1", cwe_ids=["CWE-89", "CWE-564"])])
    _, items = await db.query_findings({})
    assert items[0]["cwe_ids"] == ["CWE-89", "CWE-564"]


# ---------------------------------------------------------------------------
# _escape_fts_query safety
# ---------------------------------------------------------------------------


def test_escape_fts_query_plain() -> None:
    assert _escape_fts_query("hello world") == '"hello world"'


def test_escape_fts_query_with_quotes() -> None:
    result = _escape_fts_query('he said "hello"')
    assert '""' in result  # inner quotes doubled
    assert result.startswith('"')
    assert result.endswith('"')


def test_escape_fts_query_empty() -> None:
    result = _escape_fts_query("")
    assert result == '""'
