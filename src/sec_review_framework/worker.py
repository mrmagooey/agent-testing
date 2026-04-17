"""Experiment worker — entry point for K8s Job pods."""

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path

from sec_review_framework.data.experiment import (
    ExperimentRun,
    PromptSnapshot,
    RunResult,
    RunStatus,
    StrategyName,
    VerificationVariant,
)
from sec_review_framework.data.findings import StrategyOutput
from sec_review_framework.models.litellm_provider import LiteLLMProvider
from sec_review_framework.profiles.review_profiles import ProfileRegistry
from sec_review_framework.tools.registry import ToolRegistryFactory
from sec_review_framework.cost.calculator import CostCalculator
from sec_review_framework.evaluation.evaluator import FileLevelEvaluator
from sec_review_framework.ground_truth.models import LabelStore, TargetCodebase
from sec_review_framework.verification.verifier import LLMVerifier
from sec_review_framework.reporting.markdown import MarkdownReportGenerator
from sec_review_framework.strategies.single_agent import SingleAgentStrategy
from sec_review_framework.strategies.per_file import PerFileStrategy
from sec_review_framework.strategies.per_vuln_class import PerVulnClassStrategy
from sec_review_framework.strategies.sast_first import SASTFirstStrategy
from sec_review_framework.strategies.diff_review import DiffReviewStrategy


class ModelProviderFactory:
    def create(self, model_id: str, model_config: dict) -> LiteLLMProvider:
        return LiteLLMProvider(model_name=model_id, **model_config)


class StrategyFactory:
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


class ExperimentWorker:
    """
    Runs a single ExperimentRun inside a container.
    Entry point for K8s Job pods.
    """

    def run(self, run: ExperimentRun, output_dir: Path, datasets_dir: Path) -> None:
        target = TargetCodebase(datasets_dir / "targets" / run.dataset_name / "repo")
        labels = LabelStore(datasets_dir).load(run.dataset_name, run.dataset_version)
        model = ModelProviderFactory().create(run.model_id, run.model_config)
        strategy = StrategyFactory().create(run.strategy)
        tools = ToolRegistryFactory().create(run.tool_variant, target)
        profile = ProfileRegistry().get(run.review_profile)

        config = {**run.strategy_config, "review_profile": profile, "parallel": run.parallel}

        start = time.monotonic()
        findings_pre_verification = None
        verification_result = None

        try:
            strategy_output = strategy.run(target, model, tools, config)
            candidates = strategy_output.findings

            if run.verification_variant == VerificationVariant.WITH_VERIFICATION:
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
            status = RunStatus.FAILED
            error = str(e)

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

        # Prompt snapshot is captured by strategies; use a placeholder here if not attached.
        prompt_snapshot = PromptSnapshot.capture(
            system_prompt="",
            user_message_template="",
            finding_output_format="",
        )

        result = RunResult(
            experiment=run,
            status=status,
            findings=findings,
            findings_pre_verification=findings_pre_verification,
            verification_result=verification_result,
            evaluation=evaluation,
            strategy_output=strategy_output,
            prompt_snapshot=prompt_snapshot,
            tool_call_count=len(tools.audit_log.entries),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            verification_tokens=verification_tokens,
            estimated_cost_usd=estimated_cost,
            duration_seconds=duration,
            error=error,
            completed_at=datetime.utcnow(),
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "run_result.json").write_text(result.model_dump_json(indent=2))
        self._write_jsonl(output_dir / "findings.jsonl", findings)
        self._write_jsonl(output_dir / "tool_calls.jsonl", tools.audit_log.entries)
        self._write_jsonl(output_dir / "conversation.jsonl", model.conversation_log)
        if findings_pre_verification:
            self._write_jsonl(
                output_dir / "findings_pre_verification.jsonl",
                findings_pre_verification,
            )
        MarkdownReportGenerator().render_run(result, output_dir)

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
    worker.run(run, Path(args.output_dir), Path(args.datasets_dir))


if __name__ == "__main__":
    main()
