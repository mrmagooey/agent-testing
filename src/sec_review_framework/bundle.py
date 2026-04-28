"""Experiment bundle helpers: export/import of experiments as portable ZIP archives.

Bundle layout (ZIP, deflate for JSON/MD, store for .jsonl >1 MiB):
    manifest.json                  — metadata and inventory
    experiment.json                — experiments DB row
    runs.json                      — runs DB rows array
    outputs/matrix_report.{json,md}
    outputs/runs/<run_id>/         — per-run artifacts
    config/runs/<run_id>.json
    datasets.json                  — dataset rows (descriptor mode only)
    dataset_labels.json            — dataset_labels rows (descriptor mode only)
    dataset_negative_labels.json   — dataset_negative_labels rows (descriptor mode only)

Schema version is 3.  Importers MUST reject unknown schema_version values.
Version 1 bundles (lacking dataset_negative_labels.json) still import cleanly —
the missing file is treated as an empty array.
Version 2 bundles (lacking archive_* dataset columns) still import cleanly —
those fields default to None for non-archive rows.
Version 3 adds archive_url / archive_sha256 / archive_format columns to
datasets.json rows for kind='archive' datasets.

dataset_mode values:
  "descriptor" — datasets.json + dataset_labels.json included (default)
  "reference"  — only dataset names recorded in manifest; no JSON files

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
from collections.abc import Awaitable, Callable
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

SCHEMA_VERSION = 3
BUNDLE_KIND = "experiment"

_VALID_DATASET_MODES = frozenset({"reference", "descriptor"})


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
# Pure-sync bundle writer (given pre-fetched DB rows + dataset data)
# ---------------------------------------------------------------------------

def _write_bundle_from_rows(
    exp_row: dict,
    run_rows: list[dict],
    storage_root: Path,
    dataset_mode: str,
    out_path: Path,
    *,
    dataset_rows: list[dict] | None = None,
    dataset_label_rows: list[dict] | None = None,
    dataset_negative_label_rows: list[dict] | None = None,
) -> Path:
    """Write a bundle ZIP from already-fetched DB rows.  Pure sync.

    Parameters
    ----------
    dataset_mode:
        "descriptor" — embed datasets.json + dataset_labels.json +
                       dataset_negative_labels.json.
        "reference"  — record names only in the manifest.
    dataset_rows:
        Pre-fetched dataset DB rows (required when dataset_mode == "descriptor").
    dataset_label_rows:
        Pre-fetched dataset_labels DB rows (required when dataset_mode == "descriptor").
    dataset_negative_label_rows:
        Pre-fetched dataset_negative_labels DB rows (descriptor mode only).
    """
    if dataset_mode not in _VALID_DATASET_MODES:
        raise ValueError(
            f"Invalid dataset_mode {dataset_mode!r}. "
            f"Must be one of: {sorted(_VALID_DATASET_MODES)}"
        )

    experiment_id = exp_row["id"]
    outputs_dir = storage_root / "outputs" / experiment_id
    config_runs_dir = storage_root / "config" / "runs"

    run_ids = [r["id"] for r in run_rows]
    artifact_counts: dict[str, int] = {}
    uncompressed_bytes = 0

    # Collect dataset names referenced by runs (from config_json → dataset_name)
    dataset_names: list[str] = []
    seen_names: set[str] = set()
    for run in run_rows:
        try:
            cfg = json.loads(run.get("config_json") or "{}")
            ds_name = cfg.get("dataset_name", "")
            if ds_name and ds_name not in seen_names:
                dataset_names.append(ds_name)
                seen_names.add(ds_name)
        except Exception:
            pass
    # Also check experiment config_json
    try:
        exp_cfg = json.loads(exp_row.get("config_json") or "{}")
        ds_name = exp_cfg.get("dataset_name", "")
        if ds_name and ds_name not in seen_names:
            dataset_names.append(ds_name)
            seen_names.add(ds_name)
    except Exception:
        pass

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

        # Datasets — descriptor mode embeds datasets.json + dataset_labels.json
        #            + dataset_negative_labels.json
        dataset_count = 0
        dataset_label_count = 0
        dataset_negative_label_count = 0

        if dataset_mode == "descriptor":
            ds_rows = dataset_rows or []
            lbl_rows = dataset_label_rows or []
            neg_lbl_rows = dataset_negative_label_rows or []

            dataset_count = len(ds_rows)
            dataset_label_count = len(lbl_rows)
            dataset_negative_label_count = len(neg_lbl_rows)

            if ds_rows:
                ds_bytes = json.dumps([dict(r) for r in ds_rows], indent=2).encode()
                zf.writestr(zipfile.ZipInfo("datasets.json"), ds_bytes)
                uncompressed_bytes += len(ds_bytes)

            if lbl_rows:
                lbl_bytes = json.dumps([dict(r) for r in lbl_rows], indent=2).encode()
                zf.writestr(zipfile.ZipInfo("dataset_labels.json"), lbl_bytes)
                uncompressed_bytes += len(lbl_bytes)

            if neg_lbl_rows:
                neg_lbl_bytes = json.dumps([dict(r) for r in neg_lbl_rows], indent=2).encode()
                zf.writestr(zipfile.ZipInfo("dataset_negative_labels.json"), neg_lbl_bytes)
                uncompressed_bytes += len(neg_lbl_bytes)

        artifact_counts["dataset_count"] = dataset_count
        artifact_counts["dataset_label_count"] = dataset_label_count
        artifact_counts["dataset_negative_label_count"] = dataset_negative_label_count

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
            "artifact_counts": {
                **artifact_counts,
                "dataset_count": dataset_count,
                "dataset_label_count": dataset_label_count,
                "dataset_negative_label_count": dataset_negative_label_count,
            },
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
) -> tuple[list[str], list[dict], list[dict], list[dict]]:
    """Extract bundle files into storage_root.

    Returns
    -------
    (manifest_dataset_names, dataset_rows, dataset_label_rows, dataset_negative_label_rows)

    Parameters
    ----------
    rename_experiment_id:
        When set (rename policy), rewrite any embedded experiment_id in each
        extracted ``run_result.json`` from the original to this new id.
    """
    outputs_dir = storage_root / "outputs" / target_experiment_id
    config_runs_dir = storage_root / "config" / "runs"
    manifest_dataset_names: list[str] = []
    dataset_rows: list[dict] = []
    dataset_label_rows: list[dict] = []
    dataset_negative_label_rows: list[dict] = []

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

        # Read datasets.json if present
        if "datasets.json" in zf.namelist():
            try:
                with zf.open("datasets.json") as f:
                    dataset_rows = json.loads(f.read())
            except Exception:
                pass

        # Read dataset_labels.json if present
        if "dataset_labels.json" in zf.namelist():
            try:
                with zf.open("dataset_labels.json") as f:
                    dataset_label_rows = json.loads(f.read())
            except Exception:
                pass

        # Read dataset_negative_labels.json if present (absent in schema_version 1 bundles)
        if "dataset_negative_labels.json" in zf.namelist():
            try:
                with zf.open("dataset_negative_labels.json") as f:
                    dataset_negative_label_rows = json.loads(f.read())
            except Exception:
                pass

        for member in zf.namelist():
            _check_zip_entry(member)
            mp = PurePosixPath(member)
            parts = mp.parts
            if not parts:
                continue
            # Skip top-level JSON files — handled separately
            if member in (
                "manifest.json", "experiment.json", "runs.json",
                "datasets.json", "dataset_labels.json",
                "dataset_negative_labels.json",
            ):
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

            # Note: "datasets/" byte paths are NOT extracted — descriptor mode
            # uses datasets.json / dataset_labels.json instead (Phase 2C).
            # Any datasets/<name>/... entries in old bundles are silently skipped.

    return manifest_dataset_names, dataset_rows, dataset_label_rows, dataset_negative_label_rows


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


_MIN_SCHEMA_VERSION = 1  # Oldest bundle format still accepted for import

def _validate_schema(zip_path: Path) -> dict:
    """Read and validate manifest schema_version.  Returns manifest dict."""
    m = read_manifest(zip_path)
    sv = m.get("schema_version")
    if not isinstance(sv, int) or not (_MIN_SCHEMA_VERSION <= sv <= SCHEMA_VERSION):
        raise ValueError(
            f"Unsupported bundle schema_version={sv!r}. "
            f"Supported range: {_MIN_SCHEMA_VERSION}–{SCHEMA_VERSION}."
        )
    return m


# ---------------------------------------------------------------------------
# Async helpers for collecting dataset rows recursively
# ---------------------------------------------------------------------------

async def _collect_dataset_rows(db, names: list[str]) -> list[dict]:
    """Recursively collect dataset rows for all given names.

    For each name, if the row has kind='derived', also include the base_dataset
    row (and its base, transitively). Uses a set to deduplicate.
    Returns list of rows in topological order (base before derived).
    """
    collected: dict[str, dict] = {}  # name → row
    queue = list(names)

    while queue:
        name = queue.pop(0)
        if name in collected:
            continue
        row = await db.get_dataset(name)
        if row is None:
            continue
        collected[name] = row
        if row.get("kind") == "derived" and row.get("base_dataset"):
            base = row["base_dataset"]
            if base not in collected:
                queue.append(base)

    # Return in topological order: base datasets before derived ones
    # Simple approach: sort so that rows without base_dataset come first
    result = []
    remaining = dict(collected)

    # Iteratively emit rows whose base_dataset is already emitted (or None)
    emitted: set[str] = set()
    max_iterations = len(remaining) + 1
    iteration = 0
    while remaining and iteration < max_iterations:
        iteration += 1
        for name in list(remaining.keys()):
            row = remaining[name]
            base = row.get("base_dataset")
            if base is None or base in emitted or base not in remaining:
                result.append(row)
                emitted.add(name)
                del remaining[name]

    # Any remaining rows (circular references or broken chains) — append as-is
    result.extend(remaining.values())

    return result


# ---------------------------------------------------------------------------
# Async (primary) implementations
# ---------------------------------------------------------------------------

async def async_write_bundle(
    db,
    storage_root: Path,
    experiment_id: str,
    *,
    dataset_mode: str = "descriptor",
    # Legacy kwarg for backward compat — maps True→"embedded" is no longer valid;
    # callers that passed include_datasets=False now get dataset_mode="descriptor"
    # (the new default) unless they pass dataset_mode explicitly.
    include_datasets: bool | None = None,
    out_path: Path,
) -> Path:
    """Async bundle writer.  Safe to await from within async contexts.

    Parameters
    ----------
    dataset_mode:
        "descriptor" (default) — embed datasets.json + dataset_labels.json.
        "reference"            — record names only; skip JSON files.
    include_datasets:
        Deprecated. Kept for backward compatibility; ignored when dataset_mode
        is explicitly provided. Callers should use dataset_mode instead.
    """
    # Backward-compat shim: if caller passed the old include_datasets kwarg
    # but did not pass dataset_mode explicitly, map it.
    # Since Python doesn't distinguish "not passed" from default, we check
    # if dataset_mode is still the default "descriptor" and include_datasets was given.
    # The old include_datasets=False → use default "descriptor".
    # The old include_datasets=True → was "embedded" which is now removed;
    #   treat as "descriptor" (closest equivalent).
    if include_datasets is not None and dataset_mode == "descriptor":
        # Caller used old API — the mapping is best-effort:
        # include_datasets=False → "descriptor" (fine, same behavior as new default)
        # include_datasets=True  → "descriptor" (embed the DB metadata, not repo bytes)
        dataset_mode = "descriptor"

    if dataset_mode not in _VALID_DATASET_MODES:
        raise ValueError(
            f"Invalid dataset_mode {dataset_mode!r}. "
            f"Must be one of: {sorted(_VALID_DATASET_MODES)}"
        )

    exp_row = await db.get_experiment(experiment_id)
    if exp_row is None:
        raise ValueError(f"Experiment {experiment_id!r} not found in database")
    run_rows = await db.list_runs(experiment_id)

    dataset_rows: list[dict] = []
    dataset_label_rows: list[dict] = []
    dataset_negative_label_rows: list[dict] = []

    if dataset_mode == "descriptor":
        # Collect unique dataset names from runs + experiment config
        ds_names: list[str] = []
        seen: set[str] = set()
        for run in run_rows:
            try:
                cfg = json.loads(run.get("config_json") or "{}")
                ds_name = cfg.get("dataset_name", "")
                if ds_name and ds_name not in seen:
                    ds_names.append(ds_name)
                    seen.add(ds_name)
            except Exception:
                pass
        try:
            exp_cfg = json.loads(exp_row.get("config_json") or "{}")
            ds_name = exp_cfg.get("dataset_name", "")
            if ds_name and ds_name not in seen:
                ds_names.append(ds_name)
                seen.add(ds_name)
        except Exception:
            pass

        # Recursively collect dataset rows (includes base datasets)
        dataset_rows = await _collect_dataset_rows(db, ds_names)

        # Collect positive and negative labels for all included datasets
        all_labels: list[dict] = []
        all_neg_labels: list[dict] = []
        for ds_row in dataset_rows:
            labels = await db.list_dataset_labels(ds_row["name"])
            all_labels.extend(labels)
            neg_labels = await db.list_dataset_negative_labels(ds_row["name"])
            all_neg_labels.extend(neg_labels)
        dataset_label_rows = all_labels
        dataset_negative_label_rows = all_neg_labels

    return _write_bundle_from_rows(
        exp_row=exp_row,
        run_rows=run_rows,
        storage_root=storage_root,
        dataset_mode=dataset_mode,
        out_path=out_path,
        dataset_rows=dataset_rows,
        dataset_label_rows=dataset_label_rows,
        dataset_negative_label_rows=dataset_negative_label_rows,
    )


async def async_apply_bundle(
    db,
    storage_root: Path,
    zip_path: Path,
    *,
    conflict_policy: str = "reject",
    materialize: Callable[[str], Awaitable[None]] | None = None,
) -> dict:
    """Async bundle importer.  Safe to await from within async contexts.

    Parameters
    ----------
    materialize:
        Optional callable ``async (name: str) -> None`` that clones/rebuilds
        a dataset repo onto disk and stamps ``materialized_at``.  When
        supplied, called for each imported dataset row whose on-disk repo is
        absent.  Typically ``coordinator.materialize_dataset``.  When None
        (default), materialization is skipped and ``datasets_rehydrated``
        will be empty.
    """
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
    datasets_rehydrated: list[str] = []

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
        bundle_dataset_names, bundle_dataset_rows, bundle_label_rows, bundle_neg_label_rows = _extract_bundle_files(
            zip_path,
            storage_root,
            target_experiment_id,
            rename_experiment_id=rename_id_for_files,
        )
    except Exception:
        # N3: clean up partially-written experiment output dir on extraction failure
        shutil.rmtree(experiment_outputs_dir, ignore_errors=True)
        raise

    # --- Import datasets (descriptor mode) ---
    datasets_imported = 0
    dataset_labels_imported = 0

    if bundle_dataset_rows:
        # Clear materialized_at from imported rows: the destination hasn't
        # materialized them yet; materialize_dataset will set it on success.
        bundle_dataset_rows = [
            {**r, "materialized_at": None} for r in bundle_dataset_rows
        ]
        # Use the same conflict_policy for datasets as for experiments.
        try:
            # Snapshot existing names before import so we can count truly new rows
            # (N8: accurate counter for merge policy).
            existing_ds_names_before = {r["name"] for r in await db.list_datasets()}

            final_names = await db.import_datasets(
                bundle_dataset_rows,
                conflict_policy=conflict_policy,
            )

            # Count rows that were actually inserted (not skipped / pre-existing).
            datasets_imported = sum(
                1 for final in final_names
                if final not in existing_ds_names_before
            )
        except Exception as exc:
            # C3: when conflict_policy is 'reject', surface the error as a
            # BundleConflictError rather than silently demoting it to a warning.
            if conflict_policy == "reject":
                raise BundleConflictError(
                    f"Dataset import conflict: {exc}"
                ) from exc
            warnings.append(f"Dataset import warning: {exc}")
            final_names = []
            datasets_imported = 0

        # Phase 3: call materialize_dataset for each dataset whose repo is absent.
        if materialize is not None:
            for name in final_names:
                repo_dir = storage_root / "datasets" / name / "repo"
                if not repo_dir.exists():
                    try:
                        await materialize(name)
                        datasets_rehydrated.append(name)
                    except Exception as exc:
                        # Detect templates_version mismatch (409 from HTTPException
                        # or any exception whose detail/message contains the phrase)
                        exc_str = str(exc)
                        if "templates_version mismatch" in exc_str or (
                            hasattr(exc, "status_code") and exc.status_code == 409
                            and "templates_version" in exc_str
                        ):
                            warnings.append(
                                f"templates_version mismatch for derived dataset {name}; "
                                "left unmaterialized"
                            )
                        else:
                            warnings.append(
                                f"Failed to rehydrate dataset {name!r}: {exc}"
                            )

    dataset_negative_labels_imported = 0

    if bundle_label_rows:
        try:
            # C2: Rewrite dataset_name in label rows to the final (possibly renamed)
            # dataset name so labels are queryable under the new name after a 'rename'
            # policy import.
            original_to_final: dict[str, str] = {
                r["name"]: final
                for r, final in zip(bundle_dataset_rows, final_names)
            }
            rewritten_label_rows = [
                {**row, "dataset_name": original_to_final.get(row["dataset_name"], row["dataset_name"])}
                for row in bundle_label_rows
            ]
            await db.append_dataset_labels(rewritten_label_rows)
            dataset_labels_imported = len(rewritten_label_rows)
        except Exception as exc:
            warnings.append(f"Dataset labels import warning: {exc}")
            dataset_labels_imported = 0

    # Restore negative labels (absent in schema_version 1 bundles — tolerate gracefully)
    if bundle_neg_label_rows:
        try:
            original_to_final_neg: dict[str, str] = {
                r["name"]: final
                for r, final in zip(bundle_dataset_rows, final_names)
            }
            rewritten_neg_label_rows = [
                {**row, "dataset_name": original_to_final_neg.get(row["dataset_name"], row["dataset_name"])}
                for row in bundle_neg_label_rows
            ]
            await db.append_dataset_negative_labels(rewritten_neg_label_rows)
            dataset_negative_labels_imported = len(rewritten_neg_label_rows)
        except Exception as exc:
            warnings.append(f"Dataset negative labels import warning: {exc}")
            dataset_negative_labels_imported = 0

    # Check for missing datasets (referenced in manifest but not in bundle or on disk)
    datasets_dir = storage_root / "datasets"
    bundled_ds_names = {r["name"] for r in bundle_dataset_rows}
    for ds_name in bundle_dataset_names:
        ds_path = datasets_dir / ds_name
        if ds_name not in bundled_ds_names and not ds_path.exists():
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
        "datasets_imported": datasets_imported,
        "datasets_rehydrated": datasets_rehydrated,
        "datasets_missing": datasets_missing,
        "dataset_labels_imported": dataset_labels_imported,
        "dataset_negative_labels_imported": dataset_negative_labels_imported,
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
    dataset_mode: str = "descriptor",
    include_datasets: bool | None = None,
    out_path: Path,
    _exp_row: dict | None = None,
    _run_rows: list[dict] | None = None,
    _dataset_rows: list[dict] | None = None,
    _dataset_label_rows: list[dict] | None = None,
    _dataset_negative_label_rows: list[dict] | None = None,
) -> Path:
    """Sync bundle writer.  Supply *_exp_row*/*_run_rows* to bypass async DB fetch.

    Must not be called from within a running event loop unless pre-fetched
    rows are supplied.

    Parameters
    ----------
    dataset_mode:
        "descriptor" (default) — embed datasets.json + dataset_labels.json +
                                 dataset_negative_labels.json.
        "reference"            — record names only.
    include_datasets:
        Deprecated legacy parameter. Ignored when _exp_row/_run_rows supplied
        directly (caller controls dataset_rows). Mapped to dataset_mode otherwise.
    """
    import asyncio

    # Legacy compat: map include_datasets to dataset_mode
    if include_datasets is not None and dataset_mode == "descriptor":
        dataset_mode = "descriptor"  # both True and False map to "descriptor" now

    if _exp_row is not None and _run_rows is not None:
        return _write_bundle_from_rows(
            exp_row=_exp_row,
            run_rows=_run_rows,
            storage_root=storage_root,
            dataset_mode=dataset_mode,
            out_path=out_path,
            dataset_rows=_dataset_rows,
            dataset_label_rows=_dataset_label_rows,
            dataset_negative_label_rows=_dataset_negative_label_rows,
        )

    return asyncio.run(
        async_write_bundle(
            db, storage_root, experiment_id,
            dataset_mode=dataset_mode, out_path=out_path,
        )
    )


def apply_bundle(
    db,
    storage_root: Path,
    zip_path: Path,
    *,
    conflict_policy: str = "reject",
    materialize: Callable[[str], Awaitable[None]] | None = None,
) -> dict:
    """Sync bundle importer.  Must not be called from within a running event loop."""
    import asyncio

    return asyncio.run(
        async_apply_bundle(
            db, storage_root, zip_path,
            conflict_policy=conflict_policy,
            materialize=materialize,
        )
    )
