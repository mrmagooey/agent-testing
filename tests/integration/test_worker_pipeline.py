"""Integration tests for ExperimentWorker.run().

Uses FakeModelProvider to inject canned LLM responses so no real API calls
are made.  Uses a real temp directory as the datasets root and output root.
"""

from __future__ import annotations

import json
import resource
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import httpx

from tests.conftest import FakeModelProvider
from sec_review_framework.data.experiment import (
    ExperimentRun,
    ReviewProfileName,
    RunStatus,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.models.base import ModelResponse, RetryPolicy
from sec_review_framework.worker import ExperimentWorker


# ---------------------------------------------------------------------------
# Canned finding JSON that the fake model will return
# ---------------------------------------------------------------------------

SQLI_FINDING_JSON = json.dumps([
    {
        "file_path": "myapp/views.py",
        "line_start": 3,
        "line_end": 4,
        "vuln_class": "sqli",
        "cwe_ids": ["CWE-89"],
        "severity": "high",
        "title": "SQL Injection in search",
        "description": "User input concatenated directly into SQL query.",
        "recommendation": "Use parameterized queries.",
        "confidence": 0.95,
    }
])

EMPTY_FINDINGS_JSON = json.dumps([])


def _canned_response(content: str) -> ModelResponse:
    return ModelResponse(
        content=f"Analysis complete.\n\n```json\n{content}\n```",
        tool_calls=[],
        input_tokens=100,
        output_tokens=50,
        model_id="fake-model",
        raw={},
    )


def _no_finding_response() -> ModelResponse:
    return ModelResponse(
        content=f"No issues found.\n\n```json\n{EMPTY_FINDINGS_JSON}\n```",
        tool_calls=[],
        input_tokens=50,
        output_tokens=20,
        model_id="fake-model",
        raw={},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def datasets_dir(tmp_path: Path) -> Path:
    """Create a minimal datasets directory matching LabelStore.load() expectations."""
    ds_root = tmp_path / "datasets"
    repo_dir = ds_root / "targets" / "test-dataset" / "repo"
    repo_dir.mkdir(parents=True)

    # Source files the strategy will read
    (repo_dir / "myapp").mkdir()
    (repo_dir / "myapp" / "views.py").write_text(
        'def search(request):\n'
        '    q = request.GET.get("q")\n'
        '    query = "SELECT * FROM users WHERE name = \'%s\'" % q\n'
        '    cursor.execute(query)\n'
        '    return cursor.fetchall()\n'
    )
    (repo_dir / "myapp" / "__init__.py").write_text("")

    # Labels JSONL — required by LabelStore.load()
    labels_path = ds_root / "targets" / "test-dataset" / "labels.jsonl"
    label = {
        "id": "lbl-sqli",
        "dataset_version": "1.0.0",
        "file_path": "myapp/views.py",
        "line_start": 3,
        "line_end": 4,
        "cwe_id": "CWE-89",
        "vuln_class": "sqli",
        "severity": "high",
        "description": "SQL injection via string formatting",
        "source": "injected",
        "confidence": "confirmed",
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    labels_path.write_text(json.dumps(label) + "\n")

    return ds_root


@pytest.fixture
def base_run() -> ExperimentRun:
    """A minimal ExperimentRun for builtin.single_agent."""
    return ExperimentRun(
        id="experiment-w_fake-model_single_agent_with_tools_default_none",
        experiment_id="experiment-w",
        strategy_id="builtin.single_agent",
        model_id="fake-model",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITHOUT_TOOLS,  # avoids real semgrep/file tools
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="test-dataset",
        dataset_version="1.0.0",
        created_at=datetime(2026, 4, 16, tzinfo=timezone.utc),
    )


def _run_worker(
    run: ExperimentRun,
    datasets_dir: Path,
    output_dir: Path,
    responses: list[ModelResponse],
) -> Path:
    """Patch ModelProviderFactory to return our FakeModelProvider, then run worker."""
    from unittest.mock import patch
    from sec_review_framework.worker import ModelProviderFactory

    fake = FakeModelProvider(responses, retry_policy=RetryPolicy(max_retries=0))

    def _fake_create(self, model_id, model_config):  # noqa: ANN001
        return fake

    with patch.object(ModelProviderFactory, "create", _fake_create):
        worker = ExperimentWorker()
        worker.run(run, output_dir, datasets_dir)

    return output_dir


# ---------------------------------------------------------------------------
# Test 1: run() completes and writes run_result.json
# ---------------------------------------------------------------------------

def test_worker_writes_run_result_json(base_run, datasets_dir, tmp_path):
    output_dir = tmp_path / "output" / base_run.id
    _run_worker(base_run, datasets_dir, output_dir, [_canned_response(SQLI_FINDING_JSON)])

    result_file = output_dir / "run_result.json"
    assert result_file.exists(), "run_result.json was not created"

    from sec_review_framework.data.experiment import RunResult
    result = RunResult.model_validate_json(result_file.read_text())
    assert result.status == RunStatus.COMPLETED
    assert result.error is None


# ---------------------------------------------------------------------------
# Test 2: findings.jsonl written with correct format
# ---------------------------------------------------------------------------

def test_worker_writes_findings_jsonl(base_run, datasets_dir, tmp_path):
    output_dir = tmp_path / "output" / base_run.id
    _run_worker(base_run, datasets_dir, output_dir, [_canned_response(SQLI_FINDING_JSON)])

    findings_file = output_dir / "findings.jsonl"
    assert findings_file.exists()

    lines = [l for l in findings_file.read_text().splitlines() if l.strip()]
    assert len(lines) == 1

    finding = json.loads(lines[0])
    assert finding["vuln_class"] == "sqli"
    assert finding["file_path"] == "myapp/views.py"
    assert "title" in finding


# ---------------------------------------------------------------------------
# Test 3: tool_calls.jsonl written (may be empty for WITHOUT_TOOLS)
# ---------------------------------------------------------------------------

def test_worker_writes_tool_calls_jsonl(base_run, datasets_dir, tmp_path):
    output_dir = tmp_path / "output" / base_run.id
    _run_worker(base_run, datasets_dir, output_dir, [_canned_response(EMPTY_FINDINGS_JSON)])

    assert (output_dir / "tool_calls.jsonl").exists()


# ---------------------------------------------------------------------------
# Test 4: conversation.jsonl written
# ---------------------------------------------------------------------------

def test_worker_writes_conversation_jsonl(base_run, datasets_dir, tmp_path):
    output_dir = tmp_path / "output" / base_run.id
    _run_worker(base_run, datasets_dir, output_dir, [_canned_response(EMPTY_FINDINGS_JSON)])

    conv_file = output_dir / "conversation.jsonl"
    assert conv_file.exists()
    # At least one turn must be logged
    lines = [l for l in conv_file.read_text().splitlines() if l.strip()]
    assert len(lines) >= 1


# ---------------------------------------------------------------------------
# Test 5: strategy that raises → status=FAILED, error captured
# ---------------------------------------------------------------------------

def test_worker_failed_strategy_captured(datasets_dir, tmp_path):
    """When the strategy raises, the worker captures the error and writes FAILED status."""
    from unittest.mock import patch, MagicMock
    from sec_review_framework.worker import ModelProviderFactory
    from sec_review_framework.models.base import RetryPolicy

    run = ExperimentRun(
        id="experiment-fail_single_agent",
        experiment_id="experiment-fail",
        strategy_id="builtin.single_agent",
        model_id="fake-model",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITHOUT_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="test-dataset",
        dataset_version="1.0.0",
    )

    fake = FakeModelProvider([], retry_policy=RetryPolicy(max_retries=0))

    def _fake_create_model(self, model_id, model_config):  # noqa: ANN001
        return fake

    # Patch the SingleAgentStrategy.run to raise an error
    output_dir = tmp_path / "output" / run.id

    with patch.object(ModelProviderFactory, "create", _fake_create_model):
        from sec_review_framework.strategies.single_agent import SingleAgentStrategy
        with patch.object(SingleAgentStrategy, "run", side_effect=RuntimeError("Strategy deliberately failed")):
            worker = ExperimentWorker()
            worker.run(run, output_dir, datasets_dir)

    from sec_review_framework.data.experiment import RunResult
    result = RunResult.model_validate_json((output_dir / "run_result.json").read_text())
    assert result.status == RunStatus.FAILED
    assert result.error is not None
    assert "deliberately failed" in result.error


# ---------------------------------------------------------------------------
# Test 6: output directory is created if it doesn't exist
# ---------------------------------------------------------------------------

def test_worker_creates_output_dir(base_run, datasets_dir, tmp_path):
    output_dir = tmp_path / "deeply" / "nested" / "output" / base_run.id
    assert not output_dir.exists()

    _run_worker(base_run, datasets_dir, output_dir, [_canned_response(EMPTY_FINDINGS_JSON)])

    assert output_dir.exists()
    assert (output_dir / "run_result.json").exists()


# ---------------------------------------------------------------------------
# Test 7: Worker with builtin.single_agent strategy_id dispatches correctly
# ---------------------------------------------------------------------------

def test_worker_dispatches_builtin_single_agent(datasets_dir, tmp_path):
    """Worker with builtin.single_agent strategy_id constructs SingleAgentStrategy."""
    from unittest.mock import patch, MagicMock
    from sec_review_framework.worker import ModelProviderFactory, _SHAPE_TO_STRATEGY
    from sec_review_framework.data.strategy_bundle import OrchestrationShape
    from sec_review_framework.strategies.single_agent import SingleAgentStrategy

    run = ExperimentRun(
        id="test-dispatch_builtin.single_agent",
        experiment_id="test-dispatch",
        strategy_id="builtin.single_agent",
        model_id="fake-model",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITHOUT_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="test-dataset",
        dataset_version="1.0.0",
    )

    fake = FakeModelProvider(
        [_canned_response(EMPTY_FINDINGS_JSON)],
        retry_policy=RetryPolicy(max_retries=0),
    )

    dispatched = []

    original_run = SingleAgentStrategy.run

    def spy_run(self, *args, **kwargs):
        dispatched.append(type(self).__name__)
        return original_run(self, *args, **kwargs)

    output_dir = tmp_path / "output" / run.id

    with patch.object(ModelProviderFactory, "create", lambda self, mid, mkw: fake):
        with patch.object(SingleAgentStrategy, "run", spy_run):
            worker = ExperimentWorker()
            worker.run(run, output_dir, datasets_dir)

    assert dispatched == ["SingleAgentStrategy"], (
        f"Expected SingleAgentStrategy to be dispatched, got {dispatched}"
    )


# ---------------------------------------------------------------------------
# Test 8: Worker with unknown strategy_id raises a clear error
# ---------------------------------------------------------------------------

def test_worker_unknown_strategy_id_raises(datasets_dir, tmp_path):
    """Worker with an unknown strategy_id raises a clear KeyError."""
    from unittest.mock import patch
    from sec_review_framework.worker import ModelProviderFactory

    run = ExperimentRun(
        id="test-unknown-strategy",
        experiment_id="test-unknown",
        strategy_id="nonexistent.strategy.id.xyz",
        model_id="fake-model",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITHOUT_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="test-dataset",
        dataset_version="1.0.0",
    )

    fake = FakeModelProvider([], retry_policy=RetryPolicy(max_retries=0))
    output_dir = tmp_path / "output" / run.id

    with patch.object(ModelProviderFactory, "create", lambda self, mid, mkw: fake):
        worker = ExperimentWorker()
        worker.run(run, output_dir, datasets_dir)

    # The worker catches the error and writes FAILED status
    from sec_review_framework.data.experiment import RunResult
    result = RunResult.model_validate_json((output_dir / "run_result.json").read_text())
    assert result.status == RunStatus.FAILED
    assert result.error is not None
    assert "nonexistent.strategy.id.xyz" in result.error


# ---------------------------------------------------------------------------
# HTTP upload transport tests (Step 5)
# ---------------------------------------------------------------------------


def _make_http_run(base_run: ExperimentRun, upload_url: str, token: str) -> ExperimentRun:
    """Return a copy of base_run configured for HTTP transport."""
    return base_run.model_copy(update={
        "result_transport": "http",
        "upload_url": upload_url,
        "upload_token": token,
    })


def test_worker_http_transport_uploads_artifacts(base_run, datasets_dir, tmp_path):
    """With result_transport='http', worker calls _upload_artifacts instead of PVC copy."""
    from sec_review_framework.worker import ModelProviderFactory

    upload_calls: list[dict] = []

    def _fake_upload(self, run, local_dir):
        # Record what was in the local_dir at upload time
        upload_calls.append({
            "run_id": run.id,
            "files": [f.name for f in local_dir.iterdir()],
        })

    run = _make_http_run(base_run, "http://coordinator/api/internal/runs/r1/result", "tok123")
    output_dir = tmp_path / "output" / run.id
    output_dir.mkdir(parents=True, exist_ok=True)

    fake = FakeModelProvider([_canned_response(SQLI_FINDING_JSON)], retry_policy=RetryPolicy(max_retries=0))

    with patch.object(ModelProviderFactory, "create", lambda self, mid, mkw: fake):
        with patch.object(ExperimentWorker, "_upload_artifacts", _fake_upload):
            worker = ExperimentWorker()
            worker.run(run, output_dir, datasets_dir)

    assert len(upload_calls) == 1
    assert "run_result.json" in upload_calls[0]["files"]


def test_worker_pvc_transport_copies_to_output_dir(base_run, datasets_dir, tmp_path):
    """With result_transport='pvc' (default), worker copies artifacts to output_dir."""
    from sec_review_framework.worker import ModelProviderFactory

    output_dir = tmp_path / "output" / base_run.id
    fake = FakeModelProvider([_canned_response(SQLI_FINDING_JSON)], retry_policy=RetryPolicy(max_retries=0))

    with patch.object(ModelProviderFactory, "create", lambda self, mid, mkw: fake):
        worker = ExperimentWorker()
        worker.run(base_run, output_dir, datasets_dir)

    # Under PVC transport, artifacts appear directly in output_dir
    assert (output_dir / "run_result.json").exists()
    assert (output_dir / "findings.jsonl").exists()


def test_worker_upload_retries_on_5xx(base_run, datasets_dir, tmp_path):
    """_upload_artifacts retries on 5xx and succeeds on second attempt."""
    from sec_review_framework.worker import ModelProviderFactory

    attempt_count = [0]

    def _mock_post(url, files, headers, **kwargs):
        attempt_count[0] += 1
        if attempt_count[0] < 2:
            return MagicMock(status_code=503, text="Service Unavailable")
        return MagicMock(status_code=200, text="OK")

    run = _make_http_run(base_run, "http://coordinator/api/internal/runs/r1/result", "tok123")
    output_dir = tmp_path / "output" / run.id
    fake = FakeModelProvider([_canned_response(EMPTY_FINDINGS_JSON)], retry_policy=RetryPolicy(max_retries=0))

    with patch.object(ModelProviderFactory, "create", lambda self, mid, mkw: fake):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post = _mock_post
            mock_client_cls.return_value = mock_client

            import sec_review_framework.worker as worker_mod
            with patch.object(worker_mod.time, "sleep", return_value=None):
                worker = ExperimentWorker()
                worker.run(run, output_dir, datasets_dir)

    assert attempt_count[0] == 2


def test_worker_upload_fast_exits_on_403(base_run, datasets_dir, tmp_path):
    """_upload_artifacts fast-exits (no retry) on 403."""
    from sec_review_framework.worker import ModelProviderFactory

    attempt_count = [0]

    def _mock_post(url, files, headers, **kwargs):
        attempt_count[0] += 1
        return MagicMock(status_code=403, text="Forbidden")

    run = _make_http_run(base_run, "http://coordinator/api/internal/runs/r1/result", "tok123")
    output_dir = tmp_path / "output" / run.id
    fake = FakeModelProvider([_canned_response(EMPTY_FINDINGS_JSON)], retry_policy=RetryPolicy(max_retries=0))

    with patch.object(ModelProviderFactory, "create", lambda self, mid, mkw: fake):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post = _mock_post
            mock_client_cls.return_value = mock_client

            worker = ExperimentWorker()
            worker.run(run, output_dir, datasets_dir)  # Must not raise

    # Only one attempt (no retry on 403)
    assert attempt_count[0] == 1


def test_worker_upload_fast_exits_on_409(base_run, datasets_dir, tmp_path):
    """_upload_artifacts fast-exits on 409 (another replica already committed)."""
    from sec_review_framework.worker import ModelProviderFactory

    attempt_count = [0]

    def _mock_post(url, files, headers, **kwargs):
        attempt_count[0] += 1
        return MagicMock(status_code=409, text="Conflict")

    run = _make_http_run(base_run, "http://coordinator/api/internal/runs/r1/result", "tok123")
    output_dir = tmp_path / "output" / run.id
    fake = FakeModelProvider([_canned_response(EMPTY_FINDINGS_JSON)], retry_policy=RetryPolicy(max_retries=0))

    with patch.object(ModelProviderFactory, "create", lambda self, mid, mkw: fake):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post = _mock_post
            mock_client_cls.return_value = mock_client

            worker = ExperimentWorker()
            worker.run(run, output_dir, datasets_dir)  # Must not raise

    assert attempt_count[0] == 1


def test_worker_upload_raises_after_max_retries(base_run, datasets_dir, tmp_path):
    """_upload_artifacts raises RuntimeError after exhausting all retry attempts."""
    from sec_review_framework.worker import ModelProviderFactory

    def _mock_post(url, files, headers, **kwargs):
        return MagicMock(status_code=500, text="Internal Server Error")

    run = _make_http_run(base_run, "http://coordinator/api/internal/runs/r1/result", "tok123")
    output_dir = tmp_path / "output" / run.id
    fake = FakeModelProvider([_canned_response(EMPTY_FINDINGS_JSON)], retry_policy=RetryPolicy(max_retries=0))

    with patch.object(ModelProviderFactory, "create", lambda self, mid, mkw: fake):
        with patch("httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client.__enter__ = lambda s: s
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.post = _mock_post
            mock_client_cls.return_value = mock_client

            import sec_review_framework.worker as worker_mod
            with patch.object(worker_mod.time, "sleep", return_value=None):
                with pytest.raises(RuntimeError, match="failed after"):
                    worker = ExperimentWorker()
                    worker._upload_artifacts(
                        run,
                        tmp_path / "fake-local-dir",
                    )


def test_worker_http_upload_memory_bounded(base_run, datasets_dir, tmp_path):
    """Assembling and uploading a 10MB conversation fixture stays under 200 MB RSS.

    This is a scaled-down version of the 100MB test; peak RSS limit is 200 MB.
    We mock the actual HTTP call so we're testing our streaming logic, not httpx.
    """
    from sec_review_framework.worker import ModelProviderFactory, ExperimentWorker

    # Create a 10 MB conversation.jsonl in a temp dir to simulate worker output
    local_dir = tmp_path / "local-artifacts"
    local_dir.mkdir()
    conversation_file = local_dir / "conversation.jsonl"

    line = json.dumps({"role": "user", "content": "x" * 990}) + "\n"
    target_size = 10 * 1024 * 1024  # 10 MB
    with open(conversation_file, "w") as f:
        written = 0
        while written < target_size:
            f.write(line)
            written += len(line)

    # Also create a minimal run_result.json
    (local_dir / "run_result.json").write_text('{"status": "completed"}')

    upload_calls = [0]

    def _mock_post(url, files, headers, **kwargs):
        upload_calls[0] += 1
        # Consume the file iterables to simulate real streaming
        for _name, fh_tuple in files:
            _fname, fh, _ct = fh_tuple
            while chunk := fh.read(65536):
                pass
        return MagicMock(status_code=200, text="OK")

    run = _make_http_run(base_run, "http://coordinator/api/internal/runs/r1/result", "tok123")

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    with patch("httpx.Client") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__enter__ = lambda s: s
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post = _mock_post
        mock_client_cls.return_value = mock_client

        worker = ExperimentWorker()
        worker._upload_artifacts(run, local_dir)

    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # On Linux ru_maxrss is in kilobytes; on macOS it's in bytes.
    import sys
    if sys.platform == "darwin":
        rss_delta_mb = (rss_after - rss_before) / (1024 * 1024)
    else:
        rss_delta_mb = (rss_after - rss_before) / 1024

    assert rss_delta_mb < 200, (
        f"Upload RSS delta {rss_delta_mb:.1f} MB exceeds 200 MB limit. "
        "Check that file handles are passed to httpx (streaming) rather than buffering contents."
    )
    assert upload_calls[0] == 1
