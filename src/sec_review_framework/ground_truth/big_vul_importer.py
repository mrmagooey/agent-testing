"""Big-Vul importer: ingest real-world C/C++ CVEs from the Big-Vul dataset.

Big-Vul (Fan, Li, Wang, Nguyen, MSR'20) is a dataset of ~3,754 real-world
C/C++ CVEs paired with their fix commits.  Each row captures both the
vulnerable function (``func_before``) and the fixed function (``func_after``),
giving paired polarity ground truth.

Reference: https://github.com/ZeoVan/MSR_20_Code_vulnerability_CSV_Dataset
           DOI: 10.1145/3379597.3387501

Distribution format assumed by this importer
--------------------------------------------
Big-Vul is distributed as a CSV file (``MSR_data_cleaned.csv``) whose key
columns include:

    CVE ID         — CVE identifier string
    commit_id      — Git SHA of the fix commit
    project        — Short project name (used in dataset naming)
    repo_url       — Full GitHub URL of the project repository
    lang           — Language identifier (e.g. "C", "C++")
    vul            — 1 = vulnerable row, 0 = fixed/non-vulnerable row
    func_before    — Function text before the fix (vulnerable)
    func_after     — Function text after the fix (fixed)
    CWE ID         — CWE identifier string (may be empty)
    CVSS Score     — CVSS numeric score (may be empty)

Column names vary slightly across Big-Vul mirrors; this importer accepts the
common aliases listed in ``_COLUMN_ALIASES``.

Diff acquisition strategy
--------------------------
Big-Vul does NOT bundle unified diffs.  This importer resolves each fix
commit diff via a local cache under ``fix_clone_root``:

    fix_clone_root/<project_slug>/<commit_hash[:16]>.diff

The cache strategy is identical to the CrossVul importer:
- Cache hit  → use immediately, no network needed.
- Cache miss → attempt to fetch from GitHub's ``.patch`` endpoint.
- If the fetch fails (no network, 404, etc.) → skip with reason
  ``diff_cache_miss`` rather than raising.

Polarity
--------
Big-Vul rows carry a ``vul`` column:

- ``vul=1`` rows represent the **vulnerable** (pre-fix) state → positive
  labels stored in ``dataset_labels``.
- ``vul=0`` rows represent the **fixed** (post-fix) state → negative labels
  stored in ``dataset_negative_labels`` (file-scoped, one per file in the
  diff, keyed ``bigvul::<cve_id>::<commit_id[:8]>::<filename>::neg``).

Idempotency
-----------
Deterministic IDs are used for both label tables; ``INSERT OR IGNORE`` on
the primary key deduplicates on re-runs.

NOTE: This module does NOT vendor real Big-Vul CSV content.  The caller must
download ``MSR_data_cleaned.csv`` from the canonical distribution URL and pass
its path as ``csv_path``.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sec_review_framework.db import Database
from sec_review_framework.ground_truth._source_check_migration import (
    ensure_negative_source_check_includes,
    ensure_source_check_includes,
)

# Reuse shared helpers from the CVEfixes importer (DRY)
from sec_review_framework.ground_truth.cvefixes_importer import (
    _cwe_to_vuln_class,
    _normalise_severity,
    _parse_hunk_ranges,
    _project_slug,
)

# Reuse diff cache helpers from the CrossVul importer
from sec_review_framework.ground_truth.crossvul_importer import (
    _diff_cache_path,
    _fetch_github_patch,
    _load_cached_diff,
    _split_diff_by_file,
)

__all__ = ["BigVulImportResult", "import_big_vul"]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class BigVulImportResult:
    """Summary returned by :func:`import_big_vul`."""

    imported_cves: int = 0
    imported_datasets: int = 0
    imported_labels: int = 0
    imported_negative_labels: int = 0
    skipped_cves: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Column alias resolution
# ---------------------------------------------------------------------------

#: Known alternate column names across Big-Vul mirrors/versions.
_COLUMN_ALIASES: dict[str, list[str]] = {
    "cve_id":      ["CVE ID", "cve_id", "CVE_ID", "cve id"],
    "commit_id":   ["commit_id", "commit_hash", "hash", "fix_commit"],
    "project":     ["project", "Project", "project_name"],
    "repo_url":    ["repo_url", "project_url", "Repo_url", "RepoUrl", "url"],
    "lang":        ["lang", "language", "Language", "lang_cluster"],
    "vul":         ["vul", "vulnerable", "label", "Vul"],
    "cwe_id":      ["CWE ID", "cwe_id", "CWE_ID", "cwe id", "cwe"],
    "cvss_score":  ["CVSS Score", "cvss_score", "CVSS_score", "cvss"],
}


def _resolve_col(row: dict[str, Any], field_name: str) -> Any:
    """Return the value for *field_name* from *row*, trying known aliases.

    Returns ``None`` if no alias matches.
    """
    for alias in _COLUMN_ALIASES.get(field_name, [field_name]):
        if alias in row:
            return row[alias]
    return None


def _detect_columns(header: list[str]) -> dict[str, str]:
    """Build a mapping from canonical field name → actual CSV column name.

    Raises ``ValueError`` if mandatory columns cannot be found.
    """
    canonical_to_actual: dict[str, str] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in header:
                canonical_to_actual[canonical] = alias
                break
    return canonical_to_actual


# ---------------------------------------------------------------------------
# Language normalisation
# ---------------------------------------------------------------------------


def _normalise_lang(raw: str | None) -> str:
    """Return a canonical lowercase language name for Big-Vul rows.

    Big-Vul is C/C++ only.  The ``lang`` column typically holds ``"C"`` or
    ``"C++"``.  We map these to ``"c"`` and ``"cpp"`` respectively.
    Other values are lowercased as-is.
    """
    if not raw:
        return "c"
    mapping: dict[str, str] = {
        "C": "c",
        "c": "c",
        "C++": "cpp",
        "c++": "cpp",
        "CPP": "cpp",
        "cpp": "cpp",
    }
    return mapping.get(raw.strip(), raw.strip().lower())


# ---------------------------------------------------------------------------
# Severity from CVSS score
# ---------------------------------------------------------------------------


def _cvss_to_severity(raw_cvss: str | None) -> str:
    """Map a numeric CVSS score string to a canonical severity label.

    CVSS v3 thresholds:
        0.0–3.9  → LOW
        4.0–6.9  → MEDIUM
        7.0–8.9  → HIGH
        9.0–10.0 → CRITICAL

    Falls back to MEDIUM if the score cannot be parsed.
    """
    if not raw_cvss:
        return "MEDIUM"
    try:
        score = float(raw_cvss)
    except (ValueError, TypeError):
        return "MEDIUM"
    if score >= 9.0:
        return "CRITICAL"
    if score >= 7.0:
        return "HIGH"
    if score >= 4.0:
        return "MEDIUM"
    return "LOW"


# ---------------------------------------------------------------------------
# Diff cache helpers (thin wrappers over CrossVul shared functions)
# ---------------------------------------------------------------------------


def _resolve_diff_big_vul(
    fix_clone_root: Path,
    repo_url: str,
    commit_id: str,
    *,
    allow_network: bool = True,
) -> str:
    """Return the unified diff for *commit_id*, using or populating the cache.

    If the cache file is present, returns it immediately.  If not and
    *allow_network* is True, fetches from GitHub and writes to the cache.
    If not and *allow_network* is False, raises RuntimeError.

    Raises:
        RuntimeError: If the cache is cold and *allow_network* is False, or
            if the network fetch fails.
    """
    cache_path = _diff_cache_path(fix_clone_root, repo_url, commit_id)
    cached = _load_cached_diff(cache_path)
    if cached is not None:
        return cached

    if not allow_network:
        raise RuntimeError(
            f"Diff cache miss for {_project_slug(repo_url)}/{commit_id[:8]} "
            f"and network access is disabled. "
            f"Expected cache file: {cache_path}. "
            "Pre-populate the cache or set allow_network=True."
        )

    diff_text = _fetch_github_patch(repo_url, commit_id)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(diff_text, encoding="utf-8")
    return diff_text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def import_big_vul(
    db: Database,
    *,
    csv_path: Path,
    fix_clone_root: Path,
    languages: list[str] | None = None,
    max_cves: int | None = None,
    dataset_doi: str = "10.1145/3379597.3387501",
) -> BigVulImportResult:
    """Idempotent import of Big-Vul records into the sec-review framework DB.

    Each unique (cve_id, project, commit_id) triple becomes one ``kind='git'``
    dataset pinned to the *parent* (buggy) commit reference ``<commit_id>^``.

    Polarity is driven by the ``vul`` column:
    - ``vul=1`` → positive labels in ``dataset_labels`` (vulnerable rows).
    - ``vul=0`` → negative labels in ``dataset_negative_labels`` (fixed rows).

    Idempotency is achieved via ``INSERT OR IGNORE`` on deterministic label
    primary keys.

    Args:
        db: Initialised framework :class:`~sec_review_framework.db.Database`.
        csv_path: Local path to the Big-Vul CSV file (``MSR_data_cleaned.csv``
            or equivalent).  The file is iterated row-by-row; it is never
            loaded entirely into memory.
        fix_clone_root: Root directory for the diff cache.  Each diff is
            stored as ``fix_clone_root/<slug>/<commit_id[:16]>.diff``.
        languages: If provided, only import rows whose ``lang`` field
            (after normalisation) matches one of these values
            (case-insensitive).
        max_cves: Optional cap on the number of unique (cve_id, commit_id)
            pairs processed (useful for testing).
        dataset_doi: DOI pinned in ``metadata_json`` for traceability.

    Returns:
        :class:`BigVulImportResult` with counts and any per-CVE errors.
    """
    if not csv_path.exists():
        raise FileNotFoundError(
            f"Big-Vul CSV not found at {csv_path}. "
            "Download MSR_data_cleaned.csv from "
            "https://github.com/ZeoVan/MSR_20_Code_vulnerability_CSV_Dataset "
            "and supply the correct path."
        )

    # Extend source CHECK constraints for both positive and negative tables
    await ensure_source_check_includes(db, "bigvul")
    await ensure_negative_source_check_includes(db, "bigvul")

    result = BigVulImportResult()

    # Normalise language filter to lowercase set for O(1) lookup
    lang_filter: set[str] | None = (
        {lang.lower() for lang in languages} if languages else None
    )

    now_iso = datetime.now(UTC).isoformat()

    # Track processed (cve_id, commit_id) pairs for dataset creation and max_cves.
    # These are keyed without polarity so a single dataset is created per CVE+commit.
    seen_pairs: set[tuple[str, str]] = set()
    n_cves_processed = 0

    # Track whether a dataset was newly created in this run (True) or was
    # already present (False).  Shared across all polarity rows for the same
    # (cve_id, commit_id) so label counting is consistent.
    pair_is_new_dataset: dict[tuple[str, str], bool] = {}

    # Track (cve_id, commit_id, vul_flag) triples to deduplicate label emission
    # within a single run.  The same function can appear multiple times in the CSV
    # (e.g. one row per function in the same CVE+commit+polarity).
    seen_label_keys: set[tuple[str, str, int | None]] = set()

    # Open CSV with utf-8-sig to strip BOM if present (common in CSV exports)
    with csv_path.open(encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)

        for row in reader:
            # --- Extract required fields via alias resolution ---
            cve_id = _resolve_col(row, "cve_id")
            commit_id = _resolve_col(row, "commit_id")
            project = _resolve_col(row, "project")
            repo_url = _resolve_col(row, "repo_url")
            raw_lang = _resolve_col(row, "lang")
            raw_vul = _resolve_col(row, "vul")
            raw_cwe = _resolve_col(row, "cwe_id")
            raw_cvss = _resolve_col(row, "cvss_score")

            # Skip rows missing mandatory fields
            if not cve_id or not commit_id:
                continue
            cve_id = str(cve_id).strip()
            commit_id = str(commit_id).strip()
            if not cve_id or not commit_id:
                continue

            # Skip if both project and repo_url are absent
            project_str = str(project).strip() if project else ""
            repo_url_str = str(repo_url).strip() if repo_url else ""
            if not project_str and not repo_url_str:
                continue

            # Normalise language and apply filter
            language = _normalise_lang(raw_lang)
            if lang_filter is not None and language not in lang_filter:
                continue

            # Determine polarity (vul=1 → positive, vul=0 → negative)
            try:
                vul_flag = int(str(raw_vul).strip()) if raw_vul is not None else None
            except (ValueError, TypeError):
                vul_flag = None

            # Track (cve_id, commit_id) for dataset creation and max_cves cap.
            pair_key = (cve_id, commit_id)
            is_first_dataset_occurrence = pair_key not in seen_pairs
            if is_first_dataset_occurrence:
                seen_pairs.add(pair_key)
                n_cves_processed += 1
                if max_cves is not None and n_cves_processed > max_cves:
                    break

            # Track (cve_id, commit_id, vul) for per-polarity label dedup.
            # Multiple rows for the same CVE+commit+polarity (different functions)
            # should all emit the same diff-based label set, so we only do so once.
            label_key = (cve_id, commit_id, vul_flag)
            is_first_polarity_occurrence = label_key not in seen_label_keys
            if is_first_polarity_occurrence:
                seen_label_keys.add(label_key)

            try:
                n_ds, n_labels, n_neg_labels, skip_reason = await _import_single_row(
                    db,
                    cve_id=cve_id,
                    commit_id=commit_id,
                    project=project_str,
                    repo_url=repo_url_str,
                    language=language,
                    vul_flag=vul_flag,
                    raw_cwe=str(raw_cwe).strip() if raw_cwe else None,
                    raw_cvss=str(raw_cvss).strip() if raw_cvss else None,
                    fix_clone_root=fix_clone_root,
                    dataset_doi=dataset_doi,
                    now_iso=now_iso,
                    is_first_dataset_occurrence=is_first_dataset_occurrence,
                    is_first_polarity_occurrence=is_first_polarity_occurrence,
                    pair_is_new_dataset=pair_is_new_dataset,
                )
                if skip_reason:
                    result.skipped_cves += 1
                    result.skipped_reasons[skip_reason] = (
                        result.skipped_reasons.get(skip_reason, 0) + 1
                    )
                else:
                    if is_first_dataset_occurrence:
                        result.imported_cves += 1
                    result.imported_datasets += n_ds
                    result.imported_labels += n_labels
                    result.imported_negative_labels += n_neg_labels
            except Exception as exc:  # noqa: BLE001
                result.errors.append((cve_id, str(exc)))

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _import_single_row(
    db: Database,
    *,
    cve_id: str,
    commit_id: str,
    project: str,
    repo_url: str,
    language: str,
    vul_flag: int | None,
    raw_cwe: str | None,
    raw_cvss: str | None,
    fix_clone_root: Path,
    dataset_doi: str,
    now_iso: str,
    is_first_dataset_occurrence: bool,
    is_first_polarity_occurrence: bool,
    pair_is_new_dataset: dict[tuple[str, str], bool],
) -> tuple[int, int, int, str | None]:
    """Import one Big-Vul CSV row.

    Returns:
        (n_datasets_created, n_labels_created, n_negative_labels_created,
         skip_reason_or_None)

    Skip reasons (returned rather than raised):
    - ``"diff_cache_miss"``: diff not in cache and network unavailable/failed.
    - ``"empty_diff"``: diff resolved to an empty string.

    Raised exceptions are propagated to the caller for error tracking.

    Args:
        is_first_dataset_occurrence: True when this is the first row seen for
            this (cve_id, commit_id) pair, regardless of polarity.  Used to
            control dataset row creation and diff resolution.
        is_first_polarity_occurrence: True when this is the first row seen for
            this (cve_id, commit_id, vul_flag) triple.  Used to control label
            emission — both polarities must emit labels independently even when
            they share the same dataset.
        pair_is_new_dataset: Mutable dict shared across all rows for the same
            import run, keyed by (cve_id, commit_id).  Records whether the
            dataset was freshly created (True) or already existed (False).
            Updated by this function on the first dataset occurrence.
    """
    # Build a best-effort repo URL from what we have
    effective_repo_url = repo_url
    if not effective_repo_url and project:
        # Cannot resolve without a URL; skip with a useful reason
        return 0, 0, 0, "no_repo_url"

    # Severity
    severity = _cvss_to_severity(raw_cvss)

    # CWE
    first_cwe = raw_cwe if raw_cwe else "CWE-UNKNOWN"
    vuln_class = _cwe_to_vuln_class(first_cwe)

    # Parent reference (symbolic — we don't have the parent hash from the CSV)
    parent_ref = f"{commit_id}^"

    # Build deterministic dataset name
    slug = _project_slug(effective_repo_url) if effective_repo_url else re.sub(
        r"[^a-z0-9]+", "-", project.lower()
    )
    dataset_name = f"bigvul-{cve_id.lower()}-{slug}-{commit_id[:8]}"

    # Resolve the diff (cache-first, network-fallback) — only on first
    # dataset occurrence to avoid redundant network calls for multi-row CVEs.
    diff_text: str | None = None
    if is_first_dataset_occurrence:
        try:
            diff_text = _resolve_diff_big_vul(
                fix_clone_root,
                effective_repo_url,
                commit_id,
                allow_network=True,
            )
        except RuntimeError:
            return 0, 0, 0, "diff_cache_miss"

        if not diff_text or not diff_text.strip():
            return 0, 0, 0, "empty_diff"

    # For subsequent occurrences (same CVE+commit, different polarity or
    # function), retrieve diff from the already-populated cache.
    if diff_text is None:
        cache_path = _diff_cache_path(fix_clone_root, effective_repo_url, commit_id)
        diff_text = _load_cached_diff(cache_path) or ""

    if not diff_text.strip():
        # Diff unavailable even from cache — nothing to label
        return 0, 0, 0, None

    # Upsert the dataset row (idempotent via existence check).
    # Only attempt dataset creation on the first dataset occurrence.
    pair_key = (cve_id, commit_id)
    n_datasets = 0

    if is_first_dataset_occurrence:
        dataset_was_preexisting = (await db.get_dataset(dataset_name)) is not None
        if not dataset_was_preexisting:
            metadata = {
                "source": "bigvul",
                "language": language,
                "paper_doi": dataset_doi,
                "fix_commit": commit_id,
            }
            await db.create_dataset(
                {
                    "name": dataset_name,
                    "kind": "git",
                    "origin_url": effective_repo_url,
                    "origin_commit": parent_ref,
                    "cve_id": cve_id,
                    "metadata_json": json.dumps(metadata),
                    "created_at": now_iso,
                }
            )
            n_datasets = 1
        # Record whether the dataset is new for all subsequent rows sharing this pair
        pair_is_new_dataset[pair_key] = not dataset_was_preexisting

    # Only emit labels on the first occurrence for this polarity within this run.
    # INSERT OR IGNORE handles dedup on re-runs.
    if not is_first_polarity_occurrence:
        return n_datasets, 0, 0, None

    # Split the diff into per-file sections and build labels
    file_sections = _split_diff_by_file(diff_text)
    if not file_sections:
        file_sections = [("unknown", diff_text)]

    n_labels = 0
    n_neg_labels = 0

    # Count labels only when the dataset is new to this run.  On re-runs the
    # dataset is pre-existing and INSERT OR IGNORE silently deduplicates, so
    # we must not double-count.  Use the shared dict so polarities that come
    # after the first dataset occurrence (vul=0 after vul=1) can see whether
    # the dataset was newly created.
    is_new_dataset = pair_is_new_dataset.get(pair_key, False)

    if vul_flag == 1:
        # Positive labels (vulnerable side)
        label_rows: list[dict] = []
        for filename, file_diff in file_sections:
            try:
                hunk_ranges = _parse_hunk_ranges(file_diff)
            except Exception as exc:
                raise ValueError(
                    f"Malformed diff for {cve_id}/{commit_id[:8]}/{filename}: {exc}"
                ) from exc

            if not hunk_ranges:
                hunk_ranges = [(1, 1)]

            for old_start, old_end in hunk_ranges:
                label_id = (
                    f"bigvul::{cve_id}::{commit_id[:8]}::{filename}::{old_start}"
                )
                label_rows.append(
                    {
                        "id": label_id,
                        "dataset_name": dataset_name,
                        "dataset_version": parent_ref,
                        "file_path": filename,
                        "line_start": old_start,
                        "line_end": old_end,
                        "cwe_id": first_cwe,
                        "vuln_class": vuln_class,
                        "severity": severity,
                        "description": f"{cve_id} fix commit {commit_id[:8]}",
                        "source": "bigvul",
                        "source_ref": cve_id,
                        "confidence": "HIGH",
                        "created_at": now_iso,
                    }
                )

        if label_rows:
            await db.append_dataset_labels(label_rows)
            if is_new_dataset:
                n_labels = len(label_rows)

    elif vul_flag == 0:
        # Negative labels (fixed/non-vulnerable side)
        neg_rows: list[dict] = []
        for filename, _file_diff in file_sections:
            neg_id = f"bigvul::{cve_id}::{commit_id[:8]}::{filename}::neg"
            neg_rows.append(
                {
                    "id": neg_id,
                    "dataset_name": dataset_name,
                    "dataset_version": parent_ref,
                    "file_path": filename,
                    "cwe_id": first_cwe,
                    "vuln_class": vuln_class,
                    "source": "bigvul",
                    "source_ref": cve_id,
                    "created_at": now_iso,
                    "notes": None,
                }
            )

        if neg_rows:
            await db.append_dataset_negative_labels(neg_rows)
            if is_new_dataset:
                n_neg_labels = len(neg_rows)

    return n_datasets, n_labels, n_neg_labels, None
