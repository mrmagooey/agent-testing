"""Shared helper: extend the dataset_labels.source CHECK constraint.

SQLite does not support ALTER TABLE … MODIFY COLUMN, so adding a new
accepted value requires the standard table-rebuild pattern.  Both the
CVEfixes and CrossVul (and future) importers call this helper with their
own source value rather than each maintaining their own copy.

Usage::

    from sec_review_framework.ground_truth._source_check_migration import (
        ensure_source_check_includes,
    )

    await ensure_source_check_includes(db, "crossvul")

The call is idempotent: if the value is already accepted the function
returns immediately without touching the schema.
"""

from __future__ import annotations

import aiosqlite

from sec_review_framework.db import Database

# ---------------------------------------------------------------------------
# All accepted source values (extended incrementally as importers are added)
# ---------------------------------------------------------------------------

#: The current canonical set of allowed source values.  Kept sorted so the
#: SQL is stable across runs (helpful for diffing migration probes).
_BASE_SOURCE_VALUES: tuple[str, ...] = (
    "benchmark",
    "cve_patch",
    "cvefixes",
    "crossvul",
    "injected",
    "manual",
)


def _build_check_sql(accepted: tuple[str, ...]) -> str:
    """Return the CHECK expression for the given accepted source values."""
    quoted = ", ".join(f"'{v}'" for v in sorted(accepted))
    return f"CHECK (source IN ({quoted}))"


async def ensure_source_check_includes(db: Database, new_source: str) -> None:
    """Idempotent migration: guarantee that *new_source* is accepted by the
    ``dataset_labels.source`` CHECK constraint.

    Steps:
    1. Probe whether *new_source* is already accepted (SAVEPOINT + INSERT +
       ROLLBACK).  If yes, return immediately.
    2. If not, determine the full desired accepted set (union of
       ``_BASE_SOURCE_VALUES`` and *new_source*), then rebuild
       ``dataset_labels`` with the wider CHECK.

    The rebuild is wrapped in a single BEGIN/COMMIT transaction.  Foreign-key
    enforcement is disabled for the duration to allow the table drop.

    Args:
        db: Initialised framework :class:`~sec_review_framework.db.Database`.
        new_source: The source string that must be accepted (e.g.
            ``"crossvul"``).
    """
    # Determine the full desired accepted set
    accepted = tuple(sorted(set(_BASE_SOURCE_VALUES) | {new_source}))
    check_sql = _build_check_sql(accepted)

    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = OFF")

        # --- 1. Probe ---
        already_ok = False
        try:
            await conn.execute("SAVEPOINT _probe_source_check")
            await conn.execute(
                f"""
                INSERT INTO dataset_labels (
                    id, dataset_name, dataset_version, file_path,
                    line_start, line_end, cwe_id, vuln_class, severity,
                    description, source, confidence, created_at
                ) VALUES (
                    '_probe_{new_source}', '_probe_ds', 'v0', 'probe.py',
                    1, 1, 'CWE-0', 'other', 'LOW',
                    'probe', '{new_source}', 'HIGH', '2000-01-01T00:00:00'
                )
                """
            )
            await conn.execute("ROLLBACK TO SAVEPOINT _probe_source_check")
            await conn.execute("RELEASE SAVEPOINT _probe_source_check")
            already_ok = True
        except Exception:
            try:
                await conn.execute("ROLLBACK TO SAVEPOINT _probe_source_check")
                await conn.execute("RELEASE SAVEPOINT _probe_source_check")
            except Exception:
                pass

        if already_ok:
            await conn.execute("PRAGMA foreign_keys = ON")
            return

        # --- 2. Rebuild with wider CHECK ---
        await conn.execute("BEGIN")
        try:
            await conn.execute(
                f"""
                CREATE TABLE dataset_labels_new (
                    id                   TEXT PRIMARY KEY,
                    dataset_name         TEXT NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
                    dataset_version      TEXT NOT NULL,
                    file_path            TEXT NOT NULL,
                    line_start           INTEGER NOT NULL,
                    line_end             INTEGER NOT NULL,
                    cwe_id               TEXT NOT NULL,
                    vuln_class           TEXT NOT NULL,
                    severity             TEXT NOT NULL,
                    description          TEXT NOT NULL,
                    source               TEXT NOT NULL {check_sql},
                    source_ref           TEXT,
                    confidence           TEXT NOT NULL,
                    created_at           TEXT NOT NULL,
                    notes                TEXT,
                    introduced_in_diff   INTEGER,
                    patch_lines_changed  INTEGER
                )
                """
            )

            await conn.execute(
                "INSERT INTO dataset_labels_new SELECT * FROM dataset_labels"
            )

            await conn.execute("DROP TABLE dataset_labels")
            await conn.execute(
                "ALTER TABLE dataset_labels_new RENAME TO dataset_labels"
            )

            # Recreate indexes
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dataset_labels_dataset "
                "ON dataset_labels(dataset_name, dataset_version)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_dataset_labels_cwe "
                "ON dataset_labels(cwe_id)"
            )

            await conn.execute("COMMIT")
        except Exception:
            await conn.execute("ROLLBACK")
            raise

        await conn.execute("PRAGMA foreign_keys = ON")
