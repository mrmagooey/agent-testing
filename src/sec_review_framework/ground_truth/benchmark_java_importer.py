"""OWASP BenchmarkJava ground truth importer.

Ingests ``expectedresults-1.2.csv`` from a locally cloned BenchmarkJava
working tree and populates ``dataset_labels`` (positives) and
``dataset_negative_labels`` (negatives) in the framework database.

The importer is idempotent: re-running with the same ``origin_commit`` will
not create duplicate rows because label IDs are deterministic and the DB uses
INSERT OR IGNORE.

NOTE: BenchmarkJava test files are Apache-2.0.  This module intentionally
does NOT vendor those files.  The caller must clone the repo separately and
pass ``csv_path`` and ``testcode_dir`` pointing at the working tree.

Pinned default ``origin_commit``:
    6e809e5a8f41b59b842bb3c5547f0cba88b5d76e
    (HEAD of OWASP-Benchmark/BenchmarkJava main as of 2026-04-28)
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from sec_review_framework.db import Database

# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class ImportResult:
    """Summary returned by :func:`import_benchmark_java`."""

    dataset_name: str
    dataset_version: str
    positives_count: int
    negatives_count: int
    skipped_count: int


# ---------------------------------------------------------------------------
# Category → vuln_class mapping
# ---------------------------------------------------------------------------

# Loaded lazily from config/vuln_classes.yaml via _load_category_map().
_CATEGORY_MAP: dict[str, str] | None = None

# Location of the config file relative to this module.
_CONFIG_PATH = Path(__file__).parent.parent.parent.parent / "config" / "vuln_classes.yaml"


def _load_category_map() -> dict[str, str]:
    """Return {csv_category: vuln_class_key} built from vuln_classes.yaml.

    Every key in the YAML whose name matches a BenchmarkJava category is
    eligible — no special-casing required because those keys are added
    directly to the YAML.
    """
    global _CATEGORY_MAP
    if _CATEGORY_MAP is not None:
        return _CATEGORY_MAP

    data = yaml.safe_load(_CONFIG_PATH.read_text())
    # All top-level keys are valid vuln_class values; the CSV category name
    # must equal the YAML key (e.g. "pathtraver", "sqli", "xss", ...).
    _CATEGORY_MAP = {key: key for key in data.get("vuln_classes", {})}
    return _CATEGORY_MAP


def _category_to_vuln_class(category: str, category_map: dict[str, str]) -> str:
    """Map a BenchmarkJava CSV category to a ``vuln_class`` string.

    Raises ``ValueError`` on unknown categories so corpus drift is caught
    immediately rather than silently dropped.
    """
    try:
        return category_map[category]
    except KeyError:
        known = ", ".join(sorted(category_map))
        raise ValueError(
            f"Unknown BenchmarkJava category {category!r}. "
            f"Known categories: {known}. "
            "Update config/vuln_classes.yaml if the benchmark has added new categories."
        ) from None


# ---------------------------------------------------------------------------
# CSV parsing
# ---------------------------------------------------------------------------


def _parse_csv(csv_path: Path, category_map: dict[str, str]) -> list[dict]:
    """Parse expectedresults CSV and return a list of row dicts.

    Each dict has keys: test_name, category, vuln_class, is_positive, cwe_id.
    The header line (starts with '#') is skipped; trailing whitespace is
    stripped.  Unknown categories raise immediately.
    """
    rows: list[dict] = []
    with csv_path.open(newline="") as fh:
        for raw_line in fh:
            stripped = raw_line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            # Use stdlib csv to handle any quoting edge-cases.
            parts = next(csv.reader([stripped]))
            if len(parts) < 4:
                continue  # malformed — skip silently (not a data row)
            test_name = parts[0].strip()
            category = parts[1].strip()
            real_vuln = parts[2].strip().lower()
            cwe_num = parts[3].strip()

            vuln_class = _category_to_vuln_class(category, category_map)
            cwe_id = f"CWE-{cwe_num}"
            is_positive = real_vuln == "true"

            rows.append(
                {
                    "test_name": test_name,
                    "category": category,
                    "vuln_class": vuln_class,
                    "cwe_id": cwe_id,
                    "is_positive": is_positive,
                }
            )
    return rows


# ---------------------------------------------------------------------------
# Public importer
# ---------------------------------------------------------------------------

_DEFAULT_ORIGIN_COMMIT = "6e809e5a8f41b59b842bb3c5547f0cba88b5d76e"


async def import_benchmark_java(
    db: Database,
    *,
    origin_url: str = "https://github.com/OWASP-Benchmark/BenchmarkJava",
    origin_commit: str = _DEFAULT_ORIGIN_COMMIT,
    csv_path: Path,
    testcode_dir: Path,
    version: str = "1.2",
    dataset_name: str = "owasp-benchmark-java-v1.2",
) -> ImportResult:
    """Import OWASP BenchmarkJava ground truth into the framework database.

    Idempotent on (origin_url, origin_commit): re-running with the same
    arguments yields no duplicate rows (INSERT OR IGNORE on deterministic IDs).

    ``dataset_version`` for all labels is set to ``origin_commit`` so that
    different pinned SHAs produce distinct version namespaces.

    Args:
        db: Initialised :class:`~sec_review_framework.db.Database` instance.
        origin_url: Canonical GitHub URL of the BenchmarkJava repo.
        origin_commit: Pinned SHA of the cloned working tree.
        csv_path: Path to ``expectedresults-1.2.csv`` in the cloned tree.
        testcode_dir: Path to the ``src/main/java/org/owasp/benchmark/testcode/``
            directory (used for line counts).
        version: Human-readable benchmark version string (default ``"1.2"``).
        dataset_name: Name used as the ``datasets`` row PK and label namespace.

    Returns:
        :class:`ImportResult` with counts of positives, negatives, and skipped.
    """
    dataset_version = origin_commit

    # ------------------------------------------------------------------
    # 1. Create or update the datasets row (idempotent via INSERT OR IGNORE)
    # ------------------------------------------------------------------
    existing = await db.get_dataset(dataset_name)
    if existing is None:
        await db.create_dataset(
            {
                "name": dataset_name,
                "kind": "git",
                "origin_url": origin_url,
                "origin_commit": origin_commit,
                "cve_id": None,
                "metadata_json": json.dumps(
                    {
                        "benchmark": "owasp-java",
                        "version": version,
                        "language": "java",
                        "iteration": "per-test-file",
                        "test_glob": "src/main/java/**/BenchmarkTest*.java",
                    }
                ),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    # ------------------------------------------------------------------
    # 2. Parse CSV
    # ------------------------------------------------------------------
    category_map = _load_category_map()
    parsed_rows = _parse_csv(csv_path, category_map)

    now_iso = datetime.now(UTC).isoformat()
    positive_labels: list[dict] = []
    negative_labels: list[dict] = []
    skipped = 0

    for row in parsed_rows:
        test_name = row["test_name"]
        file_path = f"src/main/java/org/owasp/benchmark/testcode/{test_name}.java"
        java_file = testcode_dir / f"{test_name}.java"

        if row["is_positive"]:
            # ------------------------------------------------------------------
            # 3. Positive: read line count from testcode_dir
            # ------------------------------------------------------------------
            if java_file.exists():
                line_count = len(java_file.read_text(errors="replace").splitlines())
                line_count = max(line_count, 1)
            else:
                # File absent — record what we can, note the gap.
                line_count = 1
                skipped += 1

            positive_labels.append(
                {
                    "id": f"{dataset_name}::{test_name}::pos",
                    "dataset_name": dataset_name,
                    "dataset_version": dataset_version,
                    "file_path": file_path,
                    "line_start": 1,
                    "line_end": line_count,
                    "cwe_id": row["cwe_id"],
                    "vuln_class": row["vuln_class"],
                    "severity": "MEDIUM",
                    "confidence": "HIGH",
                    "description": (
                        f"OWASP Benchmark expected vulnerability ({row['category']})"
                    ),
                    "source": "benchmark",
                    "source_ref": test_name,
                    "created_at": now_iso,
                }
            )
        else:
            # ------------------------------------------------------------------
            # 4. Negative: expected-clean assertion
            # ------------------------------------------------------------------
            negative_labels.append(
                {
                    "id": f"{dataset_name}::{test_name}::neg",
                    "dataset_name": dataset_name,
                    "dataset_version": dataset_version,
                    "file_path": file_path,
                    "cwe_id": row["cwe_id"],
                    "vuln_class": row["vuln_class"],
                    "source": "benchmark",
                    "source_ref": test_name,
                    "created_at": now_iso,
                }
            )

    # ------------------------------------------------------------------
    # 5. Persist (idempotent INSERT OR IGNORE)
    # ------------------------------------------------------------------
    if positive_labels:
        await db.append_dataset_labels(positive_labels)
    if negative_labels:
        await db.append_dataset_negative_labels(negative_labels)

    return ImportResult(
        dataset_name=dataset_name,
        dataset_version=dataset_version,
        positives_count=len(positive_labels),
        negatives_count=len(negative_labels),
        skipped_count=skipped,
    )
