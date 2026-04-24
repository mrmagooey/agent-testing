"""SQLite-based persistence for the coordinator service. Uses aiosqlite for async access."""

import aiosqlite
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path


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
        data_sql = (
            f"SELECT f.*, e.config_json AS _experiment_config_json "
            f"FROM findings f "
            f"LEFT JOIN experiments e ON e.id = f.experiment_id "
            f"{where_sql} "
            f"ORDER BY f.{sort_col} {sort_dir} "
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

    async def update_llm_provider(self, provider_id: str, fields: dict) -> None:
        """Partial update. ``fields`` must not include ``id``."""
        if not fields:
            return
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

    async def update_app_settings(self, fields: dict) -> dict:
        """Partial update. Returns the updated row."""
        if fields:
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


def _infer_match_status(finding: dict) -> str | None:
    """Infer match_status from evaluation fields in a raw finding dict."""
    # Check for explicit verified field
    verified = finding.get("verified")
    if verified is True:
        return "tp"
    if verified is False:
        return "fp"
    return None
