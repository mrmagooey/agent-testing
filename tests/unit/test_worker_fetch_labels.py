"""Unit tests for ExperimentWorker._fetch_labels (C1 regression).

Workers run in K8s pods with no direct DB access; labels must be fetched
from the coordinator over HTTP.  This file verifies:
  - _fetch_labels makes the correct GET request and parses the response.
  - When no coordinator URL is available (PVC transport, offline), an empty
    list is returned.
  - Coordinator HTTP errors surface as RuntimeError.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource
from sec_review_framework.data.experiment import ExperimentRun
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.worker import ExperimentWorker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(
    upload_url: str | None = None,
    result_transport: str = "pvc",
) -> ExperimentRun:
    return ExperimentRun(
        id="test-run-001",
        experiment_id="test-exp",
        strategy_id="builtin.single_agent",
        dataset_name="my-dataset",
        dataset_version="v1",
        model_id="fake-model",
        upload_url=upload_url,
        result_transport=result_transport,  # type: ignore[arg-type]
    )


_LABEL_JSON = {
    "id": "lbl-001",
    "dataset_name": "my-dataset",
    "dataset_version": "v1",
    "file_path": "src/auth.py",
    "line_start": 10,
    "line_end": 12,
    "cwe_id": "CWE-89",
    "vuln_class": "sqli",
    "severity": "high",
    "description": "SQL injection",
    "source": "cve_patch",
    "confidence": "confirmed",
    "created_at": "2026-01-01T00:00:00+00:00",
}


# ---------------------------------------------------------------------------
# C1: _fetch_labels via HTTP coordinator endpoint
# ---------------------------------------------------------------------------


class TestFetchLabelsHTTP:
    """ExperimentWorker._fetch_labels calls GET /datasets/{name}/labels via HTTP."""

    def test_fetch_labels_derives_coordinator_url_from_upload_url(self, tmp_path: Path):
        """When run.upload_url is set, base URL is extracted and labels fetched."""
        run = _make_run(
            upload_url="http://coordinator-svc:8080/api/internal/runs/test-run-001/result",
            result_transport="http",
        )
        worker = ExperimentWorker()

        # Stub httpx.Client so no real network call is made
        mock_response = MagicMock()
        mock_response.json.return_value = [_LABEL_JSON]
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("httpx.Client", return_value=mock_client):
            labels = worker._fetch_labels(run, tmp_path)

        # Verify the correct endpoint was called
        called_url = mock_client.get.call_args[0][0]
        assert called_url == "http://coordinator-svc:8080/datasets/my-dataset/labels", (
            f"Expected coordinator labels URL, got {called_url!r}"
        )

        # Verify labels were parsed correctly
        assert len(labels) == 1
        assert isinstance(labels[0], GroundTruthLabel)
        assert labels[0].id == "lbl-001"
        assert labels[0].file_path == "src/auth.py"
        assert labels[0].vuln_class == VulnClass.SQLI

    def test_fetch_labels_uses_coordinator_internal_url_env_var(self, tmp_path: Path, monkeypatch):
        """Falls back to COORDINATOR_INTERNAL_URL env var when upload_url absent."""
        run = _make_run()  # no upload_url
        monkeypatch.setenv("COORDINATOR_INTERNAL_URL", "http://coord:9090")
        worker = ExperimentWorker()

        mock_response = MagicMock()
        mock_response.json.return_value = [_LABEL_JSON]
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("httpx.Client", return_value=mock_client):
            labels = worker._fetch_labels(run, tmp_path)

        called_url = mock_client.get.call_args[0][0]
        assert called_url == "http://coord:9090/datasets/my-dataset/labels"
        assert len(labels) == 1
        assert labels[0].id == "lbl-001"

    def test_fetch_labels_passes_version_as_query_param(self, tmp_path: Path):
        """dataset_version is sent as the ?version= query parameter."""
        run = _make_run(
            upload_url="http://coord:8080/api/internal/runs/x/result",
            result_transport="http",
        )
        worker = ExperimentWorker()

        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(return_value=mock_response)

        with patch("httpx.Client", return_value=mock_client):
            labels = worker._fetch_labels(run, tmp_path)

        call_kwargs = mock_client.get.call_args[1]
        params = call_kwargs.get("params", {})
        assert params.get("version") == "v1", f"Expected version=v1 in params, got {params!r}"
        assert labels == []

    def test_fetch_labels_returns_empty_when_no_coordinator_url(self, tmp_path: Path, monkeypatch):
        """PVC transport with no env var → empty list, no HTTP call made."""
        run = _make_run()  # no upload_url
        monkeypatch.delenv("COORDINATOR_INTERNAL_URL", raising=False)
        worker = ExperimentWorker()

        with patch("httpx.Client") as mock_httpx:
            labels = worker._fetch_labels(run, tmp_path)

        # httpx.Client must NOT have been called
        mock_httpx.assert_not_called()
        assert labels == []

    def test_fetch_labels_raises_on_http_error(self, tmp_path: Path):
        """HTTP 500 from coordinator surfaces as RuntimeError."""
        import httpx

        run = _make_run(
            upload_url="http://coord:8080/api/internal/runs/x/result",
            result_transport="http",
        )
        worker = ExperimentWorker()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get = MagicMock(
            side_effect=httpx.HTTPStatusError(
                "500 Internal Server Error",
                request=MagicMock(),
                response=MagicMock(status_code=500),
            )
        )

        with patch("httpx.Client", return_value=mock_client):
            with pytest.raises(RuntimeError, match="Failed to fetch labels"):
                worker._fetch_labels(run, tmp_path)
