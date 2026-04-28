"""JSONReportGenerator — per-run and matrix reports in JSON."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from sec_review_framework.data.evaluation import EvidenceQuality
from sec_review_framework.data.experiment import RunResult, RunStatus
from sec_review_framework.reporting.generator import ReportGenerator


def _safe_rate(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else 0.0


class JSONReportGenerator(ReportGenerator):
    """Renders experiment results as JSON reports."""

    # ------------------------------------------------------------------
    # Per-run report
    # ------------------------------------------------------------------

    def render_run(self, result: RunResult, output_dir: Path) -> None:
        """Write report.json for a single experiment run."""
        output_dir.mkdir(parents=True, exist_ok=True)
        data = self._serialize_run(result)
        path = output_dir / "report.json"
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def _serialize_run(self, result: RunResult) -> dict:
        """Serialize a RunResult to a JSON-compatible dict."""
        exp = result.experiment
        ev = result.evaluation

        # Metrics block
        if ev is not None:
            eq = ev.evidence_quality_counts
            metrics = {
                "precision": ev.precision,
                "recall": ev.recall,
                "f1": ev.f1,
                "false_positive_rate": ev.false_positive_rate,
                "true_positives": ev.true_positives,
                "false_positives": ev.false_positives,
                "false_negatives": ev.false_negatives,
                "unlabeled_real": ev.unlabeled_real_count,
                "evidence_quality": {
                    "strong": eq.get(EvidenceQuality.STRONG.value, 0),
                    "adequate": eq.get(EvidenceQuality.ADEQUATE.value, 0),
                    "weak": eq.get(EvidenceQuality.WEAK.value, 0),
                    "not_assessed": eq.get(EvidenceQuality.NOT_ASSESSED.value, 0),
                },
            }
        else:
            metrics = None

        # Dedup block
        so = result.strategy_output
        pre = so.pre_dedup_count
        post = so.post_dedup_count
        dedup = {
            "pre_count": pre,
            "post_count": post,
            "dedup_rate": _safe_rate(pre - post, pre),
        }

        # Verification block
        vr = result.verification_result
        if vr is not None:
            verification = {
                "candidates": vr.total_candidates,
                "verified": len(vr.verified),
                "rejected": len(vr.rejected),
                "uncertain": len(vr.uncertain),
                "verification_tokens": vr.verification_tokens,
            }
        else:
            verification = None

        # Findings list (serialized as dicts via pydantic)
        findings_data = [f.model_dump() for f in result.findings]

        return {
            "run_id": exp.id,
            "experiment_id": exp.experiment_id,
            "model_id": exp.model_id,
            "strategy": exp.strategy.value,
            "tool_variant": exp.tool_variant.value,
            "tool_extensions": sorted(e.value for e in exp.tool_extensions),
            "review_profile": exp.review_profile.value,
            "verification_variant": exp.verification_variant.value,
            "dataset_name": exp.dataset_name,
            "dataset_version": exp.dataset_version,
            "status": result.status.value,
            "metrics": metrics,
            "dedup": dedup,
            "verification": verification,
            "token_usage": {
                "input": result.total_input_tokens,
                "output": result.total_output_tokens,
            },
            "estimated_cost_usd": result.estimated_cost_usd,
            "duration_seconds": result.duration_seconds,
            "tool_call_count": result.tool_call_count,
            "error": result.error,
            "completed_at": result.completed_at.isoformat() if result.completed_at else None,
            "findings": findings_data,
        }

    # ------------------------------------------------------------------
    # Matrix report
    # ------------------------------------------------------------------

    def render_matrix(self, results: list[RunResult], output_dir: Path) -> None:
        """Write matrix_report.json summarising all runs in the experiment."""
        output_dir.mkdir(parents=True, exist_ok=True)
        data = self._build_matrix_data(results)
        path = output_dir / "matrix_report.json"
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")

    def _build_matrix_data(self, results: list[RunResult]) -> dict:
        if not results:
            return {"runs": [], "findings": [], "dimension_analysis": {}, "cost_analysis": {}}

        experiment_id = results[0].experiment.experiment_id
        dataset_name = results[0].experiment.dataset_name
        dataset_version = results[0].experiment.dataset_version

        completed = [r for r in results if r.status == RunStatus.COMPLETED and r.evaluation is not None]

        runs_data = [self._serialize_run(r) for r in results]

        dimension_analysis = {
            "by_model": self._aggregate_by(completed, lambda r: r.experiment.model_id),
            "by_strategy": self._aggregate_by(completed, lambda r: r.experiment.strategy.value),
            "tool_access_impact": self._aggregate_by(completed, lambda r: r.experiment.tool_variant.value),
            "review_profile_impact": self._aggregate_by(completed, lambda r: r.experiment.review_profile.value),
            "verification_impact": self._aggregate_by(completed, lambda r: r.experiment.verification_variant.value),
            "tool_extension_impact": self._aggregate_by(
                completed,
                lambda r: ",".join(sorted(e.value for e in r.experiment.tool_extensions)),
            ),
            "evidence_quality_by_model": self._evidence_quality_by_model(completed),
        }

        cost_analysis = self._build_cost_analysis(results)

        # Aggregate findings across all runs so the UI's FindingsExplorer
        # can render them without a separate fetch per run.
        findings_data: list[dict] = []
        for run in results:
            for finding in run.findings:
                record = finding.model_dump()
                record.setdefault("run_id", run.experiment.id)
                findings_data.append(record)

        return {
            "experiment_id": experiment_id,
            "dataset": {
                "name": dataset_name,
                "version": dataset_version,
            },
            "generated_at": datetime.now(UTC).isoformat(),
            "runs": runs_data,
            "findings": findings_data,
            "dimension_analysis": dimension_analysis,
            "cost_analysis": cost_analysis,
        }

    # ------------------------------------------------------------------
    # Aggregation helpers
    # ------------------------------------------------------------------

    def _aggregate_by(self, results: list[RunResult], key_fn) -> dict:
        """Average metrics grouped by the result of key_fn."""
        grouped: dict[str, list[RunResult]] = defaultdict(list)
        for r in results:
            grouped[key_fn(r)].append(r)

        out: dict[str, dict] = {}
        for dim_val, group in grouped.items():
            evs = [r.evaluation for r in group if r.evaluation is not None]
            if not evs:
                continue

            def _mean(values) -> float:
                total = list(values)
                return sum(total) / len(total) if total else 0.0

            out[dim_val] = {
                "run_count": len(group),
                "avg_precision": _mean(e.precision for e in evs),
                "avg_recall": _mean(e.recall for e in evs),
                "avg_f1": _mean(e.f1 for e in evs),
                "avg_false_positive_rate": _mean(e.false_positive_rate for e in evs),
                "total_true_positives": sum(e.true_positives for e in evs),
                "total_false_positives": sum(e.false_positives for e in evs),
                "total_false_negatives": sum(e.false_negatives for e in evs),
            }
        return out

    def _evidence_quality_by_model(self, results: list[RunResult]) -> dict:
        """Evidence quality breakdown aggregated per model."""
        by_model: dict[str, dict] = defaultdict(lambda: {
            "strong": 0, "adequate": 0, "weak": 0, "not_assessed": 0, "total_tp": 0,
        })
        for r in results:
            if r.evaluation is None:
                continue
            model_id = r.experiment.model_id
            eq = r.evaluation.evidence_quality_counts
            by_model[model_id]["strong"] += eq.get(EvidenceQuality.STRONG.value, 0)
            by_model[model_id]["adequate"] += eq.get(EvidenceQuality.ADEQUATE.value, 0)
            by_model[model_id]["weak"] += eq.get(EvidenceQuality.WEAK.value, 0)
            by_model[model_id]["not_assessed"] += eq.get(EvidenceQuality.NOT_ASSESSED.value, 0)
            by_model[model_id]["total_tp"] += r.evaluation.true_positives
        return dict(by_model)

    def _build_cost_analysis(self, results: list[RunResult]) -> dict:
        """Build cost_analysis section: per-model aggregates and experiment total."""
        by_model: dict[str, list[RunResult]] = defaultdict(list)
        for r in results:
            by_model[r.experiment.model_id].append(r)

        total_experiment_cost = sum(r.estimated_cost_usd for r in results)
        by_model_data: dict[str, dict] = {}
        cost_per_tp: dict[str, float | None] = {}

        for model_id, model_results in by_model.items():
            total_cost = sum(r.estimated_cost_usd for r in model_results)
            avg_cost = total_cost / len(model_results) if model_results else 0.0
            total_tp = sum(
                r.evaluation.true_positives
                for r in model_results
                if r.evaluation is not None
            )
            cpt = total_cost / total_tp if total_tp > 0 else None

            by_model_data[model_id] = {
                "avg_cost_per_run": avg_cost,
                "total_cost": total_cost,
                "run_count": len(model_results),
            }
            cost_per_tp[model_id] = cpt

        return {
            "by_model": by_model_data,
            "total_experiment_cost": total_experiment_cost,
            "cost_per_true_positive": cost_per_tp,
        }
