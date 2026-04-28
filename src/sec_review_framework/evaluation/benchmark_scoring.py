"""Polarity-aware benchmark scoring for datasets with negative labels.

This module is activated only when ``list_dataset_negative_labels()`` returns
a non-empty list for the dataset under evaluation.  When negatives are absent
(all existing CVE datasets) every code path remains byte-identical to the
pre-benchmark behaviour.

The per-(CWE, dataset) breakdown follows the OWASP Benchmark scoring model:
    TP  — agent reports CWE-X on file_path that has a *positive* label
    FP  — agent reports CWE-X on file_path that has a *negative* label
    TN  — negative label with no agent finding on that file/CWE
    FN  — positive label with no agent finding on that file/CWE
    owasp_score = TPR - FPR  (the OWASP Benchmark headline metric)

Out-of-scope findings (CWE not in either label set) do not affect the scorecard.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_MIN_SAMPLE_SIZE = 25


@dataclass
class CWEScorecard:
    """Per-CWE metrics for one dataset."""

    cwe_id: str
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    fp_rate: float | None = None
    owasp_score: float | None = None
    warning: str | None = None

    def to_dict(self) -> dict:
        return {
            "cwe_id": self.cwe_id,
            "tp": self.tp,
            "fp": self.fp,
            "tn": self.tn,
            "fn": self.fn,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "fp_rate": self.fp_rate,
            "owasp_score": self.owasp_score,
            "warning": self.warning,
        }


@dataclass
class BenchmarkScorecard:
    """Per-dataset benchmark scoring result."""

    dataset_name: str
    per_cwe: list[CWEScorecard] = field(default_factory=list)
    aggregate: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "dataset_name": self.dataset_name,
            "per_cwe": [c.to_dict() for c in self.per_cwe],
            "aggregate": self.aggregate,
        }


def _line_ranges_overlap(
    f_start: int | None,
    f_end: int | None,
    l_start: int,
    l_end: int,
) -> bool:
    """Return True when the finding's line range overlaps the label's.

    When the finding has no line numbers this always returns True
    (treat it as a file-level match).
    """
    if f_start is None or f_end is None:
        return True
    # Standard overlap: [f_start, f_end] ∩ [l_start, l_end] is non-empty
    return not (f_end < l_start or f_start > l_end)


def _safe_metrics(tp: int, fp: int, tn: int, fn: int) -> tuple[
    float | None, float | None, float | None, float | None, float | None
]:
    """Return (precision, recall, f1, fp_rate, owasp_score) or None when undefined."""
    precision: float | None = tp / (tp + fp) if (tp + fp) > 0 else None
    recall: float | None = tp / (tp + fn) if (tp + fn) > 0 else None
    if precision is not None and recall is not None and (precision + recall) > 0:
        f1: float | None = 2 * precision * recall / (precision + recall)
    else:
        f1 = None

    fp_rate: float | None = fp / (fp + tn) if (fp + tn) > 0 else None
    tpr = recall  # same thing
    if tpr is not None and fp_rate is not None:
        owasp_score: float | None = tpr - fp_rate
    else:
        owasp_score = None

    return precision, recall, f1, fp_rate, owasp_score


def compute_benchmark_scorecard(
    findings: list,
    positive_labels: list[dict],
    negative_labels: list[dict],
    dataset_name: str,
) -> BenchmarkScorecard | None:
    """Compute per-(CWE, dataset) benchmark metrics.

    Parameters
    ----------
    findings:
        List of ``Finding`` objects (or compatible dicts with ``file_path``,
        ``cwe_ids``, ``line_start``, ``line_end`` fields).
    positive_labels:
        Raw dicts from ``list_dataset_labels()``.  Each must have
        ``file_path`` and ``cwe_id``.
    negative_labels:
        Raw dicts from ``list_dataset_negative_labels()``.  Same shape.
    dataset_name:
        Dataset name used as the scorecard key.

    Returns
    -------
    BenchmarkScorecard or None
        None when *negative_labels* is empty (backward-compat path).
    """
    if not negative_labels:
        return None

    # Build per-CWE label sets: (cwe_id, file_path) → True/False (polarity)
    # positive_set and negative_set are disjoint by contract (a file cannot be
    # both expected-vulnerable and expected-clean for the same CWE).
    # Index: cwe_id → {file_path, ...}
    pos_index: dict[str, set[str]] = {}
    for lbl in positive_labels:
        cwe = lbl.get("cwe_id") or ""
        fp_ = lbl.get("file_path") or ""
        if cwe and fp_:
            pos_index.setdefault(cwe, set()).add(fp_)

    neg_index: dict[str, set[str]] = {}
    for lbl in negative_labels:
        cwe = lbl.get("cwe_id") or ""
        fp_ = lbl.get("file_path") or ""
        if cwe and fp_:
            neg_index.setdefault(cwe, set()).add(fp_)

    # Union of all CWEs that appear in either polarity
    all_cwes = set(pos_index.keys()) | set(neg_index.keys())

    # For each CWE track which positive/negative files were hit by the agent.
    # hit_pos[cwe][file_path] = True means agent reported this CWE on this positive file.
    hit_pos: dict[str, set[str]] = {cwe: set() for cwe in all_cwes}
    hit_neg: dict[str, set[str]] = {cwe: set() for cwe in all_cwes}
    # Extra FP counter: findings whose file_path is in neg_index but the CWE
    # is in all_cwes — counted as FP.
    fp_counts: dict[str, int] = {cwe: 0 for cwe in all_cwes}

    # Normalise findings to dicts for uniform access
    def _field(finding, attr: str):
        if hasattr(finding, attr):
            return getattr(finding, attr)
        if isinstance(finding, dict):
            return finding.get(attr)
        return None

    for finding in findings:
        f_file = _field(finding, "file_path") or ""
        f_cwe_ids: list[str] = _field(finding, "cwe_ids") or []
        if isinstance(f_cwe_ids, str):
            # Defensive: some serialisations store JSON string
            import json as _json
            try:
                f_cwe_ids = _json.loads(f_cwe_ids)
            except Exception:
                f_cwe_ids = [f_cwe_ids]

        f_line_start = _field(finding, "line_start")
        f_line_end = _field(finding, "line_end")

        for cwe in f_cwe_ids:
            if cwe not in all_cwes:
                continue  # out-of-scope CWE

            if f_file in (pos_index.get(cwe) or set()):
                # Check line overlap against positive labels for this file/CWE
                matched = False
                for lbl in positive_labels:
                    if lbl.get("cwe_id") != cwe or lbl.get("file_path") != f_file:
                        continue
                    l_start = lbl.get("line_start")
                    l_end = lbl.get("line_end")
                    if l_start is not None and l_end is not None:
                        if _line_ranges_overlap(f_line_start, f_line_end, l_start, l_end):
                            matched = True
                            break
                    else:
                        # Label has no line range → file-level match is sufficient
                        matched = True
                        break
                if matched:
                    hit_pos[cwe].add(f_file)
                # Else: finding on positive file but wrong line range → treat as
                # out-of-scope (not scored; doesn't help or hurt).

            elif f_file in (neg_index.get(cwe) or set()):
                # Agent reported this CWE on a file expected to be clean → FP
                if f_file not in hit_neg[cwe]:
                    hit_neg[cwe].add(f_file)
                    fp_counts[cwe] += 1

    # Build per-CWE scorecards
    per_cwe: list[CWEScorecard] = []
    for cwe in sorted(all_cwes):
        pos_files = pos_index.get(cwe, set())
        neg_files = neg_index.get(cwe, set())

        tp = len(hit_pos[cwe])
        fp = fp_counts[cwe]  # one FP per distinct negative file hit (de-duped per file)
        fn = len(pos_files) - tp
        tn = len(neg_files) - len(hit_neg[cwe])

        sc = CWEScorecard(cwe_id=cwe, tp=tp, fp=fp, tn=tn, fn=fn)

        n_pos = len(pos_files)
        n_neg = len(neg_files)
        if n_pos < _MIN_SAMPLE_SIZE or n_neg < _MIN_SAMPLE_SIZE:
            sc.warning = (
                f"insufficient sample size (n<{_MIN_SAMPLE_SIZE} per polarity)"
            )
            # Counts are still correct; only derived metrics suppressed
        else:
            prec, rec, f1, fpr, owasp = _safe_metrics(tp, fp, tn, fn)
            sc.precision = prec
            sc.recall = rec
            sc.f1 = f1
            sc.fp_rate = fpr
            sc.owasp_score = owasp

        per_cwe.append(sc)

    # Aggregate across all CWEs (always computed, regardless of sample size)
    agg_tp = sum(c.tp for c in per_cwe)
    agg_fp = sum(c.fp for c in per_cwe)
    agg_tn = sum(c.tn for c in per_cwe)
    agg_fn = sum(c.fn for c in per_cwe)
    agg_prec, agg_rec, agg_f1, agg_fpr, agg_owasp = _safe_metrics(
        agg_tp, agg_fp, agg_tn, agg_fn
    )
    aggregate: dict = {
        "tp": agg_tp,
        "fp": agg_fp,
        "tn": agg_tn,
        "fn": agg_fn,
        "precision": agg_prec,
        "recall": agg_rec,
        "f1": agg_f1,
        "fp_rate": agg_fpr,
        "owasp_score": agg_owasp,
    }

    return BenchmarkScorecard(
        dataset_name=dataset_name,
        per_cwe=per_cwe,
        aggregate=aggregate,
    )
