"""End-to-end integration tests for probe-driven runs against a fake OpenAI-compat endpoint.

The probe → catalog → availability → synthesis → enrichment stack is exercised
without stubbing litellm.get_valid_models.  Instead a real HTTP server bound to
a local random port serves GET /v1/models so the whole HTTP path executes
through LiteLLM's openai provider, which honours api_base/api_key kwargs.

Phase 3 update: uses strategy_ids API; model IDs are derived from strategy bundles.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import json

import pytest

from sec_review_framework.coordinator import ExperimentCoordinator
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.experiment import ExperimentMatrix
from sec_review_framework.db import Database
from sec_review_framework.models.catalog import ProviderCatalog
from sec_review_framework.models.probes.litellm_endpoint_probe import LiteLLMEndpointProbe
from sec_review_framework.reporting.markdown import MarkdownReportGenerator

from tests.helpers import make_smoke_strategy

# ---------------------------------------------------------------------------
# Fake OpenAI-compat server — real sockets, no HTTP client mocking
# ---------------------------------------------------------------------------

_MODELS_RESPONSE = json.dumps({
    "object": "list",
    "data": [
        {"id": "fake-model-a", "object": "model"},
        {"id": "fake-model-b", "object": "model"},
    ],
}).encode()

_CHAT_RESPONSE = json.dumps({
    "id": "chatcmpl-fake",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "canned response"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}).encode()


def _run_fake_server(host: str, port: int, ready: threading.Event, stop: threading.Event) -> None:
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # suppress access log noise in test output

        def do_GET(self):
            if self.path == "/v1/models":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(_MODELS_RESPONSE)))
                self.end_headers()
                self.wfile.write(_MODELS_RESPONSE)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path == "/v1/chat/completions":
                content_len = int(self.headers.get("Content-Length", 0))
                self.rfile.read(content_len)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(_CHAT_RESPONSE)))
                self.end_headers()
                self.wfile.write(_CHAT_RESPONSE)
            else:
                self.send_response(404)
                self.end_headers()

    server = HTTPServer((host, port), Handler)
    server.timeout = 0.5
    ready.set()
    while not stop.is_set():
        server.handle_request()
    server.server_close()


@pytest.fixture(scope="module")
def fake_server() -> Iterator[str]:
    """Bind a random local port, serve a minimal OpenAI-compat API, yield the base URL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    host, port = sock.getsockname()
    sock.close()

    ready = threading.Event()
    stop = threading.Event()
    thread = threading.Thread(
        target=_run_fake_server,
        args=(host, port, ready, stop),
        daemon=True,
    )
    thread.start()
    ready.wait(timeout=5)

    yield f"http://{host}:{port}"

    stop.set()
    thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _make_coordinator(
    tmp_path: Path, db: Database, catalog: ProviderCatalog | None = None
) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={"gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)},
    )
    c = ExperimentCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        config_dir=None,
        default_cap=4,
    )
    if catalog is not None:
        c.catalog = catalog
    return c


def _make_probe() -> LiteLLMEndpointProbe:
    return LiteLLMEndpointProbe(
        provider_key="local_llm",
        api_base_env="LOCAL_LLM_BASE_URL",
        api_key_env="LOCAL_LLM_API_KEY",
        litellm_provider="openai",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_discovers_models_from_live_endpoint(fake_server, monkeypatch):
    """Probe contacts the live fake server and parses model ids correctly."""
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", fake_server)
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "dummy")

    snapshot = await _make_probe().probe()

    assert snapshot.probe_status == "fresh"
    assert snapshot.model_ids == frozenset({"openai/fake-model-a", "openai/fake-model-b"})


@pytest.mark.asyncio
async def test_submit_flow_populates_model_config_api_base_from_live_endpoint(
    fake_server, monkeypatch, tmp_path
):
    """Full pipeline: live probe → catalog.start() → enrich_model_configs returns api_base."""
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", fake_server)
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "dummy")

    catalog = ProviderCatalog(probes=[_make_probe()], ttl_seconds=600)
    await catalog.start()

    try:
        db = Database(tmp_path / "test.db")
        await db.init()
        c = _make_coordinator(tmp_path, db, catalog=catalog)

        # Insert a strategy referencing the probe-discovered model.
        # The probe returns "openai/fake-model-a" as the full LiteLLM routing string.
        strategy = make_smoke_strategy("openai/fake-model-a")
        await db.insert_user_strategy(strategy)

        matrix = ExperimentMatrix(
            experiment_id="test-e2e",
            dataset_name="ds",
            dataset_version="1.0",
            strategy_ids=[strategy.id],
        )
        enriched = await c.enrich_model_configs(matrix)

        assert enriched.get("openai/fake-model-a") == {
            "api_base": fake_server,
            "api_key": "dummy",
        }
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_expanded_runs_carry_api_base_through_model_dump_json(
    fake_server, monkeypatch, tmp_path
):
    """model_ids_from_matrix extracts the model_id and enrich_model_configs returns enrichment."""
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", fake_server)
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "dummy")

    catalog = ProviderCatalog(probes=[_make_probe()], ttl_seconds=600)
    await catalog.start()

    try:
        db = Database(tmp_path / "test.db")
        await db.init()
        c = _make_coordinator(tmp_path, db, catalog=catalog)

        strategy = make_smoke_strategy("openai/fake-model-a")
        await db.insert_user_strategy(strategy)

        matrix = ExperimentMatrix(
            experiment_id="test-e2e",
            dataset_name="ds",
            dataset_version="1.0",
            strategy_ids=[strategy.id],
        )
        enriched = await c.enrich_model_configs(matrix)
        assert enriched["openai/fake-model-a"]["api_base"] == fake_server

        # Verify the model_ids_from_matrix helper correctly derives model_ids.
        model_ids = await c.model_ids_from_matrix(matrix)
        assert "openai/fake-model-a" in model_ids

        # Matrix serialization round-trips correctly.
        restored_matrix = ExperimentMatrix.model_validate_json(matrix.model_dump_json())
        assert restored_matrix.strategy_ids == matrix.strategy_ids
    finally:
        await catalog.stop()
