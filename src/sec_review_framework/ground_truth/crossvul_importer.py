"""CrossVul importer: ingest real-world CVE fix commits from the CrossVul dataset.

CrossVul (Nikitopoulos et al., 2021) is a multi-language dataset of ~1,675 CVEs
across C/C++, Java, PHP, JavaScript, and Python.  Each entry pairs a CVE ID with
one or more GitHub fix-commit URLs.

Reference: https://github.com/nicowillis/CrossVul
           (see also: https://zenodo.org/record/5901291)

Distribution format assumed by this importer
--------------------------------------------
CrossVul is distributed as a JSON file (commonly ``crossvul.json``) whose top-
level value is a list of objects.  Each object has at minimum:

    {
      "cve_id":     "CVE-2021-XXXX",
      "language":   "Python",
      "project_url": "https://github.com/owner/repo",
      "fix_commit": "abc123def456...",
      "cwe_id":     "CWE-89",          # optional; may be absent or null
      "severity":   "HIGH"             # optional; may be absent or null
    }

If the canonical CrossVul distribution you have uses different field names, map
them to the above names before passing the manifest to the importer, or extend
``_parse_manifest_row()`` accordingly.

Diff acquisition strategy
--------------------------
CrossVul does NOT bundle the unified diffs.  This importer resolves each fix
commit diff via a local cache under ``fix_clone_root``:

    fix_clone_root/<project_slug>/<commit_hash[:16]>.diff

If the cache file is present it is used directly (no network needed).  If it is
absent the importer attempts to download the GitHub patch via the public
``.patch`` URL.  If the download fails (no network, rate-limit, 404, …) the CVE
is recorded in ``CrossVulImportResult.skipped_reasons`` rather than raising,
because a partial import is still useful.

To pre-populate the cache without running the importer, simply place a file at
the expected path containing the output of ``git diff <parent>^..<fix_commit>``
(unified diff format).

Network access required / not required
---------------------------------------
- **No network needed** when every referenced diff is already in the cache.
- **Network needed** when the cache is cold.  The importer will raise
  ``RuntimeError`` with a descriptive message if ``httpx`` is unavailable,
  so callers know to pre-populate the cache.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sec_review_framework.db import Database
from sec_review_framework.ground_truth._source_check_migration import (
    ensure_source_check_includes,
)

# Reuse shared helpers from the CVEfixes importer (DRY)
from sec_review_framework.ground_truth.cvefixes_importer import (
    _cwe_to_vuln_class,
    _normalise_severity,
    _parse_hunk_ranges,
    _project_slug,
)

__all__ = ["CrossVulImportResult", "import_crossvul"]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class CrossVulImportResult:
    """Summary returned by :func:`import_crossvul`."""

    imported_cves: int = 0
    imported_datasets: int = 0
    imported_labels: int = 0
    skipped_cves: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------

#: Alternate field names accepted in the manifest JSON.
_FIELD_ALIASES: dict[str, list[str]] = {
    "cve_id": ["cve_id", "CVE_ID", "id"],
    "language": ["language", "lang", "Language"],
    "project_url": ["project_url", "repo_url", "repo", "url"],
    "fix_commit": ["fix_commit", "commit_hash", "commit", "hash"],
    "cwe_id": ["cwe_id", "CWE_ID", "cwe"],
    "severity": ["severity", "Severity", "cvss_severity"],
}


def _get_field(record: dict[str, Any], field_name: str) -> Any:
    """Look up *field_name* in *record*, trying known aliases.

    Returns ``None`` if no alias matches.
    """
    for alias in _FIELD_ALIASES.get(field_name, [field_name]):
        if alias in record:
            return record[alias]
    return None


def _parse_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    """Parse the CrossVul manifest (JSON) into a list of normalised records.

    Each returned dict is guaranteed to have keys:
    ``cve_id``, ``language``, ``project_url``, ``fix_commit``.
    Optional keys: ``cwe_id``, ``severity``.

    Raises:
        ValueError: If the file is not valid JSON or does not contain a list.
        FileNotFoundError: If *manifest_path* does not exist.
    """
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"CrossVul manifest not found at {manifest_path}. "
            "Download it from https://zenodo.org/record/5901291 or supply the correct path."
        )

    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"CrossVul manifest is not valid JSON: {exc}") from exc

    if not isinstance(raw, list):
        raise ValueError(
            f"CrossVul manifest must be a JSON array at the top level; "
            f"got {type(raw).__name__}"
        )

    records: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue  # skip non-object elements silently

        cve_id = _get_field(item, "cve_id")
        language = _get_field(item, "language")
        project_url = _get_field(item, "project_url")
        fix_commit = _get_field(item, "fix_commit")

        # Skip records missing required fields
        if not all([cve_id, language, project_url, fix_commit]):
            continue

        records.append(
            {
                "cve_id": str(cve_id).strip(),
                "language": str(language).strip(),
                "project_url": str(project_url).strip(),
                "fix_commit": str(fix_commit).strip(),
                "cwe_id": _get_field(item, "cwe_id"),
                "severity": _get_field(item, "severity"),
            }
        )

    return records


# ---------------------------------------------------------------------------
# Diff cache helpers
# ---------------------------------------------------------------------------


def _diff_cache_path(fix_clone_root: Path, project_url: str, commit_hash: str) -> Path:
    """Return the expected cache file path for a given project + commit."""
    slug = _project_slug(project_url)
    # Use first 16 chars of the commit hash (enough for uniqueness in practice)
    return fix_clone_root / slug / f"{commit_hash[:16]}.diff"


def _load_cached_diff(cache_path: Path) -> str | None:
    """Return the cached diff text, or None if not present."""
    if cache_path.exists():
        return cache_path.read_text(encoding="utf-8", errors="replace")
    return None


def _fetch_github_patch(project_url: str, commit_hash: str) -> str:
    """Fetch the unified diff for *commit_hash* from GitHub's patch endpoint.

    Uses the synchronous ``urllib`` from the stdlib so there is no extra
    dependency.  If the request fails for any reason (no network, 404, rate
    limit, etc.) a ``RuntimeError`` is raised with a clear message so the
    caller can decide whether to skip or abort.

    Args:
        project_url: GitHub repo URL, e.g. ``https://github.com/owner/repo``.
        commit_hash: Full commit SHA.

    Returns:
        Unified diff text as a string.

    Raises:
        RuntimeError: On any network or HTTP error.
    """
    import urllib.error
    import urllib.request

    # Normalise URL: strip trailing slash and .git suffix
    base = re.sub(r"\.git$", "", project_url.rstrip("/"))
    patch_url = f"{base}/commit/{commit_hash}.patch"

    try:
        with urllib.request.urlopen(patch_url, timeout=30) as resp:  # noqa: S310
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"HTTP {exc.code} fetching patch for {commit_hash[:8]} from {patch_url}. "
            "Pre-populate the diff cache to avoid network access."
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Network error fetching patch for {commit_hash[:8]} from {patch_url}: {exc}. "
            "Pre-populate the diff cache to avoid network access."
        ) from exc


def _resolve_diff(
    fix_clone_root: Path,
    project_url: str,
    commit_hash: str,
    *,
    allow_network: bool = True,
) -> str:
    """Return the unified diff for *commit_hash*, using or populating the cache.

    If the cache file is present, returns it immediately.  If not and
    *allow_network* is True, fetches from GitHub and writes to the cache.
    If not and *allow_network* is False, raises RuntimeError.

    Args:
        fix_clone_root: Root directory for the diff cache.
        project_url: GitHub repo URL.
        commit_hash: Full commit SHA.
        allow_network: If False, raise RuntimeError rather than fetching.

    Returns:
        Unified diff text.

    Raises:
        RuntimeError: If the cache is cold and *allow_network* is False, or
            if the network fetch fails.
    """
    cache_path = _diff_cache_path(fix_clone_root, project_url, commit_hash)
    cached = _load_cached_diff(cache_path)
    if cached is not None:
        return cached

    if not allow_network:
        raise RuntimeError(
            f"Diff cache miss for {_project_slug(project_url)}/{commit_hash[:8]} "
            f"and network access is disabled. "
            f"Expected cache file: {cache_path}. "
            "Pre-populate the cache or set allow_network=True."
        )

    # Fetch and cache
    diff_text = _fetch_github_patch(project_url, commit_hash)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(diff_text, encoding="utf-8")
    return diff_text


# ---------------------------------------------------------------------------
# File-path extraction from a unified diff
# ---------------------------------------------------------------------------

_DIFF_FILE_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)
_MINUS_FILE_RE = re.compile(r"^--- a/(.+)$", re.MULTILINE)


def _split_diff_by_file(diff_text: str) -> list[tuple[str, str]]:
    """Split a multi-file unified diff into (filename, per_file_diff) pairs.

    Returns an empty list if the diff is empty or contains no file sections.
    """
    # Split on 'diff --git' boundary lines
    sections = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)
    result: list[tuple[str, str]] = []
    for section in sections:
        if not section.strip():
            continue
        # Extract filename from '--- a/<filename>' line or 'diff --git a/...' line
        m = _MINUS_FILE_RE.search(section)
        if m:
            filename = m.group(1).strip()
        else:
            gm = _DIFF_FILE_RE.search(section)
            filename = gm.group(1).strip() if gm else "unknown"
        result.append((filename, section))
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def import_crossvul(
    db: Database,
    *,
    manifest_path: Path,
    languages: list[str] | None = None,
    max_cves: int | None = None,
    fix_clone_root: Path,
) -> CrossVulImportResult:
    """Idempotent import of CrossVul records into the sec-review framework DB.

    Each unique (cve_id, project_url, fix_commit) triple becomes one ``kind='git'``
    dataset pinned to the *parent* (buggy) commit, identified as
    ``<fix_commit>^`` in ``origin_commit``.  Hunk ranges from the fix diff are
    stored as ``dataset_labels`` rows on the OLD side, matching the CVEfixes
    importer pattern.

    Idempotency is achieved via ``INSERT OR IGNORE`` on the label primary key
    and an existence check before creating each dataset.

    Args:
        db: Initialised framework :class:`~sec_review_framework.db.Database`.
        manifest_path: Local path to the CrossVul JSON manifest.
        languages: If provided, only import CVEs whose ``language`` field
            matches one of these values (case-insensitive).
        max_cves: Optional cap on the number of CVEs processed (useful for
            testing).
        fix_clone_root: Root directory for the diff cache.  Each diff is
            stored as ``fix_clone_root/<slug>/<commit_hash[:16]>.diff``.

    Returns:
        :class:`CrossVulImportResult` with counts and any per-CVE errors.
    """
    # Ensure 'crossvul' is accepted by the source CHECK constraint
    await ensure_source_check_includes(db, "crossvul")

    result = CrossVulImportResult()

    # Parse the manifest
    records = _parse_manifest(manifest_path)

    # Normalise language filter
    lang_filter: set[str] | None = (
        {lang.lower() for lang in languages} if languages else None
    )

    # Apply language filter
    if lang_filter is not None:
        records = [r for r in records if r["language"].lower() in lang_filter]

    # Apply max_cves cap
    if max_cves is not None:
        records = records[:max_cves]

    # Deduplicate on (cve_id, project_url, fix_commit) — the manifest may have
    # duplicates if CrossVul was concatenated from per-language exports.
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict] = []
    for rec in records:
        key = (rec["cve_id"], rec["project_url"], rec["fix_commit"])
        if key not in seen:
            seen.add(key)
            deduped.append(rec)
    records = deduped

    now_iso = datetime.now(UTC).isoformat()

    for record in records:
        cve_id: str = record["cve_id"]
        try:
            n_ds, n_labels, skip_reason = await _import_single_record(
                db,
                record=record,
                fix_clone_root=fix_clone_root,
                now_iso=now_iso,
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

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _import_single_record(
    db: Database,
    *,
    record: dict,
    fix_clone_root: Path,
    now_iso: str,
) -> tuple[int, int, str | None]:
    """Import one CrossVul record.

    Returns:
        (n_datasets_created, n_labels_created, skip_reason_or_None)

    Skip reasons (returned as the third element rather than raised):
    - ``"diff_cache_miss"``: diff not in cache and network unavailable.
    - ``"empty_diff"``: diff resolved to an empty string.

    Raised exceptions (propagated to the caller for error tracking):
    - ``ValueError``: malformed diff (no hunk headers, file parse error, etc.)
    - Other exceptions from the DB layer.
    """
    cve_id: str = record["cve_id"]
    language: str = record["language"]
    project_url: str = record["project_url"]
    fix_commit: str = record["fix_commit"]
    raw_severity: str | None = record.get("severity")
    raw_cwe: str | None = record.get("cwe_id")

    severity = _normalise_severity(raw_severity)
    first_cwe = raw_cwe if raw_cwe else "CWE-UNKNOWN"
    vuln_class = _cwe_to_vuln_class(first_cwe)

    # We represent the "buggy" state as the parent of the fix commit.
    # CrossVul doesn't provide the parent hash directly, so we use the
    # symbolic notation "<fix_commit>^" as the origin_commit.  This is
    # consistent with how a downstream materializer would check out the
    # pre-fix state via ``git checkout <fix_commit>^``.
    parent_ref = f"{fix_commit}^"

    # Resolve the diff (cache-first, network-fallback)
    try:
        diff_text = _resolve_diff(
            fix_clone_root,
            project_url,
            fix_commit,
            allow_network=True,
        )
    except RuntimeError as exc:
        # Cache miss and network unavailable / failed → skip with reason
        reason = "diff_cache_miss" if "cache miss" in str(exc) else "network_error"
        return 0, 0, reason

    if not diff_text or not diff_text.strip():
        return 0, 0, "empty_diff"

    # Build deterministic dataset name
    slug = _project_slug(project_url)
    dataset_name = f"crossvul-{cve_id.lower()}-{slug}-{fix_commit[:8]}"

    # Build metadata_json
    metadata = {
        "source": "crossvul",
        "language": language,
        "fix_commit": fix_commit,
        "cwe_id": first_cwe,
    }

    # Upsert dataset (idempotent via existence check)
    existing = await db.get_dataset(dataset_name)
    n_datasets = 0
    if existing is None:
        await db.create_dataset(
            {
                "name": dataset_name,
                "kind": "git",
                "origin_url": project_url,
                "origin_commit": parent_ref,
                "cve_id": cve_id,
                "metadata_json": json.dumps(metadata),
                "created_at": now_iso,
            }
        )
        n_datasets = 1

    # Split the diff into per-file sections and build labels
    file_sections = _split_diff_by_file(diff_text)

    if not file_sections:
        # Single-file diff or diff with no standard 'diff --git' headers;
        # treat the whole diff as belonging to one unnamed file.
        file_sections = [("unknown", diff_text)]

    label_rows: list[dict] = []
    for filename, file_diff in file_sections:
        try:
            hunk_ranges = _parse_hunk_ranges(file_diff)
        except Exception as exc:
            raise ValueError(
                f"Malformed diff for {cve_id}/{fix_commit[:8]}/{filename}: {exc}"
            ) from exc

        if not hunk_ranges:
            # No @@ headers: either the file was added/deleted outright, or the
            # diff header is non-standard.  Emit a single label at line 1 so the
            # file is still recorded.
            hunk_ranges = [(1, 1)]

        for old_start, old_end in hunk_ranges:
            label_id = (
                f"crossvul::{cve_id}::{fix_commit[:8]}::{filename}::{old_start}"
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
                    "description": f"{cve_id} fix commit {fix_commit[:8]}",
                    "source": "crossvul",
                    "source_ref": cve_id,
                    "confidence": "HIGH",
                    "created_at": now_iso,
                }
            )

    n_labels = 0
    if label_rows:
        await db.append_dataset_labels(label_rows)
        if existing is None:
            # Only count labels for genuinely new datasets to avoid double-
            # counting on re-runs (INSERT OR IGNORE silently drops duplicates).
            n_labels = len(label_rows)

    return n_datasets, n_labels, None
