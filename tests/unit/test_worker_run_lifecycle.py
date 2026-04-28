"""Unit tests for ExperimentWorker.run() lifecycle and _upload_artifacts().

Covers:
- run() happy path: strategy loaded, executed, evaluated, artifacts written via PVC
- run() strategy-load failure → stub-strategy BundleSnapshot fallback
- run() with WITH_VERIFICATION: verifier invoked, findings filtered to verified+uncertain
- _upload_artifacts(): first-attempt success
- _upload_artifacts(): success on a later attempt (retry after 5xx)
- _upload_artifacts(): exhausts all 5 attempts → RuntimeError
- _upload_artifacts(): 403 fast-exit (no further retries)
- _upload_artifacts(): 409 fast-exit (result already committed)
- _upload_artifacts(): TransportError → retry
- _upload_artifacts(): missing upload_url/upload_token → RuntimeError (no HTTP)
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from sec_review_framework.data.evaluation import (
    EvaluationResult,
    VerificationOutcome,
    VerificationResult,
    VerifiedFinding,
)
from sec_review_framework.data.experiment import (
    ExperimentRun,
    RunStatus,
    VerificationVariant,
)
from sec_review_framework.data.findings import (
    Finding,
    Severity,
    StrategyOutput,
    VulnClass,
)
from sec_review_framework.tools.registry import ToolCallAuditLog, ToolRegistry
from sec_review_framework.worker import ExperimentWorker

# ---------------------------------------------------------------------------
# Inject a fake sec_review_framework.strategies.runner into sys.modules so that
# worker.py's lazy `from sec_review_framework.strategies.runner import run_strategy`
# resolves without needing the pydantic-ai "agent" extra installed.
# This must happen before any call that exercises worker.run().
# ---------------------------------------------------------------------------


def _fake_runner_ctx(strategy_output: StrategyOutput):
    """Context manager that installs a fake runner returning *strategy_output*."""
    import contextlib

    @contextlib.contextmanager
    def _ctx():
        fake_mod = types.ModuleType("sec_review_framework.strategies.runner")
        fake_mod.run_strategy = MagicMock(return_value=strategy_output)  # type: ignore[attr-defined]
        prev = sys.modules.get("sec_review_framework.strategies.runner")
        sys.modules["sec_review_framework.strategies.runner"] = fake_mod
        try:
            yield fake_mod
        finally:
            if prev is None:
                sys.modules.pop("sec_review_framework.strategies.runner", None)
            else:
                sys.modules["sec_review_framework.strategies.runner"] = prev

    return _ctx()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_run(
    *,
    result_transport: str = "pvc",
    upload_url: str | None = None,
    upload_token: str | None = None,
    verification_variant: VerificationVariant = VerificationVariant.NONE,
) -> ExperimentRun:
    return ExperimentRun(
        id="run-test-001",
        experiment_id="exp-test",
        strategy_id="builtin.single_agent",
        dataset_name="test-dataset",
        dataset_version="1.0.0",
        model_id="fake-model",
        result_transport=result_transport,  # type: ignore[arg-type]
        upload_url=upload_url,
        upload_token=upload_token,
        verification_variant=verification_variant,
    )


def _make_finding() -> Finding:
    return Finding(
        id="f-001",
        file_path="src/auth.py",
        line_start=10,
        line_end=12,
        vuln_class=VulnClass.SQLI,
        cwe_ids=["CWE-89"],
        severity=Severity.HIGH,
        title="SQL Injection",
        description="Raw SQL built from user input",
        confidence=0.9,
        raw_llm_output="<raw>",
        produced_by="single_agent",
        experiment_id="exp-test",
    )


def _make_strategy(verification: str = "none"):
    """Return a minimal UserStrategy with controllable verification setting."""
    from sec_review_framework.data.strategy_bundle import (
        OrchestrationShape,
        StrategyBundleDefault,
        UserStrategy,
    )
    return UserStrategy(
        id="builtin.single_agent",
        name="Single Agent",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="sys",
            user_prompt_template="user",
            model_id="fake-model",
            tools=frozenset(["read_file"]),
            verification=verification,
            max_turns=10,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        is_builtin=True,
    )


def _make_mock_tools() -> MagicMock:
    """Return a mock ToolRegistry-like object with an empty audit_log."""
    mock = MagicMock(spec=ToolRegistry)
    mock.audit_log = ToolCallAuditLog()
    return mock


def _make_strategy_output(findings: list[Finding] | None = None) -> StrategyOutput:
    findings = findings or []
    return StrategyOutput(
        findings=findings,
        pre_dedup_count=len(findings),
        post_dedup_count=len(findings),
        dedup_log=[],
        child_token_log=[],
        child_conversation_log=[],
    )


def _make_mock_model() -> MagicMock:
    """Return a mock model provider with empty token/conversation logs."""
    m = MagicMock()
    m.token_log = []
    m.conversation_log = []
    return m


def _run_worker(worker, run, output_dir, datasets_dir, *, strategy, strategy_output,
                mock_model=None, mock_tools=None):
    """Drive worker.run() with all external I/O mocked out.

    Installs the fake runner module, patches worker-level factories, and calls
    worker.run().
    """
    import contextlib

    mock_model = mock_model or _make_mock_model()
    mock_tools = mock_tools or _make_mock_tools()

    with _fake_runner_ctx(strategy_output):
        stack = contextlib.ExitStack()
        with stack:
            stack.enter_context(
                patch("sec_review_framework.worker._load_user_strategy", return_value=strategy)
            )
            stack.enter_context(
                patch("sec_review_framework.worker.get_enabled_extensions", return_value=frozenset())
            )
            stack.enter_context(patch("sec_review_framework.worker.check_tool_extension_superset"))
            mock_mpf = stack.enter_context(
                patch("sec_review_framework.worker.ModelProviderFactory")
            )
            mock_trf = stack.enter_context(
                patch("sec_review_framework.worker.ToolRegistryFactory")
            )
            mock_eval = stack.enter_context(
                patch("sec_review_framework.worker.FileLevelEvaluator")
            )
            mock_cc = stack.enter_context(
                patch("sec_review_framework.worker.CostCalculator")
            )
            mock_md = stack.enter_context(
                patch("sec_review_framework.worker.MarkdownReportGenerator")
            )

            mock_mpf.return_value.create.return_value = mock_model
            mock_trf.return_value.create.return_value = mock_tools
            mock_eval.return_value.evaluate.return_value = MagicMock(spec=EvaluationResult)
            mock_cc.from_config.return_value.compute.return_value = 0.0
            mock_md.return_value.render_run.return_value = None

            worker.run(run, output_dir, datasets_dir)

    return mock_tools


# ---------------------------------------------------------------------------
# run() lifecycle tests
# ---------------------------------------------------------------------------


class TestRunLifecycleHappyPath:
    """run() orchestrates strategy execution, evaluation, and PVC artifact write."""

    def test_run_happy_path_produces_completed_status(self, tmp_path: Path):
        """A successful run writes run_result.json with COMPLETED status."""
        run = _make_run()
        worker = ExperimentWorker()
        finding = _make_finding()
        strategy = _make_strategy()
        strategy_output = _make_strategy_output([finding])
        datasets_dir = tmp_path / "datasets"
        (datasets_dir / "targets" / "test-dataset" / "repo").mkdir(parents=True)
        output_dir = tmp_path / "output"

        _run_worker(worker, run, output_dir, datasets_dir,
                    strategy=strategy, strategy_output=strategy_output)

        result_path = output_dir / "run_result.json"
        assert result_path.exists(), "run_result.json must be written to output_dir"
        result_json = json.loads(result_path.read_text())
        assert result_json["status"] == RunStatus.COMPLETED.value
        assert result_json["error"] is None

    def test_run_happy_path_writes_findings_jsonl(self, tmp_path: Path):
        """Findings are serialised to findings.jsonl."""
        run = _make_run()
        worker = ExperimentWorker()
        finding = _make_finding()
        strategy = _make_strategy()
        strategy_output = _make_strategy_output([finding])
        datasets_dir = tmp_path / "datasets"
        (datasets_dir / "targets" / "test-dataset" / "repo").mkdir(parents=True)
        output_dir = tmp_path / "output"

        _run_worker(worker, run, output_dir, datasets_dir,
                    strategy=strategy, strategy_output=strategy_output)

        findings_path = output_dir / "findings.jsonl"
        assert findings_path.exists()
        lines = [ln for ln in findings_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1
        assert json.loads(lines[0])["id"] == finding.id

    def test_run_closes_tools_registry_on_success(self, tmp_path: Path):
        """tools.close() is called in the finally block even on the happy path."""
        run = _make_run()
        worker = ExperimentWorker()
        mock_tools = _make_mock_tools()
        datasets_dir = tmp_path / "datasets"
        (datasets_dir / "targets" / "test-dataset" / "repo").mkdir(parents=True)

        _run_worker(worker, run, tmp_path / "output", datasets_dir,
                    strategy=_make_strategy(),
                    strategy_output=_make_strategy_output(),
                    mock_tools=mock_tools)

        mock_tools.close.assert_called_once()


# ---------------------------------------------------------------------------
# run() strategy-load failure → stub-strategy fallback
# ---------------------------------------------------------------------------


class TestRunStrategyLoadFailure:
    """When _load_user_strategy raises, run() writes FAILED status and a stub BundleSnapshot."""

    def _run_with_load_failure(self, worker, run, output_dir, datasets_dir) -> dict:
        with (
            patch(
                "sec_review_framework.worker._load_user_strategy",
                side_effect=KeyError("Strategy not found"),
            ),
            patch("sec_review_framework.worker.ModelProviderFactory") as mock_mpf,
            patch("sec_review_framework.worker.FileLevelEvaluator"),
            patch("sec_review_framework.worker.CostCalculator") as mock_cc,
            patch("sec_review_framework.worker.MarkdownReportGenerator") as mock_md,
        ):
            mock_mpf.return_value.create.return_value = _make_mock_model()
            mock_cc.from_config.return_value.compute.return_value = 0.0
            mock_md.return_value.render_run.return_value = None
            worker.run(run, output_dir, datasets_dir)

        return json.loads((output_dir / "run_result.json").read_text())

    def test_strategy_load_failure_yields_failed_status(self, tmp_path: Path):
        """KeyError in _load_user_strategy produces a FAILED run_result.json."""
        run = _make_run()
        datasets_dir = tmp_path / "datasets"
        (datasets_dir / "targets" / "test-dataset" / "repo").mkdir(parents=True)
        output_dir = tmp_path / "output"

        result = self._run_with_load_failure(ExperimentWorker(), run, output_dir, datasets_dir)

        assert result["status"] == RunStatus.FAILED.value
        assert result["error"] is not None

    def test_strategy_load_failure_creates_stub_bundle_snapshot(self, tmp_path: Path):
        """Stub BundleSnapshot carries '<load failed>' as the strategy name."""
        run = _make_run()
        datasets_dir = tmp_path / "datasets"
        (datasets_dir / "targets" / "test-dataset" / "repo").mkdir(parents=True)
        output_dir = tmp_path / "output"

        result = self._run_with_load_failure(ExperimentWorker(), run, output_dir, datasets_dir)

        bundle = result["bundle_snapshot"]
        # Stub strategy encodes "<load failed>" in the serialised bundle JSON
        assert "<load failed>" in bundle["bundle_json"]

    def test_strategy_load_failure_does_not_crash_on_missing_tools(self, tmp_path: Path):
        """tools is None when strategy load fails; the `if tools is not None` guard must hold."""
        run = _make_run()
        datasets_dir = tmp_path / "datasets"
        (datasets_dir / "targets" / "test-dataset" / "repo").mkdir(parents=True)

        # Must complete without raising — the finally block must handle tools=None
        self._run_with_load_failure(ExperimentWorker(), run, tmp_path / "output", datasets_dir)


# ---------------------------------------------------------------------------
# run() verification flow
# ---------------------------------------------------------------------------


class TestRunVerificationFlow:
    """run() invokes the verifier when strategy specifies WITH_VERIFICATION."""

    def _run_with_verification(
        self, worker, run, output_dir, datasets_dir, strategy, strategy_output,
        verification_result,
    ) -> dict:
        """Drive worker.run() with verification enabled and a mock LLMVerifier."""
        with _fake_runner_ctx(strategy_output):
            with (
                patch("sec_review_framework.worker._load_user_strategy", return_value=strategy),
                patch("sec_review_framework.worker.get_enabled_extensions", return_value=frozenset()),
                patch("sec_review_framework.worker.check_tool_extension_superset"),
                patch("sec_review_framework.worker.ModelProviderFactory") as mock_mpf,
                patch("sec_review_framework.worker.ToolRegistryFactory") as mock_trf,
                patch("sec_review_framework.worker.FileLevelEvaluator") as mock_eval,
                patch("sec_review_framework.worker.CostCalculator") as mock_cc,
                patch("sec_review_framework.worker.MarkdownReportGenerator") as mock_md,
                patch("sec_review_framework.worker.LLMVerifier") as mock_verifier_cls,
            ):
                mock_mpf.return_value.create.return_value = _make_mock_model()
                mock_trf.return_value.create.return_value = _make_mock_tools()
                mock_eval.return_value.evaluate.return_value = MagicMock(spec=EvaluationResult)
                mock_cc.from_config.return_value.compute.return_value = 0.0
                mock_md.return_value.render_run.return_value = None
                mock_verifier_cls.return_value.verify.return_value = verification_result
                worker.run(run, output_dir, datasets_dir)

        return json.loads((output_dir / "run_result.json").read_text())

    def test_verification_excludes_rejected_findings(self, tmp_path: Path):
        """WITH_VERIFICATION: rejected findings must not appear in the final result."""
        run = _make_run(verification_variant=VerificationVariant.WITH_VERIFICATION)
        finding_good = _make_finding()
        finding_bad = Finding(
            id="f-rejected",
            file_path="src/other.py",
            line_start=5,
            line_end=6,
            vuln_class=VulnClass.XSS,
            cwe_ids=["CWE-79"],
            severity=Severity.MEDIUM,
            title="Possible XSS",
            description="Might be XSS.",
            confidence=0.3,
            raw_llm_output="<raw>",
            produced_by="single_agent",
            experiment_id="exp-test",
        )
        strategy = _make_strategy(verification="with_verification")
        strategy_output = _make_strategy_output([finding_good, finding_bad])

        verification_result = VerificationResult(
            verified=[VerifiedFinding(
                finding=finding_good,
                outcome=VerificationOutcome.VERIFIED,
                evidence="Trace confirms.",
                cited_lines=[],
            )],
            rejected=[VerifiedFinding(
                finding=finding_bad,
                outcome=VerificationOutcome.REJECTED,
                evidence="No vuln here.",
                cited_lines=[],
            )],
            uncertain=[],
            total_candidates=2,
            verification_tokens=50,
        )

        datasets_dir = tmp_path / "datasets"
        (datasets_dir / "targets" / "test-dataset" / "repo").mkdir(parents=True)
        output_dir = tmp_path / "output"

        result_json = self._run_with_verification(
            ExperimentWorker(), run, output_dir, datasets_dir,
            strategy, strategy_output, verification_result,
        )

        final_ids = [f["id"] for f in result_json["findings"]]
        assert "f-001" in final_ids, "Verified finding must be retained"
        assert "f-rejected" not in final_ids, "Rejected finding must be excluded"

    def test_verification_retains_uncertain_findings(self, tmp_path: Path):
        """WITH_VERIFICATION: verified + uncertain are kept, rejected are dropped."""
        run = _make_run(verification_variant=VerificationVariant.WITH_VERIFICATION)
        finding_a = _make_finding()
        finding_b = Finding(
            id="f-uncertain",
            file_path="src/utils.py",
            line_start=20,
            line_end=22,
            vuln_class=VulnClass.XSS,
            cwe_ids=["CWE-79"],
            severity=Severity.MEDIUM,
            title="Possible XSS",
            description="Might be XSS.",
            confidence=0.5,
            raw_llm_output="<raw>",
            produced_by="single_agent",
            experiment_id="exp-test",
        )
        finding_c = Finding(
            id="f-rejected-2",
            file_path="src/models.py",
            line_start=1,
            line_end=2,
            vuln_class=VulnClass.AUTH_BYPASS,
            cwe_ids=[],
            severity=Severity.LOW,
            title="Maybe auth bypass",
            description="Probably fine.",
            confidence=0.1,
            raw_llm_output="<raw>",
            produced_by="single_agent",
            experiment_id="exp-test",
        )
        strategy = _make_strategy(verification="with_verification")
        strategy_output = _make_strategy_output([finding_a, finding_b, finding_c])

        verification_result = VerificationResult(
            verified=[VerifiedFinding(
                finding=finding_a,
                outcome=VerificationOutcome.VERIFIED,
                evidence="confirmed",
                cited_lines=[],
            )],
            rejected=[VerifiedFinding(
                finding=finding_c,
                outcome=VerificationOutcome.REJECTED,
                evidence="false positive",
                cited_lines=[],
            )],
            uncertain=[VerifiedFinding(
                finding=finding_b,
                outcome=VerificationOutcome.UNCERTAIN,
                evidence="unclear",
                cited_lines=[],
            )],
            total_candidates=3,
            verification_tokens=100,
        )

        datasets_dir = tmp_path / "datasets"
        (datasets_dir / "targets" / "test-dataset" / "repo").mkdir(parents=True)
        output_dir = tmp_path / "output"

        result_json = self._run_with_verification(
            ExperimentWorker(), run, output_dir, datasets_dir,
            strategy, strategy_output, verification_result,
        )

        final_ids = {f["id"] for f in result_json["findings"]}
        assert "f-001" in final_ids, "Verified finding must be retained"
        assert "f-uncertain" in final_ids, "Uncertain finding must be retained"
        assert "f-rejected-2" not in final_ids, "Rejected finding must be excluded"

    def test_verification_writes_pre_verification_jsonl(self, tmp_path: Path):
        """findings_pre_verification.jsonl captures all candidates before filtering."""
        run = _make_run(verification_variant=VerificationVariant.WITH_VERIFICATION)
        finding = _make_finding()
        strategy = _make_strategy(verification="with_verification")
        strategy_output = _make_strategy_output([finding])

        verification_result = VerificationResult(
            verified=[VerifiedFinding(
                finding=finding,
                outcome=VerificationOutcome.VERIFIED,
                evidence="OK",
                cited_lines=[],
            )],
            rejected=[],
            uncertain=[],
            total_candidates=1,
            verification_tokens=10,
        )

        datasets_dir = tmp_path / "datasets"
        (datasets_dir / "targets" / "test-dataset" / "repo").mkdir(parents=True)
        output_dir = tmp_path / "output"

        self._run_with_verification(
            ExperimentWorker(), run, output_dir, datasets_dir,
            strategy, strategy_output, verification_result,
        )

        pre_verification_path = output_dir / "findings_pre_verification.jsonl"
        assert pre_verification_path.exists(), (
            "findings_pre_verification.jsonl must be written when verification ran"
        )
        lines = [ln for ln in pre_verification_path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# _upload_artifacts() tests
# ---------------------------------------------------------------------------


def _make_http_run() -> ExperimentRun:
    return _make_run(
        result_transport="http",
        upload_url="http://coordinator:8080/api/internal/runs/run-test-001/result",
        upload_token="tok-secret",
    )


def _make_resp(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = f"HTTP {status_code}"
    return resp


def _stub_client(response: MagicMock) -> MagicMock:
    """Build a context-manager mock httpx.Client that returns *response* on .post()."""
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.post = MagicMock(return_value=response)
    return client


class TestUploadArtifactsSuccess:
    def test_success_on_first_attempt(self, tmp_path: Path):
        """200 on the first post → returns without retry."""
        run = _make_http_run()
        worker = ExperimentWorker()
        (tmp_path / "run_result.json").write_text('{"status": "completed"}')

        client = _stub_client(_make_resp(200))
        with patch("httpx.Client", return_value=client):
            worker._upload_artifacts(run, tmp_path)

        assert client.post.call_count == 1

    def test_success_after_one_retry(self, tmp_path: Path):
        """5xx on attempt 1, 200 on attempt 2 → returns after two total posts."""
        run = _make_http_run()
        worker = ExperimentWorker()
        (tmp_path / "run_result.json").write_text('{"status": "completed"}')

        call_count = 0
        clients: list[MagicMock] = []

        def client_factory(**_kwargs):
            nonlocal call_count
            call_count += 1
            c = _stub_client(_make_resp(500) if call_count == 1 else _make_resp(200))
            clients.append(c)
            return c

        with (
            patch("httpx.Client", side_effect=client_factory),
            patch("time.sleep"),
        ):
            worker._upload_artifacts(run, tmp_path)

        assert sum(c.post.call_count for c in clients) == 2


class TestUploadArtifactsRetryExhaustion:
    def test_five_consecutive_failures_raises(self, tmp_path: Path):
        """5 consecutive 5xx responses exhaust all attempts → RuntimeError."""
        run = _make_http_run()
        worker = ExperimentWorker()
        (tmp_path / "run_result.json").write_text('{"status": "completed"}')

        clients: list[MagicMock] = []

        def client_factory(**_kwargs):
            c = _stub_client(_make_resp(500))
            clients.append(c)
            return c

        with (
            patch("httpx.Client", side_effect=client_factory),
            patch("time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="failed after 5 attempts"):
                worker._upload_artifacts(run, tmp_path)

        assert sum(c.post.call_count for c in clients) == 5

    def test_429_is_retried_up_to_five_times(self, tmp_path: Path):
        """429 Too Many Requests is treated as retriable — exhausts all 5 attempts."""
        run = _make_http_run()
        worker = ExperimentWorker()
        (tmp_path / "run_result.json").write_text('{"status": "completed"}')

        clients: list[MagicMock] = []

        def client_factory(**_kwargs):
            c = _stub_client(_make_resp(429))
            clients.append(c)
            return c

        with (
            patch("httpx.Client", side_effect=client_factory),
            patch("time.sleep"),
        ):
            with pytest.raises(RuntimeError, match="failed after 5 attempts"):
                worker._upload_artifacts(run, tmp_path)

        assert sum(c.post.call_count for c in clients) == 5


class TestUploadArtifactsFastExit:
    def test_403_exits_without_retrying(self, tmp_path: Path):
        """403 means another replica completed the run — return immediately, no retry."""
        run = _make_http_run()
        worker = ExperimentWorker()
        (tmp_path / "run_result.json").write_text('{"status": "completed"}')

        client = _stub_client(_make_resp(403))

        with (
            patch("httpx.Client", return_value=client),
            patch("time.sleep") as mock_sleep,
        ):
            worker._upload_artifacts(run, tmp_path)  # must not raise

        assert client.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_409_exits_without_retrying(self, tmp_path: Path):
        """409 means result already committed — return immediately, no retry."""
        run = _make_http_run()
        worker = ExperimentWorker()
        (tmp_path / "run_result.json").write_text('{"status": "completed"}')

        client = _stub_client(_make_resp(409))

        with (
            patch("httpx.Client", return_value=client),
            patch("time.sleep") as mock_sleep,
        ):
            worker._upload_artifacts(run, tmp_path)  # must not raise

        assert client.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_non_retriable_4xx_raises_immediately(self, tmp_path: Path):
        """A non-retriable 4xx (e.g. 400) raises RuntimeError without retrying."""
        run = _make_http_run()
        worker = ExperimentWorker()
        (tmp_path / "run_result.json").write_text('{"status": "completed"}')

        client = _stub_client(_make_resp(400))

        with (
            patch("httpx.Client", return_value=client),
            patch("time.sleep") as mock_sleep,
        ):
            with pytest.raises(RuntimeError, match="Upload failed with status 400"):
                worker._upload_artifacts(run, tmp_path)

        assert client.post.call_count == 1
        mock_sleep.assert_not_called()


class TestUploadArtifactsTransportError:
    def test_connect_error_is_retried(self, tmp_path: Path):
        """httpx.ConnectError (network failure) triggers retry, not immediate failure."""
        run = _make_http_run()
        worker = ExperimentWorker()
        (tmp_path / "run_result.json").write_text('{"status": "completed"}')

        call_count = 0

        def client_factory(**_kwargs):
            nonlocal call_count
            call_count += 1
            c = MagicMock()
            c.__enter__ = MagicMock(return_value=c)
            c.__exit__ = MagicMock(return_value=False)
            # Fail for the first two attempts, succeed on the third
            c.post = MagicMock(
                side_effect=httpx.ConnectError("connection refused")
                if call_count < 3
                else None
            )
            if call_count >= 3:
                c.post.return_value = _make_resp(200)
                c.post.side_effect = None
            return c

        with (
            patch("httpx.Client", side_effect=client_factory),
            patch("time.sleep"),
        ):
            worker._upload_artifacts(run, tmp_path)

        assert call_count == 3, "Two failures then one success = 3 total attempts"


class TestUploadArtifactsMissingCredentials:
    def test_missing_upload_url_raises_before_http(self, tmp_path: Path):
        """http transport with no upload_url → RuntimeError without any HTTP call."""
        run = _make_run(result_transport="http", upload_url=None, upload_token="tok")
        worker = ExperimentWorker()

        with patch("httpx.Client") as mock_http:
            with pytest.raises(RuntimeError, match="missing upload_url or upload_token"):
                worker._upload_artifacts(run, tmp_path)

        mock_http.assert_not_called()

    def test_missing_upload_token_raises_before_http(self, tmp_path: Path):
        """http transport with no upload_token → RuntimeError without any HTTP call."""
        run = _make_run(
            result_transport="http",
            upload_url="http://coord/api/internal/runs/x/result",
            upload_token=None,
        )
        worker = ExperimentWorker()

        with patch("httpx.Client") as mock_http:
            with pytest.raises(RuntimeError, match="missing upload_url or upload_token"):
                worker._upload_artifacts(run, tmp_path)

        mock_http.assert_not_called()
