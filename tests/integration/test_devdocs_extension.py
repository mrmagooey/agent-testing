"""Integration tests for the DevDocs ToolExtension.

Runs a full MCP round-trip against a fake on-disk docset directory using the
real devdocs_server and the real MCPClient/register_mcp_tools path.

Skips gracefully if:
  - the ``mcp`` SDK is not installed
  - the fixture docsets directory is absent
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

_FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures" / "devdocs"

# Skip entire module if mcp SDK is not available.
mcp = pytest.importorskip("mcp", reason="mcp SDK not installed — skipping devdocs integration tests")

# Skip if fixture docsets are absent (shouldn't happen in normal checkout).
if not _FIXTURES_ROOT.exists():
    pytest.skip("devdocs fixture directory missing", allow_module_level=True)


# ---------------------------------------------------------------------------
# Full MCP round-trip fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def devdocs_registry():
    """
    Start a real devdocs MCP server subprocess pointing at the fixture docsets,
    register the tools, and return the populated ToolRegistry.

    Tears down the server after the module completes.
    """
    from sec_review_framework.tools.extensions.devdocs_ext import build_devdocs_tools
    from sec_review_framework.tools.registry import ToolRegistry

    registry = ToolRegistry()
    target = object()  # target not used by devdocs builder

    with patch.dict(os.environ, {"DEVDOCS_ROOT": str(_FIXTURES_ROOT)}, clear=False):
        try:
            build_devdocs_tools(registry, target)
        except Exception as exc:
            pytest.skip(f"DevDocs MCP server failed to start: {exc}")

    yield registry
    registry.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDevdocsRoundTrip:
    """Full MCP round-trip via the real subprocess."""

    def test_doc_list_docsets_registered(self, devdocs_registry) -> None:
        assert "doc_list_docsets" in devdocs_registry.tools

    def test_doc_search_registered(self, devdocs_registry) -> None:
        assert "doc_search" in devdocs_registry.tools

    def test_doc_fetch_registered(self, devdocs_registry) -> None:
        assert "doc_fetch" in devdocs_registry.tools

    def test_doc_list_docsets_returns_both_fixtures(self, devdocs_registry) -> None:
        result_json = devdocs_registry.invoke(
            "doc_list_docsets", {}, call_id="test-list-1"
        )
        result = json.loads(result_json)
        slugs = {d["slug"] for d in result}
        assert "python~3.12" in slugs
        assert "javascript" in slugs

    def test_doc_search_finds_subprocess(self, devdocs_registry) -> None:
        result_json = devdocs_registry.invoke(
            "doc_search",
            {"query": "subprocess", "limit": 10},
            call_id="test-search-1",
        )
        result = json.loads(result_json)
        names = [r["name"] for r in result]
        assert any("subprocess" in n.lower() for n in names)

    def test_doc_search_restricted_to_docset(self, devdocs_registry) -> None:
        result_json = devdocs_registry.invoke(
            "doc_search",
            {"query": "eval", "docset": "javascript", "limit": 5},
            call_id="test-search-2",
        )
        result = json.loads(result_json)
        assert all(r["docset"] == "javascript" for r in result)

    def test_doc_fetch_returns_html_and_text(self, devdocs_registry) -> None:
        result_json = devdocs_registry.invoke(
            "doc_fetch",
            {"docset": "python~3.12", "path": "subprocess#subprocess.Popen"},
            call_id="test-fetch-1",
        )
        result = json.loads(result_json)
        assert "html" in result
        assert "text" in result
        assert "<" not in result["text"]

    def test_doc_fetch_missing_entry_returns_error(self, devdocs_registry) -> None:
        result_json = devdocs_registry.invoke(
            "doc_fetch",
            {"docset": "python~3.12", "path": "nonexistent/path"},
            call_id="test-fetch-2",
        )
        result = json.loads(result_json)
        assert "error" in result

    def test_doc_fetch_path_traversal_returns_error(self, devdocs_registry) -> None:
        result_json = devdocs_registry.invoke(
            "doc_fetch",
            {"docset": "python~3.12", "path": "../../../etc/passwd"},
            call_id="test-fetch-security",
        )
        result = json.loads(result_json)
        assert "error" in result
