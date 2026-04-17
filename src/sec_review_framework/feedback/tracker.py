"""Feedback tracker for cross-batch comparison and false positive pattern detection."""

from dataclasses import dataclass, field

from sec_review_framework.data.findings import FindingIdentity, VulnClass


@dataclass
class FalsePositivePattern:
    model_id: str
    vuln_class: VulnClass
    pattern_description: str
    occurrence_count: int
    example_finding_ids: list[str]
    suggested_action: str


@dataclass
class BatchComparison:
    batch_a_id: str
    batch_b_id: str
    metric_deltas: dict[str, dict]          # experiment_key → {precision, recall, f1 deltas}
    persistent_false_positives: list[FalsePositivePattern]
    persistent_misses: list[dict]
    improvements: list[dict]
    regressions: list[dict]
    finding_stability: dict[str, float]     # FindingIdentity str → fraction of runs it appeared in


def _experiment_key(result) -> tuple:
    """Stable key for matching runs across batches."""
    exp = result.experiment
    ext_tuple = tuple(sorted(e.value for e in (exp.tool_extensions or frozenset())))
    return (exp.model_id, exp.strategy.value, exp.tool_variant.value, ext_tuple)


def _finding_identities(result) -> set[str]:
    """Set of FindingIdentity strings for all findings in a result."""
    ids = set()
    for f in (result.findings or []):
        try:
            ids.add(str(FindingIdentity.from_finding(f)))
        except Exception:
            pass
    return ids


class FeedbackTracker:
    """
    Compares results across batches to surface patterns in model behavior.
    Produces a structured comparison that the team reviews to refine
    prompts, profiles, and strategies.
    """

    def compare_batches(
        self,
        batch_a_results: list,
        batch_b_results: list,
    ) -> BatchComparison:
        batch_a_id = batch_a_results[0].experiment.batch_id if batch_a_results else "batch_a"
        batch_b_id = batch_b_results[0].experiment.batch_id if batch_b_results else "batch_b"

        # Index by experiment key
        a_by_key: dict[tuple, list] = {}
        for r in batch_a_results:
            a_by_key.setdefault(_experiment_key(r), []).append(r)

        b_by_key: dict[tuple, list] = {}
        for r in batch_b_results:
            b_by_key.setdefault(_experiment_key(r), []).append(r)

        metric_deltas: dict[str, dict] = {}
        improvements: list[dict] = []
        regressions: list[dict] = []
        persistent_misses: list[dict] = []

        # FP tracking across batches: {identity_str: {model_id: count_in_a, count_in_b}}
        fp_in_a: dict[str, set[str]] = {}  # identity → set of model_ids
        fp_in_b: dict[str, set[str]] = {}

        shared_keys = set(a_by_key.keys()) & set(b_by_key.keys())
        for key in shared_keys:
            model_id = key[0]
            ext_part = ("+".join(key[3]) if key[3] else "none")
            exp_label = f"{key[0]}/{key[1]}/{key[2]}/ext:{ext_part}"

            # Use first result for each batch (repetition 0 or just the first)
            r_a = a_by_key[key][0]
            r_b = b_by_key[key][0]

            eval_a = r_a.evaluation
            eval_b = r_b.evaluation

            if eval_a is not None and eval_b is not None:
                delta_precision = (eval_b.precision or 0.0) - (eval_a.precision or 0.0)
                delta_recall = (eval_b.recall or 0.0) - (eval_a.recall or 0.0)
                delta_f1 = (eval_b.f1 or 0.0) - (eval_a.f1 or 0.0)
                metric_deltas[exp_label] = {
                    "precision": round(delta_precision, 4),
                    "recall": round(delta_recall, 4),
                    "f1": round(delta_f1, 4),
                }
                if delta_f1 > 0:
                    improvements.append({"experiment": exp_label, "f1_delta": round(delta_f1, 4)})
                elif delta_f1 < 0:
                    regressions.append({"experiment": exp_label, "f1_delta": round(delta_f1, 4)})

            # Track findings present in A but absent in B (potential regressions)
            ids_a = _finding_identities(r_a)
            ids_b = _finding_identities(r_b)

            for identity in ids_a - ids_b:
                if identity not in fp_in_a:
                    fp_in_a[identity] = set()
                fp_in_a[identity].add(model_id)

            for identity in ids_b - ids_a:
                if identity not in fp_in_b:
                    fp_in_b[identity] = set()
                fp_in_b[identity].add(model_id)

        # Persistent misses: labels missed in both batches
        a_all_ids = set()
        for r in batch_a_results:
            a_all_ids |= _finding_identities(r)
        b_all_ids = set()
        for r in batch_b_results:
            b_all_ids |= _finding_identities(r)

        # Build persistent false positives from patterns seen in both batches
        persistent_fp_identities = set(fp_in_a.keys()) & set(fp_in_b.keys())
        # Group by (model_id, vuln_class) for pattern aggregation
        pattern_groups: dict[tuple, list[str]] = {}
        for identity_str in persistent_fp_identities:
            # Parse vuln_class from identity string: "file_path:vuln_class:L0-9"
            parts = identity_str.split(":")
            vuln_class_str = parts[1] if len(parts) >= 2 else "other"
            for model_id in fp_in_a.get(identity_str, set()) & fp_in_b.get(identity_str, set()):
                group_key = (model_id, vuln_class_str)
                pattern_groups.setdefault(group_key, []).append(identity_str)

        persistent_false_positives: list[FalsePositivePattern] = []
        for (model_id, vuln_class_str), identities in pattern_groups.items():
            try:
                vuln_class = VulnClass(vuln_class_str)
            except ValueError:
                vuln_class = VulnClass.OTHER
            persistent_false_positives.append(FalsePositivePattern(
                model_id=model_id,
                vuln_class=vuln_class,
                pattern_description=f"{model_id} repeatedly produces {vuln_class_str} findings across batches",
                occurrence_count=len(identities),
                example_finding_ids=identities[:3],
                suggested_action=f"Add {vuln_class_str} exemption rules for {model_id} or refine its system prompt",
            ))

        # Finding stability in batch_b: fraction of b runs each identity appears in
        b_total = len(batch_b_results) or 1
        identity_counts: dict[str, int] = {}
        for r in batch_b_results:
            for identity in _finding_identities(r):
                identity_counts[identity] = identity_counts.get(identity, 0) + 1
        finding_stability = {k: round(v / b_total, 4) for k, v in identity_counts.items()}

        return BatchComparison(
            batch_a_id=batch_a_id,
            batch_b_id=batch_b_id,
            metric_deltas=metric_deltas,
            persistent_false_positives=persistent_false_positives,
            persistent_misses=persistent_misses,
            improvements=improvements,
            regressions=regressions,
            finding_stability=finding_stability,
        )

    def extract_fp_patterns(self, results: list) -> list[FalsePositivePattern]:
        """
        Across all runs in a batch, find recurring false positive patterns.
        Returns patterns where the same (model_id, vuln_class, FindingIdentity)
        appears in multiple runs.
        """
        # group_key → list of (identity_str, finding_id)
        groups: dict[tuple, list[tuple[str, str]]] = {}

        for result in results:
            model_id = result.experiment.model_id
            for finding in (result.findings or []):
                try:
                    identity = FindingIdentity.from_finding(finding)
                    vuln_class = finding.vuln_class
                    group_key = (model_id, vuln_class)
                    groups.setdefault(group_key, []).append((str(identity), finding.id))
                except Exception:
                    pass

        patterns: list[FalsePositivePattern] = []
        seen_identities: dict[tuple, set[str]] = {}

        for (model_id, vuln_class), entries in groups.items():
            # Count unique identities (not just raw occurrences)
            identity_to_finding_ids: dict[str, list[str]] = {}
            for identity_str, finding_id in entries:
                identity_to_finding_ids.setdefault(identity_str, []).append(finding_id)

            # Patterns: identities that appear more than once across runs
            recurring = {k: v for k, v in identity_to_finding_ids.items() if len(v) >= 2}
            if not recurring:
                continue

            total_occurrences = sum(len(v) for v in recurring.values())
            example_ids = []
            for ids in list(recurring.values())[:3]:
                example_ids.extend(ids[:1])

            patterns.append(FalsePositivePattern(
                model_id=model_id,
                vuln_class=vuln_class,
                pattern_description=f"{model_id} repeatedly flags {vuln_class.value} findings",
                occurrence_count=total_occurrences,
                example_finding_ids=example_ids[:3],
                suggested_action=(
                    f"Review {vuln_class.value} detection in {model_id} — "
                    f"appears {total_occurrences} times across runs"
                ),
            ))

        return patterns
