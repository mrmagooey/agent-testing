#!/usr/bin/env python3
"""Import an experiment from a .secrev.zip bundle.

Usage:
    python scripts/import_experiment.py /path/to/bundle.secrev.zip \
        [--storage-root /data] [--db /data/coordinator.db] \
        [--conflict-policy reject|rename|merge] \
        [--no-rebuild-findings]

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


async def do_import(
    zip_path: Path,
    storage_root: Path,
    db_path: Path,
    conflict_policy: str,
    rebuild_findings: bool,
) -> dict:
    from sec_review_framework.db import Database
    from sec_review_framework.bundle import apply_bundle, BundleConflictError
    from sec_review_framework.data.experiment import RunResult

    db = Database(db_path)
    await db.init()

    try:
        summary = apply_bundle(
            db,
            storage_root,
            zip_path,
            conflict_policy=conflict_policy,
        )
    except BundleConflictError as exc:
        raise SystemExit(f"Conflict error: {exc}")
    except ValueError as exc:
        raise SystemExit(f"Bundle error: {exc}")

    # Re-index findings
    findings_indexed = 0
    if rebuild_findings:
        exp_id = summary["experiment_id"]
        exp_outputs = storage_root / "outputs" / exp_id
        if exp_outputs.exists():
            for run_dir in exp_outputs.iterdir():
                if not run_dir.is_dir():
                    continue
                result_file = run_dir / "run_result.json"
                if not result_file.exists():
                    continue
                try:
                    result = RunResult.model_validate_json(result_file.read_text())
                    if result.findings:
                        await db.upsert_findings_for_run(
                            run_id=result.experiment.id,
                            experiment_id=exp_id,
                            findings=[f.model_dump(mode="json") for f in result.findings],
                            model_id=result.experiment.model_id,
                            strategy=result.experiment.strategy.value,
                            dataset_name=result.experiment.dataset_name,
                        )
                        findings_indexed += 1
                        logger.info(
                            "Indexed %d findings for run %s",
                            len(result.findings),
                            result.experiment.id,
                        )
                except Exception as exc:
                    logger.warning(
                        "Could not index findings for run in %s: %s", run_dir, exc
                    )

    summary["findings_indexed"] = findings_indexed
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Import an experiment bundle")
    parser.add_argument("bundle", help="Path to the .secrev.zip bundle file")
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
        "--conflict-policy",
        choices=["reject", "rename", "merge"],
        default="reject",
        help="Conflict resolution policy (default: reject)",
    )
    parser.add_argument(
        "--no-rebuild-findings",
        action="store_true",
        default=False,
        help="Skip re-indexing findings after import",
    )
    args = parser.parse_args()

    zip_path = Path(args.bundle)
    storage_root = Path(args.storage_root)
    db_path = Path(args.db) if args.db else storage_root / "coordinator.db"

    try:
        summary = asyncio.run(
            do_import(
                zip_path,
                storage_root,
                db_path,
                args.conflict_policy,
                not args.no_rebuild_findings,
            )
        )
        print(json.dumps(summary, indent=2))
        if summary.get("warnings"):
            for w in summary["warnings"]:
                logger.warning("%s", w)
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as exc:
        logger.error("Import failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
