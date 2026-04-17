"""FastAPI coordinator service for the security review testing framework."""

import asyncio
import json
import logging
import os
import shutil
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sec_review_framework.data.experiment import (
    BatchStatus,
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

class BatchCostTracker:
    """Tracks running spend per batch. Thread-safe."""

    def __init__(self, batch_id: str, cap_usd: float | None):
        self.batch_id = batch_id
        self.cap_usd = cap_usd
        self._spent = 0.0
        self._lock = threading.Lock()
        self._cancelled = False

    def record_job_cost(self, cost_usd: float) -> bool:
        """Returns True if cap exceeded and batch should halt."""
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
# Batch coordinator
# ---------------------------------------------------------------------------

class BatchCoordinator:
    """
    Manages the lifecycle of an experiment batch:
    1. Receives a batch submission (ExperimentMatrix + config)
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
        self.default_cap = default_cap
        self.worker_resources = worker_resources
        self.pvc_name = pvc_name
        self.llm_secret_name = llm_secret_name
        self.config_map_name = config_map_name
        self.worker_image_pull_policy = worker_image_pull_policy
        self._cost_trackers: dict[str, BatchCostTracker] = {}
        self._feedback_tracker = FeedbackTracker()

    # ------------------------------------------------------------------
    # Batch lifecycle
    # ------------------------------------------------------------------

    async def submit_batch(self, matrix: ExperimentMatrix) -> str:
        runs = matrix.expand()
        batch_id = matrix.batch_id

        await self.db.create_batch(
            batch_id=batch_id,
            config_json=matrix.model_dump_json(),
            total_runs=len(runs),
            max_cost_usd=matrix.max_batch_cost_usd,
        )
        for run in runs:
            await self.db.create_run(
                run_id=run.id,
                batch_id=batch_id,
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

        self._cost_trackers[batch_id] = BatchCostTracker(
            batch_id, matrix.max_batch_cost_usd
        )

        thread = threading.Thread(
            target=self._schedule_jobs_in_thread, args=(batch_id, runs), daemon=True
        )
        thread.start()
        return batch_id

    def _schedule_jobs_in_thread(self, batch_id: str, runs: list) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._schedule_jobs(batch_id, runs))
        finally:
            loop.close()

    async def _schedule_jobs(self, batch_id: str, runs: list) -> None:
        """
        Schedules K8s Jobs respecting per-model concurrency caps.
        Uses a polling loop: check running counts, schedule where under cap, sleep, repeat.
        """
        pending = list(runs)
        running_by_model: dict[str, int] = {}

        while pending:
            scheduled_this_round = []
            for run in pending:
                cap = self.concurrency_caps.get(run.model_id, self.default_cap)
                current = running_by_model.get(run.model_id, 0)
                if current < cap:
                    try:
                        self._create_k8s_job(batch_id, run)
                        await self.db.update_run(run.id, status="running")
                        running_by_model[run.model_id] = current + 1
                        scheduled_this_round.append(run)
                    except Exception as e:
                        logger.error(f"Failed to create K8s job for run {run.id}: {e}")

            for run in scheduled_this_round:
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

    def _create_k8s_job(self, batch_id: str, run) -> None:
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

        job = kubernetes.client.V1Job(
            metadata=kubernetes.client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                labels={
                    "app": "sec-review-worker",
                    "batch": self._k8s_safe(batch_id),
                    "model": self._k8s_safe(run.model_id),
                    "strategy": self._k8s_safe(run.strategy.value),
                    "run-hash": run_hash,
                },
                annotations={
                    "sec-review.io/run-id": run.id,
                    "sec-review.io/batch-id": batch_id,
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
                                    "--output-dir", f"/data/outputs/{batch_id}/{run.id}",
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
                                volume_mounts=[
                                    kubernetes.client.V1VolumeMount(
                                        name="shared-data",
                                        mount_path="/data",
                                    ),
                                    kubernetes.client.V1VolumeMount(
                                        name="config",
                                        mount_path="/app/config",
                                    ),
                                ],
                            )
                        ],
                        volumes=[
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
                        ],
                    )
                ),
            ),
        )
        self.k8s_client.create_namespaced_job(self.namespace, job)

    async def get_batch_status(self, batch_id: str) -> BatchStatus:
        batch = await self.db.get_batch(batch_id)
        if not batch:
            raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
        counts = await self.db.count_runs_by_status(batch_id)
        spend = await self.db.get_batch_spend(batch_id)
        return BatchStatus(
            batch_id=batch_id,
            total=batch["total_runs"] or 0,
            completed=counts.get("completed", 0),
            failed=counts.get("failed", 0),
            running=counts.get("running", 0),
            pending=counts.get("pending", 0),
            estimated_cost_so_far=spend,
        )

    def collect_results(self, batch_id: str) -> list[RunResult]:
        results = []
        batch_dir = self.storage_root / "outputs" / batch_id
        if not batch_dir.exists():
            return results
        for run_dir in batch_dir.iterdir():
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
            for batch_dir in outputs_dir.iterdir():
                if not batch_dir.is_dir():
                    continue
                for run_dir in batch_dir.iterdir():
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

    async def finalize_batch(self, batch_id: str) -> None:
        results = self.collect_results(batch_id)
        output_dir = self.storage_root / "outputs" / batch_id
        output_dir.mkdir(parents=True, exist_ok=True)
        if results and self.reporter:
            self.reporter.render_matrix(results, output_dir)
        await self.db.update_batch_status(
            batch_id, "completed", completed_at=datetime.utcnow().isoformat()
        )

    async def cancel_batch(self, batch_id: str) -> int:
        cancelled = 0
        if K8S_AVAILABLE and self.k8s_client:
            try:
                jobs = self.k8s_client.list_namespaced_job(
                    self.namespace, label_selector=f"batch={batch_id}"
                )
                for job in jobs.items:
                    if not job.status.succeeded and not job.status.failed:
                        self.k8s_client.delete_namespaced_job(
                            job.metadata.name, self.namespace
                        )
                        cancelled += 1
            except Exception as e:
                logger.error(f"Error cancelling K8s jobs for batch {batch_id}: {e}")
        await self.db.update_batch_status(batch_id, "cancelled")
        return cancelled

    async def delete_batch(self, batch_id: str) -> None:
        await self.cancel_batch(batch_id)
        batch_dir = self.storage_root / "outputs" / batch_id
        if batch_dir.exists():
            shutil.rmtree(batch_dir, ignore_errors=True)
        config_dir = self.storage_root / "config" / "runs"
        runs = await self.db.list_runs(batch_id)
        for run in runs:
            run_config = config_dir / f"{run['id']}.json"
            if run_config.exists():
                run_config.unlink()

    # ------------------------------------------------------------------
    # Reconciliation (called at startup)
    # ------------------------------------------------------------------

    async def reconcile(self) -> None:
        """
        Called once at coordinator startup. Scans for batches in non-terminal
        states, identifies runs that were never dispatched as K8s Jobs, and
        creates their Jobs. Idempotent — safe to call multiple times.
        """
        batches = await self.db.list_batches()
        active = [b for b in batches if b["status"] not in ("completed", "cancelled", "failed")]

        for batch in active:
            batch_id = batch["id"]
            dispatched_run_ids: set[str] = set()

            if K8S_AVAILABLE and self.k8s_client:
                try:
                    existing_jobs = self.k8s_client.list_namespaced_job(
                        self.namespace, label_selector=f"batch={batch_id}"
                    )
                    dispatched_run_ids = {
                        job.metadata.labels.get("run-id")
                        for job in existing_jobs.items
                        if job.metadata.labels
                    }
                except Exception as e:
                    logger.error(f"Error listing jobs for batch {batch_id}: {e}")

            all_runs = await self.db.list_runs(batch_id)
            orphaned = [
                r for r in all_runs
                if r["status"] == "pending" and r["id"] not in dispatched_run_ids
            ]

            if orphaned:
                logger.warning(
                    f"Batch {batch_id}: {len(orphaned)} orphaned runs — dispatching"
                )
                config_dir = self.storage_root / "config" / "runs"
                for run_data in orphaned:
                    run_config_path = config_dir / f"{run_data['id']}.json"
                    if not run_config_path.exists():
                        continue
                    from sec_review_framework.data.experiment import ExperimentRun
                    run = ExperimentRun.model_validate_json(run_config_path.read_text())
                    try:
                        self._create_k8s_job(batch_id, run)
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

            # Finalize any batch where all jobs are done
            status = await self.get_batch_status(batch_id)
            if status.running == 0 and status.pending == 0 and status.total > 0:
                await self.finalize_batch(batch_id)

    # ------------------------------------------------------------------
    # Result access
    # ------------------------------------------------------------------

    async def _serialize_batch_summary(self, row: dict) -> dict:
        batch_id = row["id"]
        counts = await self.db.count_runs_by_status(batch_id)
        dataset = ""
        try:
            config = json.loads(row.get("config_json") or "{}")
            dataset = config.get("dataset_name", "") or ""
        except (json.JSONDecodeError, TypeError):
            pass
        return {
            "batch_id": batch_id,
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

    async def list_batches(self) -> list[dict]:
        rows = await self.db.list_batches()
        return [await self._serialize_batch_summary(r) for r in rows]

    async def get_batch_detail(self, batch_id: str) -> dict:
        row = await self.db.get_batch(batch_id)
        if not row:
            raise HTTPException(status_code=404, detail=f"Batch {batch_id} not found")
        return await self._serialize_batch_summary(row)

    async def list_runs(self, batch_id: str) -> list[dict]:
        return await self.db.list_runs(batch_id)

    async def get_run_result(self, batch_id: str, run_id: str) -> dict:
        run_data = await self.db.get_run(run_id)
        if not run_data or run_data.get("batch_id") != batch_id:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        result_path = run_data.get("result_path")
        if result_path and Path(result_path).exists():
            return RunResult.model_validate_json(Path(result_path).read_text()).model_dump()

        result_file = self.storage_root / "outputs" / batch_id / run_id / "run_result.json"
        if result_file.exists():
            return RunResult.model_validate_json(result_file.read_text()).model_dump()

        raise HTTPException(
            status_code=404, detail=f"Result for run {run_id} not available yet"
        )

    def get_matrix_report_json(self, batch_id: str) -> dict:
        report_file = self.storage_root / "outputs" / batch_id / "matrix_report.json"
        if not report_file.exists():
            raise HTTPException(status_code=404, detail="Matrix report not available yet")
        return json.loads(report_file.read_text())

    def get_matrix_report_markdown(self, batch_id: str) -> str:
        report_file = self.storage_root / "outputs" / batch_id / "matrix_report.md"
        if not report_file.exists():
            raise HTTPException(status_code=404, detail="Matrix report not available yet")
        return report_file.read_text()

    # ------------------------------------------------------------------
    # Findings
    # ------------------------------------------------------------------

    async def search_findings(
        self, batch_id: str, q: str, run_id: str | None = None
    ) -> list[dict]:
        results = self.collect_results(batch_id)
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

    async def reclassify_finding(self, batch_id: str, run_id: str, req) -> dict:
        result_file = self.storage_root / "outputs" / batch_id / run_id / "run_result.json"
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
        return {"status": "reclassified", "finding_id": req.finding_id}

    async def audit_tool_calls(self, batch_id: str, run_id: str) -> dict:
        import re
        tool_calls_file = (
            self.storage_root / "outputs" / batch_id / run_id / "tool_calls.jsonl"
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
            except Exception:
                pass

        return {"counts_by_tool": counts, "suspicious_calls": suspicious}

    async def compare_runs(self, batch_id: str, run_a_id: str, run_b_id: str) -> dict:
        from sec_review_framework.data.findings import FindingIdentity, Finding

        result_a_dict = await self.get_run_result(batch_id, run_a_id)
        result_b_dict = await self.get_run_result(batch_id, run_b_id)

        def index_findings(result_dict: dict) -> dict[str, dict]:
            out = {}
            for f in result_dict.get("findings") or []:
                try:
                    finding = Finding.model_validate(f)
                    identity = str(FindingIdentity.from_finding(finding))
                    out[identity] = f
                except Exception:
                    pass
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

    def import_cve(self, spec) -> list:
        raise NotImplementedError("CVE import not yet implemented")

    def inject_vuln(self, dataset_name: str, req) -> object:
        raise NotImplementedError("Vulnerability injection not yet implemented")

    def preview_injection(self, dataset_name: str, req) -> dict:
        raise NotImplementedError("Injection preview not yet implemented")

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

    def get_file_content(self, dataset_name: str, path: str) -> dict:
        dataset_dir = self.storage_root / "datasets" / dataset_name
        file_path = dataset_dir / path
        if not file_path.exists() or not file_path.is_file():
            raise HTTPException(status_code=404, detail=f"File {path} not found")
        content = file_path.read_text(errors="replace")
        suffix = file_path.suffix.lstrip(".")
        lang_map = {
            "py": "python", "js": "javascript", "ts": "typescript",
            "java": "java", "go": "go", "rb": "ruby", "rs": "rust",
            "cpp": "cpp", "c": "c", "cs": "csharp", "php": "php",
        }
        language = lang_map.get(suffix, suffix or "text")
        file_labels = [lb for lb in self.get_labels(dataset_name) if lb.get("file_path") == path]
        return {
            "path": path,
            "content": content,
            "language": language,
            "line_count": content.count("\n") + 1,
            "size_bytes": file_path.stat().st_size,
            "labels": file_labels,
        }

    # ------------------------------------------------------------------
    # Feedback
    # ------------------------------------------------------------------

    async def compare_batches(self, batch_a_id: str, batch_b_id: str) -> dict:
        batch_a_results = self.collect_results(batch_a_id)
        batch_b_results = self.collect_results(batch_b_id)
        comparison = self._feedback_tracker.compare_batches(batch_a_results, batch_b_results)
        return {
            "batch_a_id": comparison.batch_a_id,
            "batch_b_id": comparison.batch_b_id,
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

    def get_fp_patterns(self, batch_id: str) -> list[dict]:
        results = self.collect_results(batch_id)
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

    def package_reports(self, batch_id: str) -> Path:
        output_dir = self.storage_root / "outputs" / batch_id
        if not output_dir.exists():
            raise HTTPException(status_code=404, detail=f"Batch {batch_id} outputs not found")
        zip_path = output_dir / f"{batch_id}_reports.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in output_dir.rglob("*"):
                if f.is_file() and f.suffix in (".json", ".md", ".txt") and f != zip_path:
                    zf.write(f, f.relative_to(output_dir))
        return zip_path

    def list_models(self) -> list[dict]:
        config_file = self.storage_root.parent / "config" / "models.yaml"
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
        templates_dir = self.storage_root.parent / "config" / "injection_templates"
        if not templates_dir.exists():
            return []
        try:
            import yaml
            templates = []
            for f in templates_dir.glob("*.yaml"):
                try:
                    data = yaml.safe_load(f.read_text())
                    templates.append({"id": f.stem, **data})
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
    """Background task: deletes batch directories older than retention_days."""
    while True:
        await asyncio.sleep(interval_s)
        cutoff = datetime.utcnow() - timedelta(days=retention_days)
        outputs_dir = storage_root / "outputs"
        if not outputs_dir.exists():
            continue
        for batch_dir in outputs_dir.iterdir():
            if not batch_dir.is_dir():
                continue
            mtime = datetime.utcfromtimestamp(batch_dir.stat().st_mtime)
            if mtime < cutoff:
                shutil.rmtree(batch_dir, ignore_errors=True)
                logger.info(f"Retention cleanup: deleted {batch_dir.name}")


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class ReclassifyRequest(BaseModel):
    finding_id: str
    status: str  # e.g. "unlabeled_real"
    note: str | None = None


class CompareRequest(BaseModel):
    batch_a_id: str
    batch_b_id: str


class EstimateRequest(BaseModel):
    matrix: ExperimentMatrix
    target_kloc: float


AVG_TOKENS_PER_KLOC = 1400  # prompt + completion, calibrated empirically


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(title="Security Review Framework", version="1.0.0")
coordinator: BatchCoordinator = None  # type: ignore[assignment]  # initialized at startup


@app.middleware("http")
async def strip_api_prefix(request, call_next):
    if request.scope["path"].startswith("/api/"):
        request.scope["path"] = request.scope["path"][4:]
        request.scope["raw_path"] = request.scope["path"].encode()
    return await call_next(request)


def build_coordinator_from_env() -> BatchCoordinator:
    """Build a BatchCoordinator from env vars + files mounted at CONFIG_DIR.

    Env vars (set by the Helm chart):
        STORAGE_ROOT  — shared storage mount path (default: /data)
        WORKER_IMAGE  — image used for worker K8s Jobs
        NAMESPACE     — namespace to create Jobs in
        CONFIG_DIR    — directory containing pricing.yaml + concurrency.yaml
                        (default: /app/config)
    """
    from sec_review_framework.config import ConcurrencyConfig
    from sec_review_framework.reporting.markdown import MarkdownReportGenerator

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

    return BatchCoordinator(
        k8s_client=k8s_client,
        storage_root=storage_root,
        concurrency_caps=caps,
        worker_image=worker_image,
        namespace=namespace,
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator.from_config(config_dir),
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
    asyncio.create_task(
        retention_cleanup_loop(coordinator.storage_root, retention_days=30)
    )


# --- Health ---

@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


# --- Smoke test ---

@app.post("/smoke-test")
async def run_smoke_test() -> dict:
    """
    Submit a minimal 1-model × 1-strategy smoke test batch.
    Uses the first configured model (or gpt-4o-mini as a fallback),
    single_agent strategy, with_tools only, no verification.
    Capped at $5.00 for safety.
    """
    if coordinator is None:
        raise HTTPException(status_code=503, detail="Coordinator not initialised")

    # Pick the first configured model, fall back to a sensible default
    configured_models = coordinator.list_models()
    model_id = configured_models[0]["id"] if configured_models else "gpt-4o-mini"

    # Pick the first available dataset, fall back to a placeholder name
    datasets = coordinator.list_datasets()
    dataset_name = datasets[0]["name"] if datasets else "smoke-test-dataset"

    timestamp = int(datetime.utcnow().timestamp())
    batch_id = f"smoke-test-{timestamp}"

    matrix = ExperimentMatrix(
        batch_id=batch_id,
        dataset_name=dataset_name,
        dataset_version="latest",
        model_ids=[model_id],
        strategies=[StrategyName.SINGLE_AGENT],
        tool_variants=[ToolVariant.WITH_TOOLS],
        review_profiles=[ReviewProfileName.DEFAULT],
        verification_variants=[VerificationVariant.NONE],
        num_repetitions=1,
        max_batch_cost_usd=5.0,
    )

    await coordinator.submit_batch(matrix)
    return {
        "batch_id": batch_id,
        "message": "Smoke test batch submitted. Monitor progress on the batch detail page.",
        "total_runs": len(matrix.expand()),
    }


# --- Batches ---

@app.post("/batches", status_code=201)
async def submit_batch(matrix: ExperimentMatrix) -> dict:
    """Submit a new experiment batch. Returns batch_id."""
    batch_id = await coordinator.submit_batch(matrix)
    return {"batch_id": batch_id, "total_runs": len(matrix.expand())}


@app.get("/batches")
async def list_batches() -> list[dict]:
    """List all batches with summary status."""
    return await coordinator.list_batches()


@app.post("/batches/estimate")
async def estimate_batch(req: EstimateRequest) -> dict:
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


@app.get("/batches/{batch_id}")
async def get_batch(batch_id: str) -> dict:
    """Batch summary: dataset, status, per-status run counts, cost so far and cap."""
    return await coordinator.get_batch_detail(batch_id)


@app.get("/batches/{batch_id}/results")
async def get_results_json(batch_id: str) -> dict:
    """Matrix report as JSON. Available after batch completes."""
    return coordinator.get_matrix_report_json(batch_id)


@app.get("/batches/{batch_id}/results/markdown")
async def get_results_markdown(batch_id: str) -> str:
    """Matrix report as Markdown. Available after batch completes."""
    return coordinator.get_matrix_report_markdown(batch_id)


@app.get("/batches/{batch_id}/results/download")
async def download_reports(batch_id: str) -> FileResponse:
    """Download matrix_report.md, matrix_report.json, and all run reports as a .zip."""
    zip_path = coordinator.package_reports(batch_id)
    return FileResponse(
        zip_path, media_type="application/zip", filename=f"{batch_id}_reports.zip"
    )


@app.get("/batches/{batch_id}/runs")
async def list_runs(batch_id: str) -> list[dict]:
    """List all runs in a batch with status and summary metrics."""
    return await coordinator.list_runs(batch_id)


@app.get("/batches/{batch_id}/runs/{run_id}")
async def get_run(batch_id: str, run_id: str) -> dict:
    """Full RunResult including findings, evaluation, and verification details."""
    return await coordinator.get_run_result(batch_id, run_id)


@app.post("/batches/{batch_id}/cancel")
async def cancel_batch(batch_id: str) -> dict:
    """Cancel pending jobs. Running jobs complete but no new ones are scheduled."""
    cancelled = await coordinator.cancel_batch(batch_id)
    return {"cancelled_jobs": cancelled}


@app.get("/batches/{batch_id}/compare-runs")
async def compare_runs(batch_id: str, run_a: str, run_b: str) -> dict:
    """
    Side-by-side comparison of two runs. Returns findings matched
    by FindingIdentity: which were found by both, only by A, only by B.
    """
    return await coordinator.compare_runs(batch_id, run_a, run_b)


@app.delete("/batches/{batch_id}", status_code=204)
async def delete_batch(batch_id: str) -> None:
    """Delete a batch and all its outputs from shared storage."""
    await coordinator.delete_batch(batch_id)


# --- Findings ---

@app.get("/batches/{batch_id}/findings/search")
async def search_findings(
    batch_id: str, q: str, run_id: str | None = None
) -> list[dict]:
    """
    Free-text search across all finding descriptions in a batch.
    Searches: title, description, recommendation, raw_llm_output.
    Optional run_id filter to scope to a single run.
    """
    return await coordinator.search_findings(batch_id, q, run_id)


@app.post("/batches/{batch_id}/runs/{run_id}/reclassify")
async def reclassify_finding(
    batch_id: str, run_id: str, req: ReclassifyRequest
) -> dict:
    """Reclassify a false positive as unlabeled_real. Recomputes metrics."""
    return await coordinator.reclassify_finding(batch_id, run_id, req)


@app.get("/batches/{batch_id}/runs/{run_id}/tool-audit")
async def tool_audit(batch_id: str, run_id: str) -> dict:
    """Tool call audit: counts by tool, any calls with URL-like strings in inputs."""
    return await coordinator.audit_tool_calls(batch_id, run_id)


# --- Datasets ---

@app.get("/datasets")
def list_datasets() -> list[dict]:
    """List available target datasets with label counts."""
    return coordinator.list_datasets()


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
def get_file_content(dataset_name: str, path: str) -> dict:
    """Read a file from the dataset repo. Returns content with metadata."""
    return coordinator.get_file_content(dataset_name, path)


# --- Feedback ---

@app.post("/feedback/compare")
async def compare_batches(req: CompareRequest) -> dict:
    """Compare two batches: metric deltas, persistent FP patterns, regressions."""
    return await coordinator.compare_batches(req.batch_a_id, req.batch_b_id)


@app.get("/feedback/patterns/{batch_id}")
def get_fp_patterns(batch_id: str) -> list[dict]:
    """Extract recurring false positive patterns from a batch."""
    return coordinator.get_fp_patterns(batch_id)


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
try:
    app.mount("/", StaticFiles(directory="frontend/dist", html=True), name="frontend")
except Exception:
    pass  # frontend not built in dev
