#!/usr/bin/env python3
"""Export an experiment as a portable .secrev.zip bundle.

Usage:
    python scripts/export_experiment.py <experiment_id> [--storage-root /data] \
        [--db /data/coordinator.db] [--out /path/to/output.secrev.zip] \
        [--include-datasets]

Exit codes:
    0 — success
    1 — error (check stderr)
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


async def export(
    experiment_id: str,
    storage_root: Path,
    db_path: Path,
    out_path: Path,
    include_datasets: bool,
) -> Path:
    from sec_review_framework.db import Database
    from sec_review_framework.bundle import write_bundle

    db = Database(db_path)
    await db.init()

    result = write_bundle(
        db,
        storage_root,
        experiment_id,
        include_datasets=include_datasets,
        out_path=out_path,
    )
    logger.info("Bundle written to %s", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Export an experiment bundle")
    parser.add_argument("experiment_id", help="Experiment ID to export")
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
    parser.add_argument(
        "--out",
        default=None,
        help=(
            "Output path for the .secrev.zip bundle "
            "(default: <storage-root>/outputs/<experiment_id>/<experiment_id>.secrev.zip)"
        ),
    )
    parser.add_argument(
        "--include-datasets",
        action="store_true",
        default=False,
        help="Embed dataset repos and labels in the bundle",
    )
    args = parser.parse_args()

    storage_root = Path(args.storage_root)
    db_path = Path(args.db) if args.db else storage_root / "coordinator.db"
    out_path = (
        Path(args.out)
        if args.out
        else storage_root / "outputs" / args.experiment_id / f"{args.experiment_id}.secrev.zip"
    )

    try:
        result = asyncio.run(
            export(
                args.experiment_id,
                storage_root,
                db_path,
                out_path,
                args.include_datasets,
            )
        )
        print(str(result))
        sys.exit(0)
    except Exception as exc:
        logger.error("Export failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
