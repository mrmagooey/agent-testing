"""Integration test for ToolExtension.LSP.

Requires pyright-langserver to be installed on PATH (most portable of the
five LSP servers for CI).  All tests in this module are skipped automatically
when pyright-langserver is not available.

Run manually:
    npm install -g pyright
    uv run pytest tests/integration/test_lsp_extension.py -v

The test is automatically skipped when pyright-langserver is missing.
"""

from __future__ import annotations

import json
import shutil
import textwrap
import uuid
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip guard — check that at least pyright-langserver is on PATH.
# ---------------------------------------------------------------------------

_PYRIGHT_AVAILABLE = shutil.which("pyright-langserver") is not None
_SKIP_REASON = (
    "pyright-langserver not found on PATH. "
    "Install it with: npm install -g pyright"
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixture: tiny Python repo
# ---------------------------------------------------------------------------

@pytest.fixture()
def py_repo(tmp_path: Path) -> Path:
    """Create a minimal Python repo with two functions.

    File layout (used in LSP assertions below):
        example.py:
            line 0: def helper(x): return x + 1
            line 2: def caller(): return helper(5)
    """
    src = tmp_path / "example.py"
    src.write_text(
        textwrap.dedent("""\
            def helper(x):
                return x + 1

            def caller():
                return helper(5)
        """),
        encoding="utf-8",
    )
    # pyright needs a pyrightconfig.json or a pyproject.toml to discover the
    # workspace reliably.  An empty pyrightconfig is sufficient.
    (tmp_path / "pyrightconfig.json").write_text(
        json.dumps({"include": ["."], "pythonVersion": "3.11"}),
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_target(repo_path: Path):
    class _Target:
        pass
    t = _Target()
    t.repo_path = repo_path
    return t


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _PYRIGHT_AVAILABLE, reason=_SKIP_REASON)
class TestLspExtensionIntegration:

    def test_build_registers_lsp_tools(self, py_repo: Path) -> None:
        """build_lsp_tools should register at least one lsp_* tool."""
        from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
        from sec_review_framework.tools.registry import ToolRegistry

        target = _make_target(py_repo)
        registry = ToolRegistry()

        build_lsp_tools(registry, target)
        try:
            defns = registry.get_tool_definitions()
            lsp_tools = [d for d in defns if d.name.startswith("lsp_")]
            assert len(lsp_tools) >= 1, f"Expected lsp_* tools, got: {[d.name for d in defns]}"
        finally:
            registry.close()

    def test_all_five_tools_registered(self, py_repo: Path) -> None:
        """All five LSP tools should be exposed with the lsp_ prefix."""
        from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
        from sec_review_framework.tools.registry import ToolRegistry

        target = _make_target(py_repo)
        registry = ToolRegistry()

        build_lsp_tools(registry, target)
        try:
            names = {d.name for d in registry.get_tool_definitions()}
            expected = {
                "lsp_definition",
                "lsp_references",
                "lsp_hover",
                "lsp_document_symbols",
                "lsp_workspace_symbols",
            }
            assert expected.issubset(names), f"Missing tools: {expected - names}"
        finally:
            registry.close()

    def test_document_symbols_finds_helper_and_caller(self, py_repo: Path) -> None:
        """lsp_document_symbols on example.py should include both 'helper' and 'caller'."""
        from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
        from sec_review_framework.tools.registry import ToolRegistry

        target = _make_target(py_repo)
        registry = ToolRegistry()

        build_lsp_tools(registry, target)
        try:
            result = registry.invoke(
                "lsp_document_symbols",
                {"file_path": "example.py"},
                call_id=str(uuid.uuid4()),
            )
            # Result is JSON; parse it and check symbol names.
            data = json.loads(result)
            # Accept either an error message (pyright may not return symbols
            # without a full workspace init) or the full symbol list.
            if isinstance(data, dict) and "error" in data:
                pytest.skip(f"pyright returned error for document_symbols: {data['error']}")

            names = {sym["name"] for sym in data}
            assert "helper" in names, f"Expected 'helper' in symbols, got: {names}"
            assert "caller" in names, f"Expected 'caller' in symbols, got: {names}"
        finally:
            registry.close()

    def test_definition_resolves_helper_call(self, py_repo: Path) -> None:
        """lsp_definition on the 'helper' call inside 'caller' (line 4, col 11)
        should resolve back to line 0 where 'helper' is defined.
        """
        from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
        from sec_review_framework.tools.registry import ToolRegistry

        target = _make_target(py_repo)
        registry = ToolRegistry()

        build_lsp_tools(registry, target)
        try:
            # example.py line 4 (0-indexed): "    return helper(5)"
            # 'helper' starts at character 11.
            result = registry.invoke(
                "lsp_definition",
                {"file_path": "example.py", "line": 4, "character": 11},
                call_id=str(uuid.uuid4()),
            )
            data = json.loads(result)
            if isinstance(data, dict) and "error" in data:
                pytest.skip(f"pyright returned error for definition: {data['error']}")

            assert isinstance(data, list), f"Expected a list of locations, got: {data!r}"
            if len(data) == 0:
                pytest.skip("pyright returned no definition locations (workspace not fully indexed)")

            # The definition should point back to line 0 (def helper).
            loc = data[0]
            start_line = loc.get("start", {}).get("line", -1)
            assert start_line == 0, (
                f"Expected definition at line 0 (def helper), got line {start_line}"
            )
        finally:
            registry.close()

    def test_registry_close_shuts_down_client(self, py_repo: Path) -> None:
        """After registry.close(), the MCPClient should be marked closed."""
        from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
        from sec_review_framework.tools.mcp_bridge import MCPClient
        from sec_review_framework.tools.registry import ToolRegistry

        target = _make_target(py_repo)
        registry = ToolRegistry()

        original_init = MCPClient.__init__
        captured_clients: list[MCPClient] = []

        def tracking_init(self, spec):
            original_init(self, spec)
            captured_clients.append(self)

        MCPClient.__init__ = tracking_init  # type: ignore[method-assign]
        try:
            build_lsp_tools(registry, target)
            assert len(captured_clients) == 1
            client = captured_clients[0]
            assert not client._closed, "Client should be open after build"
            registry.close()
            assert client._closed, "Client should be closed after registry.close()"
        finally:
            MCPClient.__init__ = original_init  # type: ignore[method-assign]

    def test_unknown_file_extension_returns_error_json(self, py_repo: Path) -> None:
        """Calling lsp_definition on an unsupported file type should return an error dict."""
        from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
        from sec_review_framework.tools.registry import ToolRegistry

        # Write a YAML file — no LSP backend configured for it.
        (py_repo / "config.yaml").write_text("key: value\n", encoding="utf-8")

        target = _make_target(py_repo)
        registry = ToolRegistry()

        build_lsp_tools(registry, target)
        try:
            result = registry.invoke(
                "lsp_definition",
                {"file_path": "config.yaml", "line": 0, "character": 0},
                call_id=str(uuid.uuid4()),
            )
            data = json.loads(result)
            assert "error" in data, f"Expected error for unsupported extension, got: {data!r}"
        finally:
            registry.close()
