"""Statistical analysis — Wilson confidence intervals and McNemar's test."""

import math

from scipy.stats import chi2

from sec_review_framework.data.evaluation import ConfidenceInterval, EvaluationResult, GroundTruthLabel


def wilson_ci(successes: int, n: int, alpha: float = 0.05) -> ConfidenceInterval:
    """
    Wilson score interval — handles p near 0 or 1 better than normal approx.

    Parameters
    ----------
    successes:
        Number of successes (e.g. true positives).
    n:
        Total trials (e.g. true positives + false positives for precision).
    alpha:
        Significance level; default 0.05 gives a 95% CI.

    Returns
    -------
    ConfidenceInterval
    """
    if n == 0:
        return ConfidenceInterval(point_estimate=0.0, lower=0.0, upper=0.0, alpha=alpha)
    z = 1.96  # z_{0.975} for alpha=0.05
    p_hat = successes / n
    center = (p_hat + z**2 / (2 * n)) / (1 + z**2 / n)
    margin = (z / (1 + z**2 / n)) * math.sqrt(
        p_hat * (1 - p_hat) / n + z**2 / (4 * n**2)
    )
    return ConfidenceInterval(
        point_estimate=p_hat,
        lower=max(0.0, center - margin),
        upper=min(1.0, center + margin),
        alpha=alpha,
    )


class StatisticalAnalyzer:
    """Computes confidence intervals and pairwise significance tests."""

    def __init__(self, alpha: float = 0.05) -> None:
        self.alpha = alpha

    def precision_ci(self, results: list[EvaluationResult]) -> ConfidenceInterval:
        """Wilson CI on precision across repeated runs."""
        tp_total = sum(r.true_positives for r in results)
        fp_total = sum(r.false_positives for r in results)
        return wilson_ci(tp_total, tp_total + fp_total, self.alpha)

    def recall_ci(self, results: list[EvaluationResult]) -> ConfidenceInterval:
        """Wilson CI on recall across repeated runs."""
        tp_total = sum(r.true_positives for r in results)
        fn_total = sum(r.false_negatives for r in results)
        return wilson_ci(tp_total, tp_total + fn_total, self.alpha)

    def mcnemar_test(
        self,
        results_a: list[EvaluationResult],
        results_b: list[EvaluationResult],
        labels: list[GroundTruthLabel],
    ) -> dict:
        """
        Pairwise McNemar's test: does model A detect vulns that model B misses,
        and vice versa? Requires both to be evaluated against the same labels.

        Parameters
        ----------
        results_a:
            EvaluationResult list from model/config A.
        results_b:
            EvaluationResult list from model/config B.
        labels:
            The shared set of GroundTruthLabels both were evaluated against.

        Returns
        -------
        dict with keys: statistic, p_value, significant, a_only, b_only,
        interpretation.
        """
        a_found = self._label_detection_set(results_a, labels)
        b_found = self._label_detection_set(results_b, labels)

        # Discordant pairs
        b_count = sum(
            1 for label in labels if label.id in a_found and label.id not in b_found
        )
        c_count = sum(
            1 for label in labels if label.id not in a_found and label.id in b_found
        )
        n = b_count + c_count
        if n == 0:
            return {
                "statistic": 0.0,
                "p_value": 1.0,
                "significant": False,
                "a_only": 0,
                "b_only": 0,
                "interpretation": "No discordant pairs — models perform identically",
            }

        chi2_stat = (abs(b_count - c_count) - 1) ** 2 / n
        p_value = 1 - chi2.cdf(chi2_stat, df=1)
        winner = "A" if b_count > c_count else "B"
        return {
            "statistic": chi2_stat,
            "p_value": p_value,
            "significant": p_value < self.alpha,
            "a_only": b_count,
            "b_only": c_count,
            "interpretation": f"{winner} detects more (p={p_value:.4f})",
        }

    def _label_detection_set(
        self,
        results: list[EvaluationResult],
        labels: list[GroundTruthLabel],
    ) -> set[str]:
        """
        Return the set of label IDs that were detected as TPs in any of the
        given results.
        """
        from sec_review_framework.data.evaluation import MatchStatus

        detected: set[str] = set()
        for result in results:
            for mf in result.matched_findings:
                if (
                    mf.match_status == MatchStatus.TRUE_POSITIVE
                    and mf.matched_label is not None
                ):
                    detected.add(mf.matched_label.id)
        return detected
