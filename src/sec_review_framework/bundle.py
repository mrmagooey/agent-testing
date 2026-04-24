"""Experiment bundle helpers: export/import of experiments as portable ZIP archives.

Bundle layout (ZIP, deflate for JSON/MD, store for .jsonl >1 MiB):
    manifest.json           — metadata and inventory
    experiment.json         — experiments DB row
    runs.json               — runs DB rows array
    outputs/matrix_report.{json,md}
    outputs/runs/<run_id>/  — per-run artifacts
    config/runs/<run_id>.json
    datasets/<name>/…       — only when include_datasets=True

Schema version is 1.  Importers MUST reject unknown schema_version values.

Architecture:
  - ``async_write_bundle`` / ``async_apply_bundle`` — fully async, safe to
    await from within an async context (e.g. FastAPI route, pytest-asyncio).
  - ``write_bundle`` / ``apply_bundle`` — sync entry points that call
    ``asyncio.run()``; only usable outside a running event loop
    (scripts, CLI tools).
  - All file I/O is synchronous and streaming (never buffers whole files).
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

# Streaming buffer size: 64 KiB
_BUFSIZE = 64 * 1024

# Threshold above which .jsonl files are stored without compression (bytes)
_JSONL_STORE_THRESHOLD = 1024 * 1024  # 1 MiB

# Maximum aggregate uncompressed bytes allowed during bundle extraction.
# Configurable via BUNDLE_EXTRACT_MAX_BYTES env var; default 2 GiB.
_BUNDLE_EXTRACT_MAX_BYTES: int = int(
    os.environ.get("BUNDLE_EXTRACT_MAX_BYTES", str(2 * 1024 * 1024 * 1024))
)

# Maximum allowed upload size for imported bundles.
# Configurable via BUNDLE_UPLOAD_MAX_BYTES env var; default 2 GiB.
_BUNDLE_UPLOAD_MAX_BYTES: int = int(
    os.environ.get("BUNDLE_UPLOAD_MAX_BYTES", str(2 * 1024 * 1024 * 1024))
)

SCHEMA_VERSION = 1
BUNDLE_KIND = "experiment"


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------

class BundleConflictError(Exception):
    """Raised when a conflict policy check fails (maps to HTTP 409)."""
    pass


# ---------------------------------------------------------------------------
# Path-traversal guard
# ---------------------------------------------------------------------------

def _check_zip_entry(member: str) -> None:
    """Raise ValueError if *member* contains path-traversal components."""
    # Check for literal ".." in the raw path segments before normalization
    raw_parts = member.replace("\\", "/").split("/")
    if ".." in raw_parts:
        raise ValueError(f"Path traversal detected in zip entry: {member!r}")
    # Also check that normalized form doesn't escape (belt-and-suspenders)
    resolved = posixpath.normpath("/" + member)
    parts = PurePosixPath(resolved).parts
    if ".." in parts:
        raise ValueError(f"Path traversal detected in zip entry: {member!r}")


# ---------------------------------------------------------------------------
# Low-level streaming helper
# ---------------------------------------------------------------------------

def _stream_file_into_zip(
    zf: zipfile.ZipFile,
    arcname: str,
    src_path: Path,
    compress_type: int,
) -> None:
    """Stream *src_path* into the open ZipFile — never loads the whole file."""
    info = zipfile.ZipInfo(arcname)
    info.compress_type = compress_type
    with zf.open(info, "w", force_zip64=True) as dest, open(src_path, "rb") as src:
        shutil.copyfileobj(src, dest, _BUFSIZE)


def _pick_compress(path: Path) -> int:
    """ZIP_STORED for large .jsonl, ZIP_DEFLATED for everything else."""
    if path.suffix == ".jsonl" and path.stat().st_size > _JSONL_STORE_THRESHOLD:
        return zipfile.ZIP_STORED
    return zipfile.ZIP_DEFLATED


# ---------------------------------------------------------------------------
# Pure-sync bundle writer (given pre-fetched DB rows)
# ---------------------------------------------------------------------------

def _write_bundle_from_rows(
    exp_row: dict,
    run_rows: list[dict],
    storage_root: Path,
    include_datasets: bool,
    out_path: Path,
) -> Path:
    """Write a bundle ZIP from already-fetched DB rows.  Pure sync."""
    experiment_id = exp_row["id"]
    outputs_dir = storage_root / "outputs" / experiment_id
    config_runs_dir = storage_root / "config" / "runs"
    datasets_dir = storage_root / "datasets"

    run_ids = [r["id"] for r in run_rows]
    dataset_names: list[str] = []
    artifact_counts: dict[str, int] = {}
    uncompressed_bytes = 0

    out_path.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        # experiment.json — FIRST
        exp_bytes = json.dumps(dict(exp_row), indent=2).encode()
        zf.writestr(zipfile.ZipInfo("experiment.json"), exp_bytes)
        uncompressed_bytes += len(exp_bytes)

        # runs.json — SECOND
        runs_bytes = json.dumps([dict(r) for r in run_rows], indent=2).encode()
        zf.writestr(zipfile.ZipInfo("runs.json"), runs_bytes)
        uncompressed_bytes += len(runs_bytes)

        # Matrix reports
        matrix_count = 0
        for fname in ("matrix_report.json", "matrix_report.md"):
            fp = outputs_dir / fname
            if fp.exists():
                _stream_file_into_zip(zf, f"outputs/{fname}", fp, zipfile.ZIP_DEFLATED)
                uncompressed_bytes += fp.stat().st_size
                matrix_count += 1
        artifact_counts["matrix_reports"] = matrix_count

        # Per-run artifacts
        run_artifact_count = 0
        for run_id in run_ids:
            run_dir = outputs_dir / run_id
            if run_dir.exists():
                for fpath in sorted(run_dir.rglob("*")):
                    if not fpath.is_file() or fpath.name.endswith(".secrev.zip"):
                        continue
                    rel = fpath.relative_to(outputs_dir)
                    arcname = f"outputs/runs/{rel.as_posix()}"
                    _stream_file_into_zip(zf, arcname, fpath, _pick_compress(fpath))
                    uncompressed_bytes += fpath.stat().st_size
                    run_artifact_count += 1
        artifact_counts["run_artifacts"] = run_artifact_count

        # Per-run config files — upload_token stripped before bundling (N4)
        config_count = 0
        for run_id in run_ids:
            cfg = config_runs_dir / f"{run_id}.json"
            if cfg.exists():
                try:
                    cfg_data = json.loads(cfg.read_bytes())
                    cfg_data.pop("upload_token", None)
                    cfg_bytes = json.dumps(cfg_data, indent=2).encode()
                except Exception:
                    cfg_bytes = cfg.read_bytes()
                info = zipfile.ZipInfo(f"config/runs/{run_id}.json")
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, cfg_bytes)
                uncompressed_bytes += len(cfg_bytes)
                config_count += 1
        artifact_counts["run_configs"] = config_count

        # Datasets (optional)
        dataset_mode = "reference"
        if include_datasets:
            dataset_mode = "embedded"
            try:
                config = json.loads(exp_row.get("config_json") or "{}")
                ds_name = config.get("dataset_name", "")
                if ds_name:
                    dataset_names = [ds_name]
            except Exception:
                pass

            ds_count = 0
            for ds_name in dataset_names:
                ds_path = datasets_dir / ds_name
                if not ds_path.exists():
                    continue
                for fpath in sorted(ds_path.rglob("*")):
                    if not fpath.is_file():
                        continue
                    rel = fpath.relative_to(datasets_dir)
                    _stream_file_into_zip(
                        zf, f"datasets/{rel.as_posix()}", fpath, _pick_compress(fpath)
                    )
                    uncompressed_bytes += fpath.stat().st_size
                    ds_count += 1
            artifact_counts["dataset_files"] = ds_count

        # manifest.json — last (counts known)
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "bundle_kind": BUNDLE_KIND,
            "source_deployment": {
                "hostname": os.environ.get("HOSTNAME", "unknown"),
                "storage_root": str(storage_root),
            },
            "exported_at": datetime.now(UTC).isoformat(),
            "experiment_id": experiment_id,
            "run_ids": run_ids,
            "dataset_names": dataset_names,
            "dataset_mode": dataset_mode,
            "artifact_counts": artifact_counts,
            "uncompressed_bytes": uncompressed_bytes,
            "notes": "",
        }
        zf.writestr(zipfile.ZipInfo("manifest.json"), json.dumps(manifest, indent=2).encode())

    return out_path


# ---------------------------------------------------------------------------
# Pure-sync file extractor (given conflict-resolved exp/run rows)
# ---------------------------------------------------------------------------

def _extract_bundle_files(
    zip_path: Path,
    storage_root: Path,
    target_experiment_id: str,
    *,
    rename_experiment_id: str | None = None,
) -> list[str]:
    """Extract bundle files into storage_root.  Returns list of dataset names found.

    Parameters
    ----------
    rename_experiment_id:
        When set (rename policy), rewrite any embedded experiment_id in each
        extracted ``run_result.json`` from the original to this new id.
    """
    outputs_dir = storage_root / "outputs" / target_experiment_id
    config_runs_dir = storage_root / "config" / "runs"
    datasets_dir = storage_root / "datasets"
    manifest_dataset_names: list[str] = []

    with zipfile.ZipFile(zip_path, "r") as zf:
        # --- Decompression bomb guard: check aggregate uncompressed size ---
        total_uncompressed = sum(
            info.file_size for info in zf.infolist()
        )
        if total_uncompressed > _BUNDLE_EXTRACT_MAX_BYTES:
            raise ValueError(
                f"Bundle uncompressed size ({total_uncompressed:,} bytes) exceeds "
                f"the {_BUNDLE_EXTRACT_MAX_BYTES // (1024 * 1024 * 1024)} GiB limit. "
                "Refusing to extract."
            )

        # Collect manifest dataset names
        try:
            with zf.open("manifest.json") as f:
                m = json.loads(f.read())
                manifest_dataset_names = m.get("dataset_names", [])
        except Exception:
            pass

        for member in zf.namelist():
            _check_zip_entry(member)
            mp = PurePosixPath(member)
            parts = mp.parts
            if not parts:
                continue
            if member in ("manifest.json", "experiment.json", "runs.json"):
                continue

            if parts[0] == "outputs":
                rel_parts = parts[1:]
                if not rel_parts:
                    continue
                if len(rel_parts) >= 1 and rel_parts[0] == "runs":
                    dest_path = outputs_dir / Path("/".join(rel_parts[1:]))
                else:
                    dest_path = outputs_dir / Path("/".join(rel_parts))
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                # N1: rewrite embedded experiment_id in run_result.json when
                # rename policy is active.
                if rename_experiment_id is not None and dest_path.name == "run_result.json":
                    raw = zf.read(member)
                    try:
                        result_data = json.loads(raw)
                        _rewrite_experiment_id_in_result(
                            result_data, rename_experiment_id
                        )
                        dest_path.write_bytes(
                            json.dumps(result_data, indent=2).encode()
                        )
                    except Exception:
                        # Fall back to verbatim copy on any parse error
                        dest_path.write_bytes(raw)
                else:
                    with zf.open(member) as src, open(dest_path, "wb") as dst:
                        shutil.copyfileobj(src, dst, _BUFSIZE)

            elif parts[0] == "config" and len(parts) >= 3 and parts[1] == "runs":
                dest_path = config_runs_dir / parts[2]
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                # N4: strip upload_token from config files before writing
                raw = zf.read(member)
                try:
                    cfg_data = json.loads(raw)
                    cfg_data.pop("upload_token", None)
                    dest_path.write_bytes(json.dumps(cfg_data, indent=2).encode())
                except Exception:
                    # Fall back to verbatim copy on any parse error
                    dest_path.write_bytes(raw)

            elif parts[0] == "datasets" and len(parts) >= 3:
                ds_name = parts[1]
                dest_path = datasets_dir / ds_name / Path("/".join(parts[2:]))
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(dest_path, "wb") as dst:
                    shutil.copyfileobj(src, dst, _BUFSIZE)

    return manifest_dataset_names


def _rewrite_experiment_id_in_result(result_data: dict, new_experiment_id: str) -> None:
    """In-place rewrite of experiment_id fields inside a run_result.json dict.

    Rewrites:
      - result_data["experiment"]["experiment_id"]
      - every finding's ["experiment_id"] in result_data["findings"]
      - every finding's ["experiment_id"] in result_data["strategy_output"]["findings"]
    """
    exp = result_data.get("experiment")
    if isinstance(exp, dict):
        exp["experiment_id"] = new_experiment_id

    for key in ("findings", "findings_pre_verification"):
        findings = result_data.get(key)
        if isinstance(findings, list):
            for f in findings:
                if isinstance(f, dict):
                    f["experiment_id"] = new_experiment_id

    strategy_output = result_data.get("strategy_output")
    if isinstance(strategy_output, dict):
        for f in strategy_output.get("findings", []):
            if isinstance(f, dict):
                f["experiment_id"] = new_experiment_id


# ---------------------------------------------------------------------------
# Read-only helper
# ---------------------------------------------------------------------------

def read_manifest(zip_path: Path) -> dict:
    """Return the manifest.json dict without extracting anything else."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        try:
            with zf.open("manifest.json") as f:
                return json.loads(f.read())
        except KeyError:
            raise ValueError(f"manifest.json not found in {zip_path}")


def _read_bundle_rows(zip_path: Path) -> tuple[dict, list[dict]]:
    """Read experiment.json and runs.json from the bundle."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        with zf.open("experiment.json") as f:
            exp_row = json.loads(f.read())
        with zf.open("runs.json") as f:
            run_rows = json.loads(f.read())
    return exp_row, run_rows


def _validate_bundle_entries(zip_path: Path) -> None:
    """Raise ValueError if any zip entry has path-traversal components."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            _check_zip_entry(member)


def _validate_schema(zip_path: Path) -> dict:
    """Read and validate manifest schema_version.  Returns manifest dict."""
    m = read_manifest(zip_path)
    sv = m.get("schema_version")
    if sv != SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported bundle schema_version={sv!r}. "
            f"Only version {SCHEMA_VERSION} is supported."
        )
    return m


# ---------------------------------------------------------------------------
# Async (primary) implementations
# ---------------------------------------------------------------------------

async def async_write_bundle(
    db,
    storage_root: Path,
    experiment_id: str,
    *,
    include_datasets: bool,
    out_path: Path,
) -> Path:
    """Async bundle writer.  Safe to await from within async contexts."""
    exp_row = await db.get_experiment(experiment_id)
    if exp_row is None:
        raise ValueError(f"Experiment {experiment_id!r} not found in database")
    run_rows = await db.list_runs(experiment_id)
    return _write_bundle_from_rows(
        exp_row=exp_row,
        run_rows=run_rows,
        storage_root=storage_root,
        include_datasets=include_datasets,
        out_path=out_path,
    )


async def async_apply_bundle(
    db,
    storage_root: Path,
    zip_path: Path,
    *,
    conflict_policy: str = "reject",
) -> dict:
    """Async bundle importer.  Safe to await from within async contexts."""
    if conflict_policy not in ("reject", "rename", "merge"):
        raise ValueError(
            f"Invalid conflict_policy {conflict_policy!r}. "
            "Must be one of: reject, rename, merge"
        )

    _validate_schema(zip_path)       # raises ValueError on bad schema
    _validate_bundle_entries(zip_path)  # raises ValueError on path traversal

    exp_row, run_rows = _read_bundle_rows(zip_path)
    orig_experiment_id = exp_row["id"]

    existing_exp = await db.get_experiment(orig_experiment_id)

    renamed_from: str | None = None
    warnings: list[str] = []
    datasets_missing: list[str] = []

    target_experiment_id = orig_experiment_id

    if conflict_policy == "reject":
        if existing_exp is not None:
            raise BundleConflictError(
                f"Experiment {orig_experiment_id!r} already exists. "
                "Use conflict_policy='rename' or 'merge'."
            )

    elif conflict_policy == "rename":
        if existing_exp is not None:
            short = uuid.uuid4().hex[:8]
            new_id = f"{orig_experiment_id}_imported_{short}"
            renamed_from = orig_experiment_id
            target_experiment_id = new_id
            exp_row = dict(exp_row)
            exp_row["id"] = new_id
            run_rows = [dict(r, experiment_id=new_id) for r in run_rows]
            warnings.append(
                f"Experiment {orig_experiment_id!r} already exists; "
                f"imported as {new_id!r}."
            )

    elif conflict_policy == "merge":
        if existing_exp is None:
            raise BundleConflictError(
                f"conflict_policy='merge' requires experiment {orig_experiment_id!r} "
                "to already exist, but it was not found."
            )
        existing_runs = await db.list_runs(orig_experiment_id)
        existing_run_ids = {r["id"] for r in existing_runs}
        incoming_run_ids = {r["id"] for r in run_rows}
        collisions = existing_run_ids & incoming_run_ids
        if collisions:
            raise BundleConflictError(
                f"conflict_policy='merge' failed: run IDs already exist: "
                f"{sorted(collisions)}"
            )

    # Extract files (sync, but no DB calls).
    # N1: when rename policy produced a new id, pass it so run_result.json is patched.
    rename_id_for_files = target_experiment_id if renamed_from is not None else None
    experiment_outputs_dir = storage_root / "outputs" / target_experiment_id
    try:
        bundle_dataset_names = _extract_bundle_files(
            zip_path,
            storage_root,
            target_experiment_id,
            rename_experiment_id=rename_id_for_files,
        )
    except Exception:
        # N3: clean up partially-written experiment output dir on extraction failure
        shutil.rmtree(experiment_outputs_dir, ignore_errors=True)
        raise

    # Check for missing datasets
    datasets_dir = storage_root / "datasets"
    for ds_name in bundle_dataset_names:
        ds_path = datasets_dir / ds_name
        if not ds_path.exists():
            datasets_missing.append(ds_name)
            warnings.append(
                f"Dataset {ds_name!r} is referenced but not present after import "
                "(not embedded in bundle or embedding failed)."
            )

    # DB insert (async).
    # N3: clean up on DB failure to avoid orphaned artifact files.
    import sqlite3 as _sqlite3
    try:
        if conflict_policy == "merge":
            await db.import_experiment_rows(None, run_rows)
        else:
            await db.import_experiment_rows(exp_row, run_rows)
    except _sqlite3.IntegrityError as exc:
        shutil.rmtree(experiment_outputs_dir, ignore_errors=True)
        raise BundleConflictError(
            f"Import failed due to a database conflict: {exc}"
        ) from exc
    except Exception:
        shutil.rmtree(experiment_outputs_dir, ignore_errors=True)
        raise

    return {
        "experiment_id": target_experiment_id,
        "renamed_from": renamed_from,
        "runs_imported": len(run_rows),
        "runs_skipped": 0,
        "datasets_missing": datasets_missing,
        "warnings": warnings,
        "findings_indexed": 0,  # Caller is responsible for indexing
    }


# ---------------------------------------------------------------------------
# Sync (script/CLI) entry points — only usable outside a running event loop
# ---------------------------------------------------------------------------

def write_bundle(
    db,
    storage_root: Path,
    experiment_id: str,
    *,
    include_datasets: bool,
    out_path: Path,
    _exp_row: dict | None = None,
    _run_rows: list[dict] | None = None,
) -> Path:
    """Sync bundle writer.  Supply *_exp_row*/*_run_rows* to bypass async DB fetch.

    Must not be called from within a running event loop unless pre-fetched
    rows are supplied.
    """
    import asyncio

    if _exp_row is not None and _run_rows is not None:
        return _write_bundle_from_rows(
            exp_row=_exp_row,
            run_rows=_run_rows,
            storage_root=storage_root,
            include_datasets=include_datasets,
            out_path=out_path,
        )

    return asyncio.run(
        async_write_bundle(
            db, storage_root, experiment_id,
            include_datasets=include_datasets, out_path=out_path,
        )
    )


def apply_bundle(
    db,
    storage_root: Path,
    zip_path: Path,
    *,
    conflict_policy: str = "reject",
) -> dict:
    """Sync bundle importer.  Must not be called from within a running event loop."""
    import asyncio

    return asyncio.run(
        async_apply_bundle(db, storage_root, zip_path, conflict_policy=conflict_policy)
    )
