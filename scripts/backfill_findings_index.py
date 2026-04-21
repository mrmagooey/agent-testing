#!/usr/bin/env python3
"""Backfill the findings index from existing run_result.json files.

Usage:
    python scripts/backfill_findings_index.py [--storage-root /data] [--db /data/coordinator.db]

This script is idempotent: it upserts findings for every run_result.json found
under the outputs directory. Existing rows are replaced so it is safe to re-run
after schema changes or to correct stale data.

Exit codes:
    0 — success
    1 — error (check stderr)
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


async def backfill(storage_root: Path, db_path: Path) -> int:
    """Walk outputs directory and index all findings. Returns count of runs indexed."""
    from sec_review_framework.db import Database
    from sec_review_framework.data.experiment import RunResult

    db = Database(db_path)
    await db.init()

    outputs_dir = storage_root / "outputs"
    if not outputs_dir.exists():
        logger.warning("outputs directory %s does not exist — nothing to backfill", outputs_dir)
        return 0

    indexed = 0
    skipped = 0

    for experiment_dir in sorted(outputs_dir.iterdir()):
        if not experiment_dir.is_dir():
            continue
        experiment_id = experiment_dir.name
        for run_dir in sorted(experiment_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            result_file = run_dir / "run_result.json"
            if not result_file.exists():
                continue
            try:
                result = RunResult.model_validate_json(result_file.read_text())
            except Exception as exc:
                logger.warning("Skipping %s — parse error: %s", result_file, exc)
                skipped += 1
                continue

            if not result.findings:
                continue

            try:
                await db.upsert_findings_for_run(
                    run_id=result.experiment.id,
                    experiment_id=experiment_id,
                    findings=[f.model_dump(mode="json") for f in result.findings],
                    model_id=result.experiment.model_id,
                    strategy=result.experiment.strategy.value,
                    dataset_name=result.experiment.dataset_name,
                )
                indexed += 1
                logger.info(
                    "Indexed %d findings from %s/%s",
                    len(result.findings), experiment_id, result.experiment.id,
                )
            except Exception as exc:
                logger.error("Failed to index run %s/%s: %s", experiment_id, result.experiment.id, exc)
                skipped += 1

    logger.info(
        "Backfill complete: indexed %d runs, skipped %d", indexed, skipped
    )
    return indexed


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill findings index from run_result.json files")
    parser.add_argument(
        "--storage-root",
        default="/data",
        help="Root of the shared storage volume (default: /data)",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="Path to coordinator.db (default: <storage-root>/coordinator.db)",
    )
    args = parser.parse_args()

    storage_root = Path(args.storage_root)
    db_path = Path(args.db) if args.db else storage_root / "coordinator.db"

    try:
        indexed = asyncio.run(backfill(storage_root, db_path))
        print(f"Backfill complete: {indexed} run(s) indexed.", file=sys.stderr)
        sys.exit(0)
    except Exception as exc:
        logger.error("Backfill failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
