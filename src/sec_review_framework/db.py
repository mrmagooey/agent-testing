"""SQLite-based persistence for the coordinator service. Uses aiosqlite for async access."""

import aiosqlite
import hashlib
import hmac
import json
import re
import secrets
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING


class UploadTokenAlreadyExists(Exception):
    """Raised by issue_upload_token when a token for run_id already exists."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"Upload token already issued for run {run_id!r}")

if TYPE_CHECKING:
    from sec_review_framework.data.strategy_bundle import UserStrategy


# ---------------------------------------------------------------------------
# Safe FTS query escaping
# ---------------------------------------------------------------------------

def _escape_fts_query(q: str) -> str:
    """Escape a user-supplied query for use with FTS5 MATCH.

    FTS5 supports a rich query syntax; unescaped user input can cause
    parse errors or unexpected behaviour.  We wrap the whole input as a
    phrase query using double-quotes and escape any embedded double-quotes
    by doubling them.  This gives simple substring/phrase search semantics
    without exposing FTS query operators to the user.
    """
    escaped = q.replace('"', '""')
    return f'"{escaped}"'


class Database:
    """Async SQLite database for experiment and run tracking."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    config_json TEXT,
                    status TEXT DEFAULT 'pending',
                    total_runs INTEGER,
                    max_cost_usd REAL,
                    spent_usd REAL DEFAULT 0,
                    created_at TEXT,
                    completed_at TEXT
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    experiment_id TEXT REFERENCES experiments(id),
                    config_json TEXT,
                    status TEXT DEFAULT 'pending',
                    model_id TEXT,
                    strategy TEXT,
                    tool_variant TEXT,
                    review_profile TEXT,
                    verification_variant TEXT,
                    estimated_cost_usd REAL,
                    duration_seconds REAL,
                    result_path TEXT,
                    error TEXT,
                    created_at TEXT,
                    completed_at TEXT
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_experiment_id ON runs(experiment_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_model_id ON runs(model_id)"
            )
            # Idempotent migration: add tool_extensions column to existing DBs.
            try:
                await db.execute(
                    "ALTER TABLE runs ADD COLUMN tool_extensions TEXT DEFAULT ''"
                )
                await db.commit()
            except Exception:
                pass  # Column already exists — safe to ignore

            # ---------------------------------------------------------------------------
            # User strategies table
            # ---------------------------------------------------------------------------
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_strategies (
                    id TEXT PRIMARY KEY,
                    parent_strategy_id TEXT,
                    is_builtin INTEGER NOT NULL DEFAULT 0,
                    orchestration_shape TEXT NOT NULL,
                    name TEXT NOT NULL,
                    bundle_json TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL
                )
            """)

            # ---------------------------------------------------------------------------
            # Findings index table
            # ---------------------------------------------------------------------------
            await db.execute("""
                CREATE TABLE IF NOT EXISTS findings (
                    id TEXT PRIMARY KEY,
                    run_id TEXT REFERENCES runs(id),
                    experiment_id TEXT REFERENCES experiments(id),
                    file_path TEXT,
                    line_start INT,
                    line_end INT,
                    vuln_class TEXT,
                    cwe_ids TEXT,
                    severity TEXT,
                    confidence REAL,
                    title TEXT,
                    description TEXT,
                    match_status TEXT,
                    model_id TEXT,
                    strategy TEXT,
                    dataset_name TEXT,
                    created_at TEXT
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_vuln_class ON findings(vuln_class)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_experiment ON findings(experiment_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_model ON findings(model_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_strategy ON findings(strategy)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_match_status ON findings(match_status)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_findings_created_at ON findings(created_at)"
            )

            # FTS5 virtual table for full-text search across findings
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS findings_fts USING fts5(
                    title,
                    description,
                    vuln_class,
                    cwe_ids,
                    content='findings',
                    content_rowid='rowid'
                )
            """)

            # Triggers to keep FTS in sync
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS findings_fts_ai
                AFTER INSERT ON findings BEGIN
                    INSERT INTO findings_fts(rowid, title, description, vuln_class, cwe_ids)
                    VALUES (new.rowid, new.title, new.description, new.vuln_class, new.cwe_ids);
                END
            """)
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS findings_fts_ad
                AFTER DELETE ON findings BEGIN
                    INSERT INTO findings_fts(findings_fts, rowid, title, description, vuln_class, cwe_ids)
                    VALUES ('delete', old.rowid, old.title, old.description, old.vuln_class, old.cwe_ids);
                END
            """)
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS findings_fts_au
                AFTER UPDATE ON findings BEGIN
                    INSERT INTO findings_fts(findings_fts, rowid, title, description, vuln_class, cwe_ids)
                    VALUES ('delete', old.rowid, old.title, old.description, old.vuln_class, old.cwe_ids);
                    INSERT INTO findings_fts(rowid, title, description, vuln_class, cwe_ids)
                    VALUES (new.rowid, new.title, new.description, new.vuln_class, new.cwe_ids);
                END
            """)

            # ---------------------------------------------------------------------------
            # Upload tokens table (HTTP result transport)
            # ---------------------------------------------------------------------------
            await db.execute("""
                CREATE TABLE IF NOT EXISTS run_upload_tokens (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    token_hash TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    consumed_at TEXT
                )
            """)

            # ---------------------------------------------------------------------------
            # LLM providers (user-configurable)
            # ---------------------------------------------------------------------------
            await db.execute("""
                CREATE TABLE IF NOT EXISTS llm_providers (
                    id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    display_name TEXT NOT NULL,
                    adapter TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    api_base TEXT,
                    api_key_ciphertext BLOB,
                    auth_type TEXT NOT NULL DEFAULT 'api_key',
                    region TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    last_probe_at TEXT,
                    last_probe_status TEXT,
                    last_probe_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_providers_name ON llm_providers(name)"
            )

            # ---------------------------------------------------------------------------
            # App settings — single-row keyed by id=1
            # ---------------------------------------------------------------------------
            await db.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    allow_unavailable_models INTEGER NOT NULL DEFAULT 0,
                    evidence_assessor TEXT NOT NULL DEFAULT 'heuristic',
                    evidence_judge_model TEXT
                )
            """)
            # Ensure the singleton row exists.
            await db.execute("""
                INSERT OR IGNORE INTO app_settings (id, allow_unavailable_models, evidence_assessor, evidence_judge_model)
                VALUES (1, 0, 'heuristic', NULL)
            """)

            # ---------------------------------------------------------------------------
            # Datasets + dataset labels
            # ---------------------------------------------------------------------------
            await db.execute("""
                CREATE TABLE IF NOT EXISTS datasets (
                    name             TEXT PRIMARY KEY,
                    kind             TEXT NOT NULL CHECK (kind IN ('git', 'derived')),
                    origin_url       TEXT,
                    origin_commit    TEXT,
                    origin_ref       TEXT,
                    cve_id           TEXT,
                    base_dataset     TEXT REFERENCES datasets(name),
                    recipe_json      TEXT,
                    metadata_json    TEXT NOT NULL DEFAULT '{}',
                    created_at       TEXT NOT NULL,
                    materialized_at  TEXT,
                    CHECK (
                        (kind = 'git'     AND origin_url IS NOT NULL AND origin_commit IS NOT NULL)
                     OR (kind = 'derived' AND base_dataset IS NOT NULL AND recipe_json IS NOT NULL)
                    )
                )
            """)
            # ---------------------------------------------------------------------------
            # Migration E1: extend datasets.kind to allow 'archive' plus three
            # archive_* columns. SQLite cannot ALTER a CHECK constraint, so the
            # table is rebuilt atomically when the existing DDL doesn't yet
            # mention 'archive'.
            # ---------------------------------------------------------------------------
            async with db.execute(
                "SELECT sql FROM sqlite_schema WHERE type='table' AND name='datasets'"
            ) as _cur:
                _row = await _cur.fetchone()
            _datasets_ddl: str = _row[0] if _row else ""
            if "archive" not in _datasets_ddl:
                await db.execute("PRAGMA foreign_keys = OFF")
                await db.execute("""
                    CREATE TABLE datasets_new (
                        name             TEXT PRIMARY KEY,
                        kind             TEXT NOT NULL CHECK (kind IN ('git', 'derived', 'archive')),
                        origin_url       TEXT,
                        origin_commit    TEXT,
                        origin_ref       TEXT,
                        cve_id           TEXT,
                        base_dataset     TEXT REFERENCES datasets_new(name),
                        recipe_json      TEXT,
                        metadata_json    TEXT NOT NULL DEFAULT '{}',
                        created_at       TEXT NOT NULL,
                        materialized_at  TEXT,
                        archive_url      TEXT,
                        archive_sha256   TEXT,
                        archive_format   TEXT,
                        CHECK (
                            (kind = 'git'     AND origin_url    IS NOT NULL AND origin_commit  IS NOT NULL)
                         OR (kind = 'derived' AND base_dataset  IS NOT NULL AND recipe_json    IS NOT NULL)
                         OR (kind = 'archive' AND archive_url   IS NOT NULL AND archive_sha256 IS NOT NULL
                                              AND archive_format IS NOT NULL)
                        )
                    )
                """)
                await db.execute("""
                    INSERT INTO datasets_new (
                        name, kind, origin_url, origin_commit, origin_ref,
                        cve_id, base_dataset, recipe_json, metadata_json,
                        created_at, materialized_at
                    )
                    SELECT
                        name, kind, origin_url, origin_commit, origin_ref,
                        cve_id, base_dataset, recipe_json, metadata_json,
                        created_at, materialized_at
                    FROM datasets
                """)
                await db.execute("DROP TABLE datasets")
                await db.execute("ALTER TABLE datasets_new RENAME TO datasets")
                await db.execute("PRAGMA foreign_keys = ON")
                await db.commit()
            # ---------------------------------------------------------------------------
            # Migration A1: extend dataset_labels.source CHECK to include 'benchmark'.
            #
            # SQLite cannot ALTER a CHECK constraint; we must do a table-rebuild.
            # We detect whether the migration is needed by inspecting sqlite_schema.
            # ---------------------------------------------------------------------------
            async with db.execute(
                "SELECT sql FROM sqlite_schema WHERE type='table' AND name='dataset_labels'"
            ) as cur:
                row = await cur.fetchone()

            if row is not None and "'benchmark'" not in row[0]:
                # The table exists but its CHECK does not yet include 'benchmark'.
                # Perform the standard rebuild migration.
                #
                # aiosqlite manages its own transaction state, so we commit any
                # pending transaction before running DDL (which implicitly commits
                # in SQLite) then execute the rebuild steps atomically via a
                # fresh inner connection in autocommit mode.
                await db.commit()
                inner_db_path = self.db_path
                async with aiosqlite.connect(inner_db_path, isolation_level=None) as mdb:
                    await mdb.execute("BEGIN")
                    try:
                        await mdb.execute(
                            "ALTER TABLE dataset_labels RENAME TO _dataset_labels_old"
                        )
                        await mdb.execute("""
                            CREATE TABLE dataset_labels (
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
                                source               TEXT NOT NULL CHECK (source IN ('cve_patch','injected','manual','benchmark')),
                                source_ref           TEXT,
                                confidence           TEXT NOT NULL,
                                created_at           TEXT NOT NULL,
                                notes                TEXT,
                                introduced_in_diff   INTEGER,
                                patch_lines_changed  INTEGER
                            )
                        """)
                        await mdb.execute("""
                            INSERT INTO dataset_labels
                            SELECT id, dataset_name, dataset_version, file_path,
                                   line_start, line_end, cwe_id, vuln_class,
                                   severity, description, source, source_ref,
                                   confidence, created_at, notes,
                                   introduced_in_diff, patch_lines_changed
                            FROM _dataset_labels_old
                        """)
                        await mdb.execute("DROP TABLE _dataset_labels_old")
                        await mdb.execute("COMMIT")
                    except Exception:
                        await mdb.execute("ROLLBACK")
                        raise
            elif row is None:
                # Fresh DB — create with the extended CHECK from the start.
                await db.execute("""
                    CREATE TABLE IF NOT EXISTS dataset_labels (
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
                        source               TEXT NOT NULL CHECK (source IN ('cve_patch','injected','manual','benchmark')),
                        source_ref           TEXT,
                        confidence           TEXT NOT NULL,
                        created_at           TEXT NOT NULL,
                        notes                TEXT,
                        introduced_in_diff   INTEGER,
                        patch_lines_changed  INTEGER
                    )
                """)
            # else: table already has 'benchmark' in CHECK — no-op.

            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_dataset_labels_dataset "
                "ON dataset_labels(dataset_name, dataset_version)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_dataset_labels_cwe "
                "ON dataset_labels(cwe_id)"
            )

            # ---------------------------------------------------------------------------
            # Negative labels — "this file is expected clean of CWE-X"
            # ---------------------------------------------------------------------------
            await db.execute("""
                CREATE TABLE IF NOT EXISTS dataset_negative_labels (
                    id              TEXT PRIMARY KEY,
                    dataset_name    TEXT NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
                    dataset_version TEXT NOT NULL,
                    file_path       TEXT NOT NULL,
                    cwe_id          TEXT NOT NULL,
                    vuln_class      TEXT NOT NULL,
                    source          TEXT NOT NULL CHECK (source IN ('benchmark','manual')),
                    source_ref      TEXT,
                    created_at      TEXT NOT NULL,
                    notes           TEXT
                )
            """)
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_dataset_negative_labels_dataset "
                "ON dataset_negative_labels(dataset_name, dataset_version)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_dataset_negative_labels_cwe "
                "ON dataset_negative_labels(cwe_id)"
            )

            # Enable foreign key enforcement for cascade deletes on dataset_labels
            await db.execute("PRAGMA foreign_keys = ON")

            await db.commit()

    async def create_experiment(
        self,
        experiment_id: str,
        config_json: str,
        total_runs: int,
        max_cost_usd: float | None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO experiments (id, config_json, status, total_runs, max_cost_usd, spent_usd, created_at)
                VALUES (?, ?, 'pending', ?, ?, 0, ?)
                """,
                (experiment_id, config_json, total_runs, max_cost_usd, datetime.now(UTC).isoformat()),
            )
            await db.commit()

    async def import_experiment_rows(
        self,
        experiment_row: dict | None,
        run_rows: list[dict],
    ) -> None:
        """Insert imported experiment and run rows in a single transaction.

        Preserves ``created_at``, ``completed_at``, and ``tool_extensions``
        verbatim from the source bundle.  Pass ``experiment_row=None`` for the
        ``merge`` conflict policy (experiment already exists; only runs inserted).
        """
        async with aiosqlite.connect(self.db_path) as db:
            if experiment_row is not None:
                await db.execute(
                    """
                    INSERT INTO experiments (
                        id, config_json, status, total_runs, max_cost_usd,
                        spent_usd, created_at, completed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        experiment_row.get("id"),
                        experiment_row.get("config_json"),
                        experiment_row.get("status", "completed"),
                        experiment_row.get("total_runs"),
                        experiment_row.get("max_cost_usd"),
                        experiment_row.get("spent_usd", 0.0),
                        experiment_row.get("created_at"),
                        experiment_row.get("completed_at"),
                    ),
                )

            for run in run_rows:
                await db.execute(
                    """
                    INSERT INTO runs (
                        id, experiment_id, config_json, status, model_id,
                        strategy, tool_variant, review_profile,
                        verification_variant, estimated_cost_usd,
                        duration_seconds, result_path, error,
                        created_at, completed_at, tool_extensions
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.get("id"),
                        run.get("experiment_id"),
                        run.get("config_json"),
                        run.get("status", "completed"),
                        run.get("model_id"),
                        run.get("strategy"),
                        run.get("tool_variant"),
                        run.get("review_profile"),
                        run.get("verification_variant"),
                        run.get("estimated_cost_usd"),
                        run.get("duration_seconds"),
                        run.get("result_path"),
                        run.get("error"),
                        run.get("created_at"),
                        run.get("completed_at"),
                        run.get("tool_extensions", ""),
                    ),
                )

            await db.commit()

    async def get_experiment(self, experiment_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM experiments WHERE id = ?", (experiment_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_experiments(self) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM experiments ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def update_experiment_status(
        self,
        experiment_id: str,
        status: str,
        completed_at: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE experiments SET status = ?, completed_at = ? WHERE id = ?",
                (status, completed_at, experiment_id),
            )
            await db.commit()

    async def create_run(
        self,
        run_id: str,
        experiment_id: str,
        config_json: str,
        model_id: str,
        strategy: str,
        tool_variant: str,
        review_profile: str,
        verification_variant: str,
        estimated_cost_usd: float | None = None,
        tool_extensions: "frozenset | Iterable[str] | None" = None,
    ) -> None:
        if tool_extensions is None:
            ext_str = ""
        else:
            ext_str = ",".join(sorted(str(e.value if hasattr(e, "value") else e) for e in tool_extensions))
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO runs (
                    id, experiment_id, config_json, status, model_id, strategy,
                    tool_variant, review_profile, verification_variant,
                    estimated_cost_usd, created_at, tool_extensions
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, experiment_id, config_json, model_id, strategy,
                    tool_variant, review_profile, verification_variant,
                    estimated_cost_usd, datetime.now(UTC).isoformat(), ext_str,
                ),
            )
            await db.commit()

    async def update_run(
        self,
        run_id: str,
        status: str,
        duration_seconds: float | None = None,
        result_path: str | None = None,
        error: str | None = None,
        completed_at: str | None = None,
        estimated_cost_usd: float | None = None,
    ) -> None:
        fields = ["status = ?"]
        values: list = [status]

        if duration_seconds is not None:
            fields.append("duration_seconds = ?")
            values.append(duration_seconds)
        if result_path is not None:
            fields.append("result_path = ?")
            values.append(result_path)
        if error is not None:
            fields.append("error = ?")
            values.append(error)
        if completed_at is not None:
            fields.append("completed_at = ?")
            values.append(completed_at)
        if estimated_cost_usd is not None:
            fields.append("estimated_cost_usd = ?")
            values.append(estimated_cost_usd)

        values.append(run_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE runs SET {', '.join(fields)} WHERE id = ?",
                values,
            )
            await db.commit()

    async def get_run(self, run_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_runs(self, experiment_id: str) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM runs WHERE experiment_id = ? ORDER BY created_at",
                (experiment_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def count_runs_by_status(self, experiment_id: str) -> dict[str, int]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT status, COUNT(*) FROM runs WHERE experiment_id = ? GROUP BY status",
                (experiment_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}

    async def add_experiment_spend(self, experiment_id: str, amount_usd: float) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE experiments SET spent_usd = spent_usd + ? WHERE id = ?",
                (amount_usd, experiment_id),
            )
            await db.commit()

    async def get_experiment_spend(self, experiment_id: str) -> float:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT spent_usd FROM experiments WHERE id = ?", (experiment_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0.0

    # ---------------------------------------------------------------------------
    # Findings index
    # ---------------------------------------------------------------------------

    async def upsert_findings_for_run(
        self,
        run_id: str,
        experiment_id: str,
        findings: list[dict],
        model_id: str,
        strategy: str,
        dataset_name: str,
    ) -> None:
        """Idempotent bulk upsert of findings for a run.

        Deletes all existing rows for the run then inserts fresh rows, so
        repeated calls are safe (e.g. after reclassification).
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Delete existing findings for this run (triggers clean FTS)
            await db.execute("DELETE FROM findings WHERE run_id = ?", (run_id,))

            for f in findings:
                cwe_ids_raw = f.get("cwe_ids") or []
                if isinstance(cwe_ids_raw, list):
                    cwe_ids_str = json.dumps(cwe_ids_raw)
                else:
                    cwe_ids_str = str(cwe_ids_raw)

                # Determine match_status from evaluation fields if present
                match_status = f.get("match_status") or _infer_match_status(f)

                await db.execute(
                    """
                    INSERT INTO findings (
                        id, run_id, experiment_id, file_path, line_start, line_end,
                        vuln_class, cwe_ids, severity, confidence, title, description,
                        match_status, model_id, strategy, dataset_name, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f.get("id", ""),
                        run_id,
                        experiment_id,
                        f.get("file_path"),
                        f.get("line_start"),
                        f.get("line_end"),
                        f.get("vuln_class") or "",
                        cwe_ids_str,
                        f.get("severity") or "",
                        f.get("confidence"),
                        f.get("title") or "",
                        f.get("description") or "",
                        match_status,
                        model_id,
                        strategy,
                        dataset_name,
                        datetime.now(UTC).isoformat(),
                    ),
                )
            await db.commit()

    async def update_finding_match_status(
        self, finding_id: str, match_status: str
    ) -> None:
        """Update a single finding's match_status after reclassification."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE findings SET match_status = ? WHERE id = ?",
                (match_status, finding_id),
            )
            await db.commit()

    async def count_all_findings(self) -> int:
        """Return total number of indexed findings."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM findings") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def query_findings(
        self,
        filters: dict,
        limit: int = 50,
        offset: int = 0,
        sort: str = "created_at desc",
    ) -> tuple[int, list[dict]]:
        """Search findings with optional FTS and filter facets.

        Returns (total_count, rows).

        ``filters`` keys:
          q, vuln_class, severity, match_status, model_id, strategy,
          experiment_id, dataset_name, created_from, created_to
        All list-valued filters use IN logic.  ``q`` triggers FTS MATCH.
        """
        # Validate / allow-list sort column and direction to prevent injection
        _SORTABLE = {
            "created_at", "severity", "vuln_class", "match_status",
            "model_id", "strategy", "confidence",
        }
        sort_parts = sort.strip().lower().split()
        sort_col = sort_parts[0] if sort_parts else "created_at"
        sort_dir = sort_parts[1] if len(sort_parts) > 1 else "desc"
        if sort_col not in _SORTABLE:
            sort_col = "created_at"
        if sort_dir not in ("asc", "desc"):
            sort_dir = "desc"

        where_clauses: list[str] = []
        params: list = []

        # FTS path: join against findings_fts virtual table
        use_fts = bool(filters.get("q"))
        if use_fts:
            safe_q = _escape_fts_query(str(filters["q"]))
            where_clauses.append(
                "f.rowid IN (SELECT rowid FROM findings_fts WHERE findings_fts MATCH ?)"
            )
            params.append(safe_q)

        # List filters (multi-value IN)
        for col in ("vuln_class", "severity", "match_status", "model_id",
                    "strategy", "experiment_id", "dataset_name"):
            vals = filters.get(col)
            if vals:
                if isinstance(vals, str):
                    vals = [vals]
                placeholders = ",".join("?" * len(vals))
                where_clauses.append(f"f.{col} IN ({placeholders})")
                params.extend(vals)

        # Date range filters
        if filters.get("created_from"):
            where_clauses.append("f.created_at >= ?")
            params.append(filters["created_from"])
        if filters.get("created_to"):
            where_clauses.append("f.created_at <= ?")
            params.append(filters["created_to"])

        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        count_sql = f"SELECT COUNT(*) FROM findings f {where_sql}"

        # Build ORDER BY expression.  Severity has a semantic rank that does not
        # match alphabetical order, so we map it to an integer rank.  The ranks
        # are assigned so that DESC puts critical (highest severity) first and
        # ASC puts low (lowest severity) first, matching user expectations:
        #   critical=3, high=2, medium=1, low=0, unknown/other=-1
        if sort_col == "severity":
            order_expr = (
                f"CASE f.severity "
                f"WHEN 'critical' THEN 3 "
                f"WHEN 'high'     THEN 2 "
                f"WHEN 'medium'   THEN 1 "
                f"WHEN 'low'      THEN 0 "
                f"ELSE -1 END {sort_dir}"
            )
        else:
            order_expr = f"f.{sort_col} {sort_dir}"

        data_sql = (
            f"SELECT f.*, e.config_json AS _experiment_config_json "
            f"FROM findings f "
            f"LEFT JOIN experiments e ON e.id = f.experiment_id "
            f"{where_sql} "
            f"ORDER BY {order_expr} "
            f"LIMIT ? OFFSET ?"
        )

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(count_sql, params) as cursor:
                total_row = await cursor.fetchone()
                total = total_row[0] if total_row else 0

            async with db.execute(data_sql, params + [limit, offset]) as cursor:
                rows = await cursor.fetchall()

        # Enrich each row with experiment_name from experiment config_json
        result = []
        for row in rows:
            d = dict(row)
            config_json = d.pop("_experiment_config_json", None)
            experiment_name = d.get("experiment_id", "")
            if config_json:
                try:
                    cfg = json.loads(config_json)
                    experiment_name = cfg.get("experiment_id") or experiment_name
                except Exception:
                    pass
            d["experiment_name"] = experiment_name
            # Parse cwe_ids back to list for API consumers
            try:
                d["cwe_ids"] = json.loads(d.get("cwe_ids") or "[]")
            except Exception:
                d["cwe_ids"] = []
            result.append(d)

        return total, result

    async def facet_findings(self, filters: dict) -> dict:
        """Return per-facet counts, each excluding its own filter.

        Returns a dict like:
          { "vuln_class": {"sqli": 5, "xss": 2}, "severity": {...}, ... }
        """
        facet_columns = [
            "vuln_class", "severity", "match_status",
            "model_id", "strategy", "dataset_name",
        ]
        result: dict[str, dict] = {}
        async with aiosqlite.connect(self.db_path) as db:
            for facet_col in facet_columns:
                # Build WHERE excluding the facet's own filter
                where_clauses: list[str] = []
                params: list = []

                if filters.get("q"):
                    safe_q = _escape_fts_query(str(filters["q"]))
                    where_clauses.append(
                        "f.rowid IN (SELECT rowid FROM findings_fts WHERE findings_fts MATCH ?)"
                    )
                    params.append(safe_q)

                for col in ("vuln_class", "severity", "match_status", "model_id",
                            "strategy", "experiment_id", "dataset_name"):
                    if col == facet_col:
                        continue  # Exclude this facet's own filter
                    vals = filters.get(col)
                    if vals:
                        if isinstance(vals, str):
                            vals = [vals]
                        placeholders = ",".join("?" * len(vals))
                        where_clauses.append(f"f.{col} IN ({placeholders})")
                        params.extend(vals)

                if filters.get("created_from"):
                    where_clauses.append("f.created_at >= ?")
                    params.append(filters["created_from"])
                if filters.get("created_to"):
                    where_clauses.append("f.created_at <= ?")
                    params.append(filters["created_to"])

                where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
                sql = (
                    f"SELECT f.{facet_col}, COUNT(*) "
                    f"FROM findings f {where_sql} "
                    f"GROUP BY f.{facet_col} "
                    f"ORDER BY COUNT(*) DESC"
                )
                async with db.execute(sql, params) as cursor:
                    rows = await cursor.fetchall()
                result[facet_col] = {row[0]: row[1] for row in rows if row[0]}

        return result

    # ---------------------------------------------------------------------------
    # LLM providers CRUD
    # ---------------------------------------------------------------------------

    async def create_llm_provider(self, row: dict) -> None:
        """Insert a new row into llm_providers. ``row`` must have all required fields."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO llm_providers (
                    id, name, display_name, adapter, model_id, api_base,
                    api_key_ciphertext, auth_type, region, enabled,
                    last_probe_at, last_probe_status, last_probe_error,
                    created_at, updated_at
                ) VALUES (
                    :id, :name, :display_name, :adapter, :model_id, :api_base,
                    :api_key_ciphertext, :auth_type, :region, :enabled,
                    :last_probe_at, :last_probe_status, :last_probe_error,
                    :created_at, :updated_at
                )
                """,
                row,
            )
            await db.commit()

    async def get_llm_provider(self, provider_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM llm_providers WHERE id = ?", (provider_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_llm_provider_by_name(self, name: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM llm_providers WHERE name = ?", (name,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_llm_providers(self) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM llm_providers ORDER BY created_at"
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # Allowlist of columns that callers may update in llm_providers.
    _LLM_PROVIDER_UPDATABLE_COLS: frozenset[str] = frozenset({
        "display_name",
        "adapter",
        "model_id",
        "api_base",
        "api_key_ciphertext",
        "auth_type",
        "region",
        "enabled",
        "last_probe_at",
        "last_probe_status",
        "last_probe_error",
        "updated_at",
    })

    async def update_llm_provider(self, provider_id: str, fields: dict) -> None:
        """Partial update. ``fields`` must not include ``id``."""
        if not fields:
            return
        unknown = set(fields) - self._LLM_PROVIDER_UPDATABLE_COLS
        if unknown:
            raise ValueError(
                f"update_llm_provider: unknown column(s): {sorted(unknown)}. "
                f"Allowed: {sorted(self._LLM_PROVIDER_UPDATABLE_COLS)}"
            )
        set_clauses = ", ".join(f"{k} = :{k}" for k in fields)
        fields["_id"] = provider_id
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE llm_providers SET {set_clauses} WHERE id = :_id",
                fields,
            )
            await db.commit()

    async def delete_llm_provider(self, provider_id: str) -> bool:
        """Hard delete. Returns True if a row was deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM llm_providers WHERE id = ?", (provider_id,)
            )
            await db.commit()
            return cursor.rowcount > 0

    # ---------------------------------------------------------------------------
    # App settings
    # ---------------------------------------------------------------------------

    async def get_app_settings(self) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM app_settings WHERE id = 1") as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return {
                        "allow_unavailable_models": False,
                        "evidence_assessor": "heuristic",
                        "evidence_judge_model": None,
                    }
                d = dict(row)
                d.pop("id", None)
                d["allow_unavailable_models"] = bool(d["allow_unavailable_models"])
                return d

    # Allowlist of columns that callers may update in app_settings.
    _APP_SETTINGS_UPDATABLE_COLS: frozenset[str] = frozenset({
        "allow_unavailable_models",
        "evidence_assessor",
        "evidence_judge_model",
    })

    async def update_app_settings(self, fields: dict) -> dict:
        """Partial update. Returns the updated row."""
        if fields:
            unknown = set(fields) - self._APP_SETTINGS_UPDATABLE_COLS
            if unknown:
                raise ValueError(
                    f"update_app_settings: unknown column(s): {sorted(unknown)}. "
                    f"Allowed: {sorted(self._APP_SETTINGS_UPDATABLE_COLS)}"
                )
            # Coerce bool → int for SQLite
            row = dict(fields)
            if "allow_unavailable_models" in row:
                row["allow_unavailable_models"] = int(bool(row["allow_unavailable_models"]))
            set_clauses = ", ".join(f"{k} = :{k}" for k in row)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    f"UPDATE app_settings SET {set_clauses} WHERE id = 1",
                    row,
                )
                await db.commit()
        return await self.get_app_settings()


    # ---------------------------------------------------------------------------
    # User strategies
    # ---------------------------------------------------------------------------

    async def insert_user_strategy(self, strategy: "UserStrategy") -> None:
        """Persist a UserStrategy to the database.

        Serialises the full UserStrategy via canonical_json so round-trips
        via get_user_strategy reconstruct the complete object.
        """
        from sec_review_framework.data.strategy_bundle import canonical_json

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO user_strategies
                    (id, parent_strategy_id, is_builtin, orchestration_shape,
                     name, bundle_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    strategy.id,
                    strategy.parent_strategy_id,
                    1 if strategy.is_builtin else 0,
                    strategy.orchestration_shape.value,
                    strategy.name,
                    canonical_json(strategy),
                    strategy.created_at.isoformat(),
                ),
            )
            await db.commit()

    async def get_user_strategy(self, strategy_id: str) -> "UserStrategy | None":
        """Return the UserStrategy with *strategy_id*, or None if not found."""
        from sec_review_framework.data.strategy_bundle import UserStrategy

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT bundle_json FROM user_strategies WHERE id = ?",
                (strategy_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                return UserStrategy.model_validate_json(row["bundle_json"])

    async def list_user_strategies(self) -> "list[UserStrategy]":
        """Return all UserStrategy objects, ordered by created_at then id."""
        from sec_review_framework.data.strategy_bundle import UserStrategy

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT bundle_json FROM user_strategies ORDER BY created_at, id"
            ) as cursor:
                rows = await cursor.fetchall()
                return [UserStrategy.model_validate_json(row["bundle_json"]) for row in rows]

    async def delete_user_strategy(self, strategy_id: str) -> bool:
        """Hard-delete the strategy with *strategy_id*.

        Returns True if a row was deleted, False if no such strategy existed.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "DELETE FROM user_strategies WHERE id = ?",
                (strategy_id,),
            ) as cursor:
                deleted = cursor.rowcount > 0
            await db.commit()
            return deleted

    async def strategy_is_referenced_by_runs(self, strategy_id: str) -> bool:
        """Return True if any experiment's config references *strategy_id*.

        Strategies are listed in experiments.config_json under the
        "strategy_ids" array. We probe that JSON via SQLite's JSON1 extension.

        If the JSON1 path doesn't exist (e.g. config_json doesn't have a
        ``strategy_ids`` key, or the JSON parse fails), ``json_extract`` returns
        NULL and ``json_each`` over NULL yields no rows — so the check correctly
        returns False.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                SELECT EXISTS(
                    SELECT 1 FROM experiments
                    WHERE EXISTS(
                        SELECT 1 FROM json_each(
                            json_extract(config_json, '$.strategy_ids')
                        )
                        WHERE value = ?
                    )
                )
                """,
                (strategy_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return bool(row[0]) if row else False

    # ---------------------------------------------------------------------------
    # Upload tokens (HTTP result transport)
    # ---------------------------------------------------------------------------

    async def issue_upload_token(self, run_id: str) -> str:
        """Generate a fresh bearer token for *run_id*, store its SHA-256 hash.

        Returns the plaintext token (32 URL-safe bytes).  The token is stored
        hashed; the plaintext is never persisted.

        Raises:
            UploadTokenAlreadyExists: if a token for *run_id* already exists.
                Callers must not rely on "issue or get existing" semantics — call
                get_upload_token_issued() first if re-issue must be skipped.
        """
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        issued_at = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                INSERT INTO run_upload_tokens (run_id, token_hash, issued_at, consumed_at)
                VALUES (?, ?, ?, NULL)
                ON CONFLICT(run_id) DO NOTHING
                """,
                (run_id, token_hash, issued_at),
            ) as cursor:
                rowcount = cursor.rowcount
            await db.commit()
        if rowcount == 0:
            raise UploadTokenAlreadyExists(run_id)
        return token

    async def consume_upload_token(self, run_id: str, token: str) -> bool:
        """Atomically mark the token as consumed if it matches and was not yet used.

        Returns True if the token was valid and is now consumed; False otherwise.
        Uses timing-safe comparison via ``hmac.compare_digest``.
        """
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        async with aiosqlite.connect(self.db_path) as db:
            # Fetch the stored hash without side-effects first so we can do a
            # timing-safe comparison in Python (SQLite's = is not timing-safe).
            async with db.execute(
                "SELECT token_hash FROM run_upload_tokens WHERE run_id = ? AND consumed_at IS NULL",
                (run_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if row is None:
                return False

            stored_hash: str = row[0]
            if not hmac.compare_digest(stored_hash, token_hash):
                return False

            # Atomically mark as consumed; the WHERE guards against races.
            consumed_at = datetime.now(UTC).isoformat()
            async with db.execute(
                """
                UPDATE run_upload_tokens
                SET consumed_at = ?
                WHERE run_id = ? AND consumed_at IS NULL AND token_hash = ?
                """,
                (consumed_at, run_id, token_hash),
            ) as cursor:
                updated = cursor.rowcount

            await db.commit()
            return updated > 0

    async def get_upload_token_issued(self, run_id: str) -> bool:
        """Return True if a token has been issued for *run_id* (consumed or not)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT 1 FROM run_upload_tokens WHERE run_id = ?",
                (run_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return row is not None

    async def is_upload_token_consumed(self, run_id: str) -> bool:
        """Return True if the token for *run_id* has already been consumed."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT consumed_at FROM run_upload_tokens WHERE run_id = ?",
                (run_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return False
                return row[0] is not None

    async def revoke_upload_tokens_for_experiment(self, experiment_id: str) -> int:
        """Delete all upload tokens for runs belonging to *experiment_id*.

        Returns the number of tokens deleted.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                """
                DELETE FROM run_upload_tokens
                WHERE run_id IN (
                    SELECT id FROM runs WHERE experiment_id = ?
                )
                """,
                (experiment_id,),
            ) as cursor:
                deleted = cursor.rowcount
            await db.commit()
            return deleted

    async def delete_experiment(self, experiment_id: str) -> None:
        """Remove all DB rows for *experiment_id* in a single connection.

        Deletes child rows before the parent to satisfy foreign-key ordering:
          1. findings (references both runs and experiments)
          2. run_upload_tokens (references runs)
          3. runs (references experiments)
          4. experiments

        The findings_fts_ad trigger fires for each deleted finding row, so
        the FTS index stays consistent without any extra work here.

        Safe to call for a non-existent experiment_id — the DELETEs are
        no-ops and no error is raised.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM findings WHERE experiment_id = ?",
                (experiment_id,),
            )
            await db.execute(
                """
                DELETE FROM run_upload_tokens
                WHERE run_id IN (
                    SELECT id FROM runs WHERE experiment_id = ?
                )
                """,
                (experiment_id,),
            )
            await db.execute(
                "DELETE FROM runs WHERE experiment_id = ?",
                (experiment_id,),
            )
            await db.execute(
                "DELETE FROM experiments WHERE id = ?",
                (experiment_id,),
            )
            await db.commit()

    # ---------------------------------------------------------------------------
    # Datasets
    # ---------------------------------------------------------------------------

    async def create_dataset(self, row: dict) -> None:
        """Insert a new dataset row. Raises ``sqlite3.IntegrityError`` on
        duplicate name or CHECK violation. Caller must supply: name, kind,
        created_at, plus kind-appropriate fields (origin_url+origin_commit for
        'git'; base_dataset+recipe_json for 'derived'). metadata_json defaults
        to '{}' if absent.

        Note: :meth:`import_datasets` also raises on collision, but surfaces
        the error as a plain ``Exception`` with a descriptive message (not
        necessarily ``sqlite3.IntegrityError``) to decouple callers from the
        storage engine.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            await db.execute(
                """
                INSERT INTO datasets (
                    name, kind, origin_url, origin_commit, origin_ref,
                    cve_id, base_dataset, recipe_json, metadata_json,
                    created_at, materialized_at,
                    archive_url, archive_sha256, archive_format
                ) VALUES (
                    :name, :kind, :origin_url, :origin_commit, :origin_ref,
                    :cve_id, :base_dataset, :recipe_json, :metadata_json,
                    :created_at, :materialized_at,
                    :archive_url, :archive_sha256, :archive_format
                )
                """,
                {
                    "name": row["name"],
                    "kind": row["kind"],
                    "origin_url": row.get("origin_url"),
                    "origin_commit": row.get("origin_commit"),
                    "origin_ref": row.get("origin_ref"),
                    "cve_id": row.get("cve_id"),
                    "base_dataset": row.get("base_dataset"),
                    "recipe_json": row.get("recipe_json"),
                    "metadata_json": row.get("metadata_json", "{}"),
                    "created_at": row["created_at"],
                    "materialized_at": row.get("materialized_at"),
                    "archive_url": row.get("archive_url"),
                    "archive_sha256": row.get("archive_sha256"),
                    "archive_format": row.get("archive_format"),
                },
            )
            await db.commit()

    async def get_dataset(self, name: str) -> dict | None:
        """Return the row as a dict (None if missing). Keys mirror columns."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM datasets WHERE name = ?", (name,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_datasets(self) -> list[dict]:
        """All rows, ordered by created_at desc."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM datasets ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def update_dataset_materialized_at(self, name: str, ts: str) -> None:
        """Set materialized_at = ts for the named dataset. No-op if missing."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE datasets SET materialized_at = ? WHERE name = ?",
                (ts, name),
            )
            await db.commit()

    async def import_datasets(
        self, rows: list[dict], *, conflict_policy: str
    ) -> list[str]:
        """Insert N dataset rows in one transaction. conflict_policy in
        {"reject","rename","merge"}:
          - reject: raise on any name collision (no rows inserted)
          - rename: append "_imported_<8-char-uuid>" suffix on collisions; rewrite
            any base_dataset references in the same batch that pointed at a
            renamed name. Returns the final names in input order.
          - merge: skip rows whose name already exists; insert the rest. Returns
            the final names (existing-name preserved for skipped rows) in input
            order.
        Returns the list of final names so the caller can rewrite references."""
        if conflict_policy not in ("reject", "rename", "merge"):
            raise ValueError(
                f"import_datasets: invalid conflict_policy {conflict_policy!r}. "
                "Must be one of: reject, rename, merge"
            )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")

            # Fetch all existing dataset names for collision detection.
            async with db.execute("SELECT name FROM datasets") as cursor:
                existing_names: set[str] = {r[0] for r in await cursor.fetchall()}

            # Build a mapping from original names → final names for the batch.
            # We also track names used within the batch itself to catch intra-batch dups.
            original_to_final: dict[str, str] = {}
            names_used_in_batch: set[str] = set()

            for r in rows:
                orig = r["name"]
                if orig in existing_names or orig in names_used_in_batch:
                    if conflict_policy == "reject":
                        raise Exception(
                            f"import_datasets(reject): dataset {orig!r} already exists"
                        )
                    elif conflict_policy == "rename":
                        suffix = uuid.uuid4().hex[:8]
                        new_name = f"{orig}_imported_{suffix}"
                        # Ensure the new name is also not taken.
                        while new_name in existing_names or new_name in names_used_in_batch:
                            suffix = uuid.uuid4().hex[:8]
                            new_name = f"{orig}_imported_{suffix}"
                        original_to_final[orig] = new_name
                        names_used_in_batch.add(new_name)
                    else:
                        # merge: keep original name; row will be skipped
                        original_to_final[orig] = orig
                        names_used_in_batch.add(orig)
                else:
                    original_to_final[orig] = orig
                    names_used_in_batch.add(orig)

            for r in rows:
                orig = r["name"]
                final_name = original_to_final[orig]

                if conflict_policy == "merge" and orig in existing_names:
                    # Skip rows that already exist.
                    continue

                # Rewrite base_dataset if it referred to a renamed original name.
                base_dataset = r.get("base_dataset")
                if base_dataset is not None and base_dataset in original_to_final:
                    base_dataset = original_to_final[base_dataset]

                await db.execute(
                    """
                    INSERT INTO datasets (
                        name, kind, origin_url, origin_commit, origin_ref,
                        cve_id, base_dataset, recipe_json, metadata_json,
                        created_at, materialized_at
                    ) VALUES (
                        :name, :kind, :origin_url, :origin_commit, :origin_ref,
                        :cve_id, :base_dataset, :recipe_json, :metadata_json,
                        :created_at, :materialized_at
                    )
                    """,
                    {
                        "name": final_name,
                        "kind": r["kind"],
                        "origin_url": r.get("origin_url"),
                        "origin_commit": r.get("origin_commit"),
                        "origin_ref": r.get("origin_ref"),
                        "cve_id": r.get("cve_id"),
                        "base_dataset": base_dataset,
                        "recipe_json": r.get("recipe_json"),
                        "metadata_json": r.get("metadata_json", "{}"),
                        "created_at": r["created_at"],
                        "materialized_at": r.get("materialized_at"),
                    },
                )

            await db.commit()

        return [original_to_final[r["name"]] for r in rows]

    # ---------------------------------------------------------------------------
    # Dataset labels
    # ---------------------------------------------------------------------------

    async def append_dataset_labels(self, rows: list[dict]) -> None:
        """INSERT OR IGNORE on PK (id). Single transaction. Idempotent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            for row in rows:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO dataset_labels (
                        id, dataset_name, dataset_version, file_path,
                        line_start, line_end, cwe_id, vuln_class, severity,
                        description, source, source_ref, confidence,
                        created_at, notes, introduced_in_diff, patch_lines_changed
                    ) VALUES (
                        :id, :dataset_name, :dataset_version, :file_path,
                        :line_start, :line_end, :cwe_id, :vuln_class, :severity,
                        :description, :source, :source_ref, :confidence,
                        :created_at, :notes, :introduced_in_diff, :patch_lines_changed
                    )
                    """,
                    {
                        "id": row["id"],
                        "dataset_name": row["dataset_name"],
                        "dataset_version": row["dataset_version"],
                        "file_path": row["file_path"],
                        "line_start": row["line_start"],
                        "line_end": row["line_end"],
                        "cwe_id": row["cwe_id"],
                        "vuln_class": row["vuln_class"],
                        "severity": row["severity"],
                        "description": row["description"],
                        "source": row["source"],
                        "source_ref": row.get("source_ref"),
                        "confidence": row["confidence"],
                        "created_at": row["created_at"],
                        "notes": row.get("notes"),
                        "introduced_in_diff": row.get("introduced_in_diff"),
                        "patch_lines_changed": row.get("patch_lines_changed"),
                    },
                )
            await db.commit()

    async def list_dataset_labels(
        self,
        name: str,
        version: str | None = None,
        cwe: str | None = None,
        severity: str | None = None,
        source: str | None = None,
    ) -> list[dict]:
        """List labels for a dataset, optionally filtered. Returns dict rows
        matching column names. Use parameterised SQL — no string interpolation."""
        where_clauses: list[str] = ["dataset_name = ?"]
        params: list = [name]

        if version is not None:
            where_clauses.append("dataset_version = ?")
            params.append(version)
        if cwe is not None:
            where_clauses.append("cwe_id = ?")
            params.append(cwe)
        if severity is not None:
            where_clauses.append("severity = ?")
            params.append(severity)
        if source is not None:
            where_clauses.append("source = ?")
            params.append(source)

        where_sql = " AND ".join(where_clauses)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM dataset_labels WHERE {where_sql}",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # ---------------------------------------------------------------------------
    # Dataset negative labels
    # ---------------------------------------------------------------------------

    async def append_dataset_negative_labels(self, rows: list[dict]) -> None:
        """INSERT OR IGNORE on PK (id). Single transaction. Idempotent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys = ON")
            for row in rows:
                await db.execute(
                    """
                    INSERT OR IGNORE INTO dataset_negative_labels (
                        id, dataset_name, dataset_version, file_path,
                        cwe_id, vuln_class, source, source_ref,
                        created_at, notes
                    ) VALUES (
                        :id, :dataset_name, :dataset_version, :file_path,
                        :cwe_id, :vuln_class, :source, :source_ref,
                        :created_at, :notes
                    )
                    """,
                    {
                        "id": row["id"],
                        "dataset_name": row["dataset_name"],
                        "dataset_version": row["dataset_version"],
                        "file_path": row["file_path"],
                        "cwe_id": row["cwe_id"],
                        "vuln_class": row["vuln_class"],
                        "source": row["source"],
                        "source_ref": row.get("source_ref"),
                        "created_at": row["created_at"],
                        "notes": row.get("notes"),
                    },
                )
            await db.commit()

    async def list_dataset_negative_labels(
        self,
        dataset_name: str,
        dataset_version: str | None = None,
    ) -> list[dict]:
        """List negative labels for a dataset, optionally filtered by version.

        Returns dict rows keyed by column name. If dataset_version is None,
        all versions are returned.
        """
        where_clauses: list[str] = ["dataset_name = ?"]
        params: list = [dataset_name]

        if dataset_version is not None:
            where_clauses.append("dataset_version = ?")
            params.append(dataset_version)

        where_sql = " AND ".join(where_clauses)

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"SELECT * FROM dataset_negative_labels WHERE {where_sql}",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]


def _infer_match_status(finding: dict) -> str | None:
    """Infer match_status from evaluation fields in a raw finding dict."""
    # Check for explicit verified field
    verified = finding.get("verified")
    if verified is True:
        return "tp"
    if verified is False:
        return "fp"
    return None
