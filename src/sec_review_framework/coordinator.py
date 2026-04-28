"""FastAPI coordinator service for the security review testing framework."""

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import threading
import zipfile
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from sec_review_framework.config import ModelProviderConfig, ToolExtensionAvailability
from sec_review_framework.cost.calculator import CostCalculator
from sec_review_framework.cost.pricing_view import CatalogPricingView
from sec_review_framework.data.evaluation import GroundTruthLabel
from sec_review_framework.data.experiment import (
    ExperimentMatrix,
    ExperimentStatus,
    RunResult,
    StrategyName,
    ToolExtension,
)
from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    OverrideRule,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.db import Database
from sec_review_framework.feedback.tracker import FeedbackTracker
from sec_review_framework.ground_truth.cve_importer import (
    CVECandidate as _CVECandidate,
)
from sec_review_framework.ground_truth.cve_importer import (
    CVEDiscovery,
    CVEImporter,
    CVEImportSpec,
    CVEResolver,
    CVESelectionCriteria,
    DiscoveryResult as _DiscoveryResult,
)
from sec_review_framework.ground_truth.vuln_injector import VulnInjector
from sec_review_framework.llm_providers import router as llm_providers_router
from sec_review_framework.models.aliases import rewrite_legacy_ids
from sec_review_framework.models.availability import (
    build_effective_registry,
    build_id_to_status,
    compute_availability,
    flat_model_list,
    groups_to_dicts,
)
from sec_review_framework.models.catalog import ProviderCatalog
from sec_review_framework.models.probes import build_probes
from sec_review_framework.profiles.review_profiles import BUILTIN_PROFILES

RECONCILE_INTERVAL_S = int(os.environ.get("RECONCILE_INTERVAL_S", "5"))
RUN_STALL_TIMEOUT_S = int(os.environ.get("RUN_STALL_TIMEOUT_S", "300"))

# Import endpoint gate.  Default ON (empty string / unset → enabled).
# Set IMPORT_ENABLED=false to disable in production.
_IMPORT_ENABLED_RAW = os.environ.get("IMPORT_ENABLED", "true").strip().lower()
IMPORT_ENABLED: bool = _IMPORT_ENABLED_RAW not in ("false", "0", "no", "off")

# Resolve the frontend dist directory once at import time.
# Allow override via FRONTEND_DIST_DIR for non-standard layouts / tests.
# When the package is installed via pip the module lands in site-packages, so
# walking parent dirs from __file__ no longer reaches the repo/app root.
# We probe candidate paths in priority order and pick the first that exists.
def _resolve_frontend_dist() -> Path:
    env_override = os.environ.get("FRONTEND_DIST_DIR")
    if env_override:
        return Path(env_override)
    _module_dir = Path(__file__).resolve().parent  # .../sec_review_framework/
    candidates = [
        Path("/app/frontend/dist"),                          # installed-package layout in Docker
        _module_dir.parent.parent / "frontend" / "dist",    # dev-checkout layout (src/…/coordinator.py)
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    # Fall back to the first candidate even if it doesn't exist yet; the mount
    # logic below will log a warning rather than crash.
    return candidates[0]

FRONTEND_DIST_DIR = _resolve_frontend_dist()

logger = logging.getLogger(__name__)

AUDIT_URL_CHECK_EXEMPT_PREFIXES = ("doc_", "ts_", "lsp_")

# Bare YYYY-MM-DD values from <input type="date"> are normalised to end-of-day
# inclusive when used as `created_to`, so a "Created to: April 20" filter
# includes findings created at any time on that date instead of silently
# excluding them via lexicographic comparison against ISO-8601 timestamps.
_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TOOL_EXTENSION_LABELS: dict[ToolExtension, str] = {
    ToolExtension.DEVDOCS: "DevDocs",
    ToolExtension.LSP: "LSP",
    ToolExtension.SEMGREP: "Semgrep",
    ToolExtension.TREE_SITTER: "Tree-sitter",
}

# ---------------------------------------------------------------------------
# Prometheus metrics (optional — prometheus-client is a coordinator dep)
# ---------------------------------------------------------------------------
try:
    from prometheus_client import Counter as _Counter
    from prometheus_client import make_asgi_app as _make_asgi_app
    _upload_counter = _Counter(
        "upload_total",
        "Worker result upload attempts by outcome",
        ["outcome"],
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_AVAILABLE = False
    # Stub counter so the handler code is unconditionally importable in tests.
    class _StubCounter:
        def labels(self, **_kw: object) -> _StubCounter:
            return self
        def inc(self, _amount: float = 1) -> None:
            pass
    _upload_counter = _StubCounter()  # type: ignore[assignment]
    _make_asgi_app = None  # type: ignore[assignment]

# Kubernetes is optional — not available in dev environments
try:
    import kubernetes
    K8S_AVAILABLE = True
except ImportError:
    kubernetes = None  # type: ignore[assignment]
    K8S_AVAILABLE = False


# ---------------------------------------------------------------------------
# Spend cap enforcement
# ---------------------------------------------------------------------------

class ExperimentCostTracker:
    """Tracks running spend per experiment. Thread-safe."""

    def __init__(self, experiment_id: str, cap_usd: float | None):
        self.experiment_id = experiment_id
        self.cap_usd = cap_usd
        self._spent = 0.0
        self._lock = threading.Lock()
        self._cancelled = False

    def record_job_cost(self, cost_usd: float) -> bool:
        """Returns True if cap exceeded and experiment should halt."""
        with self._lock:
            self._spent += cost_usd
            if self.cap_usd and not self._cancelled and self._spent >= self.cap_usd:
                self._cancelled = True
                return True
        return False

    @property
    def spent_usd(self) -> float:
        return self._spent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _slug(path: str, max_len: int = 60) -> str:
    """Coerce a relative file path into a safe run-ID slug.

    Replaces path separators and non-alphanumeric characters with '-',
    strips leading/trailing dashes, and truncates to *max_len* characters.
    """
    import re as _re
    slug = _re.sub(r"[^a-z0-9]", "-", path.lower())
    slug = slug.strip("-")
    return slug[:max_len] or "file"


# ---------------------------------------------------------------------------
# Experiment coordinator
# ---------------------------------------------------------------------------

class ExperimentCoordinator:
    """
    Manages the lifecycle of an experiment:
    1. Receives an experiment submission (ExperimentMatrix + config)
    2. Expands the matrix into ExperimentRuns
    3. Creates K8s Jobs, respecting per-model concurrency caps
    4. Monitors job completion, collects results from shared storage
    5. Generates the matrix report when all jobs finish
    """

    def __init__(
        self,
        k8s_client,                      # kubernetes.client.BatchV1Api or None in dev
        storage_root: Path,
        concurrency_caps: dict[str, int],
        worker_image: str,
        namespace: str,
        db: Database,
        reporter,                         # ReportGenerator instance
        cost_calculator: CostCalculator,
        config_dir: Path | None = None,
        default_cap: int = 4,
        worker_resources=None,
        pvc_name: str = "sec-review-data",
        llm_secret_name: str = "llm-api-keys",
        config_map_name: str = "experiment-config",
        worker_image_pull_policy: str = "IfNotPresent",
        result_transport: str = "pvc",
        coordinator_internal_url: str = "",
    ):
        self.k8s_client = k8s_client
        self.storage_root = storage_root
        self.concurrency_caps = concurrency_caps
        self.worker_image = worker_image
        self.namespace = namespace
        self.db = db
        self.reporter = reporter
        self.cost_calculator = cost_calculator
        self.config_dir = config_dir
        self.default_cap = default_cap
        self.worker_resources = worker_resources
        self.pvc_name = pvc_name
        self.llm_secret_name = llm_secret_name
        self.config_map_name = config_map_name
        self.worker_image_pull_policy = worker_image_pull_policy
        self.result_transport = result_transport
        self.coordinator_internal_url = coordinator_internal_url
        self._cost_trackers: dict[str, ExperimentCostTracker] = {}
        self._feedback_tracker = FeedbackTracker()
        # Per-instance TTL cache for get_trends (class-level dict would be shared across instances).
        self._trends_cache: dict[tuple, tuple[dict, float]] = {}
        # ProviderCatalog — set by the lifespan handler; None until startup completes.
        self.catalog: ProviderCatalog | None = None  # type: ignore[name-defined]

    # ------------------------------------------------------------------
    # Experiment lifecycle
    # ------------------------------------------------------------------

    async def _expand_benchmark_iteration(
        self,
        runs: list,
        matrix,
    ) -> list:
        """Expand runs for datasets that declare per-test-file iteration.

        When the dataset's ``metadata_json`` contains ``{"iteration":
        "per-test-file"}``, each base run is fanned out into N runs — one per
        file matched by ``test_glob`` (default ``**/*``).  The fan-out is
        gated by ``matrix.allow_benchmark_iteration``; if the dataset requests
        per-file iteration but the flag is False, a ``ValueError`` is raised.

        For datasets without ``iteration`` in their metadata the original
        *runs* list is returned unchanged (byte-identical behaviour).

        Parameters
        ----------
        runs:
            The base list produced by ``ExperimentMatrix.expand()``.
        matrix:
            The original ``ExperimentMatrix``; used to read
            ``allow_benchmark_iteration`` and ``dataset_name``.

        Returns
        -------
        list[ExperimentRun]
            Either the original *runs* list (no iteration mode) or the
            expanded list (one run per matched file × base run).

        Raises
        ------
        ValueError
            If the dataset requests per-test-file iteration but
            ``matrix.allow_benchmark_iteration`` is False.
        """
        import glob as _glob
        import json as _json

        dataset_row = await self.db.get_dataset(matrix.dataset_name)
        if dataset_row is None:
            # Dataset not yet in DB (e.g. very early bootstrap); fall through.
            return runs

        try:
            meta = _json.loads(dataset_row.get("metadata_json") or "{}")
        except (ValueError, TypeError):
            meta = {}

        if meta.get("iteration") != "per-test-file":
            return runs

        # Dataset wants per-test-file iteration.
        if not matrix.allow_benchmark_iteration:
            raise ValueError(
                f"Dataset {matrix.dataset_name!r} requests per-test-file iteration "
                f"but the experiment was submitted without "
                f"allow_benchmark_iteration=true.  Re-submit with that flag set to "
                f"acknowledge the cost implications (~{len(runs)} strategy × N files "
                f"agent invocations)."
            )

        test_glob = meta.get("test_glob") or "**/*"

        # Resolve the glob against the materialized dataset repo.
        repo_root = self.storage_root / "datasets" / matrix.dataset_name / "repo"
        if not repo_root.exists():
            raise ValueError(
                f"Dataset {matrix.dataset_name!r} repo not found at {repo_root}; "
                f"materialize the dataset before running per-test-file iteration."
            )

        matched_paths = sorted(repo_root.glob(test_glob))
        # Only regular files — skip directories that may match the glob.
        matched_files = [p for p in matched_paths if p.is_file()]
        if not matched_files:
            raise ValueError(
                f"Dataset {matrix.dataset_name!r}: test_glob {test_glob!r} "
                f"matched no files under {repo_root}."
            )

        # Fan out: for each base run × matched file, create a file-specific run.
        expanded: list = []
        for base_run in runs:
            for file_path in matched_files:
                rel = str(file_path.relative_to(repo_root))
                file_run_id = f"{base_run.id}_file-{_slug(rel)}"
                expanded.append(
                    base_run.model_copy(update={
                        "id": file_run_id,
                        "target_file": rel,
                    })
                )
        return expanded

    async def submit_experiment(self, matrix: ExperimentMatrix) -> str:
        from sec_review_framework.strategies.strategy_registry import build_registry_from_db

        registry = await build_registry_from_db(self.db)
        runs = matrix.expand(registry=registry)
        # Expand per-test-file iteration if the dataset requests it.
        runs = await self._expand_benchmark_iteration(runs, matrix)
        experiment_id = matrix.experiment_id

        await self.db.create_experiment(
            experiment_id=experiment_id,
            config_json=matrix.model_dump_json(),
            total_runs=len(runs),
            max_cost_usd=matrix.max_experiment_cost_usd,
        )
        for run in runs:
            await self.db.create_run(
                run_id=run.id,
                experiment_id=experiment_id,
                config_json=run.model_dump_json(),
                model_id=run.model_id,
                strategy=run.strategy.value,
                tool_variant=run.tool_variant.value,
                review_profile=run.review_profile.value,
                verification_variant=run.verification_variant.value,
                tool_extensions=run.tool_extensions,
            )

        # Persist run configs to shared storage for worker pods to read.
        # When result_transport == "http", embed the upload URL and token so the
        # worker knows how to deliver its artifacts.  The token is excluded from
        # the DB config_json (model_dump excludes upload_token via Field(exclude=True))
        # but IS written to the on-disk per-run JSON that the worker reads.
        config_dir = self.storage_root / "config" / "runs"
        config_dir.mkdir(parents=True, exist_ok=True)
        for run in runs:
            if self.result_transport == "http":
                token = await self.db.issue_upload_token(run.id)
                upload_url = (
                    f"{self.coordinator_internal_url}"
                    f"/api/internal/runs/{run.id}/result"
                )
                run = run.model_copy(update={
                    "result_transport": "http",
                    "upload_url": upload_url,
                    "upload_token": token,
                })
            # Write the full run config (including upload_token) to disk for the worker.
            # upload_token is excluded from DB persistence via Field(exclude=True),
            # but we need it on disk, so we dump via model_dump and include it explicitly.
            run_dict = run.model_dump(mode="json")
            if run.upload_token is not None:
                run_dict["upload_token"] = run.upload_token
            import json as _json
            (config_dir / f"{run.id}.json").write_text(
                _json.dumps(run_dict, indent=2, default=str)
            )

        self._cost_trackers[experiment_id] = ExperimentCostTracker(
            experiment_id, matrix.max_experiment_cost_usd
        )

        thread = threading.Thread(
            target=self._schedule_jobs_in_thread, args=(experiment_id, runs), daemon=True
        )
        thread.start()
        return experiment_id

    def _schedule_jobs_in_thread(self, experiment_id: str, runs: list) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._schedule_jobs(experiment_id, runs))
        finally:
            loop.close()

    async def _schedule_jobs(self, experiment_id: str, runs: list) -> None:
        """
        Schedules K8s Jobs respecting per-model concurrency caps.
        Uses a polling loop: check running counts, schedule where under cap, sleep, repeat.
        Runs that fail to schedule MAX_SCHEDULE_ATTEMPTS times are marked failed and dropped.
        """
        MAX_SCHEDULE_ATTEMPTS = 3
        pending = list(runs)
        running_by_model: dict[str, int] = {}
        attempt_counts: dict[str, int] = {}

        while pending:
            scheduled_this_round = []
            failed_this_round = []
            for run in pending:
                cap = self.concurrency_caps.get(run.model_id, self.default_cap)
                current = running_by_model.get(run.model_id, 0)
                if current < cap:
                    try:
                        self._create_k8s_job(experiment_id, run)
                        await self.db.update_run(run.id, status="running")
                        running_by_model[run.model_id] = current + 1
                        scheduled_this_round.append(run)
                    except Exception as e:
                        attempt_counts[run.id] = attempt_counts.get(run.id, 0) + 1
                        logger.error(
                            f"Failed to create K8s job for run {run.id} "
                            f"(attempt {attempt_counts[run.id]}/{MAX_SCHEDULE_ATTEMPTS}): {e}"
                        )
                        if attempt_counts[run.id] >= MAX_SCHEDULE_ATTEMPTS:
                            logger.error(
                                f"Run {run.id} exceeded max schedule attempts; marking failed"
                            )
                            try:
                                await self.db.update_run(
                                    run.id,
                                    status="failed",
                                    error=f"K8s job creation failed after {MAX_SCHEDULE_ATTEMPTS} attempts: {e}",
                                )
                            except Exception as db_err:
                                logger.error(f"Could not mark run {run.id} failed in DB: {db_err}")
                            failed_this_round.append(run)

            for run in scheduled_this_round + failed_this_round:
                pending.remove(run)

            if pending:
                await asyncio.sleep(10)

    @staticmethod
    def _k8s_safe(value: str, max_len: int = 63) -> str:
        """Coerce an arbitrary string into an RFC 1123 subdomain / label value.

        Replaces any character outside [a-z0-9-.] with '-', lowercases, strips
        leading/trailing non-alphanumerics, and truncates to max_len.
        """
        import re
        sanitized = re.sub(r"[^a-z0-9.-]", "-", value.lower())
        sanitized = re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", sanitized)
        return sanitized[:max_len] or "x"

    def _create_k8s_job(self, experiment_id: str, run) -> None:
        if not K8S_AVAILABLE or self.k8s_client is None:
            logger.warning(f"K8s not available — skipping job creation for run {run.id}")
            return

        # K8s Job name is at most 63 chars and must be an RFC 1123 subdomain.
        # Append a short hash of the full run.id so names remain unique even
        # after aggressive truncation of structurally similar IDs.
        import hashlib
        run_hash = hashlib.sha1(run.id.encode()).hexdigest()[:8]
        job_name = self._k8s_safe(f"exp-{run.id}-{run_hash}", max_len=52)
        # Guarantee the hash stays in the name even if slugification dropped it.
        if run_hash not in job_name:
            job_name = f"{job_name[: 63 - len(run_hash) - 1]}-{run_hash}"

        # Under http transport the PVC is read-only for workers (they only read
        # datasets/devdocs/run-config).  A dedicated emptyDir is used for tmp output.
        use_http_transport = self.result_transport == "http"

        # Build volume/mount lists, then extend them if extensions need it.
        volume_mounts = [
            kubernetes.client.V1VolumeMount(
                name="shared-data",
                mount_path="/data",
                read_only=use_http_transport,
            ),
            kubernetes.client.V1VolumeMount(
                name="config",
                mount_path="/app/config",
            ),
        ]
        volumes = [
            kubernetes.client.V1Volume(
                name="shared-data",
                persistent_volume_claim=kubernetes.client.V1PersistentVolumeClaimVolumeSource(
                    claim_name=self.pvc_name
                ),
            ),
            kubernetes.client.V1Volume(
                name="config",
                config_map=kubernetes.client.V1ConfigMapVolumeSource(
                    name=self.config_map_name
                ),
            ),
        ]

        if use_http_transport:
            volume_mounts.append(
                kubernetes.client.V1VolumeMount(
                    name="worker-tmp",
                    mount_path="/tmp/worker",
                )
            )
            volumes.append(
                kubernetes.client.V1Volume(
                    name="worker-tmp",
                    empty_dir=kubernetes.client.V1EmptyDirVolumeSource(),
                )
            )

        # DevDocs emptyDir fallback ------------------------------------------------
        # In environments where the devdocs-sync Job has not run (e.g. kind/e2e
        # clusters where workerTools.devdocs.enabled=false), the devdocs docset
        # directory is absent from the shared PVC and the worker's devdocs extension
        # builder raises RuntimeError on start-up.
        #
        # Fix: when the DEVDOCS extension is requested and the docset root does NOT
        # exist on the coordinator's own filesystem (the coordinator also mounts the
        # shared PVC at STORAGE_ROOT), inject an emptyDir volume at the expected
        # mount path so the path exists in the worker pod.  The devdocs MCP server
        # starts cleanly with no docsets, which is correct for an e2e plumbing test.
        #
        # When the devdocs-sync Job HAS run (production), the docset root exists, so
        # we skip the emptyDir and the worker finds the real data through the shared
        # PVC mount at /data.
        if ToolExtension.DEVDOCS in run.tool_extensions:
            devdocs_root = Path(os.environ.get("DEVDOCS_ROOT", "/data/devdocs"))
            if not devdocs_root.exists():
                volume_mounts.append(
                    kubernetes.client.V1VolumeMount(
                        name="devdocs-empty",
                        mount_path=str(devdocs_root),
                    )
                )
                volumes.append(
                    kubernetes.client.V1Volume(
                        name="devdocs-empty",
                        empty_dir=kubernetes.client.V1EmptyDirVolumeSource(),
                    )
                )
                logger.info(
                    "DevDocs docset root %s absent from shared PVC; "
                    "mounting emptyDir for run %s so the extension starts cleanly.",
                    devdocs_root,
                    run.id,
                )

        # Labels shared between the Job metadata and the pod template metadata.
        # Kubernetes does NOT propagate Job labels to pods automatically, so we
        # set them explicitly on both to ensure the NetworkPolicy selector
        # (app: sec-review-worker) matches the spawned pods.
        _pod_labels = {
            "app": "sec-review-worker",
            "experiment": self._k8s_safe(experiment_id),
            "model": self._k8s_safe(run.model_id),
            "strategy": self._k8s_safe(run.strategy.value),
            "run-hash": run_hash,
        }

        job = kubernetes.client.V1Job(
            metadata=kubernetes.client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                labels=_pod_labels,
                annotations={
                    "sec-review.io/run-id": run.id,
                    "sec-review.io/experiment-id": experiment_id,
                },
            ),
            spec=kubernetes.client.V1JobSpec(
                backoff_limit=1,
                active_deadline_seconds=1800,
                template=kubernetes.client.V1PodTemplateSpec(
                    metadata=kubernetes.client.V1ObjectMeta(labels=_pod_labels),
                    spec=kubernetes.client.V1PodSpec(
                        restart_policy="Never",
                        containers=[
                            kubernetes.client.V1Container(
                                name="worker",
                                image=self.worker_image,
                                image_pull_policy=self.worker_image_pull_policy,
                                command=[
                                    "python", "-m", "sec_review_framework.worker",
                                    "--run-config", f"/data/config/runs/{run.id}.json",
                                    "--output-dir", (
                                        "/tmp/worker/out"
                                        if use_http_transport
                                        else f"/data/outputs/{experiment_id}/{run.id}"
                                    ),
                                    "--datasets-dir", "/data/datasets",
                                ],
                                env=[
                                    kubernetes.client.V1EnvVar(
                                        name="CONFIG_DIR", value="/app/config"
                                    ),
                                    kubernetes.client.V1EnvVar(
                                        name="TOOL_EXTENSIONS",
                                        value=",".join(sorted(e.value for e in run.tool_extensions)),
                                    ),
                                    *(
                                        [
                                            kubernetes.client.V1EnvVar(
                                                name="COORDINATOR_INTERNAL_URL",
                                                value=self.coordinator_internal_url,
                                            )
                                        ]
                                        if use_http_transport
                                        else []
                                    ),
                                ],
                                env_from=[
                                    kubernetes.client.V1EnvFromSource(
                                        secret_ref=kubernetes.client.V1SecretEnvSource(
                                            name=self.llm_secret_name
                                        )
                                    )
                                ],
                                volume_mounts=volume_mounts,
                            )
                        ],
                        volumes=volumes,
                    )
                ),
            ),
        )
        self.k8s_client.create_namespaced_job(self.namespace, job)

    async def get_experiment_status(self, experiment_id: str) -> ExperimentStatus:
        experiment = await self.db.get_experiment(experiment_id)
        if not experiment:
            raise HTTPException(status_code=404, detail=f"Experiment {experiment_id} not found")
        counts = await self.db.count_runs_by_status(experiment_id)
        spend = await self.db.get_experiment_spend(experiment_id)
        return ExperimentStatus(
            experiment_id=experiment_id,
            total=experiment["total_runs"] or 0,
            completed=counts.get("completed", 0),
            failed=counts.get("failed", 0),
            running=counts.get("running", 0),
            pending=counts.get("pending", 0),
            estimated_cost_so_far=spend,
        )

    def collect_results(self, experiment_id: str) -> list[RunResult]:
        results = []
        experiment_dir = self.storage_root / "outputs" / experiment_id
        if not experiment_dir.exists():
            return results
        for run_dir in experiment_dir.iterdir():
            if not run_dir.is_dir():
                continue
            result_file = run_dir / "run_result.json"
            if result_file.exists():
                try:
                    results.append(RunResult.model_validate_json(result_file.read_text()))
                except Exception as e:
                    logger.error(f"Failed to parse result from {result_file}: {e}")
        return results

    def get_accuracy_matrix(self) -> dict:
        outputs_dir = self.storage_root / "outputs"
        cells: dict[tuple[str, str], list[float]] = {}
        if outputs_dir.exists():
            for experiment_dir in outputs_dir.iterdir():
                if not experiment_dir.is_dir():
                    continue
                for run_dir in experiment_dir.iterdir():
                    if not run_dir.is_dir():
                        continue
                    result_file = run_dir / "run_result.json"
                    if not result_file.exists():
                        continue
                    try:
                        result = RunResult.model_validate_json(result_file.read_text())
                        if result.evaluation is None:
                            continue
                        model = result.experiment.model_id
                        strategy = result.experiment.strategy.value
                        key = (model, strategy)
                        cells.setdefault(key, []).append(result.evaluation.recall)
                    except Exception as e:
                        logger.warning(f"Skipping result in {result_file}: {e}")

        all_models = sorted({k[0] for k in cells})
        all_strategies = sorted({k[1] for k in cells})
        cell_list = []
        for (model, strategy), recalls in cells.items():
            avg_recall = sum(recalls) / len(recalls)
            cell_list.append({
                "model": model,
                "strategy": strategy,
                "accuracy": round(avg_recall, 4),
                "run_count": len(recalls),
            })
        return {
            "models": all_models,
            "strategies": all_strategies,
            "cells": cell_list,
        }

    async def handle_run_result_upload(self, run_id: str, request: Request) -> dict:
        """Handle a streamed multipart artifact upload from a worker pod.

        Flow:
          1. Extract bearer token from Authorization header.
          2. Consume (atomic single-use) the upload token — returns 403 on failure.
          3. Derive experiment_id from DB.
          4. Create a staging dir under /data/outputs/{exp}/.staging-{run_id}-{uuid}/
          5. Stream multipart parts to disk (never buffer in memory).
          6. Validate filenames against allowlist; enforce per-artifact size cap.
          7. Verify run_result.json is present.
          8. os.rename(staging → final); on FileExistsError → 409.
        """
        import time as _time
        import uuid as _uuid

        from multipart.multipart import MultipartParser as _MultipartParser

        t_start = _time.monotonic()

        # --- Auth ---
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            _upload_counter.labels(outcome="auth_missing").inc()
            raise HTTPException(status_code=403, detail="Missing bearer token")
        token = auth_header[len("Bearer "):]

        if not await self.db.consume_upload_token(run_id, token):
            _upload_counter.labels(outcome="auth_failed").inc()
            raise HTTPException(status_code=403, detail="Invalid or already-used upload token")

        # --- Resolve experiment ---
        run_row = await self.db.get_run(run_id)
        if run_row is None:
            _upload_counter.labels(outcome="not_found").inc()
            raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
        experiment_id: str = run_row["experiment_id"]

        # --- Staging dir ---
        outputs_dir = self.storage_root / "outputs" / experiment_id
        staging_dir = outputs_dir / f".staging-{run_id}-{_uuid.uuid4().hex}"
        final_dir = outputs_dir / run_id

        staging_dir.mkdir(parents=True, exist_ok=True)

        # Allowed filenames and per-file size caps (bytes).
        _DEFAULT_MAX = int(os.environ.get("MAX_ARTIFACT_BYTES", str(16 * 1024 * 1024)))
        _LARGE_MAX = int(os.environ.get("MAX_ARTIFACT_BYTES_LARGE", str(256 * 1024 * 1024)))
        # Global aggregate cap across all parts — guards against many small artifacts
        # totalling far more than any single per-artifact cap.
        _MAX_TOTAL_BYTES = int(os.environ.get("MAX_TOTAL_BYTES", str(512 * 1024 * 1024)))
        _ALLOWED = {
            "run_result.json",
            "findings.jsonl",
            "tool_calls.jsonl",
            "conversation.jsonl",
            "findings_pre_verification.jsonl",
            "report.md",
        }
        _SIZE_CAPS: dict[str, int] = {
            "conversation.jsonl": _LARGE_MAX,
        }

        # --- Stream multipart parts to disk ---
        content_type = request.headers.get("content-type", "")
        boundary: bytes | None = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part[len("boundary="):].strip('"').encode("latin-1")
                break

        if boundary is None:
            shutil.rmtree(staging_dir, ignore_errors=True)
            _upload_counter.labels(outcome="bad_request").inc()
            raise HTTPException(status_code=400, detail="Missing multipart boundary")

        received_files: set[str] = set()
        total_bytes_received = 0
        errors: list[str] = []
        # Sentinel list so _on_part_data can signal aggregate limit exceeded.
        _total_exceeded: list[bool] = [False]

        # State for the streaming parser callbacks
        current_filename: list[str | None] = [None]
        current_fh: list[object] = [None]
        current_size: list[int] = [0]
        current_cap: list[int] = [_DEFAULT_MAX]
        size_exceeded: list[bool] = [False]

        def _on_part_begin() -> None:
            current_filename[0] = None
            current_fh[0] = None
            current_size[0] = 0
            current_cap[0] = _DEFAULT_MAX
            size_exceeded[0] = False

        def _on_header_field(data: bytes, start: int, end: int) -> None:
            pass  # We'll parse header value for Content-Disposition

        _hdr_name: list[bytes] = [b""]
        _hdr_val: list[bytes] = [b""]

        def _on_header_field_cb(data: bytes, start: int, end: int) -> None:
            _hdr_name[0] = _hdr_name[0] + data[start:end]

        def _on_header_value_cb(data: bytes, start: int, end: int) -> None:
            _hdr_val[0] = _hdr_val[0] + data[start:end]

        def _on_header_end() -> None:
            name = _hdr_name[0].decode("latin-1", errors="replace").lower().strip()
            val = _hdr_val[0].decode("latin-1", errors="replace").strip()
            if name == "content-disposition":
                # Extract filename from: form-data; name="file"; filename="foo.json"
                fname: str | None = None
                for seg in val.split(";"):
                    seg = seg.strip()
                    if seg.lower().startswith("filename="):
                        fname = seg[len("filename="):].strip('"').strip("'")
                if fname is not None:
                    # Path-traversal protection: reject any non-plain filename
                    import posixpath as _pp
                    safe = _pp.basename(fname.replace("\\", "/"))
                    if safe in _ALLOWED:
                        current_filename[0] = safe
                        current_cap[0] = _SIZE_CAPS.get(safe, _DEFAULT_MAX)
            _hdr_name[0] = b""
            _hdr_val[0] = b""

        def _on_headers_finished() -> None:
            fname = current_filename[0]
            if fname and fname not in received_files:
                current_fh[0] = open(staging_dir / fname, "wb")
            else:
                current_fh[0] = None

        def _on_part_data(data: bytes, start: int, end: int) -> None:
            nonlocal total_bytes_received
            chunk = data[start:end]
            current_size[0] += len(chunk)
            total_bytes_received += len(chunk)
            if total_bytes_received > _MAX_TOTAL_BYTES:
                _total_exceeded[0] = True
                raise RuntimeError(
                    f"Aggregate upload size exceeds limit "
                    f"({_MAX_TOTAL_BYTES // (1024 * 1024)} MiB)"
                )
            if current_size[0] > current_cap[0]:
                size_exceeded[0] = True
                return
            fh = current_fh[0]
            if fh is not None:
                fh.write(chunk)  # type: ignore[union-attr]

        def _on_part_end() -> None:
            fh = current_fh[0]
            fname = current_filename[0]
            if fh is not None:
                fh.close()  # type: ignore[union-attr]
                current_fh[0] = None
            if size_exceeded[0]:
                # Remove oversized file
                if fname:
                    oversized = staging_dir / fname
                    if oversized.exists():
                        oversized.unlink()
                errors.append(
                    f"{fname}: exceeds max size "
                    f"({current_cap[0] // (1024*1024)} MiB)"
                )
            elif fname is not None:
                received_files.add(fname)

        callbacks = {
            "on_part_begin": _on_part_begin,
            "on_header_field": _on_header_field_cb,
            "on_header_value": _on_header_value_cb,
            "on_header_end": _on_header_end,
            "on_headers_finished": _on_headers_finished,
            "on_part_data": _on_part_data,
            "on_part_end": _on_part_end,
        }

        parser = _MultipartParser(boundary, callbacks)
        try:
            async for chunk in request.stream():
                parser.write(chunk)
        except Exception as exc:
            shutil.rmtree(staging_dir, ignore_errors=True)
            if _total_exceeded[0]:
                _upload_counter.labels(outcome="size_exceeded").inc()
                raise HTTPException(
                    status_code=413,
                    detail=f"Aggregate upload size exceeds limit ({_MAX_TOTAL_BYTES // (1024 * 1024)} MiB)",
                ) from exc
            _upload_counter.labels(outcome="stream_error").inc()
            logger.error("Upload stream error for run %s: %s", run_id, exc)
            raise HTTPException(status_code=400, detail=f"Stream error: {exc}") from exc
        finally:
            # Ensure any open file handle is closed even on error
            if current_fh[0] is not None:
                try:
                    current_fh[0].close()  # type: ignore[union-attr]
                except Exception:
                    pass

        if errors:
            shutil.rmtree(staging_dir, ignore_errors=True)
            _upload_counter.labels(outcome="size_exceeded").inc()
            raise HTTPException(status_code=413, detail={"errors": errors})

        if "run_result.json" not in received_files:
            shutil.rmtree(staging_dir, ignore_errors=True)
            _upload_counter.labels(outcome="missing_result").inc()
            raise HTTPException(
                status_code=422, detail="run_result.json is required but was not uploaded"
            )

        # --- Atomic rename staging → final ---
        # Pre-check: on Linux os.rename() raises OSError(ENOTEMPTY) not
        # FileExistsError when the target directory already exists, so an
        # explicit existence check gives us a reliable 409.
        if final_dir.exists():
            shutil.rmtree(staging_dir, ignore_errors=True)
            _upload_counter.labels(outcome="conflict").inc()
            raise HTTPException(
                status_code=409,
                detail=f"Result for run {run_id!r} already exists",
            )
        try:
            os.rename(staging_dir, final_dir)
        except FileExistsError:
            shutil.rmtree(staging_dir, ignore_errors=True)
            _upload_counter.labels(outcome="conflict").inc()
            raise HTTPException(
                status_code=409,
                detail=f"Result for run {run_id!r} already exists",
            )
        except OSError as exc:
            # Cross-device move fallback (shouldn't happen on single PVC but be safe)
            try:
                import shutil as _shutil
                _shutil.copytree(str(staging_dir), str(final_dir))
                shutil.rmtree(staging_dir, ignore_errors=True)
            except FileExistsError:
                shutil.rmtree(staging_dir, ignore_errors=True)
                _upload_counter.labels(outcome="conflict").inc()
                raise HTTPException(
                    status_code=409,
                    detail=f"Result for run {run_id!r} already exists",
                ) from exc
            except Exception as inner_exc:
                shutil.rmtree(staging_dir, ignore_errors=True)
                _upload_counter.labels(outcome="rename_error").inc()
                raise HTTPException(
                    status_code=500, detail=f"Failed to commit result: {inner_exc}"
                ) from inner_exc

        duration_ms = int((_time.monotonic() - t_start) * 1000)
        logger.info(
            "Upload complete",
            extra={
                "run_id": run_id,
                "experiment_id": experiment_id,
                "bytes_received": total_bytes_received,
                "duration_ms": duration_ms,
                "outcome": "ok",
            },
        )
        _upload_counter.labels(outcome="ok").inc()
        return {
            "run_id": run_id,
            "experiment_id": experiment_id,
            "files": sorted(received_files),
            "bytes_received": total_bytes_received,
        }

    async def finalize_experiment(self, experiment_id: str) -> None:
        results = self.collect_results(experiment_id)
        output_dir = self.storage_root / "outputs" / experiment_id
        output_dir.mkdir(parents=True, exist_ok=True)
        if results and self.reporter:
            self.reporter.render_matrix(results, output_dir)
        await self.db.update_experiment_status(
            experiment_id, "completed", completed_at=datetime.now(UTC).replace(tzinfo=None).isoformat()
        )
        # Invalidate the trend cache so the next request reflects this new data.
        self._invalidate_trends_cache()

    async def cancel_experiment(self, experiment_id: str) -> int:
        # Guard: do not clobber an already-terminal experiment's status.
        experiment = await self.db.get_experiment(experiment_id)
        if experiment is None:
            return 0
        if experiment["status"] in ("completed", "failed", "cancelled"):
            return 0
        cancelled = 0
        if K8S_AVAILABLE and self.k8s_client:
            try:
                jobs = self.k8s_client.list_namespaced_job(
                    self.namespace, label_selector=f"experiment={experiment_id}"
                )
                for job in jobs.items:
                    if not job.status.succeeded and not job.status.failed:
                        self.k8s_client.delete_namespaced_job(
                            job.metadata.name, self.namespace
                        )
                        cancelled += 1
            except Exception as e:
                logger.error(f"Error cancelling K8s jobs for experiment {experiment_id}: {e}")
        # Flip any non-terminal runs to "cancelled" so status counts reflect the
        # experiment state. Runs that already completed/failed on disk keep their
        # terminal status.
        runs = await self.db.list_runs(experiment_id)
        for run in runs:
            if run["status"] in ("pending", "running"):
                await self.db.update_run(run["id"], status="cancelled")
        await self.db.update_experiment_status(experiment_id, "cancelled")
        return cancelled

    async def delete_experiment(self, experiment_id: str) -> None:
        await self.cancel_experiment(experiment_id)
        # Revoke any pending upload tokens before removing files.
        await self.db.revoke_upload_tokens_for_experiment(experiment_id)
        experiment_dir = self.storage_root / "outputs" / experiment_id
        if experiment_dir.exists():
            shutil.rmtree(experiment_dir, ignore_errors=True)
        config_dir = self.storage_root / "config" / "runs"
        runs = await self.db.list_runs(experiment_id)
        for run in runs:
            run_config = config_dir / f"{run['id']}.json"
            if run_config.exists():
                run_config.unlink()
        # Remove all DB rows for the experiment (findings → runs → experiment).
        await self.db.delete_experiment(experiment_id)
        # Invalidate trends cache so a deleted completed experiment stops
        # appearing in the graph immediately instead of within the 60s TTL.
        self._invalidate_trends_cache()

    # ------------------------------------------------------------------
    # Reconciliation (called at startup)
    # ------------------------------------------------------------------

    async def reconcile(self) -> None:
        """
        Called once at coordinator startup. Scans for experiments in non-terminal
        states, identifies runs that were never dispatched as K8s Jobs, and
        creates their Jobs. Idempotent — safe to call multiple times.
        """
        experiments = await self.db.list_experiments()
        active = [b for b in experiments if b["status"] not in ("completed", "cancelled", "failed")]

        for experiment in active:
            experiment_id = experiment["id"]
            dispatched_run_ids: set[str] = set()

            if K8S_AVAILABLE and self.k8s_client:
                try:
                    existing_jobs = self.k8s_client.list_namespaced_job(
                        self.namespace, label_selector=f"experiment={experiment_id}"
                    )
                    dispatched_run_ids = {
                        job.metadata.labels.get("run-id")
                        for job in existing_jobs.items
                        if job.metadata.labels
                    }
                except Exception as e:
                    logger.error(f"Error listing jobs for experiment {experiment_id}: {e}")

            all_runs = await self.db.list_runs(experiment_id)
            orphaned = [
                r for r in all_runs
                if r["status"] == "pending" and r["id"] not in dispatched_run_ids
            ]

            if orphaned:
                logger.warning(
                    f"Experiment {experiment_id}: {len(orphaned)} orphaned runs — dispatching"
                )
                config_dir = self.storage_root / "config" / "runs"
                for run_data in orphaned:
                    run_config_path = config_dir / f"{run_data['id']}.json"
                    if not run_config_path.exists():
                        logger.warning(
                            "Run %s has no config file — experiment likely deleted; "
                            "marking failed",
                            run_data["id"],
                        )
                        await self.db.update_run(
                            run_data["id"],
                            status="failed",
                            error="run config missing — experiment likely deleted",
                        )
                        continue
                    from sec_review_framework.data.experiment import ExperimentRun
                    try:
                        run = ExperimentRun.model_validate_json(run_config_path.read_text())
                    except Exception as _parse_exc:
                        logger.warning(
                            "Run %s config file is malformed — marking failed: %s",
                            run_data["id"],
                            _parse_exc,
                        )
                        await self.db.update_run(
                            run_data["id"],
                            status="failed",
                            error=f"run config malformed: {_parse_exc}",
                        )
                        continue
                    # For HTTP transport: skip re-dispatch if the token has already been
                    # consumed (meaning the run completed successfully via upload).
                    if run.result_transport == "http":
                        if await self.db.is_upload_token_consumed(run.id):
                            logger.info(
                                "Run %s upload token already consumed — skipping re-dispatch",
                                run.id,
                            )
                            continue
                        # Token not yet issued — this shouldn't happen normally since
                        # submit_experiment issues tokens, but handle gracefully.
                        if not await self.db.get_upload_token_issued(run.id):
                            logger.warning(
                                "Run %s has no upload token; issuing one for re-dispatch",
                                run.id,
                            )
                            token = await self.db.issue_upload_token(run.id)
                            upload_url = (
                                f"{self.coordinator_internal_url}"
                                f"/api/internal/runs/{run.id}/result"
                            )
                            run = run.model_copy(update={
                                "result_transport": "http",
                                "upload_url": upload_url,
                                "upload_token": token,
                            })
                            import json as _json
                            run_dict = run.model_dump(mode="json")
                            run_dict["upload_token"] = run.upload_token
                            run_config_path.write_text(
                                _json.dumps(run_dict, indent=2, default=str)
                            )
                    try:
                        self._create_k8s_job(experiment_id, run)
                    except Exception as e:
                        if (
                            K8S_AVAILABLE
                            and kubernetes
                            and isinstance(e, kubernetes.client.ApiException)
                            and e.status == 409
                        ):
                            pass  # Job already exists — race condition, safe to ignore
                        else:
                            logger.error(f"Failed to dispatch orphaned run {run.id}: {e}")

            # Finalize any experiment where all jobs are done
            status = await self.get_experiment_status(experiment_id)
            if status.running == 0 and status.pending == 0 and status.total > 0:
                await self.finalize_experiment(experiment_id)

    # ------------------------------------------------------------------
    # Periodic reconcile loop (flips running → completed / failed)
    # ------------------------------------------------------------------

    async def _reconcile_once(self) -> None:
        """
        Single iteration of the result-scanning reconcile step.

        1. Calls existing ``reconcile()`` so any orphaned-pending runs are
           re-dispatched (idempotent).
        2. For every active experiment, reads ``run_result.json`` for each
           ``running`` run and updates the DB accordingly.
        3. Finalises experiments where running + pending == 0.

        Safe to call multiple times; will not double-finalize.
        """
        # Step 1: re-dispatch orphaned pending runs (existing logic)
        try:
            await self.reconcile()
        except Exception as exc:
            logger.error("reconcile() raised during _reconcile_once: %s", exc)

        # Step 2: scan result files for running runs
        try:
            experiments = await self.db.list_experiments()
        except Exception as exc:
            logger.error("Failed to list experiments in _reconcile_once: %s", exc)
            return

        active = [
            b for b in experiments
            if b["status"] not in ("completed", "cancelled", "failed")
        ]

        for experiment in active:
            experiment_id = experiment["id"]
            try:
                await self._reconcile_experiment_once(experiment, experiment_id)
            except Exception as exc:
                logger.error(
                    "Unhandled error processing experiment %s in _reconcile_once — skipping: %s",
                    experiment_id, exc,
                )

    async def _reconcile_experiment_once(self, experiment: dict, experiment_id: str) -> None:
        """Process one experiment within _reconcile_once.

        Extracted so that a transient DB error on one experiment does not halt
        processing of other experiments — the caller catches any exception raised
        here and continues to the next experiment.
        """
        # Re-check status in case another iteration already finalised it
        fresh_experiment = await self.db.get_experiment(experiment_id)
        if not fresh_experiment or fresh_experiment["status"] in (
            "completed", "cancelled", "failed"
        ):
            return

        try:
            runs = await self.db.list_runs(experiment_id)
        except Exception as exc:
            logger.error(
                "Failed to list runs for experiment %s: %s", experiment_id, exc
            )
            return

        for run in runs:
            if run["status"] != "running":
                continue

            run_id = run["id"]
            result_file = (
                self.storage_root / "outputs" / experiment_id / run_id / "run_result.json"
            )

            if result_file.exists():
                # Parse result and update DB
                try:
                    result = RunResult.model_validate_json(result_file.read_text())
                except Exception as exc:
                    logger.error(
                        "Failed to parse run_result.json for run %s: %s — marking failed",
                        run_id, exc,
                    )
                    await self.db.update_run(
                        run_id,
                        status="failed",
                        error=f"run_result.json parse error: {exc}",
                        completed_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
                    )
                    continue

                await self.db.update_run(
                    run_id,
                    status=result.status.value,
                    duration_seconds=result.duration_seconds,
                    error=result.error,
                    completed_at=(
                        result.completed_at.isoformat()
                        if result.completed_at
                        else datetime.now(UTC).replace(tzinfo=None).isoformat()
                    ),
                    estimated_cost_usd=result.estimated_cost_usd,
                )

                # Index findings for cross-experiment search
                if result.findings:
                    try:
                        await self.db.upsert_findings_for_run(
                            run_id=run_id,
                            experiment_id=experiment_id,
                            findings=[f.model_dump(mode="json") for f in result.findings],
                            model_id=result.experiment.model_id,
                            strategy=result.experiment.strategy.value,
                            dataset_name=result.experiment.dataset_name,
                        )
                    except Exception as exc:
                        logger.error(
                            "Failed to index findings for run %s: %s", run_id, exc
                        )

                # Record cost against the experiment — only if the run had no prior
                # cost recorded (estimated_cost_usd was NULL or zero in DB).
                prior_cost = run.get("estimated_cost_usd")
                if (prior_cost is None or prior_cost == 0.0) and result.estimated_cost_usd:
                    tracker = self._cost_trackers.get(experiment_id)
                    if tracker is None:
                        # Coordinator restarted mid-experiment; rebuild tracker from DB
                        max_cost = experiment.get("max_cost_usd")
                        tracker = ExperimentCostTracker(experiment_id, max_cost)
                        self._cost_trackers[experiment_id] = tracker
                    cap_exceeded = tracker.record_job_cost(result.estimated_cost_usd)
                    await self.db.add_experiment_spend(experiment_id, result.estimated_cost_usd)
                    if cap_exceeded:
                        logger.warning(
                            "Experiment %s exceeded cost cap — cancelling", experiment_id
                        )
                        await self.cancel_experiment(experiment_id)
                        break  # don't process more runs; experiment is now cancelled

            else:
                # No result file yet — check K8s Job status
                if K8S_AVAILABLE and self.k8s_client:
                    try:
                        jobs = self.k8s_client.list_namespaced_job(
                            self.namespace,
                            label_selector=f"experiment={experiment_id}",
                        )
                        run_jobs = [
                            j for j in jobs.items
                            if j.metadata.annotations
                            and j.metadata.annotations.get(
                                "sec-review.io/run-id"
                            ) == run_id
                        ]
                        if not run_jobs:
                            # Job TTL-reaped or never created — mark failed
                            logger.warning(
                                "No K8s Job found for run %s — marking failed", run_id
                            )
                            await self.db.update_run(
                                run_id,
                                status="failed",
                                error="K8s Job not found (TTL-reaped or creation failed)",
                                completed_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
                            )
                        else:
                            job = run_jobs[0]
                            if (job.status.failed or 0) > 0 or (
                                job.status.succeeded or 0
                            ) > 0:
                                # Job terminal but no result file
                                logger.warning(
                                    "K8s Job for run %s is terminal but no result file "
                                    "— marking failed",
                                    run_id,
                                )
                                await self.db.update_run(
                                    run_id,
                                    status="failed",
                                    error=(
                                        "K8s Job finished but run_result.json not found"
                                    ),
                                    completed_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
                                )
                            else:
                                # Job appears active but may be stalled (no live pods)
                                await self._check_stalled_job(run_id, job)
                    except Exception as exc:
                        logger.error(
                            "Error checking K8s job for run %s: %s", run_id, exc
                        )
                # No K8s available — leave status as running until result appears

        # Step 3: finalise if all runs are done
        # Re-check experiment status (may have been cancelled above)
        fresh_experiment2 = await self.db.get_experiment(experiment_id)
        if not fresh_experiment2 or fresh_experiment2["status"] in (
            "completed", "cancelled", "failed"
        ):
            return

        counts = await self.db.count_runs_by_status(experiment_id)
        running_count = counts.get("running", 0)
        pending_count = counts.get("pending", 0)
        total = fresh_experiment2.get("total_runs") or 0
        if total > 0 and running_count == 0 and pending_count == 0:
            logger.info("Experiment %s complete — finalising", experiment_id)
            try:
                await self.finalize_experiment(experiment_id)
            except Exception as exc:
                logger.error("finalize_experiment(%s) raised: %s", experiment_id, exc)

    async def _check_stalled_job(self, run_id: str, job) -> None:
        """
        Detect a K8s Job that reports active>0 but has no live pods and has
        been in that state longer than RUN_STALL_TIMEOUT_S.  Mark the run
        failed and delete the stuck Job so it doesn't keep accumulating.
        """
        if not K8S_AVAILABLE or self.k8s_client is None:
            return

        # Only inspect jobs that are actually active according to the Job status
        if (job.status.active or 0) == 0:
            return

        # Check how old the job is
        start_time = None
        if job.status.start_time:
            start_time = job.status.start_time
        elif job.metadata.creation_timestamp:
            start_time = job.metadata.creation_timestamp

        if start_time is not None:
            # start_time may be tz-aware; use utcnow with tzinfo for comparison
            now_utc = datetime.now(UTC)
            # Make start_time tz-aware if needed
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=UTC)
            age_seconds = (now_utc - start_time).total_seconds()
            if age_seconds < RUN_STALL_TIMEOUT_S:
                return  # Too young to be declared stalled

        job_name = job.metadata.name

        try:
            # CoreV1Api owns list_namespaced_pod; self.k8s_client is BatchV1Api.
            # Lazily construct a CoreV1Api here — incluster config was already
            # loaded during __init__ so this is safe.
            core_v1 = kubernetes.client.CoreV1Api()
            pod_list = core_v1.list_namespaced_pod(
                self.namespace,
                label_selector=f"job-name={job_name}",
            )
            live_pods = [
                p for p in pod_list.items
                if p.status and p.status.phase in ("Pending", "Running", "Unknown")
            ]
        except Exception as exc:
            logger.error(
                "Error listing pods for job %s (run %s): %s", job_name, run_id, exc
            )
            return

        if live_pods:
            return  # Still has live pods — not stalled

        logger.warning(
            "Run %s: Job %s has active=%d but no live pods and age >= %ds "
            "— marking failed (likely OOM or eviction)",
            run_id,
            job_name,
            job.status.active or 0,
            RUN_STALL_TIMEOUT_S,
        )
        await self.db.update_run(
            run_id,
            status="failed",
            error="Job has no active pods; likely OOM or eviction",
            completed_at=datetime.now(UTC).replace(tzinfo=None).isoformat(),
        )
        try:
            self.k8s_client.delete_namespaced_job(
                job_name,
                self.namespace,
                body=kubernetes.client.V1DeleteOptions(propagation_policy="Background"),
            )
            logger.info("Deleted stalled Job %s for run %s", job_name, run_id)
        except Exception as exc:
            logger.error(
                "Failed to delete stalled Job %s (run %s): %s", job_name, run_id, exc
            )

    async def _reconcile_loop(self) -> None:
        """
        Runs ``_reconcile_once()`` every ``RECONCILE_INTERVAL_S`` seconds.
        Exceptions inside ``_reconcile_once`` are caught and logged; the loop
        never crashes the coordinator.
        """
        while True:
            try:
                await self._reconcile_once()
            except Exception as exc:
                logger.error("Unhandled exception in _reconcile_once: %s", exc)
            await asyncio.sleep(RECONCILE_INTERVAL_S)

    # ------------------------------------------------------------------
    # Result access
    # ------------------------------------------------------------------

    async def _serialize_experiment_summary(self, row: dict) -> dict:
        experiment_id = row["id"]
        counts = await self.db.count_runs_by_status(experiment_id)
        dataset = ""
        try:
            config = json.loads(row.get("config_json") or "{}")
            dataset = config.get("dataset_name", "") or ""
        except (json.JSONDecodeError, TypeError):
            pass
        return {
            "experiment_id": experiment_id,
            "status": row.get("status") or "pending",
            "dataset": dataset,
            "created_at": row.get("created_at") or "",
            "completed_at": row.get("completed_at"),
            "total_runs": row.get("total_runs") or 0,
            "completed_runs": counts.get("completed", 0),
            "running_runs": counts.get("running", 0),
            "pending_runs": counts.get("pending", 0),
            "failed_runs": counts.get("failed", 0),
            "total_cost_usd": float(row.get("spent_usd") or 0.0),
            "spend_cap_usd": (
                float(row["max_cost_usd"]) if row.get("max_cost_usd") is not None else None
            ),
        }

    async def list_experiments(self) -> list[dict]:
        rows = await self.db.list_experiments()
        return [await self._serialize_experiment_summary(r) for r in rows]

    async def get_experiment_detail(self, experiment_id: str) -> dict:
        row = await self.db.get_experiment(experiment_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Experiment {experiment_id} not found")
        return await self._serialize_experiment_summary(row)

    async def list_runs(self, experiment_id: str) -> list[dict]:
        return await self.db.list_runs(experiment_id)

    async def get_run_result(self, experiment_id: str, run_id: str) -> dict:
        run_data = await self.db.get_run(run_id)
        if not run_data or run_data.get("experiment_id") != experiment_id:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        result_path = run_data.get("result_path")
        run_dir: Path | None = None
        if result_path and Path(result_path).exists():
            payload = RunResult.model_validate_json(Path(result_path).read_text()).model_dump()
            run_dir = Path(result_path).parent
        else:
            result_file = self.storage_root / "outputs" / experiment_id / run_id / "run_result.json"
            if not result_file.exists():
                raise HTTPException(
                    status_code=404, detail=f"Result for run {run_id} not available yet"
                )
            payload = RunResult.model_validate_json(result_file.read_text()).model_dump()
            run_dir = result_file.parent

        # Attach tool_calls and messages from companion JSONL files so the
        # RunDetail UI can render them without separate fetches.
        def _load_jsonl(path: Path) -> list[dict]:
            if not path.exists():
                return []
            out: list[dict] = []
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
            return out

        payload["tool_calls"] = _load_jsonl(run_dir / "tool_calls.jsonl")
        payload["messages"] = _load_jsonl(run_dir / "conversation.jsonl")
        return payload

    def get_matrix_report_json(self, experiment_id: str) -> dict:
        report_file = self.storage_root / "outputs" / experiment_id / "matrix_report.json"
        if not report_file.exists():
            raise HTTPException(status_code=404, detail="Matrix report not available yet")
        return json.loads(report_file.read_text())

    def get_matrix_report_markdown(self, experiment_id: str) -> str:
        report_file = self.storage_root / "outputs" / experiment_id / "matrix_report.md"
        if not report_file.exists():
            raise HTTPException(status_code=404, detail="Matrix report not available yet")
        return report_file.read_text()

    # ------------------------------------------------------------------
    # Findings
    # ------------------------------------------------------------------

    async def search_findings(
        self, experiment_id: str, q: str, run_id: str | None = None
    ) -> list[dict]:
        results = self.collect_results(experiment_id)
        q_lower = q.lower()
        matched = []
        for result in results:
            if run_id and result.experiment.id != run_id:
                continue
            for finding in result.findings:
                searchable = " ".join([
                    finding.title,
                    finding.description,
                    finding.recommendation or "",
                    finding.raw_llm_output,
                ]).lower()
                if q_lower in searchable:
                    matched.append(finding.model_dump())
        return matched

    async def search_findings_global(
        self,
        q: str | None = None,
        vuln_class: list[str] | None = None,
        severity: list[str] | None = None,
        match_status: list[str] | None = None,
        model_id: list[str] | None = None,
        strategy: list[str] | None = None,
        experiment_id: list[str] | None = None,
        dataset_name: list[str] | None = None,
        created_from: str | None = None,
        created_to: str | None = None,
        sort: str = "created_at desc",
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Cross-experiment findings search backed by the findings index table."""
        if limit < 1:
            raise HTTPException(status_code=400, detail="limit must be >= 1")
        if limit > 200:
            raise HTTPException(status_code=400, detail="limit must be <= 200")
        if offset < 0:
            raise HTTPException(status_code=400, detail="offset must be >= 0")

        filters: dict = {}
        if q:
            filters["q"] = q
        if vuln_class:
            filters["vuln_class"] = vuln_class
        if severity:
            filters["severity"] = severity
        if match_status:
            filters["match_status"] = match_status
        if model_id:
            filters["model_id"] = model_id
        if strategy:
            filters["strategy"] = strategy
        if experiment_id:
            filters["experiment_id"] = experiment_id
        if dataset_name:
            filters["dataset_name"] = dataset_name
        if created_from:
            filters["created_from"] = created_from
        if created_to:
            if _DATE_ONLY_RE.match(created_to):
                # Bare YYYY-MM-DD from <input type="date"> — treat as end-of-day
                # inclusive so findings created any time on that date are included.
                created_to = f"{created_to}T23:59:59.999999"
            filters["created_to"] = created_to

        total, items = await self.db.query_findings(
            filters=filters, limit=limit, offset=offset, sort=sort
        )
        facets = await self.db.facet_findings(filters)

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "facets": facets,
            "items": items,
        }

    async def backfill_findings_index(self) -> int:
        """Walk all run_result.json files and index any findings not yet in the DB.

        Returns the number of runs indexed. Idempotent: safe to call multiple
        times since upsert_findings_for_run deletes existing rows first.
        """
        indexed = 0
        outputs_dir = self.storage_root / "outputs"
        if not outputs_dir.exists():
            return 0
        for experiment_dir in outputs_dir.iterdir():
            if not experiment_dir.is_dir():
                continue
            experiment_id = experiment_dir.name
            for run_dir in experiment_dir.iterdir():
                if not run_dir.is_dir():
                    continue
                result_file = run_dir / "run_result.json"
                if not result_file.exists():
                    continue
                try:
                    result = RunResult.model_validate_json(result_file.read_text())
                    if result.findings:
                        await self.db.upsert_findings_for_run(
                            run_id=result.experiment.id,
                            experiment_id=experiment_id,
                            findings=[f.model_dump(mode="json") for f in result.findings],
                            model_id=result.experiment.model_id,
                            strategy=result.experiment.strategy.value,
                            dataset_name=result.experiment.dataset_name,
                        )
                        indexed += 1
                except Exception as exc:
                    logger.warning(
                        "backfill_findings_index: skipping %s — %s", result_file, exc
                    )
        return indexed

    async def reclassify_finding(self, experiment_id: str, run_id: str, req) -> dict:
        result_file = self.storage_root / "outputs" / experiment_id / run_id / "run_result.json"
        if not result_file.exists():
            raise HTTPException(status_code=404, detail="Result not found")
        result = RunResult.model_validate_json(result_file.read_text())
        for finding in result.findings:
            if finding.id == req.finding_id:
                finding.verified = True
                if req.note:
                    finding.verification_evidence = req.note
                break
        else:
            raise HTTPException(
                status_code=404, detail=f"Finding {req.finding_id} not found"
            )
        result_file.write_text(result.model_dump_json(indent=2))

        # Keep the findings index in sync with the reclassification
        try:
            await self.db.update_finding_match_status(req.finding_id, req.status)
        except Exception as exc:
            logger.warning(
                "Could not update findings index for reclassified finding %s: %s",
                req.finding_id, exc,
            )

        return {"status": "reclassified", "finding_id": req.finding_id}

    async def audit_tool_calls(self, experiment_id: str, run_id: str) -> dict:
        import re
        tool_calls_file = (
            self.storage_root / "outputs" / experiment_id / run_id / "tool_calls.jsonl"
        )
        if not tool_calls_file.exists():
            return {"counts_by_tool": {}, "suspicious_calls": []}

        counts: dict[str, int] = {}
        suspicious = []
        url_pattern = re.compile(r"https?://")

        for line in tool_calls_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                tool_name = entry.get("tool_name", "unknown")
                counts[tool_name] = counts.get(tool_name, 0) + 1
                if not tool_name.startswith(AUDIT_URL_CHECK_EXEMPT_PREFIXES):
                    if url_pattern.search(json.dumps(entry.get("inputs", {}))):
                        suspicious.append(entry)
            except Exception as exc:
                logger.warning(
                    "audit_tool_calls: skipping malformed JSONL line in %s — %s: %.80s",
                    tool_calls_file, exc, line,
                )

        return {"counts_by_tool": counts, "suspicious_calls": suspicious}

    async def compare_runs(self, experiment_id: str, run_a_id: str, run_b_id: str) -> dict:
        """Thin shim: same-experiment comparison delegates to the cross-experiment implementation."""
        return await self.compare_runs_cross(
            a_experiment=experiment_id,
            a_run=run_a_id,
            b_experiment=experiment_id,
            b_run=run_b_id,
        )

    async def compare_runs_cross(
        self,
        a_experiment: str,
        a_run: str,
        b_experiment: str,
        b_run: str,
    ) -> dict:
        from sec_review_framework.data.findings import Finding, FindingIdentity

        result_a_dict = await self.get_run_result(a_experiment, a_run)
        result_b_dict = await self.get_run_result(b_experiment, b_run)

        def index_findings(result_dict: dict, run_label: str) -> dict[str, dict]:
            out = {}
            for f in result_dict.get("findings") or []:
                try:
                    finding = Finding.model_validate(f)
                    identity = str(FindingIdentity.from_finding(finding))
                    out[identity] = f
                except Exception as exc:
                    logger.warning(
                        "compare_runs: skipping malformed finding in run %s — %s: %.80s",
                        run_label, exc, str(f),
                    )
            return out

        findings_a = index_findings(result_a_dict, a_run)
        findings_b = index_findings(result_b_dict, b_run)

        both, only_a, only_b = [], [], []
        for identity, finding in findings_a.items():
            if identity in findings_b:
                both.append({"identity": identity, "finding_a": finding, "finding_b": findings_b[identity]})
            else:
                only_a.append({"identity": identity, "finding": finding})
        for identity, finding in findings_b.items():
            if identity not in findings_a:
                only_b.append({"identity": identity, "finding": finding})

        # Gather experiment-level metadata to attach to each run side.
        warnings: list[str] = []

        async def _experiment_meta(exp_id: str) -> dict:
            row = await self.db.get_experiment(exp_id)
            if not row:
                return {"experiment_id": exp_id, "experiment_name": exp_id, "dataset": ""}
            dataset = ""
            try:
                config = json.loads(row.get("config_json") or "{}")
                dataset = config.get("dataset_name", "") or ""
            except (json.JSONDecodeError, TypeError):
                pass
            name = row.get("name") or exp_id
            return {"experiment_id": exp_id, "experiment_name": name, "dataset": dataset}

        meta_a = await _experiment_meta(a_experiment)
        meta_b = await _experiment_meta(b_experiment)

        dataset_mismatch = (
            a_experiment != b_experiment
            and meta_a["dataset"] != meta_b["dataset"]
        )
        if dataset_mismatch:
            if meta_a["dataset"] and meta_b["dataset"]:
                warnings.append(
                    f"Datasets differ: {meta_a['dataset']} vs {meta_b['dataset']} — "
                    "only findings with identical file paths will match via FindingIdentity"
                )
            else:
                # One side's dataset is unknown (missing experiment row or config).
                # Surface the uncertainty rather than silently assuming they match.
                warnings.append(
                    "Could not determine dataset for one or both runs — "
                    "comparison may mix datasets"
                )

        run_a_info: dict = {"id": a_run, **meta_a}
        run_b_info: dict = {"id": b_run, **meta_b}

        return {
            "run_a": run_a_info,
            "run_b": run_b_info,
            "found_by_both": both,
            "only_in_a": only_a,
            "only_in_b": only_b,
            "dataset_mismatch": dataset_mismatch,
            "warnings": warnings,
        }

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_dataset_name(name: str) -> None:
        """Reject dataset names that could cause path traversal or DB confusion.

        Valid names are non-empty strings that contain no NUL bytes, forward
        slashes, backslashes, or ``..`` components.  The special names ``.``
        and ``..`` are also rejected.

        Raises:
            HTTPException(400): if the name fails validation.
        """
        _INVALID = ("\x00", "/", "\\", "..")
        if not name or name in (".", ".."):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid dataset name {name!r}: must be a non-empty string",
            )
        for bad in _INVALID:
            if bad in name:
                raise HTTPException(
                    status_code=400,
                    detail=f"Invalid dataset name {name!r}: contains disallowed sequence {bad!r}",
                )

    async def list_datasets(self) -> list[dict]:
        return await self.db.list_datasets()

    async def rematerialize_dataset(self, name: str) -> dict:
        """Re-materialize a dataset and return its updated materialized_at timestamp.

        Propagates HTTPException (404 / 409) raised by materialize_dataset.
        Wraps unexpected exceptions as HTTP 502.
        """
        try:
            await self.materialize_dataset(name)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Dataset rehydration failed: {exc}",
            ) from exc
        row = await self.db.get_dataset(name)
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown dataset {name}")
        return {"materialized_at": row["materialized_at"]}

    async def materialize_dataset(self, name: str) -> None:
        """Clone (or re-materialize) a dataset onto disk and stamp materialized_at.

        For ``kind='git'``: clones the repo if absent; skips if the repo
        directory already exists (idempotent for recursive base-dataset calls
        during derived dataset materialization).
        For ``kind='derived'``: materializes the base if absent, checks
        templates_version, then replays the injection recipe.

        Validates ``name`` for path traversal characters as defense-in-depth
        (rows inserted via bundle import also pass through here).
        """
        self._validate_dataset_name(name)
        row = await self.db.get_dataset(name)
        if row is None:
            raise HTTPException(status_code=404, detail=f"unknown dataset {name}")
        target_repo_dir = self.storage_root / "datasets" / name / "repo"
        if row["kind"] == "git":
            if not target_repo_dir.exists():
                target_repo_dir.parent.mkdir(parents=True, exist_ok=True)
                # C4: Wrap clone + checkout in a try block.  If checkout fails after
                # a successful clone the directory exists but is at the wrong commit.
                # Remove it so that subsequent calls retry from scratch.
                # N6: use "--" before the URL in git clone to guard against URLs
                # starting with "-".  For checkout the revision is a SHA so we use
                # fetch+checkout to avoid `--` ambiguity (git checkout -- <sha> would
                # be interpreted as a path restore, not a branch/commit switch).
                try:
                    subprocess.run(
                        ["git", "clone", "--depth=10", "--", row["origin_url"], str(target_repo_dir)],
                        check=True,
                        capture_output=True,
                    )
                    if row.get("origin_commit"):
                        # Fetch the specific commit (shallow may not have it), then detach.
                        subprocess.run(
                            [
                                "git", "-C", str(target_repo_dir),
                                "fetch", "--depth=1", "origin", row["origin_commit"],
                            ],
                            check=True,
                            capture_output=True,
                        )
                        subprocess.run(
                            ["git", "-C", str(target_repo_dir), "checkout", row["origin_commit"]],
                            check=True,
                            capture_output=True,
                        )
                except Exception:
                    shutil.rmtree(target_repo_dir, ignore_errors=True)
                    raise
        elif row["kind"] == "derived":
            base_repo_dir_check = self.storage_root / "datasets" / row["base_dataset"] / "repo"
            if not base_repo_dir_check.exists():
                await self.materialize_dataset(row["base_dataset"])
            recipe = json.loads(row["recipe_json"])
            if recipe.get("templates_version") != self.templates_version:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"templates_version mismatch: "
                        f"bundle={recipe.get('templates_version')} "
                        f"target={self.templates_version}"
                    ),
                )
            base_repo_dir = self.storage_root / "datasets" / row["base_dataset"] / "repo"
            target_repo_dir.parent.mkdir(parents=True, exist_ok=True)
            import shutil as _shutil
            if target_repo_dir.exists():
                _shutil.rmtree(target_repo_dir)
            _shutil.copytree(base_repo_dir, target_repo_dir)
            injector = self._build_vuln_injector()
            for app_spec in recipe.get("applications", []):
                injector.inject(
                    repo_path=target_repo_dir,
                    template_id=app_spec["template_id"],
                    target_file=app_spec["target_file"],
                    substitutions=app_spec.get("substitutions"),
                )
        await self.db.update_dataset_materialized_at(name, datetime.now(UTC).isoformat())

    @property
    def templates_version(self) -> str:
        """SHA-256 hash over template files; cached after first access.

        The value is pinned at first access and does not change while the
        coordinator process is running.  If the templates directory is
        modified on disk, a coordinator restart is required to pick up the
        new hash.  This is intentional: templates should only change during
        a deployment, which always restarts the pod.
        """
        if hasattr(self, "_templates_version_cache"):
            return self._templates_version_cache  # type: ignore[return-value]
        templates_root = self.storage_root / "datasets" / "templates"
        if not templates_root.exists():
            self._templates_version_cache = "absent"
            return self._templates_version_cache
        import hashlib as _hashlib
        h = _hashlib.sha256()
        for file_path in sorted(templates_root.rglob("*")):
            if file_path.is_file():
                rel = str(file_path.relative_to(templates_root))
                h.update(rel.encode())
                h.update(file_path.read_bytes())
        self._templates_version_cache = h.hexdigest()
        return self._templates_version_cache

    async def import_cve(self, spec: dict) -> list[GroundTruthLabel]:
        # If only cve_id (and optionally dataset_name) is provided, resolve
        # the full CVE details first via the existing CVEResolver. This is
        # the path the CVE Discovery page uses — clicking Import sends just
        # the CVE id and trusts the server to fill in repo_url, fix_commit,
        # etc. Otherwise fall through to the full-spec path below.
        if set(spec.keys()) <= {"cve_id", "dataset_name"} and spec.get("cve_id"):
            importer = self._build_cve_importer()
            cve_id = spec["cve_id"]
            try:
                resolved = importer.resolver.resolve(cve_id)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"CVE resolver failed: {exc}")
            if not resolved:
                raise HTTPException(
                    status_code=404,
                    detail=f"Could not resolve {cve_id} to a GitHub repo + fix commit",
                )
            dataset_name = spec.get("dataset_name") or f"cve-{cve_id.lower().replace('-', '_')}"
            self._validate_dataset_name(dataset_name)
            try:
                labels = importer.import_from_id(cve_id, dataset_name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except subprocess.CalledProcessError:
                raise HTTPException(status_code=502, detail="git clone or checkout failed")
            now = datetime.now(UTC).isoformat()
            await self.db.create_dataset({
                "name": dataset_name,
                "kind": "git",
                "origin_url": resolved.repo_url,
                "origin_commit": resolved.fix_commit_sha,
                "origin_ref": None,
                "cve_id": resolved.cve_id,
                "metadata_json": "{}",
                "created_at": now,
            })
            await self.db.update_dataset_materialized_at(dataset_name, now)
            await self.db.append_dataset_labels([
                {**lbl.model_dump(mode="json"), "dataset_name": dataset_name}
                for lbl in labels
            ])
            return labels

        try:
            import_spec = CVEImportSpec(**spec)
        except (ValidationError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        # N7: reject dataset names that could cause path traversal.
        self._validate_dataset_name(import_spec.dataset_name)
        importer = self._build_cve_importer()
        try:
            labels = importer.import_from_spec(import_spec)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except subprocess.CalledProcessError:
            raise HTTPException(status_code=502, detail="git clone or checkout failed")
        # Persist dataset row to DB
        now = datetime.now(UTC).isoformat()
        dataset_row = {
            "name": import_spec.dataset_name,
            "kind": "git",
            "origin_url": import_spec.repo_url,
            "origin_commit": import_spec.fix_commit_sha,
            "origin_ref": None,
            "cve_id": import_spec.cve_id,
            "metadata_json": "{}",
            "created_at": now,
        }
        await self.db.create_dataset(dataset_row)
        await self.db.update_dataset_materialized_at(import_spec.dataset_name, now)
        # Persist labels to DB (add dataset_name field required by schema)
        label_rows = [
            {**lbl.model_dump(mode="json"), "dataset_name": import_spec.dataset_name}
            for lbl in labels
        ]
        await self.db.append_dataset_labels(label_rows)
        return labels

    async def inject_vuln(self, dataset_name: str, req: dict) -> object:
        # N7: reject dataset names that could cause path traversal.
        self._validate_dataset_name(dataset_name)
        repo_path, template_id, target_file, substitutions = self._parse_injection_request(
            dataset_name, req
        )
        injector = self._build_vuln_injector()
        if template_id not in injector.templates:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
        try:
            result = injector.inject(
                repo_path=repo_path,
                template_id=template_id,
                target_file=target_file,
                substitutions=substitutions,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Target file {target_file} not found")
        label = result.label
        # Build derived dataset row
        now = datetime.now(UTC).isoformat()
        derived_name = f"{dataset_name}_injected_{template_id}"
        recipe = {
            "templates_version": self.templates_version,
            "applications": [
                {
                    "template_id": template_id,
                    "target_file": target_file,
                    "substitutions": substitutions,
                }
            ],
        }
        derived_row = {
            "name": derived_name,
            "kind": "derived",
            "base_dataset": dataset_name,
            "recipe_json": json.dumps(recipe),
            "metadata_json": "{}",
            "created_at": now,
        }
        await self.db.create_dataset(derived_row)
        await self.db.update_dataset_materialized_at(derived_name, now)
        label_row = {**label.model_dump(mode="json"), "dataset_name": derived_name}
        await self.db.append_dataset_labels([label_row])
        return label

    def preview_injection(self, dataset_name: str, req: dict) -> dict:
        # N7: reject dataset names that could cause path traversal.
        self._validate_dataset_name(dataset_name)
        repo_path, template_id, target_file, substitutions = self._parse_injection_request(
            dataset_name, req
        )
        injector = self._build_vuln_injector()
        if template_id not in injector.templates:
            raise HTTPException(status_code=404, detail=f"Template {template_id} not found")
        try:
            preview = injector.preview(
                repo_path=repo_path,
                template_id=template_id,
                target_file=target_file,
                substitutions=substitutions,
            )
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail=f"Target file {target_file} not found")
        return preview.model_dump(mode="json")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parse_injection_request(
        self, dataset_name: str, req: dict
    ) -> tuple[Path, str, str, dict | None]:
        repo_path = self.storage_root / "datasets" / dataset_name / "repo"
        if not repo_path.exists():
            raise HTTPException(status_code=404, detail=f"Dataset {dataset_name} not found")
        template_id = req.get("template_id")
        target_file = req.get("target_file") or req.get("file_path")
        substitutions = req.get("substitutions")
        if not template_id or not target_file:
            raise HTTPException(status_code=400, detail="template_id and target_file are required")
        # Prevent path traversal: target_file must resolve inside repo_path.
        repo_resolved = repo_path.resolve()
        target_resolved = (repo_path / target_file).resolve()
        if not target_resolved.is_relative_to(repo_resolved):
            raise HTTPException(
                status_code=400, detail=f"target_file {target_file} escapes dataset repo"
            )
        return repo_path, template_id, target_file, substitutions

    def _build_cve_resolver(self) -> CVEResolver:
        token = os.environ.get("GITHUB_TOKEN", "")
        return CVEResolver(github_token=token)

    def _build_cve_importer(self) -> CVEImporter:
        if not hasattr(self, "_cve_importer"):
            self._cve_importer = CVEImporter(
                datasets_root=self.storage_root / "datasets",
                resolver=self._build_cve_resolver(),
            )
        return self._cve_importer

    def discover_cves(self, req: DiscoverCVEsRequest) -> DiscoverCVEsResponse:
        """Discover CVE candidates matching the provided criteria."""
        from sec_review_framework.data.findings import Severity, VulnClass

        if req.patch_size_min is not None and req.patch_size_min < 0:
            raise HTTPException(
                status_code=400,
                detail="patch_size_min must be ≥ 0",
            )
        if req.patch_size_max is not None and req.patch_size_max < 1:
            raise HTTPException(
                status_code=400,
                detail="patch_size_max must be ≥ 1",
            )
        if (
            req.patch_size_min is not None
            and req.patch_size_max is not None
            and req.patch_size_min > req.patch_size_max
        ):
            raise HTTPException(
                status_code=400,
                detail="patch_size_min cannot exceed patch_size_max",
            )
        if req.max_results < 1:
            raise HTTPException(
                status_code=400,
                detail="max_results must be ≥ 1",
            )
        if req.max_results > 500:
            raise HTTPException(
                status_code=400,
                detail="max_results must be ≤ 500",
            )
        if req.page < 1:
            raise HTTPException(
                status_code=400,
                detail="page must be ≥ 1",
            )
        if not (1 <= req.page_size <= 100):
            raise HTTPException(
                status_code=400,
                detail="page_size must be between 1 and 100",
            )

        criteria_kwargs: dict = {}
        if req.languages:
            criteria_kwargs["languages"] = req.languages
        if req.vuln_classes:
            try:
                criteria_kwargs["vuln_classes"] = [VulnClass(v) for v in req.vuln_classes]
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Invalid vuln_class: {exc}")
        if req.severities:
            try:
                sev_order = [
                    Severity.INFO, Severity.LOW, Severity.MEDIUM,
                    Severity.HIGH, Severity.CRITICAL,
                ]
                requested = [Severity(s) for s in req.severities]
                criteria_kwargs["min_severity"] = min(
                    requested, key=lambda s: sev_order.index(s)
                )
                criteria_kwargs["max_severity"] = max(
                    requested, key=lambda s: sev_order.index(s)
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=f"Invalid severity: {exc}")
        if req.patch_size_min is not None:
            criteria_kwargs["min_lines_changed"] = req.patch_size_min
        if req.patch_size_max is not None:
            criteria_kwargs["max_lines_changed"] = req.patch_size_max
        if req.date_from:
            criteria_kwargs["published_after"] = req.date_from
        if req.date_to:
            criteria_kwargs["published_before"] = req.date_to

        criteria = CVESelectionCriteria(**criteria_kwargs)
        resolver = self._build_cve_resolver()
        discovery = CVEDiscovery(resolver=resolver)
        try:
            result = discovery.discover(criteria, max_results=req.max_results)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"CVE discovery failed: {exc}")

        total = len(result.candidates)
        start = (req.page - 1) * req.page_size
        page_candidates = result.candidates[start : start + req.page_size]

        return DiscoverCVEsResponse(
            candidates=[CVECandidateResponse.from_candidate(c) for c in page_candidates],
            page=req.page,
            page_size=req.page_size,
            total=total,
            stats=DiscoveryStatsResponse(
                scanned=result.stats.scanned,
                resolved=result.stats.resolved,
                rejected=result.stats.rejected,
                # Reflect the page actually being returned, not the pre-pagination
                # cap — otherwise the UI's "showing N" line is wrong on page 2+.
                returned=len(page_candidates),
            ),
            issues=[
                DiscoveryIssueResponse(
                    level=issue.level,
                    message=issue.message,
                    detail=issue.detail,
                )
                for issue in result.issues
            ],
        )

    def resolve_cve(self, cve_id: str) -> CVECandidateResponse:
        """Resolve a single CVE by ID and return a scored candidate shape."""
        resolver = self._build_cve_resolver()
        resolved = resolver.resolve(cve_id)
        if not resolved:
            raise HTTPException(
                status_code=404,
                detail=f"Could not resolve {cve_id} via GHSA/OSV/NVD",
            )
        candidate = _CVECandidate(
            resolved=resolved,
            score=1.0,
            score_breakdown={},
            importable=bool(resolved.fix_commit_sha),
        )
        return CVECandidateResponse.from_candidate(candidate)

    def _build_vuln_injector(self) -> VulnInjector:
        # Templates are read once at construction; cache lives for the
        # coordinator's lifetime. Restart the pod to pick up on-disk changes.
        if not hasattr(self, "_vuln_injector"):
            templates_root = self.storage_root / "datasets" / "templates"
            self._vuln_injector = VulnInjector(templates_root=templates_root)
        return self._vuln_injector

    async def get_labels(
        self,
        dataset_name: str,
        version: str | None = None,
        cwe: str | None = None,
        severity: str | None = None,
        source: str | None = None,
    ) -> list[dict]:
        return await self.db.list_dataset_labels(
            dataset_name,
            version=version,
            cwe=cwe,
            severity=severity,
            source=source,
        )

    def get_file_tree(self, dataset_name: str) -> dict:
        dataset_dir = self.storage_root / "datasets" / dataset_name
        if not dataset_dir.exists():
            raise HTTPException(status_code=404, detail=f"Dataset {dataset_name} not found")

        def build_tree(path: Path) -> dict:
            if path.is_file():
                return {"name": path.name, "type": "file", "size": path.stat().st_size}
            children = []
            try:
                for child in sorted(path.iterdir()):
                    if not child.name.startswith("."):
                        children.append(build_tree(child))
            except PermissionError:
                pass
            return {"name": path.name, "type": "directory", "children": children}

        return build_tree(dataset_dir)

    _MAX_FILE_BYTES = 2 * 1024 * 1024  # 2 MiB

    async def get_file_content(
        self,
        dataset_name: str,
        path: str,
        start: int | None = None,
        end: int | None = None,
    ) -> dict:
        # Reject NUL bytes and absolute paths before any Path construction.
        if "\x00" in path:
            raise HTTPException(status_code=400, detail="path contains NUL byte")
        if path.startswith("/"):
            raise HTTPException(status_code=400, detail="path escapes dataset")

        # Validate dataset_name up front: reject separators / traversal / NUL so
        # we don't rely solely on the downstream resolve() containment check.
        if "\x00" in dataset_name or "/" in dataset_name or "\\" in dataset_name or dataset_name in ("", ".", ".."):
            raise HTTPException(status_code=400, detail="invalid dataset name")

        dataset_dir = (self.storage_root / "datasets" / dataset_name).resolve()
        candidate = (dataset_dir / path).resolve()

        # Strict containment: resolved path must stay inside dataset_dir.
        if not candidate.is_relative_to(dataset_dir):
            raise HTTPException(status_code=400, detail="path escapes dataset")

        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail=f"File {path} not found")

        size_bytes = candidate.stat().st_size
        suffix = candidate.suffix.lstrip(".")
        lang_map = {
            "py": "python", "js": "javascript", "ts": "typescript",
            "java": "java", "go": "go", "rb": "ruby", "rs": "rust",
            "cpp": "cpp", "c": "c", "cs": "csharp", "php": "php",
        }
        language = lang_map.get(suffix, suffix or "text")

        # Binary detection: first 8 KB scanned for NUL bytes.
        probe = candidate.read_bytes()[:8192]
        if b"\x00" in probe:
            return {
                "path": path,
                "binary": True,
                "content": "",
                "language": language,
                "line_count": 0,
                "size_bytes": size_bytes,
                "labels": [],
            }

        truncated = False
        if size_bytes > self._MAX_FILE_BYTES:
            truncated = True
            raw = candidate.read_bytes()
            head = raw[: self._MAX_FILE_BYTES // 2].decode("utf-8", errors="replace")
            tail = raw[-(self._MAX_FILE_BYTES // 2) :].decode("utf-8", errors="replace")
            content = head + "\n\n... [truncated] ...\n\n" + tail
        else:
            content = candidate.read_text(errors="replace")

        file_labels = [lb for lb in await self.get_labels(dataset_name) if lb.get("file_path") == path]

        result: dict = {
            "path": path,
            "content": content,
            "language": language,
            "line_count": len(content.splitlines()),
            "size_bytes": size_bytes,
            "labels": file_labels,
        }
        if truncated:
            result["truncated"] = True
        if start is not None:
            result["highlight_start"] = start
        if end is not None:
            result["highlight_end"] = end
        return result

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    async def compare_experiments(self, experiment_a_id: str, experiment_b_id: str) -> dict:
        experiment_a_results = self.collect_results(experiment_a_id)
        experiment_b_results = self.collect_results(experiment_b_id)
        comparison = self._feedback_tracker.compare_experiments(experiment_a_results, experiment_b_results)
        return {
            "experiment_a_id": comparison.experiment_a_id,
            "experiment_b_id": comparison.experiment_b_id,
            "metric_deltas": comparison.metric_deltas,
            "persistent_false_positives": [
                {
                    "model_id": p.model_id,
                    "vuln_class": p.vuln_class,
                    "pattern_description": p.pattern_description,
                    "occurrence_count": p.occurrence_count,
                    "suggested_action": p.suggested_action,
                }
                for p in comparison.persistent_false_positives
            ],
            "persistent_misses": comparison.persistent_misses,
            "improvements": comparison.improvements,
            "regressions": comparison.regressions,
        }

    def get_fp_patterns(self, experiment_id: str) -> list[dict]:
        results = self.collect_results(experiment_id)
        patterns = self._feedback_tracker.extract_fp_patterns(results)
        return [
            {
                "model_id": p.model_id,
                "vuln_class": p.vuln_class,
                "pattern_description": p.pattern_description,
                "occurrence_count": p.occurrence_count,
                "example_finding_ids": p.example_finding_ids,
                "suggested_action": p.suggested_action,
            }
            for p in patterns
        ]

    # ------------------------------------------------------------------
    # Trends
    # ------------------------------------------------------------------

    _TRENDS_CACHE_TTL_S: float = 60.0

    def _invalidate_trends_cache(self) -> None:
        """Drop all cached trend results (call when an experiment is finalised)."""
        self._trends_cache.clear()

    async def get_trends(
        self,
        dataset: str,
        limit: int = 10,
        tool_ext: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> dict:
        """Return F1 history grouped by (model, strategy, tool_variant, tool_extensions).

        Only completed experiments whose ``dataset_name`` matches *dataset* are
        included.  Results are cached in-process for ``_TRENDS_CACHE_TTL_S``
        seconds and invalidated whenever an experiment is finalised.
        """
        import time

        from sec_review_framework.feedback.tracker import _experiment_key

        cache_key = (dataset, limit, tool_ext, since, until)
        now_mono = time.monotonic()
        cached = self._trends_cache.get(cache_key)
        if cached is not None:
            result, expiry = cached
            if now_mono < expiry:
                return result

        # Fetch completed experiments for this dataset
        all_experiments = await self.db.list_experiments()
        completed = [
            b for b in all_experiments
            if b.get("status") == "completed"
        ]

        # Filter by dataset name (stored in config_json)
        def _experiment_dataset(b: dict) -> str:
            try:
                cfg = json.loads(b.get("config_json") or "{}")
                return cfg.get("dataset_name", "") or ""
            except Exception:
                return ""

        matching = [b for b in completed if _experiment_dataset(b) == dataset]

        # Optional time filters — values are ISO date strings, lexicographic comparison is valid
        if since:
            matching = [b for b in matching if (b.get("completed_at") or "") >= since]
        if until:
            matching = [b for b in matching if (b.get("completed_at") or "") <= until]

        # Sort ascending by completed_at, take the *last* `limit` entries
        matching.sort(key=lambda b: b.get("completed_at") or "")
        if limit > 0:
            matching = matching[-limit:]

        experiments_meta = [
            {
                "experiment_id": b["id"],
                "completed_at": b.get("completed_at") or "",
            }
            for b in matching
        ]

        # Build series: key → list of points
        series_points: dict[tuple, list[dict]] = {}

        for b in matching:
            experiment_id = b["id"]
            completed_at = b.get("completed_at") or ""
            results = self.collect_results(experiment_id)

            # Group by experiment key within this experiment (handle reruns — same key > 1)
            key_groups: dict[tuple, list] = {}
            for r in results:
                if r.evaluation is None:
                    continue
                k = _experiment_key(r)
                key_groups.setdefault(k, []).append(r)

            for k, group in key_groups.items():
                # Optional tool_ext filter: skip if key extensions don't include it
                if tool_ext:
                    ext_part = k[3]  # sorted tuple of extension values
                    if tool_ext not in ext_part:
                        continue

                # Mean F1/precision/recall; sum cost; count runs
                f1_vals = [
                    r.evaluation.f1 or 0.0
                    for r in group
                    if r.evaluation is not None
                ]
                prec_vals = [
                    r.evaluation.precision or 0.0
                    for r in group
                    if r.evaluation is not None
                ]
                rec_vals = [
                    r.evaluation.recall or 0.0
                    for r in group
                    if r.evaluation is not None
                ]
                cost_vals = []
                for r in group:
                    rd = r.model_dump() if hasattr(r, "model_dump") else {}
                    cost_vals.append(float(rd.get("estimated_cost_usd") or 0.0))

                if not f1_vals:
                    continue

                avg_f1 = sum(f1_vals) / len(f1_vals)
                avg_prec = sum(prec_vals) / len(prec_vals)
                avg_rec = sum(rec_vals) / len(rec_vals)
                total_cost = sum(cost_vals)

                point = {
                    "experiment_id": experiment_id,
                    "completed_at": completed_at,
                    "f1": round(avg_f1, 4),
                    "precision": round(avg_prec, 4),
                    "recall": round(avg_rec, 4),
                    "cost_usd": round(total_cost, 4),
                    "run_count": len(group),
                }
                series_points.setdefault(k, []).append(point)

        # Build output series list
        output_series: list[dict] = []
        for k, points in series_points.items():
            model_id, strategy, tool_variant, ext_tuple = k
            summary = _compute_trend_summary(points)
            output_series.append({
                "key": {
                    "model": model_id,
                    "strategy": strategy,
                    "tool_variant": tool_variant,
                    "tool_extensions": list(ext_tuple),
                },
                "points": points,
                "summary": summary,
            })

        # Sort series by latest_f1 descending for stable presentation
        output_series.sort(
            key=lambda s: s["summary"].get("latest_f1") or 0.0,
            reverse=True,
        )

        result = {
            "dataset": dataset,
            "experiments": experiments_meta,
            "series": output_series,
        }

        self._trends_cache[cache_key] = (result, now_mono + self._TRENDS_CACHE_TTL_S)
        return result

    # ------------------------------------------------------------------
    # Reference / ops
    # ------------------------------------------------------------------

    def package_reports(self, experiment_id: str) -> Path:
        output_dir = self.storage_root / "outputs" / experiment_id
        if not output_dir.exists():
            raise HTTPException(status_code=404, detail=f"Experiment {experiment_id} outputs not found")
        zip_path = output_dir / f"{experiment_id}_reports.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in output_dir.rglob("*"):
                if f.is_file() and f.suffix in (".json", ".md", ".txt") and f != zip_path:
                    zf.write(f, f.relative_to(output_dir))
        return zip_path

    # ------------------------------------------------------------------
    # Bundle export / import
    # ------------------------------------------------------------------

    async def export_experiment_bundle(
        self,
        experiment_id: str,
        *,
        dataset_mode: str = "descriptor",
    ) -> Path:
        """Build a .secrev.zip bundle and return its path.

        Delegates to ``bundle.async_write_bundle``.  The zip is written to
        ``storage_root/outputs/<id>/<id>.secrev.zip``.

        Parameters
        ----------
        dataset_mode:
            "descriptor" (default) — embed datasets.json + dataset_labels.json.
            "reference"            — record names only in the manifest.
        """
        from sec_review_framework.bundle import async_write_bundle

        output_dir = self.storage_root / "outputs" / experiment_id
        output_dir.mkdir(parents=True, exist_ok=True)
        out_path = output_dir / f"{experiment_id}.secrev.zip"

        return await async_write_bundle(
            self.db,
            self.storage_root,
            experiment_id,
            dataset_mode=dataset_mode,
            out_path=out_path,
        )

    async def import_experiment_bundle(
        self,
        upload: UploadFile,
        conflict_policy: str = "reject",
        rebuild_findings_index: bool = True,
    ) -> dict:
        """Stream *upload* to a temp file, then apply the bundle.

        After DB insert, re-indexes findings for all imported runs (same
        as ``scripts/backfill_findings_index.py``).
        """
        import tempfile as _tempfile

        from sec_review_framework.bundle import BundleConflictError, async_apply_bundle
        from sec_review_framework.data.experiment import RunResult

        tmp_dir = self.storage_root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)

        from sec_review_framework.bundle import _BUNDLE_UPLOAD_MAX_BYTES

        # Stream upload to a temp file on storage_root so large bundles
        # don't live in memory.
        with _tempfile.NamedTemporaryFile(
            dir=tmp_dir, suffix=".secrev.zip.tmp", delete=False
        ) as tmp_f:
            tmp_path = Path(tmp_f.name)
            total_bytes = 0
            try:
                while True:
                    chunk = await upload.read(64 * 1024)
                    if not chunk:
                        break
                    total_bytes += len(chunk)
                    if total_bytes > _BUNDLE_UPLOAD_MAX_BYTES:
                        tmp_path.unlink(missing_ok=True)
                        raise HTTPException(
                            status_code=413,
                            detail=(
                                f"Bundle upload exceeds size limit "
                                f"({_BUNDLE_UPLOAD_MAX_BYTES // (1024 * 1024)} MiB)."
                            ),
                        )
                    tmp_f.write(chunk)
            except HTTPException:
                raise
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

        try:
            try:
                summary = await async_apply_bundle(
                    self.db,
                    self.storage_root,
                    tmp_path,
                    conflict_policy=conflict_policy,
                    materialize=self.materialize_dataset,
                )
            except BundleConflictError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))

            # Re-index findings if requested
            findings_indexed = 0
            if rebuild_findings_index:
                target_exp_id = summary["experiment_id"]
                exp_outputs = self.storage_root / "outputs" / target_exp_id
                if exp_outputs.exists():
                    for run_dir in exp_outputs.iterdir():
                        if not run_dir.is_dir():
                            continue
                        result_file = run_dir / "run_result.json"
                        if not result_file.exists():
                            continue
                        try:
                            result = RunResult.model_validate_json(
                                result_file.read_text()
                            )
                            if result.findings:
                                await self.db.upsert_findings_for_run(
                                    run_id=result.experiment.id,
                                    experiment_id=target_exp_id,
                                    findings=[
                                        f.model_dump(mode="json")
                                        for f in result.findings
                                    ],
                                    model_id=result.experiment.model_id,
                                    strategy=result.experiment.strategy.value,
                                    dataset_name=result.experiment.dataset_name,
                                )
                                findings_indexed += 1
                        except Exception as exc:
                            logger.warning(
                                "import: could not index findings for run in %s: %s",
                                run_dir,
                                exc,
                            )

            summary["findings_indexed"] = findings_indexed
            return summary

        finally:
            tmp_path.unlink(missing_ok=True)

    def effective_registry(self) -> list[ModelProviderConfig]:
        """Return the probe-driven model registry.

        Builds ModelProviderConfig objects from the current ProviderCatalog
        snapshots.  Returns an empty list when no catalog is attached.
        """
        snapshots = self.catalog.snapshot() if self.catalog is not None else {}
        return build_effective_registry(snapshots)

    def list_models(
        self,
        *,
        flat: bool = False,
    ) -> list[dict]:
        """Return availability-enriched model list.

        Parameters
        ----------
        flat:
            When True return the legacy flat list ``[{id, display_name}]``
            instead of the grouped shape.
        """
        import os as _os

        snapshots = self.catalog.snapshot() if self.catalog is not None else {}
        sv = self.catalog.snapshot_version if self.catalog is not None else 0
        registry = self.effective_registry()
        # If the registry is empty AND there are no snapshots at all, there is
        # nothing to return.  We still continue when there are disabled snapshots
        # so the frontend can render empty-state cards per provider.
        if not registry and not snapshots:
            return []
        groups = compute_availability(registry, snapshots, _os.environ, snapshot_version=sv)

        if flat:
            return flat_model_list(groups)
        return groups_to_dicts(groups)

    def validate_model_availability(
        self,
        model_ids: list[str],
        *,
        verifier_model_id: str | None = None,
    ) -> list[dict]:
        """Validate that every requested model id is available.

        Legacy short model ids (e.g. ``bedrock-claude-3-5-sonnet``) are
        transparently rewritten to their canonical LiteLLM routing strings via
        ``rewrite_legacy_ids()``.  A deprecation warning is emitted once per
        aliased id per process.

        Returns a (possibly empty) list of problem dicts:
            [{"id": ..., "status": ..., "reason": ...}]

        An empty list means all models are fine.
        """
        import os as _os

        registry = self.effective_registry()
        if not registry:
            return []

        snapshots = self.catalog.snapshot() if self.catalog is not None else {}
        sv = self.catalog.snapshot_version if self.catalog is not None else 0
        groups = compute_availability(registry, snapshots, _os.environ, snapshot_version=sv)
        id_to_status = build_id_to_status(groups)
        known_ids = set(id_to_status.keys())

        problems: list[dict] = []
        all_requested = rewrite_legacy_ids(list(model_ids))
        if verifier_model_id:
            all_requested.append(rewrite_legacy_ids([verifier_model_id])[0])

        _reason_map = {
            "key_missing": "API key or AWS credentials not configured",
            "not_listed": "Model not returned by provider probe",
            "probe_failed": "Provider probe failed; model status unknown",
            "unknown": "Model id not in registry",
        }

        seen: set[str] = set()
        for mid in all_requested:
            if mid in seen:
                continue
            seen.add(mid)
            if mid not in known_ids:
                problems.append({
                    "id": mid,
                    "status": "unknown",
                    "reason": _reason_map["unknown"],
                })
            elif id_to_status[mid] != "available":
                status = id_to_status[mid]
                problems.append({
                    "id": mid,
                    "status": status,
                    "reason": _reason_map.get(status, status),
                })

        return problems

    async def model_ids_from_matrix(self, matrix: ExperimentMatrix) -> set[str]:
        """Derive the set of model IDs referenced by a matrix's strategy_ids.

        For each strategy in ``matrix.strategy_ids``, loads the strategy from
        the DB and collects ``default.model_id`` plus any override ``model_id``
        values.  Falls back to ``load_default_registry()`` for builtin strategies
        that may not yet be in the DB.

        Returns a set of model ID strings.
        """
        from sec_review_framework.strategies.strategy_registry import load_default_registry

        model_ids: set[str] = set()
        registry = load_default_registry()

        for strategy_id in matrix.strategy_ids:
            strategy = await self.db.get_user_strategy(strategy_id)
            if strategy is None:
                # Fall back to the in-memory builtin registry.
                try:
                    strategy = registry.get(strategy_id)
                except KeyError:
                    continue
            model_ids.add(strategy.default.model_id)
            for rule in strategy.overrides:
                if rule.override.model_id is not None:
                    model_ids.add(rule.override.model_id)

        if matrix.verifier_model_id is not None:
            model_ids.add(matrix.verifier_model_id)

        return model_ids

    async def enrich_model_configs(self, matrix: ExperimentMatrix) -> dict[str, dict]:
        """Return api_base/api_key enrichment for probe-discovered models in *matrix*.

        Derives the set of model IDs from the matrix's strategy_ids (loading
        each strategy from the DB or the builtin registry), then returns a
        ``{model_id: {"api_base": ..., "api_key": ...}}`` dict for any model
        that has a configured ``api_base`` in the effective registry.

        The returned dict can be used to enrich worker run configs.  The matrix
        itself is not mutated because ``ExperimentMatrix`` no longer carries a
        ``model_configs`` field.
        """
        import os as _os

        effective = self.effective_registry()
        ids_to_enrich = await self.model_ids_from_matrix(matrix)

        enriched: dict[str, dict] = {}
        for cfg in effective:
            if cfg.id not in ids_to_enrich:
                continue
            api_base = cfg.api_base
            if api_base is None:
                continue
            api_key = _os.environ.get(cfg.api_key_env, "") if cfg.api_key_env else ""
            enriched[cfg.id] = {"api_base": api_base, "api_key": api_key}

        return enriched

    def list_strategies(self) -> list[dict]:
        return [
            {"name": s.value, "description": f"{s.value} scan strategy"}
            for s in StrategyName
        ]

    def list_tool_extensions(self) -> list[dict]:
        availability = ToolExtensionAvailability()
        avail_map = availability.as_dict()
        return [
            {
                "key": ext.value,
                "label": _TOOL_EXTENSION_LABELS[ext],
                "available": avail_map.get(ext.value, False),
            }
            for ext in ToolExtension
        ]

    def list_profiles(self) -> list[dict]:
        return [
            {"name": name.value, "description": profile.description}
            for name, profile in BUILTIN_PROFILES.items()
        ]

    def list_templates(self) -> list[dict]:
        templates_dir = self.storage_root / "datasets" / "templates"
        if not templates_dir.exists():
            return []
        try:
            import yaml
            templates = []
            for f in templates_dir.rglob("*.yaml"):
                try:
                    data = yaml.safe_load(f.read_text())
                    if not isinstance(data, dict):
                        continue
                    templates.append({
                        "id": data.get("id", f.stem),
                        "vuln_class": data.get("vuln_class", ""),
                        "cwe_id": data.get("cwe_id", ""),
                        "description": data.get("description", ""),
                        "severity": data.get("severity", ""),
                        "language": data.get("language", ""),
                    })
                except Exception:
                    pass
            return templates
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Trend analysis helpers
# ---------------------------------------------------------------------------

def _compute_trend_summary(points: list[dict]) -> dict:
    """Compute summary statistics for a single trend series.

    Parameters
    ----------
    points:
        Ascending-time list of dicts, each with an ``f1`` key.

    Returns
    -------
    dict with keys:
        latest_f1, prev_f1, delta_f1, trailing_median_f1, is_regression
    """
    if not points:
        return {
            "latest_f1": None,
            "prev_f1": None,
            "delta_f1": None,
            "trailing_median_f1": None,
            "is_regression": False,
        }

    latest_f1 = points[-1]["f1"]
    prev_f1 = points[-2]["f1"] if len(points) >= 2 else None
    delta_f1 = round(latest_f1 - prev_f1, 4) if prev_f1 is not None else None

    # Trailing median: all points except the latest (for regression detection)
    trailing = [p["f1"] for p in points[:-1]] if len(points) > 1 else []
    if trailing:
        sorted_t = sorted(trailing)
        mid = len(sorted_t) // 2
        trailing_median = (
            sorted_t[mid] if len(sorted_t) % 2 == 1
            else (sorted_t[mid - 1] + sorted_t[mid]) / 2.0
        )
        trailing_median = round(trailing_median, 4)
    else:
        trailing_median = None

    # Regression: latest F1 is > 0.05 below trailing median, and >= 3 data points
    is_regression = (
        trailing_median is not None
        and len(points) >= 3
        and (latest_f1 - trailing_median) < -0.05
    )

    return {
        "latest_f1": round(latest_f1, 4),
        "prev_f1": round(prev_f1, 4) if prev_f1 is not None else None,
        "delta_f1": delta_f1,
        "trailing_median_f1": trailing_median,
        "is_regression": is_regression,
    }


# ---------------------------------------------------------------------------
# Retention cleanup background task
# ---------------------------------------------------------------------------

async def retention_cleanup_loop(
    storage_root: Path, retention_days: int, interval_s: int = 3600
) -> None:
    """Background task: deletes experiment directories older than retention_days.

    Also scrubs stale .staging-* directories older than 1 hour to recover
    disk space from aborted HTTP uploads.
    """
    while True:
        await asyncio.sleep(interval_s)
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        staging_cutoff = datetime.now(UTC) - timedelta(hours=1)
        outputs_dir = storage_root / "outputs"
        if not outputs_dir.exists():
            continue
        for experiment_dir in outputs_dir.iterdir():
            if not experiment_dir.is_dir():
                continue
            # Skip experiment directories that contain a .secrev.zip bundle —
            # the bundle is a deliberate export artifact and must survive retention.
            bundle_present = any(
                f.name.endswith(".secrev.zip")
                for f in experiment_dir.iterdir()
                if f.is_file()
            )
            if bundle_present:
                continue
            # Scrub stale staging dirs (HTTP upload aborts)
            for child in experiment_dir.iterdir():
                if child.is_dir() and child.name.startswith(".staging-"):
                    try:
                        mtime = datetime.fromtimestamp(child.stat().st_mtime, UTC)
                        if mtime < staging_cutoff:
                            shutil.rmtree(child, ignore_errors=True)
                            logger.info(
                                "Retention cleanup: removed stale staging dir %s", child.name
                            )
                    except Exception:
                        pass
            # Remove old experiment output directories
            mtime = datetime.fromtimestamp(experiment_dir.stat().st_mtime, UTC)
            if mtime < cutoff:
                shutil.rmtree(experiment_dir, ignore_errors=True)
                logger.info(f"Retention cleanup: deleted {experiment_dir.name}")


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class ReclassifyRequest(BaseModel):
    finding_id: str
    # Allow-list mirrors the short codes stored in the findings index
    # (`db._infer_match_status` returns 'tp'/'fp'; `unlabeled_real` is the
    # only value the reclassify modal currently sends; 'fn' kept for
    # symmetry with the typed frontend MatchStatus union).
    status: Literal["tp", "fp", "fn", "unlabeled_real"]
    note: str | None = None


class CompareRequest(BaseModel):
    experiment_a_id: str
    experiment_b_id: str


class EstimateRequest(BaseModel):
    matrix: ExperimentMatrix
    # Must be strictly positive — a 0 or negative kloc would produce a
    # misleading $0.00 or negative estimate via tokens = int(target_kloc * AVG_TOKENS_PER_KLOC),
    # letting a buggy or malicious client trick a real submit into bypassing
    # the budget check the user thinks they're getting.
    target_kloc: float = Field(gt=0)


class DiscoverCVEsRequest(BaseModel):
    """Criteria for CVE discovery — all fields are optional filters."""

    languages: list[str] | None = None
    vuln_classes: list[str] | None = None
    severities: list[str] | None = None
    patch_size_min: int | None = None
    patch_size_max: int | None = None
    date_from: str | None = None
    date_to: str | None = None
    max_results: int = 50
    page: int = 1
    page_size: int = 25


class CVECandidateResponse(BaseModel):
    """Flat CVE candidate shape that mirrors the frontend CVECandidate interface."""

    cve_id: str
    score: float
    vuln_class: str
    severity: str
    language: str
    repo: str
    files_changed: int
    lines_changed: int
    importable: bool
    description: str | None = None
    advisory_url: str | None = None
    fix_commit: str | None = None

    @classmethod
    def from_candidate(cls, c: _CVECandidate) -> CVECandidateResponse:
        r = c.resolved
        advisory_url = None
        if r.repo_url and r.fix_commit_sha:
            advisory_url = f"{r.repo_url}/commit/{r.fix_commit_sha}"
        return cls(
            cve_id=r.cve_id,
            score=c.score,
            vuln_class=r.vuln_class.value,
            severity=r.severity.value,
            language=r.language,
            repo=r.repo_url,
            files_changed=len(r.affected_files),
            lines_changed=r.lines_changed,
            importable=c.importable,
            description=r.description or None,
            advisory_url=advisory_url,
            fix_commit=r.fix_commit_sha or None,
        )


class DiscoveryIssueResponse(BaseModel):
    level: str
    message: str
    detail: str | None = None


class DiscoveryStatsResponse(BaseModel):
    scanned: int
    resolved: int
    rejected: int
    returned: int


class DiscoverCVEsResponse(BaseModel):
    candidates: list[CVECandidateResponse]
    page: int
    page_size: int
    total: int       # total candidates before pagination slice
    stats: DiscoveryStatsResponse
    issues: list[DiscoveryIssueResponse]


AVG_TOKENS_PER_KLOC = 1400  # prompt + completion, calibrated empirically


# ---------------------------------------------------------------------------
# Strategy API response models
# ---------------------------------------------------------------------------


class StrategySummary(BaseModel):
    """Lightweight summary returned by GET /strategies (list view)."""

    id: str
    name: str
    orchestration_shape: str
    is_builtin: bool
    parent_strategy_id: str | None


class StrategyCreateRequest(BaseModel):
    """Body for POST /strategies."""

    parent_strategy_id: str | None = None
    name: str
    default: StrategyBundleDefault
    overrides: list[OverrideRule] = []
    orchestration_shape: OrchestrationShape


class StrategyValidateRequest(BaseModel):
    """Body for POST /strategies/{id}/validate.

    Mirrors StrategyCreateRequest but all fields optional so partial objects
    can be validated.
    """

    name: str | None = None
    default: StrategyBundleDefault | None = None
    overrides: list[OverrideRule] | None = None
    orchestration_shape: OrchestrationShape | None = None


# ---------------------------------------------------------------------------
# Builtin seeding helper
# ---------------------------------------------------------------------------


async def _seed_builtin_strategies(db: Database) -> None:
    """Insert any builtin strategies that are not yet in the database."""
    from sec_review_framework.strategies.strategy_registry import load_default_registry

    registry = load_default_registry()
    for strategy in registry.list_all():
        existing = await db.get_user_strategy(strategy.id)
        if existing is None:
            await db.insert_user_strategy(strategy)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

coordinator: ExperimentCoordinator = None  # type: ignore[assignment]  # initialized at startup
_background_tasks: set[asyncio.Task] = set()
# One-shot fire-and-forget tasks (e.g. _maybe_backfill).  Not cancelled on
# shutdown — they complete on their own — but we keep strong references here
# so the GC doesn't collect them while they're running.  Each task removes
# itself via add_done_callback when it finishes.
_ephemeral_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global coordinator
    if coordinator is None:
        coordinator = build_coordinator_from_env()
        await coordinator.db.init()

    # Eagerly import fernet so a missing/malformed LLM_PROVIDER_ENCRYPTION_KEY
    # surfaces as a startup error rather than a runtime crash on first DB write.
    import sec_review_framework.secrets.fernet  # noqa: F401 — side-effect: fails fast

    await coordinator.reconcile()

    # Seed builtin strategies into the DB if they aren't already present.
    await _seed_builtin_strategies(coordinator.db)

    # When there is no real Kubernetes client (i.e. all tests by default), the
    # probe loop, retention loop, custom-provider injection, and backfill task
    # are wasted work — none of them produce useful results without a live
    # scheduler and live providers.  Skip them unless the caller explicitly
    # opts in via ENABLE_BACKGROUND_TASKS, mirroring the ENABLE_RECONCILE_LOOP
    # gate used for the reconcile loop below.
    _enable_background = (coordinator.k8s_client is not None) or (
        os.environ.get("ENABLE_BACKGROUND_TASKS", "").strip().lower()
        in ("1", "true", "yes")
    )

    # --- ProviderCatalog ---
    # Always construct the catalog object so coordinator.catalog is set and
    # downstream consumers can call catalog.snapshot() safely.  Only call
    # catalog.start() — which kicks off the initial probe and spawns the
    # background refresh loop — when background tasks are enabled.
    _probe_enabled_env = os.environ.get("PROVIDER_PROBE_ENABLED", "true").strip().lower()
    _probe_enabled = _probe_enabled_env != "false"
    _ttl_seconds = int(os.environ.get("PROVIDER_PROBE_TTL_SECONDS", "600"))
    _max_stale_env = os.environ.get("PROVIDER_PROBE_MAX_STALE_SECONDS")
    _max_stale_seconds = int(_max_stale_env) if _max_stale_env is not None else None
    catalog = ProviderCatalog(
        probes=build_probes(),
        ttl_seconds=_ttl_seconds,
        probe_enabled=_probe_enabled,
        max_stale_seconds=_max_stale_seconds,
    )
    coordinator.catalog = catalog

    # When _enable_background is False, the catalog is constructed but never
    # started, so it has no probe snapshots. We intentionally leave
    # cost_calculator._pricing_view as None in that case — CostCalculator
    # guards on `if self._pricing_view is not None` and falls back to the
    # static pricing dict, which is the desired behavior for tests.
    if _enable_background:
        await catalog.start()
        coordinator.cost_calculator._pricing_view = CatalogPricingView(catalog)

        # Load enabled custom providers into the catalog alongside built-ins.
        try:
            from sec_review_framework.llm_providers import _inject_custom_into_catalog
            custom_rows = await coordinator.db.list_llm_providers()
            for _row in custom_rows:
                if _row.get("enabled", 1):
                    _inject_custom_into_catalog(_row, catalog)
            if custom_rows:
                logger.info("Loaded %d custom provider(s) into catalog", len(custom_rows))
        except Exception as _exc:
            logger.warning("Failed to load custom providers into catalog: %s", _exc)

        _background_tasks.add(asyncio.create_task(
            retention_cleanup_loop(coordinator.storage_root, retention_days=30)
        ))

    # Periodic reconcile loop is only needed when there's an active scheduler
    # (K8s cluster) or when explicitly enabled for integration tests.
    _enable_loop = os.environ.get("ENABLE_RECONCILE_LOOP", "").strip().lower()
    if K8S_AVAILABLE or _enable_loop in ("1", "true", "yes"):
        _background_tasks.add(asyncio.create_task(coordinator._reconcile_loop()))

    if _enable_background:
        # Backfill findings index if the table is empty but experiments exist.
        # Runs asynchronously to not block startup.
        async def _maybe_backfill() -> None:
            try:
                finding_count = await coordinator.db.count_all_findings()
                if finding_count == 0:
                    experiments = await coordinator.db.list_experiments()
                    if experiments:
                        logger.info(
                            "findings table empty but experiments exist — running backfill"
                        )
                        n = await coordinator.backfill_findings_index()
                        logger.info("backfill_findings_index indexed %d runs", n)
            except Exception as exc:
                logger.warning("backfill check failed: %s", exc)

        # _maybe_backfill is a one-shot task: it completes on its own and must NOT
        # be cancelled by the shutdown handler (that could interrupt a partial DB
        # write).  Track it in _ephemeral_tasks so the GC doesn't collect it, and
        # remove it automatically when it finishes.
        _t = asyncio.create_task(_maybe_backfill())
        _ephemeral_tasks.add(_t)
        _t.add_done_callback(_ephemeral_tasks.discard)

    yield

    # Stop ProviderCatalog background task.
    if coordinator is not None and coordinator.catalog is not None:
        await coordinator.catalog.stop()
    # Cancel all tracked tasks so TestClient teardown (and real SIGTERM) doesn't
    # leave behind coroutines holding closures over per-test storage roots.
    for task in _background_tasks:
        task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()


app = FastAPI(title="Security Review Framework", version="1.0.0", lifespan=lifespan)
app.include_router(llm_providers_router)


@app.middleware("http")
async def api_and_spa_router(request, call_next):
    """Route /api/* to FastAPI endpoints and serve index.html for SPA routes.

    Browser navigation (Accept: text/html) to any non-API path — including
    SPA paths that collide with API routes (e.g. /experiments/{id}) — returns the
    React shell so client-side routing can render the view. httpx / curl
    requests without an Accept header fall through to the API routes.
    """
    path = request.scope["path"]
    if path.startswith("/api/"):
        request.scope["path"] = path[4:]
        request.scope["raw_path"] = request.scope["path"].encode()
        return await call_next(request)
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        index_path = FRONTEND_DIST_DIR / "index.html"
        if index_path.exists():
            return FileResponse(str(index_path), media_type="text/html")
    return await call_next(request)


def build_coordinator_from_env() -> ExperimentCoordinator:
    """Build an ExperimentCoordinator from env vars + files mounted at CONFIG_DIR.

    Env vars (set by the Helm chart):
        STORAGE_ROOT  — shared storage mount path (default: /data)
        WORKER_IMAGE  — image used for worker K8s Jobs
        NAMESPACE     — namespace to create Jobs in
        CONFIG_DIR    — directory containing pricing.yaml + concurrency.yaml
                        (default: /app/config)
    """
    from sec_review_framework.config import ConcurrencyConfig
    from sec_review_framework.reporting.generator import ReportGenerator
    from sec_review_framework.reporting.json_report import JSONReportGenerator
    from sec_review_framework.reporting.markdown import MarkdownReportGenerator

    class _MarkdownAndJSONReportGenerator(ReportGenerator):
        """Writes both markdown and JSON reports for every run + matrix."""

        def __init__(self) -> None:
            self._md = MarkdownReportGenerator()
            self._json = JSONReportGenerator()

        def render_run(self, result, output_dir: Path) -> None:
            self._md.render_run(result, output_dir)
            self._json.render_run(result, output_dir)

        def render_matrix(self, results, output_dir: Path) -> None:
            self._md.render_matrix(results, output_dir)
            self._json.render_matrix(results, output_dir)

    storage_root = Path(os.environ.get("STORAGE_ROOT", "/data"))
    worker_image = os.environ.get("WORKER_IMAGE", "sec-review-worker:latest")
    namespace = os.environ.get("NAMESPACE", "sec-review")
    config_dir = Path(os.environ.get("CONFIG_DIR", "/app/config"))

    k8s_client = None
    if K8S_AVAILABLE:
        try:
            kubernetes.config.load_incluster_config()
            k8s_client = kubernetes.client.BatchV1Api()
        except Exception as exc:
            logger.warning("Not running in-cluster, K8s job creation disabled: %s", exc)

    concurrency_path = config_dir / "concurrency.yaml"
    if concurrency_path.exists():
        cc = ConcurrencyConfig.from_yaml(concurrency_path)
        caps, default_cap = cc.per_model, cc.default_cap
    else:
        caps, default_cap = {}, 4

    storage_root.mkdir(parents=True, exist_ok=True)
    db = Database(storage_root / "coordinator.db")

    return ExperimentCoordinator(
        k8s_client=k8s_client,
        storage_root=storage_root,
        concurrency_caps=caps,
        worker_image=worker_image,
        namespace=namespace,
        db=db,
        reporter=_MarkdownAndJSONReportGenerator(),
        cost_calculator=CostCalculator.from_config(config_dir),
        config_dir=config_dir,
        default_cap=default_cap,
        pvc_name=os.environ.get("SHARED_PVC_NAME", "sec-review-data"),
        llm_secret_name=os.environ.get("LLM_SECRET_NAME", "llm-api-keys"),
        config_map_name=os.environ.get("CONFIG_MAP_NAME", "experiment-config"),
        worker_image_pull_policy=os.environ.get(
            "WORKER_IMAGE_PULL_POLICY", "IfNotPresent"
        ),
        result_transport=os.environ.get("WORKER_RESULT_TRANSPORT", "pvc"),
        coordinator_internal_url=os.environ.get("COORDINATOR_INTERNAL_URL", ""),
    )


# --- Health ---

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --- Smoke test ---

@app.post("/smoke-test")
async def run_smoke_test() -> dict:
    """
    Submit a minimal 1-model × 1-strategy smoke test experiment.
    Uses the first configured model (or gpt-4o-mini as a fallback),
    single_agent strategy, with_tools only, no verification.
    Capped at $5.00 for safety.
    """
    if coordinator is None:
        raise HTTPException(status_code=503, detail="Coordinator not initialised")

    # Pick the first configured model; require at least one to be present.
    # list_models() returns the grouped shape [{provider, models: [{id, status}]}].
    # Use the flat form to get a simple [{id}] list for backward-compatible selection.
    configured_models = coordinator.list_models(flat=True)
    if not configured_models:
        raise HTTPException(
            status_code=412,
            detail="No models configured. Add a model before running smoke tests.",
        )
    configured_models[0]["id"]

    # Pick the first available dataset; require at least one to be present
    datasets = await coordinator.list_datasets()
    if not datasets:
        raise HTTPException(
            status_code=412,
            detail="No datasets registered. Seed a dataset before running smoke tests.",
        )
    dataset_name = datasets[0]["name"]

    timestamp = int(datetime.now(UTC).timestamp())
    experiment_id = f"smoke-test-{timestamp}"

    existing_experiments = await coordinator.list_experiments()
    terminal = {"completed", "failed", "cancelled"}
    for b in existing_experiments:
        if b["experiment_id"].startswith("smoke-test-") and b["status"] not in terminal:
            raise HTTPException(
                status_code=409,
                detail=f"A smoke test is already running: {b['experiment_id']}",
            )

    # Resolve the builtin single_agent strategy from the DB (seeded at startup).
    smoke_strategy_id = "builtin.single_agent"

    matrix = ExperimentMatrix(
        experiment_id=experiment_id,
        dataset_name=dataset_name,
        dataset_version="latest",
        strategy_ids=[smoke_strategy_id],
        num_repetitions=1,
        max_experiment_cost_usd=5.0,
    )

    await coordinator.submit_experiment(matrix)
    # total_runs = one run per strategy × num_repetitions (no cross-product axes).
    total_runs = len(matrix.strategy_ids) * matrix.num_repetitions
    return {
        "experiment_id": experiment_id,
        "message": "Smoke test experiment submitted. Monitor progress on the experiment detail page.",
        "total_runs": total_runs,
    }


# --- Experiments ---

@app.post("/experiments", status_code=201)
async def submit_experiment(matrix: ExperimentMatrix) -> dict:
    """Submit a new experiment. Returns experiment_id.

    Rejects unavailable models (key_missing, not_listed, probe_failed, unknown)
    unless the request body contains ``allow_unavailable_models: true``.

    Also rejects experiments that request a tool extension that the cluster
    operator has disabled via ``workerTools.*.enabled`` in the Helm values.
    """
    if not matrix.allow_unavailable_models:
        # Derive the set of model IDs from the strategies so we can validate them.
        model_ids = sorted(await coordinator.model_ids_from_matrix(matrix))
        problems = coordinator.validate_model_availability(
            model_ids,
            verifier_model_id=matrix.verifier_model_id,
        )
        if problems:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unavailable_models",
                    "models": problems,
                },
            )

    # Gate on operator-controlled extension availability.  If the cluster
    # operator has disabled an extension via workerTools.*.enabled=false,
    # refuse any run that requests it regardless of experiment config.
    from sec_review_framework.strategies.strategy_registry import build_registry_from_db
    avail_map = ToolExtensionAvailability().as_dict()
    registry = await build_registry_from_db(coordinator.db)
    runs_preview = matrix.expand(registry=registry)
    disabled_extensions: list[str] = []
    for run in runs_preview:
        for ext in run.tool_extensions:
            if not avail_map.get(ext.value, False):
                if ext.value not in disabled_extensions:
                    disabled_extensions.append(ext.value)
    if disabled_extensions:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "disabled_extensions",
                "extensions": sorted(disabled_extensions),
            },
        )

    await coordinator.enrich_model_configs(matrix)
    experiment_id = await coordinator.submit_experiment(matrix)
    # total_runs = one run per strategy × num_repetitions (no cross-product axes).
    total_runs = len(matrix.strategy_ids) * matrix.num_repetitions
    return {"experiment_id": experiment_id, "total_runs": total_runs}


@app.get("/experiments")
async def list_experiments() -> list[dict]:
    """List all experiments with summary status."""
    return await coordinator.list_experiments()


@app.post("/experiments/estimate")
async def estimate_experiment(req: EstimateRequest) -> dict:
    """Pre-flight cost estimate. Call before submit to avoid expensive mistakes."""
    from sec_review_framework.strategies.strategy_registry import build_registry_from_db

    registry = await build_registry_from_db(coordinator.db)
    runs = req.matrix.expand(registry=registry)
    estimates_by_model: dict[str, float] = {}
    for run in runs:
        tokens = int(req.target_kloc * AVG_TOKENS_PER_KLOC)
        cost = coordinator.cost_calculator.compute(run.model_id, tokens, tokens // 3)
        estimates_by_model.setdefault(run.model_id, 0.0)
        estimates_by_model[run.model_id] += cost
    total = sum(estimates_by_model.values())
    return {
        "total_runs": len(runs),
        "estimated_cost_usd": round(total, 2),
        "by_model": {k: round(v, 2) for k, v in estimates_by_model.items()},
        "warning": (
            "Estimates based on avg tokens/KLOC. "
            "Actual cost varies with strategy and model."
        ),
    }


@app.get("/experiments/{experiment_id}")
async def get_experiment(experiment_id: str) -> dict:
    """Experiment summary: dataset, status, per-status run counts, cost so far and cap."""
    return await coordinator.get_experiment_detail(experiment_id)


@app.get("/experiments/{experiment_id}/results")
async def get_results_json(experiment_id: str) -> dict:
    """Matrix report as JSON. Available after experiment completes."""
    return coordinator.get_matrix_report_json(experiment_id)


@app.get("/experiments/{experiment_id}/results/markdown")
async def get_results_markdown(experiment_id: str) -> str:
    """Matrix report as Markdown. Available after experiment completes."""
    return coordinator.get_matrix_report_markdown(experiment_id)


@app.get("/experiments/{experiment_id}/results/download")
async def download_reports(experiment_id: str) -> FileResponse:
    """Download matrix_report.md, matrix_report.json, and all run reports as a .zip."""
    zip_path = coordinator.package_reports(experiment_id)
    return FileResponse(
        zip_path, media_type="application/zip", filename=f"{experiment_id}_reports.zip"
    )


@app.get("/experiments/{experiment_id}/export")
async def export_experiment_bundle(
    experiment_id: str,
    dataset_mode: str = "descriptor",
) -> FileResponse:
    """Export a portable .secrev.zip bundle for the given experiment.

    The bundle includes DB rows, all run artifacts, and dataset metadata
    depending on ``dataset_mode``:

    - ``descriptor`` (default) — embed ``datasets.json`` + ``dataset_labels.json``
    - ``reference`` — record dataset names only in the manifest

    Passing ``dataset_mode=embedded`` returns 422.
    """
    if dataset_mode not in ("reference", "descriptor"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid dataset_mode {dataset_mode!r}. "
                "Must be one of: 'reference', 'descriptor'."
            ),
        )
    try:
        zip_path = await coordinator.export_experiment_bundle(
            experiment_id, dataset_mode=dataset_mode
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return FileResponse(
        str(zip_path),
        media_type="application/zip",
        filename=f"{experiment_id}.secrev.zip",
    )


@app.post("/experiments/import")
async def import_experiment_bundle(
    file: UploadFile,
    conflict_policy: str = Form(default="reject"),
    rebuild_findings_index: bool = Form(default=True),
) -> dict:
    """Import a .secrev.zip bundle.

    Accepts multipart/form-data with:
      - ``file``                  — the .secrev.zip bundle
      - ``conflict_policy``       — reject (default) | rename | merge
      - ``rebuild_findings_index`` — true (default) | false

    Returns:
      {experiment_id, renamed_from, runs_imported, runs_skipped,
       datasets_imported, datasets_rehydrated, datasets_missing,
       dataset_labels_imported, warnings, findings_indexed}

    Gated by the ``IMPORT_ENABLED`` env var (default: true).
    """
    if not IMPORT_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Bundle import is disabled on this deployment. "
            "Set IMPORT_ENABLED=true to enable.",
        )
    return await coordinator.import_experiment_bundle(
        file,
        conflict_policy=conflict_policy,
        rebuild_findings_index=rebuild_findings_index,
    )


@app.get("/experiments/{experiment_id}/runs")
async def list_runs(experiment_id: str) -> list[dict]:
    """List all runs in an experiment with status and summary metrics."""
    return await coordinator.list_runs(experiment_id)


@app.get("/experiments/{experiment_id}/runs/{run_id}")
async def get_run(experiment_id: str, run_id: str) -> dict:
    """Full RunResult including findings, evaluation, and verification details."""
    return await coordinator.get_run_result(experiment_id, run_id)


@app.post("/experiments/{experiment_id}/cancel")
async def cancel_experiment(experiment_id: str) -> dict:
    """Cancel pending jobs. Running jobs complete but no new ones are scheduled."""
    cancelled = await coordinator.cancel_experiment(experiment_id)
    return {"cancelled_jobs": cancelled}


@app.get("/experiments/{experiment_id}/compare-runs")
async def compare_runs(experiment_id: str, run_a: str, run_b: str) -> dict:
    """
    Side-by-side comparison of two runs within the same experiment.
    Delegates to the cross-experiment endpoint with both sides sharing the same experiment.
    """
    return await coordinator.compare_runs(experiment_id, run_a, run_b)


@app.get("/compare-runs")
async def compare_runs_cross(
    a_experiment: str,
    a_run: str,
    b_experiment: str,
    b_run: str,
) -> dict:
    """
    Cross-experiment run comparison. Runs may belong to different experiments.

    Returns the same shape as the single-experiment endpoint, with additional
    fields on each run side: experiment_id, experiment_name, dataset.
    Top-level dataset_mismatch flag and warnings list are always present.

    Returns 404 if either run result is missing.
    """
    return await coordinator.compare_runs_cross(
        a_experiment=a_experiment,
        a_run=a_run,
        b_experiment=b_experiment,
        b_run=b_run,
    )


@app.delete("/experiments/{experiment_id}", status_code=204)
async def delete_experiment(experiment_id: str) -> None:
    """Delete an experiment and all its outputs from shared storage."""
    await coordinator.delete_experiment(experiment_id)


# --- Cross-experiment findings search ---

@app.get("/findings")
async def search_findings_global(
    q: str | None = Query(default=None),
    vuln_class: list[str] | None = Query(default=None),
    severity: list[str] | None = Query(default=None),
    match_status: list[str] | None = Query(default=None),
    model_id: list[str] | None = Query(default=None),
    strategy: list[str] | None = Query(default=None),
    experiment_id: list[str] | None = Query(default=None),
    dataset_name: list[str] | None = Query(default=None),
    created_from: str | None = None,
    created_to: str | None = None,
    sort: str = "created_at desc",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Search findings across ALL experiments.

    Supports FTS (q=), multi-value filters (vuln_class[]=...), date range,
    and pagination. Returns facet counts alongside results.
    """
    return await coordinator.search_findings_global(
        q=q,
        vuln_class=vuln_class,
        severity=severity,
        match_status=match_status,
        model_id=model_id,
        strategy=strategy,
        experiment_id=experiment_id,
        dataset_name=dataset_name,
        created_from=created_from,
        created_to=created_to,
        sort=sort,
        limit=limit,
        offset=offset,
    )


# --- Per-experiment findings ---

@app.get("/experiments/{experiment_id}/findings/search")
async def search_findings(
    experiment_id: str, q: str, run_id: str | None = None
) -> list[dict]:
    """
    Free-text search across all finding descriptions in an experiment.
    Searches: title, description, recommendation, raw_llm_output.
    Optional run_id filter to scope to a single run.
    """
    return await coordinator.search_findings(experiment_id, q, run_id)


@app.post("/experiments/{experiment_id}/runs/{run_id}/reclassify")
async def reclassify_finding(
    experiment_id: str, run_id: str, req: ReclassifyRequest
) -> dict:
    """Reclassify a false positive as unlabeled_real. Recomputes metrics."""
    return await coordinator.reclassify_finding(experiment_id, run_id, req)


@app.get("/experiments/{experiment_id}/runs/{run_id}/tool-audit")
async def tool_audit(experiment_id: str, run_id: str) -> dict:
    """Tool call audit: counts by tool, any calls with URL-like strings in inputs."""
    return await coordinator.audit_tool_calls(experiment_id, run_id)


# --- Datasets ---

@app.get("/datasets")
async def list_datasets() -> list[dict]:
    """List available target datasets with label counts."""
    return await coordinator.list_datasets()


@app.post("/datasets/discover-cves")
def discover_cves(req: DiscoverCVEsRequest) -> DiscoverCVEsResponse:
    """Search for CVE candidates matching the given criteria.

    Makes external calls to GitHub Advisory DB, OSV.dev, and/or NVD.
    Returns an empty candidates list when no candidates match (never 404).
    """
    return coordinator.discover_cves(req)


@app.get("/datasets/resolve-cve")
def resolve_cve(id: str) -> CVECandidateResponse:
    """Resolve a single CVE ID to a candidate record.

    Returns 404 if the CVE cannot be found in GHSA/OSV/NVD.
    """
    return coordinator.resolve_cve(id)


@app.post("/datasets/import-cve", status_code=201)
async def import_cve(spec: dict) -> dict:
    """Import a CVE-patched repo as a ground truth dataset."""
    labels = await coordinator.import_cve(spec)
    return {"labels_created": len(labels)}


@app.post("/datasets/{dataset_name}/inject/preview")
def preview_injection(dataset_name: str, req: dict) -> dict:
    """
    Dry-run injection: returns the unified diff and affected line range
    without modifying any files.
    """
    return coordinator.preview_injection(dataset_name, req)


@app.post("/datasets/{dataset_name}/inject", status_code=201)
async def inject_vuln(dataset_name: str, req: dict) -> dict:
    """Inject a vulnerability from a template into a dataset."""
    label = await coordinator.inject_vuln(dataset_name, req)
    return {"label_id": label.id, "file_path": label.file_path}


@app.post("/datasets/{dataset_name}/rematerialize")
async def rematerialize_dataset(dataset_name: str) -> dict:
    """Re-clone or rebuild a dataset repo and refresh materialized_at.

    Returns ``{"materialized_at": <iso-ts>}`` on success.
    404 if the dataset row does not exist.
    409 if a derived dataset's templates_version does not match the target.
    502 on clone / network failure.

    Gated by the ``IMPORT_ENABLED`` env var (default: true).
    """
    if not IMPORT_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Dataset rematerialization is disabled on this instance. "
            "Set IMPORT_ENABLED=true to enable.",
        )
    return await coordinator.rematerialize_dataset(dataset_name)


@app.get("/datasets/{dataset_name}/labels")
async def get_labels(dataset_name: str, request: Request) -> list[dict]:
    """View ground truth labels for a dataset."""
    return await coordinator.get_labels(
        dataset_name,
        version=request.query_params.get("version"),
        cwe=request.query_params.get("cwe"),
        severity=request.query_params.get("severity"),
        source=request.query_params.get("source"),
    )


@app.get("/datasets/{dataset_name}/tree")
def get_file_tree(dataset_name: str) -> dict:
    """Repo file tree. Returns nested structure: {name, type, children, size}."""
    return coordinator.get_file_tree(dataset_name)


@app.get("/datasets/{dataset_name}/file")
async def get_file_content(
    dataset_name: str,
    path: str,
    start: int | None = None,
    end: int | None = None,
) -> dict:
    """Read a file from the dataset repo. Returns content with metadata."""
    return await coordinator.get_file_content(dataset_name, path, start=start, end=end)


# --- Feedback ---

@app.post("/feedback/compare")
async def compare_experiments_endpoint(req: CompareRequest) -> dict:
    """Compare two experiments: metric deltas, persistent FP patterns, regressions."""
    return await coordinator.compare_experiments(req.experiment_a_id, req.experiment_b_id)


@app.get("/feedback/patterns/{experiment_id}")
def get_fp_patterns(experiment_id: str) -> list[dict]:
    """Extract recurring false positive patterns from an experiment."""
    return coordinator.get_fp_patterns(experiment_id)


# --- Trends ---

@app.get("/trends")
async def get_trends(
    dataset: str | None = None,
    limit: int = Query(default=10, ge=1, le=200),
    tool_ext: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict:
    """Historical F1 trend series per (model, strategy, tool_variant, tool_extensions) cell.

    Required: ``dataset`` — 400 if omitted.
    Optional: ``limit`` (1..200, default 10), ``tool_ext`` (filter by extension key),
              ``since``/``until`` (ISO date strings; validated — no SQL injection risk).
    """
    if not dataset:
        raise HTTPException(status_code=400, detail="dataset query parameter is required")
    for label, value in (("since", since), ("until", until)):
        if value is None:
            continue
        try:
            date.fromisoformat(value[:10])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"{label} must be an ISO date (YYYY-MM-DD)")
    return await coordinator.get_trends(
        dataset=dataset,
        limit=limit,
        tool_ext=tool_ext,
        since=since,
        until=until,
    )


# --- Matrix ---

@app.get("/matrix/accuracy")
def get_accuracy_matrix() -> dict:
    """Accuracy heatmap data: recall averaged across completed runs, grouped by model × strategy."""
    return coordinator.get_accuracy_matrix()


# --- Reference ---

@app.get("/models")
def list_models(
    format: str | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> list[dict]:
    """List configured model providers.

    Default response is the grouped-by-provider shape.
    Pass ``?format=flat`` or ``Accept: application/vnd.sec-review.v0+json`` for
    the legacy flat list ``[{id, display_name}]`` — retained for backward
    compatibility with older clients.
    """
    use_flat = format == "flat"
    if not use_flat and request is not None:
        accept = request.headers.get("accept", "")
        if "application/vnd.sec-review.v0+json" in accept:
            use_flat = True
    return coordinator.list_models(flat=use_flat)


@app.get("/strategies", response_model=list[StrategySummary])
async def list_strategies() -> list[StrategySummary]:
    """List all builtin and user strategies (summary view)."""
    strategies = await coordinator.db.list_user_strategies()
    return [
        StrategySummary(
            id=s.id,
            name=s.name,
            orchestration_shape=s.orchestration_shape.value,
            is_builtin=s.is_builtin,
            parent_strategy_id=s.parent_strategy_id,
        )
        for s in strategies
    ]


@app.get("/strategies/{strategy_id}", response_model=UserStrategy)
async def get_strategy(strategy_id: str) -> UserStrategy:
    """Return the full UserStrategy including overrides. 404 if not found."""
    strategy = await coordinator.db.get_user_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id!r} not found.")
    return strategy


@app.post("/strategies", response_model=UserStrategy, status_code=201)
async def create_strategy(body: StrategyCreateRequest) -> UserStrategy:
    """Create a new user strategy.

    The server mints an id of the form ``user.<slug>.<6-char-hash>``.
    The hash is derived from the content (name, shape, default, overrides,
    parent_strategy_id) — NOT from created_at — so identical submissions
    produce the same id and 409 on the second attempt.
    Returns 409 if a strategy with the same computed id already exists.
    """
    import hashlib
    import json as _json
    import re as _re

    from fastapi.exceptions import RequestValidationError
    from pydantic import ValidationError as _ValidationError

    from sec_review_framework.data.strategy_bundle import _make_json_serializable

    # Compute stable id: user.<slug>.<6-char-hex-of-hash>
    slug = _re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-") or "strategy"

    # Hash content-only fields (exclude created_at and id which vary per call).
    content_dict = {
        "name": body.name,
        "parent_strategy_id": body.parent_strategy_id,
        "orchestration_shape": body.orchestration_shape.value,
        "default": body.default.model_dump(),
        "overrides": [r.model_dump() for r in body.overrides],
    }
    content_json = _json.dumps(
        _make_json_serializable(content_dict), sort_keys=True, separators=(",", ":")
    )
    content_hash = hashlib.sha256(content_json.encode()).hexdigest()[:6]
    strategy_id = f"user.{slug}.{content_hash}"

    # Validate the full UserStrategy via pydantic (runs shape-specific validators).
    try:
        strategy = UserStrategy(
            id=strategy_id,
            name=body.name,
            parent_strategy_id=body.parent_strategy_id,
            orchestration_shape=body.orchestration_shape,
            default=body.default,
            overrides=body.overrides,
            created_at=datetime.now(UTC),
            is_builtin=False,
        )
    except _ValidationError as exc:
        raise RequestValidationError(errors=exc.errors())

    existing = await coordinator.db.get_user_strategy(strategy_id)
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail=f"A strategy with id {strategy_id!r} already exists.",
        )

    await coordinator.db.insert_user_strategy(strategy)
    return strategy


@app.post("/strategies/{strategy_id}/validate")
async def validate_strategy(strategy_id: str, body: StrategyValidateRequest) -> dict:
    """Dry-run validation of a strategy-like object without persisting.

    Returns ``{valid: true}`` or ``{valid: false, errors: [...]}``.

    Checks performed:
    - Template placeholders in user_prompt_template resolve without KeyError.
    - Glob patterns in overrides compile via fnmatch.translate without error.
    - Override keys are valid for the given orchestration_shape.
    """
    import fnmatch as _fnmatch

    errors: list[str] = []

    # Determine shape from body or from the existing strategy
    shape = body.orchestration_shape
    if shape is None:
        existing = await coordinator.db.get_user_strategy(strategy_id)
        if existing is not None:
            shape = existing.orchestration_shape

    # Validate user_prompt_template placeholders
    if body.default is not None:
        template = body.default.user_prompt_template
        try:
            template.format(repo_summary="__test__", finding_output_format="__fmt__")
        except KeyError as exc:
            errors.append(f"user_prompt_template missing placeholder: {exc}")
        except Exception as exc:
            errors.append(f"user_prompt_template invalid: {exc}")

    # Validate override keys if overrides and shape are provided
    if body.overrides is not None and shape is not None:
        if shape in (OrchestrationShape.SINGLE_AGENT, OrchestrationShape.DIFF_REVIEW):
            if body.overrides:
                errors.append(
                    f"orchestration_shape={shape.value!r} must have no overrides, "
                    f"but {len(body.overrides)} override(s) were provided."
                )
        elif shape == OrchestrationShape.PER_VULN_CLASS:
            from sec_review_framework.data.strategy_bundle import _VALID_VULN_CLASS_VALUES
            for rule in body.overrides:
                if rule.key not in _VALID_VULN_CLASS_VALUES:
                    errors.append(
                        f"Override key {rule.key!r} is not a valid VulnClass name."
                    )
        elif shape in (OrchestrationShape.PER_FILE, OrchestrationShape.SAST_FIRST):
            for rule in body.overrides:
                try:
                    _fnmatch.translate(rule.key)
                except Exception as exc:
                    errors.append(
                        f"Override key {rule.key!r} is not a valid glob pattern: {exc}"
                    )

    if errors:
        return {"valid": False, "errors": errors}
    return {"valid": True}


@app.delete("/strategies/{strategy_id}", status_code=204)
async def delete_strategy(strategy_id: str) -> None:
    """Delete a user strategy.

    Returns 403 if the strategy is a builtin.
    Returns 409 if any run references the strategy.
    Returns 404 if not found.
    Returns 204 on success.
    """
    strategy = await coordinator.db.get_user_strategy(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id!r} not found.")
    if strategy.is_builtin:
        raise HTTPException(
            status_code=403,
            detail=f"Strategy {strategy_id!r} is a builtin and cannot be deleted.",
        )
    if await coordinator.db.strategy_is_referenced_by_runs(strategy_id):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Strategy {strategy_id!r} is referenced by one or more runs "
                "and cannot be deleted."
            ),
        )
    await coordinator.db.delete_user_strategy(strategy_id)


@app.get("/profiles")
def list_profiles() -> list[dict]:
    """List available review profiles with descriptions."""
    return coordinator.list_profiles()


@app.get("/templates")
def list_templates() -> list[dict]:
    """List available vulnerability injection templates."""
    return coordinator.list_templates()


@app.get("/tool-extensions")
def list_tool_extensions() -> list[dict]:
    """List available MCP tool extensions with availability status."""
    return coordinator.list_tool_extensions()


# ---------------------------------------------------------------------------
# Internal upload endpoint (Step 4)
# ---------------------------------------------------------------------------

@app.post("/internal/runs/{run_id}/result", status_code=200)
async def upload_run_result(run_id: str, request: Request) -> dict:
    """Receive streamed multipart artifact bundle from a worker pod.

    Served at both /internal/... and /api/internal/... via the /api prefix
    middleware already present in the app.  Workers authenticate with a
    per-run bearer token issued at dispatch time.
    """
    return await coordinator.handle_run_result_upload(run_id, request)


# ---------------------------------------------------------------------------
# Prometheus metrics endpoint (Step 10)
# ---------------------------------------------------------------------------

if _PROMETHEUS_AVAILABLE and _make_asgi_app is not None:
    _metrics_app = _make_asgi_app()
    app.mount("/metrics", _metrics_app)


# Static files — mount last so all API routes take priority
if FRONTEND_DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")
else:
    logger.warning(
        "Frontend dist directory not found at %s — static file serving disabled "
        "(set FRONTEND_DIST_DIR or run `npm run build` in frontend/)",
        FRONTEND_DIST_DIR,
    )
