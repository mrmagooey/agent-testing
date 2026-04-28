"""Integration test for ToolExtension.TREE_SITTER.

Requires tree-sitter and tree-sitter-language-pack to be installed
(they are in the ``worker`` extras, not installed in the default dev venv).

Run with worker extras:
    uv run --extra worker pytest tests/integration/test_tree_sitter_extension.py -v

The test is automatically skipped when the dependencies are missing.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Skip guard — check both the MCP server module and tree-sitter deps.
# ---------------------------------------------------------------------------

_TREE_SITTER_AVAILABLE = True
_SKIP_REASON = ""

try:
    import tree_sitter  # noqa: F401
    import tree_sitter_language_pack  # noqa: F401
except ImportError as _e:
    _TREE_SITTER_AVAILABLE = False
    _SKIP_REASON = (
        f"tree-sitter packages not installed ({_e}). "
        "Install worker extras: uv sync --extra worker"
    )

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixture: tiny repo with a vulnerable Python file
# ---------------------------------------------------------------------------

@pytest.fixture()
def vuln_repo(tmp_path: Path) -> Path:
    """Create a minimal repo with one Python file containing a named function."""
    src = tmp_path / "app.py"
    src.write_text(
        textwrap.dedent("""\
            import db

            def safe_query(user_id: int):
                return db.execute("SELECT * FROM users WHERE id = ?", [user_id])

            def vulnerable_query(user_input):
                # SQL injection vulnerability
                return db.execute("SELECT * FROM t WHERE id=" + user_input)

            class UserHandler:
                def get_user(self, uid):
                    return vulnerable_query(uid)
        """),
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Helper: make a mock target object
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

@pytest.mark.skipif(not _TREE_SITTER_AVAILABLE, reason=_SKIP_REASON)
class TestTreeSitterExtensionIntegration:
    def test_build_registers_ts_tools(self, vuln_repo: Path) -> None:
        """build_tree_sitter_tools should register at least one ts_* tool."""
        from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools
        from sec_review_framework.tools.registry import ToolRegistry

        target = _make_target(vuln_repo)
        registry = ToolRegistry()

        build_tree_sitter_tools(registry, target)
        try:
            defns = registry.get_tool_definitions()
            ts_tools = [d for d in defns if d.name.startswith("ts_")]
            assert len(ts_tools) >= 1, f"Expected ts_* tools, got: {[d.name for d in defns]}"
        finally:
            registry.close()

    def test_all_four_tools_registered(self, vuln_repo: Path) -> None:
        """All four security-review tools should be exposed with the ts_ prefix."""
        from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools
        from sec_review_framework.tools.registry import ToolRegistry

        target = _make_target(vuln_repo)
        registry = ToolRegistry()

        build_tree_sitter_tools(registry, target)
        try:
            names = {d.name for d in registry.get_tool_definitions()}
            expected = {"ts_find_symbol", "ts_get_ast", "ts_list_functions", "ts_query"}
            assert expected.issubset(names), f"Missing tools: {expected - names}"
        finally:
            registry.close()

    def test_list_functions_finds_vulnerable_query(self, vuln_repo: Path) -> None:
        """ts_list_functions should return 'vulnerable_query' from app.py."""
        import uuid

        from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools
        from sec_review_framework.tools.registry import ToolRegistry

        target = _make_target(vuln_repo)
        registry = ToolRegistry()

        build_tree_sitter_tools(registry, target)
        try:
            result = registry.invoke(
                "ts_list_functions",
                {"path": "app.py"},
                call_id=str(uuid.uuid4()),
            )
            assert "vulnerable_query" in result, (
                f"Expected 'vulnerable_query' in output, got:\n{result}"
            )
        finally:
            registry.close()

    def test_find_symbol_finds_vulnerable_query(self, vuln_repo: Path) -> None:
        """ts_find_symbol should return the definition of vulnerable_query."""
        import uuid

        from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools
        from sec_review_framework.tools.registry import ToolRegistry

        target = _make_target(vuln_repo)
        registry = ToolRegistry()

        build_tree_sitter_tools(registry, target)
        try:
            result = registry.invoke(
                "ts_find_symbol",
                {"path": "app.py", "symbol": "vulnerable_query"},
                call_id=str(uuid.uuid4()),
            )
            assert "vulnerable_query" in result, (
                f"Expected 'vulnerable_query' in find_symbol output, got:\n{result}"
            )
        finally:
            registry.close()

    def test_get_ast_returns_module_node(self, vuln_repo: Path) -> None:
        """ts_get_ast should return a tree containing 'module' (Python root node)."""
        import uuid

        from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools
        from sec_review_framework.tools.registry import ToolRegistry

        target = _make_target(vuln_repo)
        registry = ToolRegistry()

        build_tree_sitter_tools(registry, target)
        try:
            result = registry.invoke(
                "ts_get_ast",
                {"path": "app.py", "max_depth": 3},
                call_id=str(uuid.uuid4()),
            )
            assert "module" in result.lower(), (
                f"Expected 'module' in AST output, got:\n{result[:300]}"
            )
        finally:
            registry.close()

    def test_registry_close_shuts_down_client(self, vuln_repo: Path) -> None:
        """After registry.close(), the MCPClient should be marked closed."""
        from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools
        from sec_review_framework.tools.mcp_bridge import MCPClient
        from sec_review_framework.tools.registry import ToolRegistry

        target = _make_target(vuln_repo)
        registry = ToolRegistry()

        # Capture the client instance to inspect its state post-close.
        original_init = MCPClient.__init__
        captured_clients: list[MCPClient] = []

        def tracking_init(self, spec):
            original_init(self, spec)
            captured_clients.append(self)

        MCPClient.__init__ = tracking_init  # type: ignore[method-assign]
        try:
            build_tree_sitter_tools(registry, target)
            assert len(captured_clients) == 1
            client = captured_clients[0]
            assert not client._closed, "Client should be open after build"
            registry.close()
            assert client._closed, "Client should be closed after registry.close()"
        finally:
            MCPClient.__init__ = original_init  # type: ignore[method-assign]
