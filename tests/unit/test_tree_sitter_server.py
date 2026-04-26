"""Unit tests for the tree-sitter MCP server tool handlers.

Exercises _dispatch, _find_symbol, _get_ast, _list_functions, _run_query
directly — no MCP transport, no subprocess.

Requires tree-sitter-language-pack (worker extras):
    uv run --extra worker pytest tests/unit/test_tree_sitter_server.py -x -q
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

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

pytestmark = pytest.mark.skipif(not _TREE_SITTER_AVAILABLE, reason=_SKIP_REASON)

# Probe whether Query.captures() is available (tree-sitter >= 0.22 changed the API).
_QUERY_CAPTURES_SUPPORTED = False
if _TREE_SITTER_AVAILABLE:
    try:
        from tree_sitter import Language, Parser, Query
        _QUERY_CAPTURES_SUPPORTED = hasattr(Query, "captures")
    except Exception:
        pass

_SKIP_CAPTURES_REASON = "tree-sitter Query.captures() not available in this version"


# ---------------------------------------------------------------------------
# Helpers / inline source snippets
# ---------------------------------------------------------------------------

_PY_SRC = textwrap.dedent("""\
    import os

    def authenticate(username, password):
        query = "SELECT * FROM users WHERE user='" + username + "'"
        return query

    class UserService:
        def delete_user(self, uid):
            os.system("rm -rf /data/" + uid)
""")

_JS_SRC = textwrap.dedent("""\
    function greet(name) {
        return "Hello " + name;
    }

    const add = (a, b) => a + b;
""")

_MALFORMED_PY_SRC = b"def foo(\x00\xff\xfe: this is not valid python syntax <<<>>>"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def py_repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(_PY_SRC, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def js_repo(tmp_path: Path) -> Path:
    (tmp_path / "util.js").write_text(_JS_SRC, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def malformed_repo(tmp_path: Path) -> Path:
    (tmp_path / "broken.py").write_bytes(_MALFORMED_PY_SRC)
    return tmp_path


# ---------------------------------------------------------------------------
# _detect_lang
# ---------------------------------------------------------------------------

class TestDetectLang:
    def test_python_extension(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _detect_lang
        assert _detect_lang(tmp_path / "foo.py") == "python"

    def test_javascript_extension(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _detect_lang
        assert _detect_lang(tmp_path / "index.js") == "javascript"

    def test_typescript_extension(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _detect_lang
        assert _detect_lang(tmp_path / "app.ts") == "typescript"

    def test_go_extension(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _detect_lang
        assert _detect_lang(tmp_path / "main.go") == "go"

    def test_dockerfile_filename(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _detect_lang
        assert _detect_lang(tmp_path / "Dockerfile") == "dockerfile"

    def test_unknown_extension_returns_none(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _detect_lang
        assert _detect_lang(tmp_path / "README.md") is None


# ---------------------------------------------------------------------------
# Path-escape rejection (_parse_file)
# ---------------------------------------------------------------------------

class TestPathEscape:
    def test_dotdot_escape_raises_value_error(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _parse_file
        with pytest.raises(ValueError, match="escapes repo root"):
            _parse_file(tmp_path, "../../etc/passwd")

    def test_absolute_path_that_escapes_raises(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _parse_file
        outside = tmp_path.parent / "outside.py"
        outside.write_text("x = 1\n")
        try:
            with pytest.raises(ValueError, match="escapes repo root"):
                _parse_file(tmp_path, "../outside.py")
        finally:
            outside.unlink(missing_ok=True)

    def test_path_within_root_does_not_raise(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _parse_file
        tree, lang = _parse_file(py_repo, "app.py")
        assert lang == "python"
        assert tree is not None

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _parse_file
        with pytest.raises(FileNotFoundError):
            _parse_file(tmp_path, "nonexistent.py")


# ---------------------------------------------------------------------------
# Unsupported language handling (_parse_file)
# ---------------------------------------------------------------------------

class TestUnsupportedLanguage:
    def test_unsupported_extension_raises_value_error(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _parse_file
        (tmp_path / "notes.md").write_text("# hello\n")
        with pytest.raises(ValueError, match="Unsupported file extension"):
            _parse_file(tmp_path, "notes.md")

    def test_dispatch_get_ast_unsupported_raises_value_error(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _dispatch
        (tmp_path / "data.txt").write_text("hello\n")
        with pytest.raises(ValueError, match="Unsupported file extension"):
            _dispatch("get_ast", {"path": "data.txt"}, tmp_path)

    def test_dispatch_list_functions_unsupported_raises_value_error(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _dispatch
        (tmp_path / "data.csv").write_text("a,b,c\n")
        with pytest.raises(ValueError, match="Unsupported file extension"):
            _dispatch("list_functions", {"path": "data.csv"}, tmp_path)


# ---------------------------------------------------------------------------
# Parse-failure handling (malformed source must not crash)
# ---------------------------------------------------------------------------

class TestParseFailure:
    def test_get_ast_malformed_returns_string(self, malformed_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _get_ast
        result = _get_ast(malformed_repo, "broken.py", 5)
        assert isinstance(result, str)

    def test_list_functions_malformed_returns_string(self, malformed_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _list_functions
        result = _list_functions(malformed_repo, "broken.py")
        assert isinstance(result, str)

    def test_find_symbol_malformed_returns_string(self, malformed_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _find_symbol
        result = _find_symbol(malformed_repo, "broken.py", "foo")
        assert isinstance(result, str)

    def test_dispatch_does_not_raise_on_malformed_input(self, malformed_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _dispatch
        result = _dispatch("get_ast", {"path": "broken.py"}, malformed_repo)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Happy-path: Python
# ---------------------------------------------------------------------------

class TestHappyPathPython:
    def test_get_ast_returns_language_header(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _get_ast
        result = _get_ast(py_repo, "app.py", 5)
        assert "Language: python" in result

    def test_get_ast_contains_module_node(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _get_ast
        result = _get_ast(py_repo, "app.py", 5)
        assert "module" in result.lower()

    def test_get_ast_respects_max_depth_zero(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _get_ast
        result = _get_ast(py_repo, "app.py", 0)
        assert "..." in result

    def test_list_functions_finds_authenticate(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _list_functions
        result = _list_functions(py_repo, "app.py")
        assert "authenticate" in result

    def test_list_functions_finds_delete_user(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _list_functions
        result = _list_functions(py_repo, "app.py")
        assert "delete_user" in result

    def test_list_functions_includes_line_numbers(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _list_functions
        result = _list_functions(py_repo, "app.py")
        assert "Line" in result

    def test_find_symbol_found(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _find_symbol
        result = _find_symbol(py_repo, "app.py", "authenticate")
        assert "authenticate" in result
        assert "Line" in result

    def test_find_symbol_not_found_returns_message(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _find_symbol
        result = _find_symbol(py_repo, "app.py", "nonexistent_xyz_symbol")
        assert "not found" in result

    def test_query_path_escape_raises(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _run_query
        inner = tmp_path / "inner"
        inner.mkdir()
        outside = tmp_path / "outside.py"
        outside.write_text("x = 1\n")
        with pytest.raises(ValueError, match="escapes repo root"):
            _run_query(inner, "../outside.py", "(identifier) @id", 50)

    def test_query_unsupported_language_raises(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _run_query
        (tmp_path / "data.txt").write_text("hello\n")
        with pytest.raises(ValueError, match="Unsupported file extension"):
            _run_query(tmp_path, "data.txt", "(identifier) @id", 50)

    @pytest.mark.skipif(not _QUERY_CAPTURES_SUPPORTED, reason=_SKIP_CAPTURES_REASON)
    def test_query_valid_call_does_not_raise_value_error(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _run_query
        q = "(function_definition name: (identifier) @fn)"
        result = _run_query(py_repo, "app.py", q, 50)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Happy-path: JavaScript (second language)
# ---------------------------------------------------------------------------

class TestHappyPathJavaScript:
    def test_get_ast_returns_language_header(self, js_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _get_ast
        result = _get_ast(js_repo, "util.js", 5)
        assert "Language: javascript" in result

    def test_list_functions_finds_greet(self, js_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _list_functions
        result = _list_functions(js_repo, "util.js")
        assert "greet" in result

    def test_find_symbol_greet(self, js_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _find_symbol
        result = _find_symbol(js_repo, "util.js", "greet")
        assert "greet" in result

    @pytest.mark.skipif(not _QUERY_CAPTURES_SUPPORTED, reason=_SKIP_CAPTURES_REASON)
    def test_query_function_declaration(self, js_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _run_query
        q = "(function_declaration name: (identifier) @fn)"
        result = _run_query(js_repo, "util.js", q, 50)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Empty-input handling
# ---------------------------------------------------------------------------

class TestEmptyInput:
    def test_empty_python_file_list_functions_returns_no_definitions(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _list_functions
        (tmp_path / "empty.py").write_text("")
        result = _list_functions(tmp_path, "empty.py")
        assert "No function definitions found" in result

    def test_empty_python_file_find_symbol_returns_not_found(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _find_symbol
        (tmp_path / "empty.py").write_text("")
        result = _find_symbol(tmp_path, "empty.py", "foo")
        assert "not found" in result

    def test_empty_python_file_get_ast_returns_string(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _get_ast
        (tmp_path / "empty.py").write_text("")
        result = _get_ast(tmp_path, "empty.py", 5)
        assert isinstance(result, str)
        assert len(result) > 0

    @pytest.mark.skipif(not _QUERY_CAPTURES_SUPPORTED, reason=_SKIP_CAPTURES_REASON)
    def test_empty_python_file_query_does_not_raise_security_error(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _run_query
        (tmp_path / "empty.py").write_text("")
        q = "(function_definition name: (identifier) @fn)"
        result = _run_query(tmp_path, "empty.py", q, 50)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _dispatch routing
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_dispatch_find_symbol(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _dispatch
        result = _dispatch("find_symbol", {"path": "app.py", "symbol": "authenticate"}, py_repo)
        assert "authenticate" in result

    def test_dispatch_get_ast(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _dispatch
        result = _dispatch("get_ast", {"path": "app.py"}, py_repo)
        assert "python" in result.lower()

    def test_dispatch_list_functions(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _dispatch
        result = _dispatch("list_functions", {"path": "app.py"}, py_repo)
        assert "authenticate" in result

    @pytest.mark.skipif(not _QUERY_CAPTURES_SUPPORTED, reason=_SKIP_CAPTURES_REASON)
    def test_dispatch_query(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _dispatch
        result = _dispatch("query", {"path": "app.py", "query_string": "(identifier) @id"}, py_repo)
        assert isinstance(result, str)

    def test_dispatch_unknown_tool_raises(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _dispatch
        with pytest.raises(ValueError, match="Unknown tool"):
            _dispatch("write_file", {"path": "app.py"}, py_repo)

    def test_dispatch_path_escape_raises(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import _dispatch
        with pytest.raises(ValueError, match="escapes repo root"):
            _dispatch("get_ast", {"path": "../../etc/passwd"}, py_repo)


# ---------------------------------------------------------------------------
# build_server / handle_call_tool — error wrapping via MCP request handler
# ---------------------------------------------------------------------------

class TestBuildServer:
    def _call_tool(self, app: Any, name: str, arguments: dict) -> Any:
        import asyncio
        import mcp.types as mcp_types
        from mcp.types import CallToolRequest

        handler = app.request_handlers[CallToolRequest]

        async def _run():
            req = CallToolRequest(
                method="tools/call",
                params=mcp_types.CallToolRequestParams(name=name, arguments=arguments),
            )
            return await handler(req)

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_run())
        finally:
            loop.close()

    def test_handle_call_tool_wraps_path_escape_as_text(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import build_server
        from mcp.types import TextContent

        app = build_server(py_repo)
        result = self._call_tool(app, "get_ast", {"path": "../../etc/passwd"})
        content = result.root.content
        assert len(content) == 1
        assert isinstance(content[0], TextContent)
        assert "Error" in content[0].text

    def test_handle_call_tool_wraps_unsupported_lang_as_text(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import build_server
        from mcp.types import TextContent

        (tmp_path / "data.txt").write_text("hello\n")
        app = build_server(tmp_path)
        result = self._call_tool(app, "list_functions", {"path": "data.txt"})
        content = result.root.content
        assert len(content) == 1
        assert isinstance(content[0], TextContent)
        assert "Error" in content[0].text

    def test_handle_call_tool_get_ast_success(self, py_repo: Path) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_server import build_server
        from mcp.types import TextContent

        app = build_server(py_repo)
        result = self._call_tool(app, "get_ast", {"path": "app.py"})
        content = result.root.content
        assert len(content) == 1
        assert isinstance(content[0], TextContent)
        assert "python" in content[0].text.lower()

    def test_handle_call_tool_list_tools_returns_four_tools(self, py_repo: Path) -> None:
        import asyncio
        from sec_review_framework.tools.extensions.tree_sitter_server import build_server
        from mcp.types import ListToolsRequest

        app = build_server(py_repo)
        handler = app.request_handlers[ListToolsRequest]

        async def _run():
            req = ListToolsRequest(method="tools/list", params=None)
            return await handler(req)

        loop = asyncio.new_event_loop()
        try:
            result = loop.run_until_complete(_run())
        finally:
            loop.close()

        tool_names = {t.name for t in result.root.tools}
        assert tool_names == {"find_symbol", "get_ast", "list_functions", "query"}
