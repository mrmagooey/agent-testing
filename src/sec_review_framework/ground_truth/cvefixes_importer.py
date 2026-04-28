"""CVEfixes importer: ingest real-world CVE fix commits from the CVEfixes dataset.

CVEfixes (Bhandari et al., 2021) is a research dataset of ~6000+ vulnerabilities
paired with their fix commits, distributed via Zenodo as a SQLite database.

Reference: https://github.com/secureIT-project/CVEfixes
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from sec_review_framework.db import Database
from sec_review_framework.ground_truth._source_check_migration import (
    ensure_source_check_includes,
)

# ---------------------------------------------------------------------------
# Expected CVEfixes schema tables / columns (raise early if missing)
# ---------------------------------------------------------------------------

_EXPECTED_TABLES = {"cve", "fixes", "commits", "file_change", "repository"}

_EXPECTED_COLUMNS: dict[str, set[str]] = {
    "cve": {"cve_id", "severity", "description"},
    "fixes": {"cve_id", "hash"},
    "commits": {"hash", "repo_url", "parent_hash"},
    "file_change": {"hash", "filename", "diff", "language"},
    "repository": {"repo_url", "language"},
}

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CVEfixesImportResult:
    """Summary returned by import_cvefixes()."""

    imported_cves: int = 0
    imported_datasets: int = 0
    imported_labels: int = 0
    skipped_cves: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Vuln-class mapping from config/vuln_classes.yaml
# ---------------------------------------------------------------------------

_CWE_TO_VULN_CLASS: dict[str, str] = {}
_VULN_CLASS_LOADED = False


def _load_vuln_class_map() -> dict[str, str]:
    """Build a CWE → vuln_class str map from config/vuln_classes.yaml.

    Searches upward from this file to locate the project root config dir.
    Falls back to an empty map if the file cannot be found.
    """
    global _CWE_TO_VULN_CLASS, _VULN_CLASS_LOADED
    if _VULN_CLASS_LOADED:
        return _CWE_TO_VULN_CLASS

    # Walk up from this file looking for config/vuln_classes.yaml
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        candidate = parent / "config" / "vuln_classes.yaml"
        if candidate.exists():
            break
    else:
        _VULN_CLASS_LOADED = True
        return _CWE_TO_VULN_CLASS

    try:
        with candidate.open() as fh:
            data = yaml.safe_load(fh)
        for vc_name, vc_info in (data.get("vuln_classes") or {}).items():
            for cwe_id in vc_info.get("cwe_ids") or []:
                _CWE_TO_VULN_CLASS[cwe_id] = vc_name
    except Exception:
        pass

    _VULN_CLASS_LOADED = True
    return _CWE_TO_VULN_CLASS


def _cwe_to_vuln_class(cwe_id: str) -> str:
    """Map a CWE ID to a vuln_class string; fall back to the literal CWE id."""
    mapping = _load_vuln_class_map()
    return mapping.get(cwe_id, cwe_id)


# ---------------------------------------------------------------------------
# Severity normalisation
# ---------------------------------------------------------------------------

_SEVERITY_NORMALISE: dict[str, str] = {
    "critical": "CRITICAL",
    "high": "HIGH",
    "medium": "MEDIUM",
    "moderate": "MEDIUM",
    "low": "LOW",
    "info": "INFO",
    "none": "LOW",
}


def _normalise_severity(raw: str | None) -> str:
    if not raw:
        return "MEDIUM"
    return _SEVERITY_NORMALISE.get(raw.strip().lower(), "MEDIUM")


# ---------------------------------------------------------------------------
# Diff hunk parsing
# ---------------------------------------------------------------------------

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)


def _parse_hunk_ranges(diff_text: str) -> list[tuple[int, int]]:
    """Return list of (old_start, old_end) line ranges from unified-diff hunk headers.

    Each @@ -<old_start>,<old_count> ... @@ header describes a region in the
    *parent* (buggy) file.  We return the closed interval
    [old_start, old_start + max(old_count-1, 0)] so a single-line hunk maps
    to (L, L) rather than (L, L-1).

    Raises ValueError if the diff text contains no hunk headers at all
    (caller treats this as a malformed diff).
    """
    ranges: list[tuple[int, int]] = []
    for line in diff_text.splitlines():
        m = _HUNK_HEADER_RE.match(line)
        if m:
            old_start = int(m.group("old_start"))
            old_count_str = m.group("old_count")
            old_count = int(old_count_str) if old_count_str is not None else 1
            old_end = old_start + max(old_count - 1, 0)
            ranges.append((old_start, old_end))
    return ranges


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def _validate_schema(con: sqlite3.Connection) -> None:
    """Raise RuntimeError with a clear message if required tables/columns are missing."""
    cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    present_tables = {row[0] for row in cur.fetchall()}
    missing_tables = _EXPECTED_TABLES - present_tables
    if missing_tables:
        raise RuntimeError(
            f"CVEfixes SQLite is missing expected tables: {sorted(missing_tables)}. "
            f"Expected tables: {sorted(_EXPECTED_TABLES)}. "
            "Ensure you are using the official CVEfixes database from Zenodo "
            "(https://github.com/secureIT-project/CVEfixes)."
        )

    for table, expected_cols in _EXPECTED_COLUMNS.items():
        cur = con.execute(f"PRAGMA table_info({table})")
        actual_cols = {row[1] for row in cur.fetchall()}
        missing_cols = expected_cols - actual_cols
        if missing_cols:
            raise RuntimeError(
                f"CVEfixes table '{table}' is missing expected columns: "
                f"{sorted(missing_cols)}. "
                f"Present columns: {sorted(actual_cols)}. "
                "Ensure you are using the official CVEfixes database from Zenodo."
            )


# ---------------------------------------------------------------------------
# Project slug helper
# ---------------------------------------------------------------------------


def _project_slug(repo_url: str) -> str:
    """Derive a short slug from a repository URL for dataset naming."""
    url = repo_url.rstrip("/")
    # Strip scheme
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^git@[^:]+:", "", url)
    # Keep last two path components (owner/repo) joined with a dash
    parts = [p for p in url.split("/") if p]
    if len(parts) >= 2:
        return f"{parts[-2]}-{parts[-1]}"
    elif parts:
        return parts[-1]
    return "unknown"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def import_cvefixes(
    db: Database,
    *,
    cvefixes_db_path: Path,
    languages: list[str] | None = None,
    max_cves: int | None = None,
    zenodo_doi: str = "10.5281/zenodo.7029359",
) -> CVEfixesImportResult:
    """Idempotent import of CVEfixes records into the sec-review framework DB.

    Args:
        db: Framework Database instance (already initialised).
        cvefixes_db_path: Local path to the unpacked CVEfixes SQLite file.
        languages: If provided, only import CVEs whose fix commit's repository
            language is in this list (case-insensitive comparison).
        max_cves: Optional cap on the number of CVEs processed (useful in tests).
        zenodo_doi: DOI pinned in metadata_json for traceability.

    Returns:
        CVEfixesImportResult with counts and any per-CVE errors.
    """
    # --- Run the source CHECK migration first so 'cvefixes' is accepted ---
    await ensure_source_check_includes(db, "cvefixes")

    result = CVEfixesImportResult()

    # Normalise language filter to lowercase set for O(1) lookup
    lang_filter: set[str] | None = (
        {lang.lower() for lang in languages} if languages else None
    )

    # Open CVEfixes read-only
    uri = cvefixes_db_path.resolve().as_uri() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot open CVEfixes SQLite at {cvefixes_db_path}: {exc}"
        ) from exc

    try:
        con.row_factory = sqlite3.Row
        _validate_schema(con)

        # Fetch all CVE rows (plus CWE ids if available in cwe_classification)
        cve_rows = _fetch_cves(con, lang_filter=lang_filter, max_cves=max_cves)

        for cve_row in cve_rows:
            cve_id: str = cve_row["cve_id"]
            try:
                n_ds, n_labels, skip_reason = await _import_single_cve(
                    db,
                    con=con,
                    cve_row=cve_row,
                    zenodo_doi=zenodo_doi,
                )
                if skip_reason:
                    result.skipped_cves += 1
                    result.skipped_reasons[skip_reason] = (
                        result.skipped_reasons.get(skip_reason, 0) + 1
                    )
                else:
                    result.imported_cves += 1
                    result.imported_datasets += n_ds
                    result.imported_labels += n_labels
            except Exception as exc:  # noqa: BLE001
                result.errors.append((cve_id, str(exc)))
    finally:
        con.close()

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _fetch_cves(
    con: sqlite3.Connection,
    *,
    lang_filter: set[str] | None,
    max_cves: int | None,
) -> list[sqlite3.Row]:
    """Fetch CVE rows from CVEfixes, optionally filtered by language.

    We join cve → fixes → commits → repository so we can filter by language
    before pulling everything.  If the cwe_classification table exists we also
    pull CWE IDs; otherwise we fall back to an empty list.
    """
    # Check if cwe_classification table exists
    cur = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cwe_classification'"
    )
    has_cwe_table = cur.fetchone() is not None

    # Check for 'cwe_ids' column directly on cve table
    cur = con.execute("PRAGMA table_info(cve)")
    cve_cols = {row[1] for row in cur.fetchall()}
    has_cwe_ids_col = "cwe_ids" in cve_cols

    base_sql = """
        SELECT DISTINCT c.cve_id, c.severity, c.description
        FROM cve c
        INNER JOIN fixes f ON f.cve_id = c.cve_id
        INNER JOIN commits co ON co.hash = f.hash
        INNER JOIN repository r ON r.repo_url = co.repo_url
        WHERE r.language IS NOT NULL
    """

    if lang_filter:
        placeholders = ",".join("?" * len(lang_filter))
        lang_clause = f" AND LOWER(r.language) IN ({placeholders})"
        base_sql += lang_clause
        params: list = list(lang_filter)
    else:
        params = []

    if max_cves is not None:
        base_sql += f" LIMIT {int(max_cves)}"

    cur = con.execute(base_sql, params)
    rows = cur.fetchall()
    return rows


async def _import_single_cve(
    db: Database,
    *,
    con: sqlite3.Connection,
    cve_row: sqlite3.Row,
    zenodo_doi: str,
) -> tuple[int, int, str | None]:
    """Import all fix commits for one CVE.

    Returns (n_datasets_created, n_labels_created, skip_reason_or_None).
    """
    cve_id: str = cve_row["cve_id"]
    raw_severity: str | None = cve_row["severity"]
    description: str = (cve_row["description"] or "")[:500]
    severity = _normalise_severity(raw_severity)

    # Resolve CWE IDs for this CVE
    cwe_ids = _fetch_cwe_ids(con, cve_id)
    first_cwe = cwe_ids[0] if cwe_ids else "CWE-UNKNOWN"
    vuln_class = _cwe_to_vuln_class(first_cwe)

    # Fetch all fix commits for this CVE
    fix_rows = con.execute(
        "SELECT hash FROM fixes WHERE cve_id = ?", (cve_id,)
    ).fetchall()

    if not fix_rows:
        return 0, 0, "no_fix_commits"

    n_datasets = 0
    n_labels = 0
    all_skipped = True

    for fix_row in fix_rows:
        commit_hash: str = fix_row["hash"]

        # Fetch commit metadata
        commit = con.execute(
            "SELECT hash, repo_url, parent_hash FROM commits WHERE hash = ?",
            (commit_hash,),
        ).fetchone()

        if commit is None:
            continue

        parent_hash: str | None = commit["parent_hash"]
        if not parent_hash:
            # Can't pin a buggy state without the parent
            continue

        repo_url: str | None = commit["repo_url"]
        if not repo_url:
            continue

        # Fetch repository language
        repo_row = con.execute(
            "SELECT language FROM repository WHERE repo_url = ?", (repo_url,)
        ).fetchone()
        language: str | None = None
        if repo_row:
            language = repo_row["language"]
        if not language:
            continue

        all_skipped = False

        # Build deterministic dataset name
        slug = _project_slug(repo_url)
        dataset_name = f"cvefixes-{cve_id.lower()}-{slug}-{commit_hash[:8]}"

        # Build metadata_json
        metadata = {
            "source": "cvefixes",
            "language": language,
            "zenodo_doi": zenodo_doi,
            "fix_commit": commit_hash,
            "cwe_ids": cwe_ids,
        }

        # Upsert the dataset row (INSERT OR IGNORE on primary key = name)
        now_iso = datetime.now(UTC).isoformat()
        existing = await db.get_dataset(dataset_name)
        if existing is None:
            await db.create_dataset(
                {
                    "name": dataset_name,
                    "kind": "git",
                    "origin_url": repo_url,
                    "origin_commit": parent_hash,
                    "cve_id": cve_id,
                    "metadata_json": json.dumps(metadata),
                    "created_at": now_iso,
                }
            )
            n_datasets += 1

        # Fetch file changes for this commit and build labels
        file_changes = con.execute(
            "SELECT filename, diff, language FROM file_change WHERE hash = ?",
            (commit_hash,),
        ).fetchall()

        label_rows: list[dict] = []
        for fc in file_changes:
            filename: str = fc["filename"] or ""
            diff_text: str = fc["diff"] or ""

            try:
                hunk_ranges = _parse_hunk_ranges(diff_text)
            except Exception as exc:
                raise ValueError(
                    f"Malformed diff for {cve_id}/{commit_hash[:8]}/{filename}: {exc}"
                ) from exc

            if not hunk_ranges:
                # No hunks means nothing changed on the old side; emit one
                # label pointing at line 1 so the file is still recorded.
                hunk_ranges = [(1, 1)]

            for old_start, old_end in hunk_ranges:
                label_id = f"cvefixes::{cve_id}::{commit_hash[:8]}::{filename}::{old_start}"
                label_rows.append(
                    {
                        "id": label_id,
                        "dataset_name": dataset_name,
                        "dataset_version": parent_hash,
                        "file_path": filename,
                        "line_start": old_start,
                        "line_end": old_end,
                        "cwe_id": first_cwe,
                        "vuln_class": vuln_class,
                        "severity": severity,
                        "description": description,
                        "source": "cvefixes",
                        "source_ref": cve_id,
                        "confidence": "HIGH",
                        "created_at": now_iso,
                    }
                )

        if label_rows:
            await db.append_dataset_labels(label_rows)
            if existing is None:
                # Only count labels for genuinely new datasets; INSERT OR IGNORE
                # silently drops rows on re-runs so we must not double-count.
                n_labels += len(label_rows)

    if all_skipped:
        return 0, 0, "all_commits_skipped"

    return n_datasets, n_labels, None


def _fetch_cwe_ids(con: sqlite3.Connection, cve_id: str) -> list[str]:
    """Return CWE IDs for a CVE from whichever column/table is available."""
    # Strategy 1: cwe_classification join table (present in many CVEfixes versions)
    cur = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='cwe_classification'"
    )
    if cur.fetchone():
        rows = con.execute(
            "SELECT cwe_id FROM cwe_classification WHERE cve_id = ?", (cve_id,)
        ).fetchall()
        ids = [r["cwe_id"] for r in rows if r["cwe_id"]]
        if ids:
            return ids

    # Strategy 2: cwe_ids column directly on cve table
    cur = con.execute("PRAGMA table_info(cve)")
    cve_cols = {row[1] for row in cur.fetchall()}
    if "cwe_ids" in cve_cols:
        row = con.execute(
            "SELECT cwe_ids FROM cve WHERE cve_id = ?", (cve_id,)
        ).fetchone()
        if row and row["cwe_ids"]:
            raw = row["cwe_ids"]
            # May be comma-separated or JSON array
            if raw.startswith("["):
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    pass
            return [c.strip() for c in raw.split(",") if c.strip()]

    return []


# ---------------------------------------------------------------------------
# Source CHECK migration
# ---------------------------------------------------------------------------
# Delegated to the shared helper in _source_check_migration.py so that all
# importers (CVEfixes, CrossVul, future Bandit / SARD, …) share one
# implementation of the SQLite table-rebuild pattern.
#
# import_cvefixes() calls:
#   await ensure_source_check_includes(db, "cvefixes")
# (imported at the top of this module)
