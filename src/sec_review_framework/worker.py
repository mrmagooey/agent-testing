"""Experiment worker — entry point for K8s Job pods."""

import argparse
import json
import os
import random
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from sec_review_framework.data.experiment import (
    BundleSnapshot,
    ExperimentRun,
    RunResult,
    RunStatus,
    StrategyName,
    ToolExtension,
    VerificationVariant,
)
from sec_review_framework.data.findings import StrategyOutput
from sec_review_framework.models.litellm_provider import LiteLLMProvider
from sec_review_framework.tools.registry import ToolRegistryFactory
from sec_review_framework.cost.calculator import CostCalculator
from sec_review_framework.evaluation.evaluator import FileLevelEvaluator
from sec_review_framework.ground_truth.models import TargetCodebase
from sec_review_framework.verification.verifier import LLMVerifier
from sec_review_framework.reporting.markdown import MarkdownReportGenerator
from sec_review_framework.strategies.single_agent import SingleAgentStrategy
from sec_review_framework.strategies.per_file import PerFileStrategy
from sec_review_framework.strategies.per_vuln_class import PerVulnClassStrategy
from sec_review_framework.strategies.sast_first import SASTFirstStrategy
from sec_review_framework.strategies.diff_review import DiffReviewStrategy
from sec_review_framework.data.strategy_bundle import OrchestrationShape


class ModelProviderFactory:
    def create(self, model_id: str, provider_kwargs: dict) -> LiteLLMProvider:
        return LiteLLMProvider(model_name=model_id, **provider_kwargs)


def get_enabled_extensions() -> frozenset[ToolExtension]:
    """Return the set of ToolExtensions enabled on this worker pod.

    Reads ``TOOL_EXT_*_AVAILABLE`` environment variables set by the
    coordinator Helm template (see config.py ``ToolExtensionAvailability``).
    """
    enabled: set[ToolExtension] = set()
    if os.environ.get("TOOL_EXT_LSP_AVAILABLE", "false").lower() == "true":
        enabled.add(ToolExtension.LSP)
    if os.environ.get("TOOL_EXT_TREE_SITTER_AVAILABLE", "false").lower() == "true":
        enabled.add(ToolExtension.TREE_SITTER)
    if os.environ.get("TOOL_EXT_DEVDOCS_AVAILABLE", "false").lower() == "true":
        enabled.add(ToolExtension.DEVDOCS)
    return frozenset(enabled)


def check_tool_extension_superset(
    run_id: str,
    strategy: "UserStrategy",  # type: ignore[name-defined]
    enabled: frozenset[ToolExtension],
) -> None:
    """Raise RuntimeError if the strategy requires extensions not enabled on this pod.

    Computes the union of ``strategy.default.tool_extensions`` and all override
    tool_extensions, then compares against *enabled*.  Missing extensions mean
    the run cannot proceed on this worker pod.

    Args:
        run_id: The run identifier for the error message.
        strategy: The UserStrategy being executed.
        enabled: The frozenset of ToolExtensions available on this pod.

    Raises:
        RuntimeError: If ``required - enabled`` is non-empty.
    """
    required: set[str] = set(strategy.default.tool_extensions)
    for rule in strategy.overrides:
        if rule.override.tool_extensions is not None:
            required |= set(rule.override.tool_extensions)

    required_exts: set[ToolExtension] = set()
    unknown: list[str] = []
    for ext_val in required:
        try:
            required_exts.add(ToolExtension(ext_val))
        except ValueError:
            unknown.append(ext_val)

    if unknown:
        raise RuntimeError(
            f"Run {run_id} references unknown tool_extension values "
            f"{sorted(unknown)} — typo in strategy bundle?"
        )

    missing = required_exts - enabled
    if missing:
        missing_names = sorted(e.value for e in missing)
        raise RuntimeError(
            f"Run {run_id} requires extensions {missing_names} "
            f"which are not enabled on this worker"
        )


_SHAPE_TO_STRATEGY = {
    OrchestrationShape.SINGLE_AGENT: SingleAgentStrategy,
    OrchestrationShape.PER_FILE: PerFileStrategy,
    OrchestrationShape.PER_VULN_CLASS: PerVulnClassStrategy,
    OrchestrationShape.SAST_FIRST: SASTFirstStrategy,
    OrchestrationShape.DIFF_REVIEW: DiffReviewStrategy,
}


class StrategyFactory:
    """Legacy factory kept for backwards-compatibility with tests that patch it.

    New code should use :func:`_dispatch_strategy` or the orchestration-shape
    dispatch map directly.
    """

    def create(self, strategy_name: StrategyName):
        mapping = {
            StrategyName.SINGLE_AGENT: SingleAgentStrategy,
            StrategyName.PER_FILE: PerFileStrategy,
            StrategyName.PER_VULN_CLASS: PerVulnClassStrategy,
            StrategyName.SAST_FIRST: SASTFirstStrategy,
            StrategyName.DIFF_REVIEW: DiffReviewStrategy,
        }
        cls = mapping.get(strategy_name)
        if cls is None:
            raise ValueError(f"Unknown strategy: {strategy_name}")
        return cls()


def _load_user_strategy(strategy_id: str, bundle_json: str = ""):
    """Reconstruct the UserStrategy for a run.

    Prefers *bundle_json* (populated by ExperimentMatrix.expand) so that
    user-created strategies persisted only in the coordinator's DB are
    visible to the worker without a DB round-trip.  Falls back to the
    builtin registry for legacy runs with an empty bundle_json.

    Raises
    ------
    KeyError
        If *strategy_id* cannot be resolved.
    """
    if bundle_json:
        from sec_review_framework.data.strategy_bundle import UserStrategy
        return UserStrategy.model_validate_json(bundle_json)

    from sec_review_framework.strategies.strategy_registry import load_default_registry
    registry = load_default_registry()
    try:
        return registry.get(strategy_id)
    except KeyError:
        raise KeyError(
            f"Strategy {strategy_id!r} not found in the default registry "
            f"and no bundle_json was embedded in the run."
        )


class ExperimentWorker:
    """
    Runs a single ExperimentRun inside a container.
    Entry point for K8s Job pods.
    """

    def _fetch_labels(
        self, run: ExperimentRun, datasets_dir: Path
    ) -> list:
        """Return ground-truth labels for the run's dataset.

        In production (HTTP transport) the labels are fetched from the
        coordinator over HTTP — workers have no direct DB access.

        The coordinator base URL is derived from ``run.upload_url`` (which
        is ``{coordinator_internal_url}/api/internal/runs/{id}/result``) or,
        failing that, from the ``COORDINATOR_INTERNAL_URL`` environment
        variable.

        If neither is available (PVC transport, offline tests) an empty list
        is returned and evaluation is skipped for this run.
        """
        from sec_review_framework.data.evaluation import GroundTruthLabel

        coordinator_url: str | None = None

        if run.upload_url:
            # Strip the run-specific suffix to get the base URL.
            # upload_url shape: {base}/api/internal/runs/{id}/result
            # We want: {base}
            import re as _re
            m = _re.match(r"(https?://[^/]+(?:/[^/]+)*?)/api/internal/runs/", run.upload_url)
            if m:
                coordinator_url = m.group(1)

        if coordinator_url is None:
            coordinator_url = os.environ.get("COORDINATOR_INTERNAL_URL", "").rstrip("/") or None

        if not coordinator_url:
            # No coordinator URL available — return empty labels list (evaluation skipped).
            return []

        import httpx as _httpx

        params: dict[str, str] = {}
        if run.dataset_version:
            params["version"] = run.dataset_version

        try:
            with _httpx.Client(timeout=30.0) as client:
                resp = client.get(
                    f"{coordinator_url}/datasets/{run.dataset_name}/labels",
                    params=params,
                )
                resp.raise_for_status()
        except _httpx.HTTPError as exc:
            raise RuntimeError(
                f"Failed to fetch labels for dataset {run.dataset_name!r}: {exc}"
            ) from exc

        raw_labels = resp.json()
        return [GroundTruthLabel.model_validate(lbl) for lbl in raw_labels]

    def run(self, run: ExperimentRun, output_dir: Path, datasets_dir: Path) -> None:
        target = TargetCodebase(datasets_dir / "targets" / run.dataset_name / "repo")
        labels = self._fetch_labels(run, datasets_dir)
        model = ModelProviderFactory().create(run.model_id, run.provider_kwargs)

        start = time.monotonic()
        findings_pre_verification = None
        verification_result = None
        tools = None

        try:
            # ------------------------------------------------------------------
            # Load the UserStrategy and check tool-extension superset
            # ------------------------------------------------------------------
            user_strategy = _load_user_strategy(run.strategy_id, run.bundle_json)
            enabled_extensions = get_enabled_extensions()
            check_tool_extension_superset(run.id, user_strategy, enabled_extensions)

            # Derive tool_variant from the strategy default bundle
            from sec_review_framework.data.experiment import ToolVariant
            # Strategy bundles list tool names; use WITH_TOOLS when tools are present
            tool_variant = (
                ToolVariant.WITH_TOOLS
                if user_strategy.default.tools
                else ToolVariant.WITHOUT_TOOLS
            )
            # Allow the legacy run.tool_variant field to override when set (non-default)
            if run.tool_variant == ToolVariant.WITHOUT_TOOLS:
                tool_variant = ToolVariant.WITHOUT_TOOLS

            # Build extension set from the strategy's default bundle
            tool_extensions_for_registry = frozenset(
                ToolExtension(e) for e in user_strategy.default.tool_extensions
            )

            tools = ToolRegistryFactory().create(
                tool_variant, target, tool_extensions=tool_extensions_for_registry
            )

            # ------------------------------------------------------------------
            # Dispatch to the concrete strategy implementation
            # ------------------------------------------------------------------
            shape = user_strategy.orchestration_shape
            strategy_cls = _SHAPE_TO_STRATEGY.get(shape)
            if strategy_cls is None:
                raise ValueError(
                    f"No strategy implementation for orchestration_shape={shape!r}"
                )
            strategy_impl = strategy_cls()

            strategy_output = strategy_impl.run(target, model, tools, user_strategy)
            candidates = strategy_output.findings

            verification_variant = VerificationVariant(user_strategy.default.verification)
            if verification_variant == VerificationVariant.WITH_VERIFICATION:
                findings_pre_verification = list(candidates)
                verifier = LLMVerifier()
                verification_result = verifier.verify(candidates, target, model, tools)
                candidates = [
                    vf.finding
                    for vf in verification_result.verified + verification_result.uncertain
                ]

            findings = candidates
            status = RunStatus.COMPLETED
            error = None
        except Exception as e:
            findings = []
            findings_pre_verification = None
            verification_result = None
            strategy_output = StrategyOutput(
                findings=[], pre_dedup_count=0, post_dedup_count=0, dedup_log=[]
            )
            user_strategy = None  # may not have been set if error was in strategy loading
            status = RunStatus.FAILED
            error = str(e)
        finally:
            if tools is not None:
                tools.close()

        duration = time.monotonic() - start

        evaluation = (
            FileLevelEvaluator().evaluate(findings, labels) if status == RunStatus.COMPLETED else None
        )

        verification_tokens = (
            verification_result.verification_tokens if verification_result else 0
        )
        total_input = sum(t.input_tokens for t in model.token_log)
        total_output = sum(t.output_tokens for t in model.token_log)
        config_dir = os.environ.get("CONFIG_DIR")
        cost_calculator = CostCalculator.from_config(
            Path(config_dir) if config_dir else None
        )
        estimated_cost = cost_calculator.compute(
            run.model_id, total_input, total_output
        )

        # Build bundle snapshot — when strategy loading failed, create a stub
        if user_strategy is not None:
            bundle_snapshot = BundleSnapshot.capture(user_strategy)
        else:
            from sec_review_framework.data.strategy_bundle import (
                OrchestrationShape,
                StrategyBundleDefault,
                UserStrategy,
            )
            from datetime import datetime as _dt
            _stub_strategy = UserStrategy(
                id=run.strategy_id,
                name="<load failed>",
                parent_strategy_id=None,
                orchestration_shape=OrchestrationShape.SINGLE_AGENT,
                default=StrategyBundleDefault(
                    system_prompt="",
                    user_prompt_template="",
                    model_id=run.model_id,
                    tools=frozenset(),
                    verification="none",
                    max_turns=0,
                    tool_extensions=frozenset(),
                ),
                overrides=[],
                created_at=_dt.now(UTC).replace(tzinfo=None),
                is_builtin=False,
            )
            bundle_snapshot = BundleSnapshot.capture(_stub_strategy)

        tool_call_count = len(tools.audit_log.entries) if tools is not None else 0

        result = RunResult(
            experiment=run,
            status=status,
            findings=findings,
            findings_pre_verification=findings_pre_verification,
            verification_result=verification_result,
            evaluation=evaluation,
            strategy_output=strategy_output,
            bundle_snapshot=bundle_snapshot,
            tool_call_count=tool_call_count,
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            verification_tokens=verification_tokens,
            estimated_cost_usd=estimated_cost,
            duration_seconds=duration,
            error=error,
            completed_at=datetime.now(UTC).replace(tzinfo=None),
        )

        # Always write artifacts first to a pod-local temp dir.
        local_dir = Path(tempfile.mkdtemp(prefix="worker-out-"))
        try:
            _result_file = local_dir / "run_result.json"
            _result_tmp = _result_file.with_suffix(".json.tmp")
            _result_tmp.write_text(result.model_dump_json(indent=2))
            _result_tmp.replace(_result_file)
            self._write_jsonl(local_dir / "findings.jsonl", findings)
            tool_audit_entries = tools.audit_log.entries if tools is not None else []
            self._write_jsonl(local_dir / "tool_calls.jsonl", tool_audit_entries)
            self._write_jsonl(local_dir / "conversation.jsonl", model.conversation_log)
            if findings_pre_verification:
                self._write_jsonl(
                    local_dir / "findings_pre_verification.jsonl",
                    findings_pre_verification,
                )
            MarkdownReportGenerator().render_run(result, local_dir)

            if run.result_transport == "http":
                self._upload_artifacts(run, local_dir)
            else:
                # PVC path: copy artifacts from temp dir to output_dir on shared PVC.
                output_dir.mkdir(parents=True, exist_ok=True)
                import shutil as _shutil
                for artifact in local_dir.iterdir():
                    _shutil.copy2(str(artifact), str(output_dir / artifact.name))
        finally:
            import shutil as _shutil2
            _shutil2.rmtree(local_dir, ignore_errors=True)

    def _upload_artifacts(self, run: ExperimentRun, local_dir: Path) -> None:
        """Stream-upload artifacts from *local_dir* to the coordinator via HTTP.

        Retry policy: 5 attempts, exponential backoff base 2s with jitter,
        max 60s per sleep. Retries on connection errors, 5xx, 429.
        Fast-exit on 403/409 (another replica won or token already consumed).
        """
        import httpx

        if not run.upload_url or not run.upload_token:
            raise RuntimeError(
                f"Run {run.id} has result_transport='http' but missing upload_url or upload_token"
            )

        _ARTIFACT_NAMES = [
            "run_result.json",
            "findings.jsonl",
            "tool_calls.jsonl",
            "conversation.jsonl",
            "findings_pre_verification.jsonl",
            "report.md",
        ]

        max_attempts = 5
        base_delay = 2.0
        max_delay = 60.0

        for attempt in range(max_attempts):
            try:
                files = []
                file_handles = []
                try:
                    for name in _ARTIFACT_NAMES:
                        p = local_dir / name
                        if p.exists():
                            fh = open(p, "rb")
                            file_handles.append(fh)
                            files.append(("file", (name, fh, "application/octet-stream")))

                    with httpx.Client(timeout=300.0) as client:
                        resp = client.post(
                            run.upload_url,
                            files=files,
                            headers={"Authorization": f"Bearer {run.upload_token}"},
                        )
                finally:
                    for fh in file_handles:
                        fh.close()

                if resp.status_code in (403, 409):
                    # 403: token invalid/consumed — another replica completed this run.
                    # 409: result already committed — safe to exit 0.
                    return

                if resp.status_code < 300:
                    return

                # 5xx or 429: retry
                if resp.status_code in (429,) or resp.status_code >= 500:
                    pass  # fall through to retry
                else:
                    # Non-retriable client error
                    raise RuntimeError(
                        f"Upload failed with status {resp.status_code}: {resp.text}"
                    )

            except httpx.TransportError:
                pass  # Connection error — retry

            if attempt < max_attempts - 1:
                delay = min(base_delay * (2 ** attempt) + random.uniform(0, 1), max_delay)
                time.sleep(delay)
            else:
                raise RuntimeError(
                    f"Upload for run {run.id} failed after {max_attempts} attempts"
                )

    def _write_jsonl(self, path: Path, items: list) -> None:
        with open(path, "w") as f:
            for item in items:
                if hasattr(item, "model_dump_json"):
                    f.write(item.model_dump_json() + "\n")
                else:
                    f.write(json.dumps(item) + "\n")


def main() -> None:
    """Entry point for worker container. Args passed by K8s Job command spec."""
    parser = argparse.ArgumentParser(description="Security review experiment worker")
    parser.add_argument("--run-config", required=True, help="Path to run config JSON")
    parser.add_argument("--output-dir", required=True, help="Output directory path")
    parser.add_argument("--datasets-dir", required=True, help="Datasets directory path")
    args = parser.parse_args()

    run = ExperimentRun.model_validate_json(Path(args.run_config).read_text())
    worker = ExperimentWorker()
    try:
        worker.run(run, Path(args.output_dir), Path(args.datasets_dir))
    except RuntimeError as exc:
        # Terminal upload failure — exit non-zero so K8s Job backoffLimit can retry.
        print(f"Worker failed: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
