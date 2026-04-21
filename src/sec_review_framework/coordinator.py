"""FastAPI coordinator service for the security review testing framework."""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

RECONCILE_INTERVAL_S = int(os.environ.get("RECONCILE_INTERVAL_S", "5"))
RUN_STALL_TIMEOUT_S = int(os.environ.get("RUN_STALL_TIMEOUT_S", "300"))

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

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ValidationError

from sec_review_framework.data.experiment import (
    ExperimentStatus,
    ExperimentMatrix,
    ReviewProfileName,
    RunResult,
    StrategyName,
    ToolExtension,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.db import Database
from sec_review_framework.profiles.review_profiles import BUILTIN_PROFILES, ProfileRegistry
from sec_review_framework.feedback.tracker import FeedbackTracker
from sec_review_framework.cost.calculator import CostCalculator
from sec_review_framework.config import ToolExtensionAvailability
from sec_review_framework.data.evaluation import GroundTruthLabel
from sec_review_framework.ground_truth.cve_importer import (
    CVECandidate as _CVECandidate,
    CVEDiscovery,
    CVEImportSpec,
    CVEImporter,
    CVEResolver,
    CVESelectionCriteria,
)
from sec_review_framework.ground_truth.vuln_injector import VulnInjector

logger = logging.getLogger(__name__)

AUDIT_URL_CHECK_EXEMPT_PREFIXES = ("doc_", "ts_", "lsp_")

_TOOL_EXTENSION_LABELS: dict[ToolExtension, str] = {
    ToolExtension.TREE_SITTER: "Tree-sitter",
    ToolExtension.LSP: "LSP",
    ToolExtension.DEVDOCS: "DevDocs",
}

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
        self._cost_trackers: dict[str, ExperimentCostTracker] = {}
        self._feedback_tracker = FeedbackTracker()

    # ------------------------------------------------------------------
    # Experiment lifecycle
    # ------------------------------------------------------------------

    async def submit_experiment(self, matrix: ExperimentMatrix) -> str:
        runs = matrix.expand()
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

        # Persist run configs to shared storage for worker pods to read
        config_dir = self.storage_root / "config" / "runs"
        config_dir.mkdir(parents=True, exist_ok=True)
        for run in runs:
            (config_dir / f"{run.id}.json").write_text(run.model_dump_json(indent=2))

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

        # Build volume/mount lists, then extend them if extensions need it.
        volume_mounts = [
            kubernetes.client.V1VolumeMount(
                name="shared-data",
                mount_path="/data",
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

        job = kubernetes.client.V1Job(
            metadata=kubernetes.client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                labels={
                    "app": "sec-review-worker",
                    "experiment": self._k8s_safe(experiment_id),
                    "model": self._k8s_safe(run.model_id),
                    "strategy": self._k8s_safe(run.strategy.value),
                    "run-hash": run_hash,
                },
                annotations={
                    "sec-review.io/run-id": run.id,
                    "sec-review.io/experiment-id": experiment_id,
                },
            ),
            spec=kubernetes.client.V1JobSpec(
                backoff_limit=1,
                active_deadline_seconds=1800,
                template=kubernetes.client.V1PodTemplateSpec(
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
                                    "--output-dir", f"/data/outputs/{experiment_id}/{run.id}",
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

    async def finalize_experiment(self, experiment_id: str) -> None:
        results = self.collect_results(experiment_id)
        output_dir = self.storage_root / "outputs" / experiment_id
        output_dir.mkdir(parents=True, exist_ok=True)
        if results and self.reporter:
            self.reporter.render_matrix(results, output_dir)
        await self.db.update_experiment_status(
            experiment_id, "completed", completed_at=datetime.utcnow().isoformat()
        )

    async def cancel_experiment(self, experiment_id: str) -> int:
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
        experiment_dir = self.storage_root / "outputs" / experiment_id
        if experiment_dir.exists():
            shutil.rmtree(experiment_dir, ignore_errors=True)
        config_dir = self.storage_root / "config" / "runs"
        runs = await self.db.list_runs(experiment_id)
        for run in runs:
            run_config = config_dir / f"{run['id']}.json"
            if run_config.exists():
                run_config.unlink()

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
                    run = ExperimentRun.model_validate_json(run_config_path.read_text())
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
                        completed_at=datetime.utcnow().isoformat(),
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
                        else datetime.utcnow().isoformat()
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
                                completed_at=datetime.utcnow().isoformat(),
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
                                    completed_at=datetime.utcnow().isoformat(),
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
            from datetime import timezone as _tz
            now_utc = datetime.now(_tz.utc)
            # Make start_time tz-aware if needed
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=_tz.utc)
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
            completed_at=datetime.utcnow().isoformat(),
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
        from sec_review_framework.data.findings import FindingIdentity, Finding

        result_a_dict = await self.get_run_result(experiment_id, run_a_id)
        result_b_dict = await self.get_run_result(experiment_id, run_b_id)

        def index_findings(result_dict: dict) -> dict[str, dict]:
            out = {}
            for f in result_dict.get("findings") or []:
                try:
                    finding = Finding.model_validate(f)
                    identity = str(FindingIdentity.from_finding(finding))
                    out[identity] = f
                except Exception as exc:
                    logger.warning(
                        "compare_runs: skipping malformed finding in run %s/%s — %s: %.80s",
                        run_a_id, run_b_id, exc, str(f),
                    )
            return out

        findings_a = index_findings(result_a_dict)
        findings_b = index_findings(result_b_dict)

        both, only_a, only_b = [], [], []
        for identity, finding in findings_a.items():
            if identity in findings_b:
                both.append({"identity": identity, "finding_a": finding, "finding_b": findings_b[identity]})
            else:
                only_a.append({"identity": identity, "finding": finding})
        for identity, finding in findings_b.items():
            if identity not in findings_a:
                only_b.append({"identity": identity, "finding": finding})

        return {
            "run_a": {"id": run_a_id},
            "run_b": {"id": run_b_id},
            "both": both,
            "only_a": only_a,
            "only_b": only_b,
        }

    # ------------------------------------------------------------------
    # Datasets
    # ------------------------------------------------------------------

    def list_datasets(self) -> list[dict]:
        datasets_dir = self.storage_root / "datasets"
        if not datasets_dir.exists():
            return []
        result = []
        for dataset_dir in datasets_dir.iterdir():
            if not dataset_dir.is_dir():
                continue
            labels_file = dataset_dir / "labels.json"
            label_count = 0
            if labels_file.exists():
                try:
                    labels = json.loads(labels_file.read_text())
                    label_count = len(labels) if isinstance(labels, list) else 0
                except Exception:
                    pass
            result.append({"name": dataset_dir.name, "label_count": label_count})
        return result

    def import_cve(self, spec: dict) -> list[GroundTruthLabel]:
        try:
            import_spec = CVEImportSpec(**spec)
        except (ValidationError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        importer = self._build_cve_importer()
        try:
            labels = importer.import_from_spec(import_spec)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except subprocess.CalledProcessError:
            raise HTTPException(status_code=502, detail="git clone or checkout failed")
        self._append_labels(import_spec.dataset_name, labels)
        return labels

    def inject_vuln(self, dataset_name: str, req: dict) -> object:
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
        self._append_labels(dataset_name, [result.label])
        return result.label

    def preview_injection(self, dataset_name: str, req: dict) -> dict:
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

    def _append_labels(self, dataset_name: str, new_labels: list[GroundTruthLabel]) -> None:
        """Atomically append new labels to datasets/{name}/labels.json."""
        labels_file = self.storage_root / "datasets" / dataset_name / "labels.json"
        labels_file.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict] = []
        if labels_file.exists():
            try:
                existing = json.loads(labels_file.read_text())
                if not isinstance(existing, list):
                    existing = []
            except Exception:
                existing = []
        combined = existing + [label.model_dump(mode="json") for label in new_labels]
        tmp = labels_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(combined, indent=2))
        tmp.replace(labels_file)

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

    def discover_cves(self, req: "DiscoverCVEsRequest") -> "list[CVECandidateResponse]":
        """Discover CVE candidates matching the provided criteria."""
        from sec_review_framework.data.findings import Severity, VulnClass

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
            candidates = discovery.discover(criteria, max_results=req.max_results)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"CVE discovery failed: {exc}")
        return [CVECandidateResponse.from_candidate(c) for c in candidates]

    def resolve_cve(self, cve_id: str) -> "CVECandidateResponse":
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

    def get_labels(self, dataset_name: str, version: str | None = None) -> list[dict]:
        labels_file = self.storage_root / "datasets" / dataset_name / "labels.json"
        if not labels_file.exists():
            return []
        try:
            return json.loads(labels_file.read_text())
        except Exception:
            return []

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

    def get_file_content(
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

        file_labels = [lb for lb in self.get_labels(dataset_name) if lb.get("file_path") == path]

        result: dict = {
            "path": path,
            "content": content,
            "language": language,
            "line_count": content.count("\n") + 1,
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

    def list_models(self) -> list[dict]:
        if self.config_dir is None:
            return []
        config_file = self.config_dir / "models.yaml"
        if not config_file.exists():
            return []
        try:
            import yaml
            data = yaml.safe_load(config_file.read_text())
            providers = data.get("providers", {})
            return [{"id": k, **v} for k, v in providers.items()]
        except Exception:
            return []

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
# Retention cleanup background task
# ---------------------------------------------------------------------------

async def retention_cleanup_loop(
    storage_root: Path, retention_days: int, interval_s: int = 3600
) -> None:
    """Background task: deletes experiment directories older than retention_days."""
    while True:
        await asyncio.sleep(interval_s)
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        outputs_dir = storage_root / "outputs"
        if not outputs_dir.exists():
            continue
        for experiment_dir in outputs_dir.iterdir():
            if not experiment_dir.is_dir():
                continue
            mtime = datetime.utcfromtimestamp(experiment_dir.stat().st_mtime)
            if mtime < cutoff:
                shutil.rmtree(experiment_dir, ignore_errors=True)
                logger.info(f"Retention cleanup: deleted {experiment_dir.name}")


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class ReclassifyRequest(BaseModel):
    finding_id: str
    status: str  # e.g. "unlabeled_real"
    note: str | None = None


class CompareRequest(BaseModel):
    experiment_a_id: str
    experiment_b_id: str


class EstimateRequest(BaseModel):
    matrix: ExperimentMatrix
    target_kloc: float


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
    def from_candidate(cls, c: _CVECandidate) -> "CVECandidateResponse":
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


AVG_TOKENS_PER_KLOC = 1400  # prompt + completion, calibrated empirically


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(title="Security Review Framework", version="1.0.0")
coordinator: ExperimentCoordinator = None  # type: ignore[assignment]  # initialized at startup
_background_tasks: set[asyncio.Task] = set()


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
    )


@app.on_event("startup")
async def startup() -> None:
    global coordinator
    if coordinator is None:
        coordinator = build_coordinator_from_env()
        await coordinator.db.init()
    await coordinator.reconcile()
    _background_tasks.add(asyncio.create_task(
        retention_cleanup_loop(coordinator.storage_root, retention_days=30)
    ))
    # Periodic reconcile loop is only needed when there's an active scheduler
    # (K8s cluster) or when explicitly enabled for integration tests.
    _enable_loop = os.environ.get("ENABLE_RECONCILE_LOOP", "").strip().lower()
    if K8S_AVAILABLE or _enable_loop in ("1", "true", "yes"):
        _background_tasks.add(asyncio.create_task(coordinator._reconcile_loop()))

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

    _background_tasks.add(asyncio.create_task(_maybe_backfill()))


@app.on_event("shutdown")
async def shutdown() -> None:
    # Cancel all tracked tasks so TestClient teardown (and real SIGTERM) doesn't
    # leave behind coroutines holding closures over per-test storage roots.
    for task in _background_tasks:
        task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()


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

    # Pick the first configured model; require at least one to be present
    configured_models = coordinator.list_models()
    if not configured_models:
        raise HTTPException(
            status_code=412,
            detail="No models configured. Add a model before running smoke tests.",
        )
    model_id = configured_models[0]["id"]

    # Pick the first available dataset; require at least one to be present
    datasets = coordinator.list_datasets()
    if not datasets:
        raise HTTPException(
            status_code=412,
            detail="No datasets registered. Seed a dataset before running smoke tests.",
        )
    dataset_name = datasets[0]["name"]

    timestamp = int(datetime.utcnow().timestamp())
    experiment_id = f"smoke-test-{timestamp}"

    existing_experiments = await coordinator.list_experiments()
    terminal = {"completed", "failed", "cancelled"}
    for b in existing_experiments:
        if b["experiment_id"].startswith("smoke-test-") and b["status"] not in terminal:
            raise HTTPException(
                status_code=409,
                detail=f"A smoke test is already running: {b['experiment_id']}",
            )

    matrix = ExperimentMatrix(
        experiment_id=experiment_id,
        dataset_name=dataset_name,
        dataset_version="latest",
        model_ids=[model_id],
        strategies=[StrategyName.SINGLE_AGENT],
        tool_variants=[ToolVariant.WITH_TOOLS],
        review_profiles=[ReviewProfileName.DEFAULT],
        verification_variants=[VerificationVariant.NONE],
        num_repetitions=1,
        max_experiment_cost_usd=5.0,
        strategy_configs={"single_agent": {"max_turns": 10}},
    )

    await coordinator.submit_experiment(matrix)
    return {
        "experiment_id": experiment_id,
        "message": "Smoke test experiment submitted. Monitor progress on the experiment detail page.",
        "total_runs": len(matrix.expand()),
    }


# --- Experiments ---

@app.post("/experiments", status_code=201)
async def submit_experiment(matrix: ExperimentMatrix) -> dict:
    """Submit a new experiment. Returns experiment_id."""
    experiment_id = await coordinator.submit_experiment(matrix)
    return {"experiment_id": experiment_id, "total_runs": len(matrix.expand())}


@app.get("/experiments")
async def list_experiments() -> list[dict]:
    """List all experiments with summary status."""
    return await coordinator.list_experiments()


@app.post("/experiments/estimate")
async def estimate_experiment(req: EstimateRequest) -> dict:
    """Pre-flight cost estimate. Call before submit to avoid expensive mistakes."""
    runs = req.matrix.expand()
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
    Side-by-side comparison of two runs. Returns findings matched
    by FindingIdentity: which were found by both, only by A, only by B.
    """
    return await coordinator.compare_runs(experiment_id, run_a, run_b)


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
def list_datasets() -> list[dict]:
    """List available target datasets with label counts."""
    return coordinator.list_datasets()


@app.post("/datasets/discover-cves")
def discover_cves(req: DiscoverCVEsRequest) -> list[CVECandidateResponse]:
    """Search for CVE candidates matching the given criteria.

    Makes external calls to GitHub Advisory DB, OSV.dev, and/or NVD.
    Returns an empty list when no candidates match (never 404).
    """
    return coordinator.discover_cves(req)


@app.get("/datasets/resolve-cve")
def resolve_cve(id: str) -> CVECandidateResponse:
    """Resolve a single CVE ID to a candidate record.

    Returns 404 if the CVE cannot be found in GHSA/OSV/NVD.
    """
    return coordinator.resolve_cve(id)


@app.post("/datasets/import-cve", status_code=201)
def import_cve(spec: dict) -> dict:
    """Import a CVE-patched repo as a ground truth dataset."""
    labels = coordinator.import_cve(spec)
    return {"labels_created": len(labels)}


@app.post("/datasets/{dataset_name}/inject/preview")
def preview_injection(dataset_name: str, req: dict) -> dict:
    """
    Dry-run injection: returns the unified diff and affected line range
    without modifying any files.
    """
    return coordinator.preview_injection(dataset_name, req)


@app.post("/datasets/{dataset_name}/inject", status_code=201)
def inject_vuln(dataset_name: str, req: dict) -> dict:
    """Inject a vulnerability from a template into a dataset."""
    label = coordinator.inject_vuln(dataset_name, req)
    return {"label_id": label.id, "file_path": label.file_path}


@app.get("/datasets/{dataset_name}/labels")
def get_labels(dataset_name: str, version: str | None = None) -> list[dict]:
    """View ground truth labels for a dataset."""
    return coordinator.get_labels(dataset_name, version)


@app.get("/datasets/{dataset_name}/tree")
def get_file_tree(dataset_name: str) -> dict:
    """Repo file tree. Returns nested structure: {name, type, children, size}."""
    return coordinator.get_file_tree(dataset_name)


@app.get("/datasets/{dataset_name}/file")
def get_file_content(
    dataset_name: str,
    path: str,
    start: int | None = None,
    end: int | None = None,
) -> dict:
    """Read a file from the dataset repo. Returns content with metadata."""
    return coordinator.get_file_content(dataset_name, path, start=start, end=end)


# --- Feedback ---

@app.post("/feedback/compare")
async def compare_experiments_endpoint(req: CompareRequest) -> dict:
    """Compare two experiments: metric deltas, persistent FP patterns, regressions."""
    return await coordinator.compare_experiments(req.experiment_a_id, req.experiment_b_id)


@app.get("/feedback/patterns/{experiment_id}")
def get_fp_patterns(experiment_id: str) -> list[dict]:
    """Extract recurring false positive patterns from an experiment."""
    return coordinator.get_fp_patterns(experiment_id)


# --- Matrix ---

@app.get("/matrix/accuracy")
def get_accuracy_matrix() -> dict:
    """Accuracy heatmap data: recall averaged across completed runs, grouped by model × strategy."""
    return coordinator.get_accuracy_matrix()


# --- Reference ---

@app.get("/models")
def list_models() -> list[dict]:
    """List configured model providers."""
    return coordinator.list_models()


@app.get("/strategies")
def list_strategies() -> list[dict]:
    """List available scan strategies."""
    return coordinator.list_strategies()


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


# Static files — mount last so all API routes take priority
if FRONTEND_DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST_DIR), html=True), name="frontend")
else:
    logger.warning(
        "Frontend dist directory not found at %s — static file serving disabled "
        "(set FRONTEND_DIST_DIR or run `npm run build` in frontend/)",
        FRONTEND_DIST_DIR,
    )
