"""NIST SARD (Software Assurance Reference Dataset) importer.

Ingests test cases from a locally extracted NIST SARD test suite archive and
populates ``dataset_labels`` (vulnerable testcases) and
``dataset_negative_labels`` (fixed/clean testcases) in the framework database.

The importer is idempotent: re-running with the same archive produces no
duplicate rows because label IDs are deterministic and the DB uses
INSERT OR IGNORE.

Distribution format
-------------------
SARD is distributed by NIST as a zip archive ("Test Suite").  After
extraction the root contains ``manifest.xml`` whose structure is:

    <manifest>
      <testcase id="100001" language="C" ...>
        <file path="CWE121_Stack_Based_Buffer_Overflow/CWE121_bad.c"
              language="C" />
        <flaw line="55" name="..." cwe="CWE-121" />
      </testcase>
      <testcase id="100002" language="C" ...>
        <file path="CWE121_Stack_Based_Buffer_Overflow/CWE121_good.c"
              language="C" />
        <fix />   <!-- no <flaw> → this is the "fixed" / non-vulnerable pair -->
      </testcase>
      ...
    </manifest>

Key attributes:
- ``<testcase id="..." language="...">`` — the top-level grouping.
- ``<file path="..." />`` — one or more source files in the testcase.
- ``<flaw line="N" cwe="CWE-X" />`` — marks a vulnerable line.
- ``<fix />`` (no ``<flaw>``) — marks a non-vulnerable counterpart.

Testcases with at least one ``<flaw>`` child are treated as *vulnerable*;
testcases with only ``<fix>`` children (and no ``<flaw>``) are *fixed*.

SARD language codes used in manifest.xml:
    C, Cpp, Java, Python, Php

They are normalised to lowercase for the ``metadata_json.language`` field:
    c, cpp, java, python, php

Dataset granularity
-------------------
One ``kind='archive'`` dataset is created **per language** (up to 5 total),
shared by all testcases of that language.  This keeps the dataset row count
small and avoids cross-testcase primary-key collisions.  Per-testcase
identity is encoded in the label ``source_ref`` field as ``SARD-<id>``.

Path safety
-----------
File paths read from the manifest are validated with ``_safe_path``.  Any
path that resolves outside the archive root (e.g. ``../../etc/passwd``) is
silently skipped and the testcase is recorded in ``skipped_reasons``.

NOTE: This module does NOT vendor real SARD content.  The caller must
download the SARD Test Suite zip from
``https://samate.nist.gov/SARD/downloads/`` and extract it locally, then
pass the extracted directory as ``sard_archive_path``.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from sec_review_framework.db import Database
from sec_review_framework.ground_truth._source_check_migration import (
    ensure_negative_source_check_includes,
    ensure_source_check_includes,
)
from sec_review_framework.ground_truth.cvefixes_importer import (
    _cwe_to_vuln_class,
)

__all__ = ["SARDImportResult", "import_sard"]


# ---------------------------------------------------------------------------
# Language normalisation
# ---------------------------------------------------------------------------

#: Map SARD XML language attribute values → lowercase canonical names.
_LANG_NORMALISE: dict[str, str] = {
    "C": "c",
    "Cpp": "cpp",
    "Java": "java",
    "Python": "python",
    "Php": "php",
    # Some releases use alternative capitalisation:
    "CPP": "cpp",
    "PHP": "php",
    "JAVA": "java",
    "PYTHON": "python",
}


def _normalise_language(raw: str) -> str:
    """Return canonical lowercase language string from SARD attribute value."""
    return _LANG_NORMALISE.get(raw, raw.lower())


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class SARDImportResult:
    """Summary returned by :func:`import_sard`."""

    imported_datasets: int = 0          # new ``kind='archive'`` rows created
    imported_labels: int = 0            # positive (vulnerable) label rows
    imported_negative_labels: int = 0   # negative (fixed) label rows
    skipped_testcases: int = 0
    skipped_reasons: dict[str, int] = field(default_factory=dict)
    errors: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------

_MANIFEST_FILENAME = "manifest.xml"


def _find_manifest(sard_archive_path: Path) -> Path | None:
    """Locate manifest.xml in the extracted SARD directory.

    SARD distributions sometimes nest the manifest one level deep inside a
    sub-directory (e.g. ``Juliet_Test_Suite_v1.3/manifest.xml``).  We check
    the root first, then one level down.

    Returns None if the manifest cannot be found.
    """
    # Direct path
    direct = sard_archive_path / _MANIFEST_FILENAME
    if direct.exists():
        return direct

    # One level of nesting (common in older distributions)
    for child in sard_archive_path.iterdir():
        if child.is_dir():
            candidate = child / _MANIFEST_FILENAME
            if candidate.exists():
                return candidate

    return None


def _safe_path(raw_path: str, archive_root: Path) -> Path | None:
    """Validate and resolve a file path from the manifest.

    Returns the resolved absolute Path if it stays within ``archive_root``,
    or None if it would escape (path traversal attempt) or is empty/absolute.

    The path is treated as relative to ``archive_root``.  Absolute paths
    (starting with ``/``) are rejected because they cannot be safely joined.
    """
    raw_path = raw_path.strip()
    if not raw_path or raw_path.startswith("/"):
        return None

    # Normalise with PurePosixPath to collapse ".." components before joining
    try:
        normalised = PurePosixPath(raw_path)
        # Resolve the string of parts through a PurePosixPath to get rid of
        # redundant separators; then check for leading ".." by inspection.
        parts = normalised.parts
        if not parts:
            return None
        if parts[0] in ("..", "."):
            # Start with traversal — always reject
            return None
        # Build the joined absolute path and check it stays inside the root
        resolved = (archive_root / Path(*parts)).resolve()
        archive_resolved = archive_root.resolve()
        resolved.relative_to(archive_resolved)  # raises ValueError if outside
        return resolved
    except (ValueError, TypeError):
        return None


def _parse_manifest(
    manifest_path: Path,
    languages_filter: set[str] | None,
    max_testcases: int | None,
) -> list[dict[str, Any]]:
    """Parse ``manifest.xml`` and return a list of normalised testcase dicts.

    Each returned dict has:
        id          : str  — testcase id attribute
        language    : str  — normalised lowercase language (e.g. "c", "java")
        files       : list[str]  — file paths relative to archive root
        flaws       : list[dict] — [{line, cwe}]
        is_fixed    : bool — True if only <fix> children, no <flaw>
        raw_cwe     : str | None — first CWE found in <flaw> or <testcase> attrib

    Raises:
        FileNotFoundError: if manifest_path does not exist.
        ValueError: if the manifest is not parseable XML.
    """
    if not manifest_path.exists():
        raise FileNotFoundError(f"SARD manifest not found at {manifest_path}")

    try:
        tree = ET.parse(str(manifest_path))
    except ET.ParseError as exc:
        raise ValueError(f"SARD manifest is not valid XML: {exc}") from exc

    root = tree.getroot()

    # The root element may be <manifest> or <testSuite> depending on the
    # SARD release.  We search for <testcase> at any depth.
    testcases_iter = (
        elem
        for elem in root.iter()
        if elem.tag.lower() in ("testcase",)
    )

    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for tc in testcases_iter:
        if max_testcases is not None and len(results) >= max_testcases:
            break

        tc_id = tc.get("id") or tc.get("testCaseId") or tc.get("testcase_id")
        if not tc_id:
            continue

        # Deduplicate by ID (manifest may have duplicates in some releases)
        if tc_id in seen_ids:
            continue
        seen_ids.add(tc_id)

        # Language
        raw_lang = tc.get("language") or tc.get("Language") or ""
        lang = _normalise_language(raw_lang) if raw_lang else "unknown"

        # Apply language filter early
        if languages_filter is not None and lang not in languages_filter:
            continue

        # Collect <file> elements
        file_paths: list[str] = []
        for f_elem in tc:
            tag = f_elem.tag.lower()
            if tag == "file":
                fp = f_elem.get("path") or f_elem.get("Path") or ""
                if fp:
                    file_paths.append(fp)

        # Collect <flaw> and <fix> elements
        flaws: list[dict] = []
        has_fix = False
        first_cwe: str | None = tc.get("cwe") or tc.get("CWE")

        for child in tc:
            ctag = child.tag.lower()
            if ctag == "flaw":
                cwe = child.get("cwe") or child.get("CWE") or first_cwe or "CWE-UNKNOWN"
                try:
                    line_n = int(child.get("line") or child.get("Line") or 0)
                except ValueError:
                    line_n = 0
                flaws.append({"line": line_n, "cwe": cwe})
                if first_cwe is None:
                    first_cwe = cwe
            elif ctag == "fix":
                has_fix = True

        # If there are no file paths but also no flaws, skip — not useful
        if not file_paths and not flaws:
            continue

        # Determine polarity:
        # - has flaws → vulnerable
        # - has_fix and no flaws → fixed/non-vulnerable
        # - neither → skip (testcase with only structural metadata)
        is_vulnerable = len(flaws) > 0
        is_fixed = has_fix and not is_vulnerable

        if not is_vulnerable and not is_fixed:
            continue

        results.append(
            {
                "id": tc_id,
                "language": lang,
                "files": file_paths,
                "flaws": flaws,
                "is_fixed": is_fixed,
                "raw_cwe": first_cwe,
            }
        )

    return results


# ---------------------------------------------------------------------------
# Dataset name helpers
# ---------------------------------------------------------------------------


def _dataset_name_for_language(
    language: str, archive_sha256: str
) -> str:
    """Build a deterministic dataset name for a given SARD language shard.

    Uses the first 12 hex characters of the archive SHA-256 to distinguish
    imports from different SARD releases while remaining human-readable.
    """
    return f"nist-sard-{language}-{archive_sha256[:12]}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def import_sard(
    db: Database,
    *,
    sard_archive_path: Path,
    languages: list[str] | None = None,
    archive_url: str,
    archive_sha256: str,
    archive_format: str = "zip",
    max_testcases: int | None = None,
) -> SARDImportResult:
    """Idempotent import of NIST SARD test cases into the sec-review DB.

    One ``kind='archive'`` dataset row is created per language (e.g.
    ``nist-sard-c-<sha12>``, ``nist-sard-java-<sha12>``).  Testcase flaws
    become ``dataset_labels`` rows; fixed testcases become
    ``dataset_negative_labels`` rows.

    The import is idempotent on (archive_sha256, testcase_id): re-running
    with the same archive produces no new rows because:
    - Dataset names are deterministic (sha256-pinned).
    - Label IDs are deterministic: ``sard::<lang>::<testcase_id>::<file_idx>::<flaw_idx>``.
    - Negative label IDs: ``sard-neg::<lang>::<testcase_id>::<file_idx>``.
    - All inserts use INSERT OR IGNORE.

    Args:
        db: Initialised framework Database.
        sard_archive_path: Path to the extracted SARD distribution directory
            (the directory that contains ``manifest.xml``).
        languages: If provided, restrict import to these normalised language
            codes (e.g. ``["c", "java"]``).  None = all SARD languages.
        archive_url: Original SARD download URL, stored in datasets.archive_url.
        archive_sha256: SHA-256 hex digest of the SARD zip, used for the
            datasets.archive_sha256 column and in deterministic dataset names.
        archive_format: Archive format string (default ``"zip"``).
        max_testcases: Optional hard cap on testcases processed (useful for
            unit tests).

    Returns:
        SARDImportResult with counts and any per-testcase errors.
    """
    result = SARDImportResult()

    # Migrate source CHECK constraints so 'sard' is accepted
    await ensure_source_check_includes(db, "sard")
    await ensure_negative_source_check_includes(db, "sard")

    # Locate manifest
    manifest_path = _find_manifest(sard_archive_path)
    if manifest_path is None:
        raise FileNotFoundError(
            f"Cannot find {_MANIFEST_FILENAME} under {sard_archive_path}. "
            "Ensure the SARD archive has been fully extracted."
        )

    # Determine archive root (directory that contains manifest.xml)
    archive_root = manifest_path.parent

    # Build language filter
    lang_filter: set[str] | None = (
        {lang.lower() for lang in languages} if languages is not None else None
    )

    # Parse manifest
    testcases = _parse_manifest(manifest_path, lang_filter, max_testcases)

    now_iso = datetime.now(UTC).isoformat()

    # Ensure one dataset per language that appears in the manifest
    lang_to_dataset_name: dict[str, str] = {}
    for tc in testcases:
        lang = tc["language"]
        if lang not in lang_to_dataset_name:
            ds_name = _dataset_name_for_language(lang, archive_sha256)
            lang_to_dataset_name[lang] = ds_name

    # Create datasets (one per language) — idempotent via existence check
    for lang, ds_name in sorted(lang_to_dataset_name.items()):
        existing = await db.get_dataset(ds_name)
        if existing is None:
            metadata = {
                "benchmark": "nist-sard",
                "language": lang,
                "archive_sha256": archive_sha256,
                "iteration": "per-testcase",
            }
            await db.create_dataset(
                {
                    "name": ds_name,
                    "kind": "archive",
                    "archive_url": archive_url,
                    "archive_sha256": archive_sha256,
                    "archive_format": archive_format,
                    "metadata_json": json.dumps(metadata),
                    "created_at": now_iso,
                }
            )
            result.imported_datasets += 1

    # Process testcases
    positive_label_rows: list[dict] = []
    negative_label_rows: list[dict] = []

    for tc in testcases:
        try:
            n_pos, n_neg, skip_reason = _build_label_rows(
                tc,
                lang_to_dataset_name=lang_to_dataset_name,
                archive_root=archive_root,
                now_iso=now_iso,
            )
            if skip_reason:
                result.skipped_testcases += 1
                result.skipped_reasons[skip_reason] = (
                    result.skipped_reasons.get(skip_reason, 0) + 1
                )
            else:
                positive_label_rows.extend(n_pos)
                negative_label_rows.extend(n_neg)
        except Exception as exc:  # noqa: BLE001
            result.errors.append((tc["id"], str(exc)))

    # Persist (idempotent INSERT OR IGNORE)
    if positive_label_rows:
        await db.append_dataset_labels(positive_label_rows)
        result.imported_labels += len(positive_label_rows)

    if negative_label_rows:
        await db.append_dataset_negative_labels(negative_label_rows)
        result.imported_negative_labels += len(negative_label_rows)

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _build_label_rows(
    tc: dict[str, Any],
    *,
    lang_to_dataset_name: dict[str, str],
    archive_root: Path,
    now_iso: str,
) -> tuple[list[dict], list[dict], str | None]:
    """Build label rows for a single SARD testcase.

    Returns:
        (positive_rows, negative_rows, skip_reason_or_None)

    Skip reasons:
        "unsafe_paths"   — all file paths were rejected by _safe_path
        "no_files"       — testcase had no file paths in the manifest
    """
    tc_id: str = tc["id"]
    lang: str = tc["language"]
    dataset_name: str = lang_to_dataset_name[lang]
    dataset_version: str = archive_root.name  # e.g. "juliet-test-suite-v1.3"
    source_ref = f"SARD-{tc_id}"

    files: list[str] = tc["files"]
    flaws: list[dict] = tc["flaws"]
    is_fixed: bool = tc["is_fixed"]
    raw_cwe: str | None = tc.get("raw_cwe")

    if not files:
        # No file paths in manifest — not very useful but not an error
        if flaws:
            # Vulnerable but no files: emit one synthetic label with no path
            # so the testcase is still visible in queries.
            files = [f"SARD-{tc_id}/unknown.{lang}"]
        else:
            return [], [], "no_files"

    positive_rows: list[dict] = []
    negative_rows: list[dict] = []

    # Validate all file paths (path safety)
    safe_files: list[tuple[int, str]] = []
    for idx, raw_fp in enumerate(files):
        resolved = _safe_path(raw_fp, archive_root)
        if resolved is None:
            # Unsafe path — record as relative string so labels still exist
            # but flag the skip if ALL files were unsafe.
            # We use the raw path string as a relative ref for the label.
            # If the path looks harmless despite resolving outside the root
            # (e.g. empty), skip entirely.
            if raw_fp and "/" not in raw_fp.split("..")[0]:
                # Almost certainly a traversal attempt — skip this file
                continue
            # Simple relative path that failed resolution — store as-is
            safe_files.append((idx, raw_fp.strip()))
        else:
            # Store path relative to archive_root for the scoring matcher
            try:
                rel = resolved.relative_to(archive_root.resolve())
                safe_files.append((idx, str(rel)))
            except ValueError:
                # resolve() expanded a symlink outside the root
                continue

    if not safe_files:
        return [], [], "unsafe_paths"

    if not flaws and not is_fixed:
        # No flaws and not explicitly marked as fix — already filtered in
        # _parse_manifest, but be defensive here.
        return [], [], "no_polarity"

    if is_fixed:
        # Non-vulnerable testcase: emit one negative label per file
        guard_cwe = raw_cwe or "CWE-UNKNOWN"
        vuln_class = _cwe_to_vuln_class(guard_cwe)
        if vuln_class == guard_cwe:
            vuln_class = "other"

        for file_idx, rel_path in safe_files:
            neg_id = f"sard-neg::{lang}::{tc_id}::{file_idx}"
            negative_rows.append(
                {
                    "id": neg_id,
                    "dataset_name": dataset_name,
                    "dataset_version": dataset_version,
                    "file_path": rel_path,
                    "cwe_id": guard_cwe,
                    "vuln_class": vuln_class,
                    "source": "sard",
                    "source_ref": source_ref,
                    "created_at": now_iso,
                }
            )
    else:
        # Vulnerable testcase: emit one positive label per (file, flaw)
        for file_idx, rel_path in safe_files:
            for flaw_idx, flaw in enumerate(flaws):
                cwe_id = flaw.get("cwe") or raw_cwe or "CWE-UNKNOWN"
                line_n = flaw.get("line") or 1
                if line_n <= 0:
                    line_n = 1
                vuln_class = _cwe_to_vuln_class(cwe_id)
                if vuln_class == cwe_id:
                    # No match in the YAML map → fall back to "other"
                    vuln_class = "other"

                label_id = f"sard::{lang}::{tc_id}::{file_idx}::{flaw_idx}"
                positive_rows.append(
                    {
                        "id": label_id,
                        "dataset_name": dataset_name,
                        "dataset_version": dataset_version,
                        "file_path": rel_path,
                        "line_start": line_n,
                        "line_end": line_n,
                        "cwe_id": cwe_id,
                        "vuln_class": vuln_class,
                        "severity": "MEDIUM",
                        "description": f"NIST SARD testcase {tc_id} ({cwe_id})",
                        "source": "sard",
                        "source_ref": source_ref,
                        "confidence": "HIGH",
                        "created_at": now_iso,
                    }
                )

    return positive_rows, negative_rows, None
