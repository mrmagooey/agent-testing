"""SQLite-based persistence for the coordinator service. Uses aiosqlite for async access."""

import aiosqlite
from datetime import datetime
from pathlib import Path


class Database:
    """Async SQLite database for batch and run tracking."""

    def __init__(self, db_path: Path):
        self.db_path = db_path

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS batches (
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
                    batch_id TEXT REFERENCES batches(id),
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
                "CREATE INDEX IF NOT EXISTS idx_runs_batch_id ON runs(batch_id)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_runs_model_id ON runs(model_id)"
            )
            await db.commit()

    async def create_batch(
        self,
        batch_id: str,
        config_json: str,
        total_runs: int,
        max_cost_usd: float | None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO batches (id, config_json, status, total_runs, max_cost_usd, spent_usd, created_at)
                VALUES (?, ?, 'pending', ?, ?, 0, ?)
                """,
                (batch_id, config_json, total_runs, max_cost_usd, datetime.utcnow().isoformat()),
            )
            await db.commit()

    async def get_batch(self, batch_id: str) -> dict | None:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM batches WHERE id = ?", (batch_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_batches(self) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM batches ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def update_batch_status(
        self,
        batch_id: str,
        status: str,
        completed_at: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE batches SET status = ?, completed_at = ? WHERE id = ?",
                (status, completed_at, batch_id),
            )
            await db.commit()

    async def create_run(
        self,
        run_id: str,
        batch_id: str,
        config_json: str,
        model_id: str,
        strategy: str,
        tool_variant: str,
        review_profile: str,
        verification_variant: str,
        estimated_cost_usd: float | None = None,
    ) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO runs (
                    id, batch_id, config_json, status, model_id, strategy,
                    tool_variant, review_profile, verification_variant,
                    estimated_cost_usd, created_at
                ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, batch_id, config_json, model_id, strategy,
                    tool_variant, review_profile, verification_variant,
                    estimated_cost_usd, datetime.utcnow().isoformat(),
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

    async def list_runs(self, batch_id: str) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM runs WHERE batch_id = ? ORDER BY created_at",
                (batch_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def count_runs_by_status(self, batch_id: str) -> dict[str, int]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT status, COUNT(*) FROM runs WHERE batch_id = ? GROUP BY status",
                (batch_id,),
            ) as cursor:
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}

    async def add_batch_spend(self, batch_id: str, amount_usd: float) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE batches SET spent_usd = spent_usd + ? WHERE id = ?",
                (amount_usd, batch_id),
            )
            await db.commit()

    async def get_batch_spend(self, batch_id: str) -> float:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT spent_usd FROM batches WHERE id = ?", (batch_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0.0
