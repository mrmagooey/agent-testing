"""MarkdownReportGenerator — per-run and matrix reports in Markdown."""

from __future__ import annotations

import statistics
from collections import defaultdict
from pathlib import Path

from sec_review_framework.data.evaluation import EvidenceQuality, MatchStatus
from sec_review_framework.data.experiment import RunResult, RunStatus, ToolExtension
from sec_review_framework.reporting.generator import ReportGenerator


def _fmt_duration(seconds: float) -> str:
    """Format a duration in seconds as XmYs."""
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def _fmt_pct(value: float) -> str:
    return f"{value:.2%}"


def _fmt_float(value: float, decimals: int = 2) -> str:
    return f"{value:.{decimals}f}"


def _fmt_cost(value: float) -> str:
    return f"${value:.2f}"


class MarkdownReportGenerator(ReportGenerator):
    """Renders experiment results as Markdown reports."""

    # ------------------------------------------------------------------
    # Per-run report
    # ------------------------------------------------------------------

    def render_run(self, result: RunResult, output_dir: Path) -> None:
        """Write report.md for a single experiment run."""
        output_dir.mkdir(parents=True, exist_ok=True)
        content = self._build_run_report(result)
        (output_dir / "report.md").write_text(content, encoding="utf-8")

    def _build_run_report(self, result: RunResult) -> str:
        exp = result.experiment
        lines: list[str] = []

        # --- Header ---
        lines.append(f"# Security Review Run: {exp.id}\n")
        lines.append("## Run Header\n")
        lines.append(f"| Field | Value |")
        lines.append(f"|-------|-------|")
        lines.append(f"| Experiment ID | `{exp.id}` |")
        lines.append(f"| Model | `{exp.model_id}` |")
        lines.append(f"| Strategy | `{exp.strategy.value}` |")
        lines.append(f"| Tool Variant | `{exp.tool_variant.value}` |")
        lines.append(f"| Review Profile | `{exp.review_profile.value}` |")
        lines.append(f"| Verification | `{exp.verification_variant.value}` |")
        lines.append(f"| Dataset | `{exp.dataset_name}` v`{exp.dataset_version}` |")
        lines.append(f"| Status | `{result.status.value}` |")
        lines.append(f"| Duration | {_fmt_duration(result.duration_seconds)} |")
        lines.append(f"| Input Tokens | {result.total_input_tokens:,} |")
        lines.append(f"| Output Tokens | {result.total_output_tokens:,} |")
        lines.append(f"| Estimated Cost | {_fmt_cost(result.estimated_cost_usd)} |")
        lines.append("")

        if result.error:
            lines.append(f"> **Error:** {result.error}\n")

        # --- Metrics Summary ---
        ev = result.evaluation
        if ev is not None:
            lines.append("## Metrics Summary\n")
            lines.append("| Metric | Value |")
            lines.append("|--------|-------|")
            lines.append(f"| Precision | {_fmt_pct(ev.precision)} |")
            lines.append(f"| Recall | {_fmt_pct(ev.recall)} |")
            lines.append(f"| F1 | {_fmt_pct(ev.f1)} |")
            lines.append(f"| False Positive Rate | {_fmt_pct(ev.false_positive_rate)} |")
            lines.append(f"| True Positives | {ev.true_positives} |")
            lines.append(f"| False Positives | {ev.false_positives} |")
            lines.append(f"| False Negatives | {ev.false_negatives} |")
            lines.append(f"| Unlabeled Real | {ev.unlabeled_real_count} |")
            lines.append("")

            # Evidence quality breakdown
            eq_counts = ev.evidence_quality_counts
            strong = eq_counts.get(EvidenceQuality.STRONG.value, 0)
            adequate = eq_counts.get(EvidenceQuality.ADEQUATE.value, 0)
            weak = eq_counts.get(EvidenceQuality.WEAK.value, 0)
            not_assessed = eq_counts.get(EvidenceQuality.NOT_ASSESSED.value, 0)
            lines.append("### Evidence Quality (True Positives)\n")
            lines.append("| Quality | Count |")
            lines.append("|---------|-------|")
            lines.append(f"| Strong | {strong} |")
            lines.append(f"| Adequate | {adequate} |")
            lines.append(f"| Weak | {weak} |")
            lines.append(f"| Not Assessed | {not_assessed} |")
            lines.append("")

        # --- Dedup Summary ---
        so = result.strategy_output
        lines.append("## Dedup Summary\n")
        lines.append("| Field | Value |")
        lines.append("|-------|-------|")
        lines.append(f"| Pre-Dedup Count | {so.pre_dedup_count} |")
        lines.append(f"| Post-Dedup Count | {so.post_dedup_count} |")
        if so.pre_dedup_count > 0:
            dedup_rate = 1.0 - so.post_dedup_count / so.pre_dedup_count
            lines.append(f"| Dedup Rate | {_fmt_pct(dedup_rate)} |")
        if so.dedup_log:
            lines.append(f"| Merges | {len(so.dedup_log)} |")
        lines.append("")

        # --- Verification Summary ---
        vr = result.verification_result
        if vr is not None:
            lines.append("## Verification Summary\n")
            lines.append("| Field | Value |")
            lines.append("|-------|-------|")
            lines.append(f"| Candidates In | {vr.total_candidates} |")
            lines.append(f"| Verified | {len(vr.verified)} |")
            lines.append(f"| Rejected | {len(vr.rejected)} |")
            lines.append(f"| Uncertain | {len(vr.uncertain)} |")
            lines.append(f"| Verification Tokens | {vr.verification_tokens:,} |")
            lines.append("")

        # --- True Positives ---
        if ev is not None:
            tp_findings = [
                mf for mf in ev.matched_findings if mf.match_status == MatchStatus.TRUE_POSITIVE
            ]
            if tp_findings:
                lines.append(f"## True Positives ({len(tp_findings)})\n")
                for mf in tp_findings:
                    f = mf.finding
                    lines.append(f"### {f.title}\n")
                    lines.append(f"- **File:** `{f.file_path}`")
                    lines.append(f"- **Vuln Class:** `{f.vuln_class.value}`")
                    lines.append(f"- **Severity:** `{f.severity.value}`")
                    if f.line_start is not None:
                        lines.append(f"- **Lines:** {f.line_start}–{f.line_end or f.line_start}")
                    lines.append(f"- **Line Overlap:** {'Yes' if mf.line_overlap else 'No'}")
                    lines.append(f"- **Evidence Quality:** `{mf.evidence_quality.value}`")
                    lines.append("")
                    lines.append(f"**Description:**\n\n{f.description}\n")

            # --- False Positives ---
            fp_findings = [
                mf for mf in ev.matched_findings if mf.match_status == MatchStatus.FALSE_POSITIVE
            ]
            if fp_findings:
                lines.append(f"## False Positives ({len(fp_findings)})\n")
                for mf in fp_findings:
                    f = mf.finding
                    lines.append(f"### {f.title}\n")
                    lines.append(f"- **File:** `{f.file_path}`")
                    lines.append(f"- **Vuln Class:** `{f.vuln_class.value}`")
                    lines.append(f"- **Severity:** `{f.severity.value}`")
                    if f.line_start is not None:
                        lines.append(f"- **Lines:** {f.line_start}–{f.line_end or f.line_start}")
                    lines.append(f"- *(not in ground truth)*")
                    lines.append("")
                    lines.append(f"**Description:**\n\n{f.description}\n")

            # --- Missed Vulnerabilities ---
            if ev.unmatched_labels:
                lines.append(f"## Missed Vulnerabilities ({len(ev.unmatched_labels)})\n")
                for label in ev.unmatched_labels:
                    lines.append(f"### {label.vuln_class.value} — `{label.file_path}`\n")
                    lines.append(f"- **Lines:** {label.line_start}–{label.line_end}")
                    lines.append(f"- **Severity:** `{label.severity.value}`")
                    lines.append(f"- **Source:** `{label.source.value}`")
                    if label.source_ref:
                        lines.append(f"- **Ref:** {label.source_ref}")
                    lines.append("")
                    lines.append(f"**Description:** {label.description}\n")

        # --- Rejected by Verification ---
        if vr is not None and vr.rejected:
            lines.append(f"## Rejected by Verification ({len(vr.rejected)})\n")
            for vf in vr.rejected:
                f = vf.finding
                lines.append(f"### {f.title}\n")
                lines.append(f"- **File:** `{f.file_path}`")
                lines.append(f"- **Rejection Reason:** {vf.evidence}")
                lines.append("")

        # --- Tool Call Summary ---
        lines.append("## Tool Call Summary\n")
        lines.append(f"- **Total Tool Calls:** {result.tool_call_count}")
        lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Matrix report
    # ------------------------------------------------------------------

    def render_matrix(self, results: list[RunResult], output_dir: Path) -> None:
        """Write matrix_report.md comparing all runs in the batch."""
        output_dir.mkdir(parents=True, exist_ok=True)
        content = self._build_matrix_report(results)
        (output_dir / "matrix_report.md").write_text(content, encoding="utf-8")

    def _build_matrix_report(self, results: list[RunResult]) -> str:
        if not results:
            return "# Security Review Framework — No Results\n"

        batch_id = results[0].experiment.batch_id
        lines: list[str] = []

        lines.append(f"# Security Review Framework — Batch `{batch_id}` Results\n")

        # --- Comparative Matrix ---
        lines.append("## Comparative Matrix\n")

        # Determine which extensions appear in any run (gate columns)
        active_extensions = [
            ext for ext in ToolExtension
            if any(ext in r.experiment.tool_extensions for r in results)
        ]
        ext_col_headers = "".join(f" ext-{ext.value} |" for ext in active_extensions)
        ext_col_seps = "".join(" ------------|" for _ in active_extensions)

        header = (
            "| Model | Strategy | Tools | Profile | Verif "
            f"| Prec | Recall | F1 | FPR | TP | FP | FN | Ev:Strong |{ext_col_headers} Cost | Duration |"
        )
        separator = (
            "|-------|----------|-------|---------|-------"
            f"|------|--------|----|-----|----|----|----|-----------|{ext_col_seps}------|----------|"
        )
        lines.append(header)
        lines.append(separator)

        for r in sorted(results, key=lambda x: (x.experiment.model_id, x.experiment.strategy.value)):
            exp = r.experiment
            ev = r.evaluation
            if ev is None:
                prec = recall = f1 = fpr = "—"
                tp = fp = fn = strong = "—"
            else:
                prec = _fmt_pct(ev.precision)
                recall = _fmt_pct(ev.recall)
                f1 = _fmt_pct(ev.f1)
                fpr = _fmt_pct(ev.false_positive_rate)
                tp = str(ev.true_positives)
                fp = str(ev.false_positives)
                fn = str(ev.false_negatives)
                strong_count = ev.evidence_quality_counts.get(EvidenceQuality.STRONG.value, 0)
                strong = f"{strong_count}/{ev.true_positives}"
            cost = _fmt_cost(r.estimated_cost_usd)
            dur = _fmt_duration(r.duration_seconds)
            ext_cells = "".join(
                f" {'yes' if ext in exp.tool_extensions else 'no'} |"
                for ext in active_extensions
            )
            lines.append(
                f"| {exp.model_id} | {exp.strategy.value} | {exp.tool_variant.value} "
                f"| {exp.review_profile.value} | {exp.verification_variant.value} "
                f"| {prec} | {recall} | {f1} | {fpr} | {tp} | {fp} | {fn} "
                f"| {strong} |{ext_cells} {cost} | {dur} |"
            )
        lines.append("")

        # --- Analysis by Dimension ---
        lines.append("## Analysis by Dimension\n")

        completed = [r for r in results if r.status == RunStatus.COMPLETED and r.evaluation is not None]

        # Model Comparison
        lines.append("### Model Comparison (averaged across other dimensions)\n")
        lines.extend(self._dimension_table(completed, key=lambda r: r.experiment.model_id, label="Model"))

        # Strategy Comparison
        lines.append("### Strategy Comparison (averaged across other dimensions)\n")
        lines.extend(self._dimension_table(completed, key=lambda r: r.experiment.strategy.value, label="Strategy"))

        # Tool Access Impact
        lines.append("### Tool Access Impact\n")
        lines.extend(self._dimension_table(completed, key=lambda r: r.experiment.tool_variant.value, label="Tool Variant"))

        # Review Profile Impact
        lines.append("### Review Profile Impact\n")
        lines.extend(self._dimension_table(completed, key=lambda r: r.experiment.review_profile.value, label="Profile"))

        # Verification Impact
        lines.append("### Verification Impact\n")
        lines.extend(self._dimension_table(completed, key=lambda r: r.experiment.verification_variant.value, label="Verification"))

        # Tool Extension Impact
        lines.append("### Tool Extension Impact\n")
        lines.extend(self._dimension_table(
            completed,
            key=lambda r: (
                "+".join(sorted(e.value for e in r.experiment.tool_extensions))
                if r.experiment.tool_extensions else "(none)"
            ),
            label="Extensions",
        ))

        # Evidence Quality by Model
        lines.append("### Evidence Quality by Model\n")
        lines.extend(self._evidence_quality_table(completed))

        # --- Dedup Analysis ---
        lines.append("## Deduplication Analysis\n")
        lines.append("| Strategy | Avg Pre-Dedup | Avg Post-Dedup | Avg Dedup Rate |")
        lines.append("|----------|---------------|----------------|----------------|")
        by_strategy: dict[str, list[RunResult]] = defaultdict(list)
        for r in results:
            by_strategy[r.experiment.strategy.value].append(r)
        for strategy, strat_results in sorted(by_strategy.items()):
            pre = statistics.mean(r.strategy_output.pre_dedup_count for r in strat_results)
            post = statistics.mean(r.strategy_output.post_dedup_count for r in strat_results)
            rate = 1.0 - post / pre if pre > 0 else 0.0
            lines.append(
                f"| {strategy} | {pre:.1f} | {post:.1f} | {_fmt_pct(rate)} |"
            )
        lines.append("")

        # --- Cost Analysis ---
        lines.append("## Cost Analysis\n")
        lines.append("| Model | Avg Cost/Run | Total Batch Cost | Cost per True Positive |")
        lines.append("|-------|-------------|------------------|----------------------|")
        by_model: dict[str, list[RunResult]] = defaultdict(list)
        for r in results:
            by_model[r.experiment.model_id].append(r)
        for model_id, model_results in sorted(by_model.items()):
            avg_cost = statistics.mean(r.estimated_cost_usd for r in model_results)
            total_cost = sum(r.estimated_cost_usd for r in model_results)
            total_tp = sum(
                r.evaluation.true_positives for r in model_results if r.evaluation is not None
            )
            cost_per_tp = _fmt_cost(total_cost / total_tp) if total_tp > 0 else "—"
            lines.append(
                f"| {model_id} | {_fmt_cost(avg_cost)} | {_fmt_cost(total_cost)} | {cost_per_tp} |"
            )
        lines.append("")

        # --- Findings Across Experiments ---
        lines.append("## Findings Across Experiments\n")

        # Vulns found by ALL models
        label_detection: dict[str, list[str]] = defaultdict(list)
        all_model_ids = list({r.experiment.model_id for r in completed})
        for r in completed:
            if r.evaluation:
                for mf in r.evaluation.matched_findings:
                    if mf.match_status == MatchStatus.TRUE_POSITIVE and mf.matched_label:
                        label_detection[mf.matched_label.id].append(r.experiment.model_id)

        universal = {lid for lid, models in label_detection.items() if len(set(models)) == len(all_model_ids)}
        lines.append(f"### Vulnerabilities Found by All Models (High Confidence)\n")
        if universal:
            lines.append(f"Found by all {len(all_model_ids)} models: **{len(universal)}** vulnerabilities\n")
        else:
            lines.append("No vulnerabilities detected by all models.\n")

        # Consistently missed
        all_label_ids: set[str] = set()
        for r in completed:
            if r.evaluation:
                for label in r.evaluation.unmatched_labels:
                    all_label_ids.add(label.id)
                for mf in r.evaluation.matched_findings:
                    if mf.matched_label:
                        all_label_ids.add(mf.matched_label.id)

        always_missed = all_label_ids - set(label_detection.keys())
        lines.append(f"### Vulnerabilities Consistently Missed\n")
        if always_missed:
            lines.append(f"Missed by all models: **{len(always_missed)}** vulnerabilities\n")
        else:
            lines.append("No vulnerabilities were missed by all models.\n")

        # Unlabeled real
        unlabeled_ids: set[str] = set()
        for r in completed:
            if r.evaluation:
                for mf in r.evaluation.matched_findings:
                    if mf.match_status == MatchStatus.UNLABELED_REAL:
                        unlabeled_ids.add(mf.finding.id)
        lines.append("### Unlabeled Real Vulnerabilities Discovered\n")
        if unlabeled_ids:
            lines.append(f"**{len(unlabeled_ids)}** potential unlabeled real vulnerabilities found.\n")
        else:
            lines.append("No unlabeled real vulnerabilities identified.\n")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _dimension_table(
        self,
        results: list[RunResult],
        key,
        label: str,
    ) -> list[str]:
        """Build an averaged metrics table grouped by the given key function."""
        grouped: dict[str, list[RunResult]] = defaultdict(list)
        for r in results:
            grouped[key(r)].append(r)

        rows = ["| {label} | Avg Prec | Avg Recall | Avg F1 | Avg FPR | Total TP | Total FP |".format(label=label)]
        rows.append("|" + "---------|" * 7)
        for dim_val, dim_results in sorted(grouped.items()):
            evs = [r.evaluation for r in dim_results if r.evaluation is not None]
            if not evs:
                continue
            avg_prec = statistics.mean(e.precision for e in evs)
            avg_recall = statistics.mean(e.recall for e in evs)
            avg_f1 = statistics.mean(e.f1 for e in evs)
            avg_fpr = statistics.mean(e.false_positive_rate for e in evs)
            total_tp = sum(e.true_positives for e in evs)
            total_fp = sum(e.false_positives for e in evs)
            rows.append(
                f"| {dim_val} | {_fmt_pct(avg_prec)} | {_fmt_pct(avg_recall)} "
                f"| {_fmt_pct(avg_f1)} | {_fmt_pct(avg_fpr)} | {total_tp} | {total_fp} |"
            )
        rows.append("")
        return rows

    def _evidence_quality_table(self, results: list[RunResult]) -> list[str]:
        """Build evidence quality breakdown table grouped by model."""
        by_model: dict[str, list[RunResult]] = defaultdict(list)
        for r in results:
            by_model[r.experiment.model_id].append(r)

        rows = ["| Model | Strong | Adequate | Weak | Not Assessed | Total TP |"]
        rows.append("|-------|--------|----------|------|--------------|----------|")
        for model_id, model_results in sorted(by_model.items()):
            strong = adequate = weak = not_assessed = total_tp = 0
            for r in model_results:
                if r.evaluation:
                    eq = r.evaluation.evidence_quality_counts
                    strong += eq.get(EvidenceQuality.STRONG.value, 0)
                    adequate += eq.get(EvidenceQuality.ADEQUATE.value, 0)
                    weak += eq.get(EvidenceQuality.WEAK.value, 0)
                    not_assessed += eq.get(EvidenceQuality.NOT_ASSESSED.value, 0)
                    total_tp += r.evaluation.true_positives
            rows.append(
                f"| {model_id} | {strong} | {adequate} | {weak} | {not_assessed} | {total_tp} |"
            )
        rows.append("")
        return rows
