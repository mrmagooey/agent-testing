"""Minimal tree-sitter MCP server for security-review workloads.

Exposes four tools (all read-only / query-only):
  - find_symbol    — find a named symbol (function/class/variable) in the repo
  - get_ast        — return the AST s-expression for a file (or excerpt)
  - list_functions — enumerate top-level function/method definitions in a file
  - query          — run an arbitrary tree-sitter query against a file

Launched by tree_sitter_ext.py as a subprocess speaking MCP over stdio.
The workspace root is passed as the first positional argument.

Usage (via MCP bridge):
    python -m sec_review_framework.tools.extensions.tree_sitter_server /path/to/repo
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

# ---------------------------------------------------------------------------
# Language detection helper
# ---------------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".php": "php",
    ".sh": "bash",
    ".bash": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".tf": "hcl",
    ".hcl": "hcl",
    ".sql": "sql",
    ".dockerfile": "dockerfile",
}


def _detect_lang(path: Path) -> str | None:
    name_lower = path.name.lower()
    if name_lower == "dockerfile":
        return "dockerfile"
    return _EXT_TO_LANG.get(path.suffix.lower())


def _get_parser(lang: str) -> Any:
    """Return a tree-sitter Parser for *lang*, or raise ImportError / ValueError."""
    from tree_sitter_language_pack import get_parser  # type: ignore[import]
    return get_parser(lang)


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _parse_file(repo_root: Path, rel_path: str) -> tuple[Any, str]:
    """Parse *rel_path* relative to *repo_root*. Returns (tree, language)."""
    full = (repo_root / rel_path).resolve()
    if not str(full).startswith(str(repo_root.resolve())):
        raise ValueError(f"Path escapes repo root: {rel_path!r}")
    if not full.is_file():
        raise FileNotFoundError(f"File not found: {rel_path}")
    lang = _detect_lang(full)
    if lang is None:
        raise ValueError(f"Unsupported file extension: {full.suffix!r}")
    source = full.read_bytes()
    parser = _get_parser(lang)
    tree = parser.parse(source)
    return tree, lang


def _node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

def build_server(repo_root: Path) -> Server:
    app = Server("tree-sitter")

    @app.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="find_symbol",
                description=(
                    "Find definitions of a named symbol (function, class, variable) "
                    "within a file using tree-sitter AST queries. Returns matching "
                    "node text and line numbers."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to repo root."},
                        "symbol": {"type": "string", "description": "Symbol name to find."},
                    },
                    "required": ["path", "symbol"],
                },
            ),
            types.Tool(
                name="get_ast",
                description=(
                    "Return the tree-sitter AST s-expression for a file. "
                    "Pass max_depth to limit nesting (default 5)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to repo root."},
                        "max_depth": {"type": "integer", "description": "Maximum nesting depth.", "default": 5},
                    },
                    "required": ["path"],
                },
            ),
            types.Tool(
                name="list_functions",
                description=(
                    "List top-level function and method definitions in a file, "
                    "including their start line numbers."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to repo root."},
                    },
                    "required": ["path"],
                },
            ),
            types.Tool(
                name="query",
                description=(
                    "Run an arbitrary tree-sitter S-expression query against a file. "
                    "Returns captured nodes and their text."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "File path relative to repo root."},
                        "query_string": {"type": "string", "description": "Tree-sitter query in S-expression syntax."},
                        "max_results": {"type": "integer", "description": "Maximum captures to return.", "default": 50},
                    },
                    "required": ["path", "query_string"],
                },
            ),
        ]

    @app.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        try:
            result = _dispatch(name, arguments, repo_root)
        except Exception as exc:  # noqa: BLE001
            result = f"Error: {exc}"
        return [types.TextContent(type="text", text=result)]

    return app


def _dispatch(name: str, args: dict, repo_root: Path) -> str:
    if name == "find_symbol":
        return _find_symbol(repo_root, args["path"], args["symbol"])
    if name == "get_ast":
        return _get_ast(repo_root, args["path"], int(args.get("max_depth", 5)))
    if name == "list_functions":
        return _list_functions(repo_root, args["path"])
    if name == "query":
        return _run_query(repo_root, args["path"], args["query_string"], int(args.get("max_results", 50)))
    raise ValueError(f"Unknown tool: {name!r}")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _find_symbol(repo_root: Path, rel_path: str, symbol: str) -> str:
    full = (repo_root / rel_path).resolve()
    source = full.read_bytes()
    tree, lang = _parse_file(repo_root, rel_path)

    results: list[str] = []
    _walk_find_symbol(tree.root_node, symbol, source, results)
    if not results:
        return f"Symbol {symbol!r} not found in {rel_path}"
    return "\n".join(results)


def _walk_find_symbol(node: Any, symbol: str, source: bytes, results: list[str]) -> None:
    # Capture identifier nodes whose text matches the symbol name exactly.
    # We look one level up to give context (e.g. the enclosing definition).
    if node.type in ("identifier", "name") and _node_text(node, source) == symbol:
        parent = node.parent
        ctx = _node_text(parent, source) if parent else _node_text(node, source)
        line = node.start_point[0] + 1
        results.append(f"Line {line}: {ctx[:200]}")
    for child in node.children:
        _walk_find_symbol(child, symbol, source, results)


def _truncated_sexp(node: Any, depth: int, max_depth: int) -> str:
    if depth >= max_depth:
        return f"({node.type} ...)"
    if not node.children:
        return f"({node.type})"
    children_repr = " ".join(_truncated_sexp(c, depth + 1, max_depth) for c in node.children)
    return f"({node.type} {children_repr})"


def _get_ast(repo_root: Path, rel_path: str, max_depth: int) -> str:
    tree, lang = _parse_file(repo_root, rel_path)
    return f"Language: {lang}\n{_truncated_sexp(tree.root_node, 0, max_depth)}"


# Node types that represent function/method definitions across languages.
_FUNCTION_NODE_TYPES = {
    "function_definition",     # Python, Ruby, Bash
    "function_declaration",    # JavaScript, Go, C, C++, Java
    "method_definition",       # JavaScript/TypeScript class methods
    "method_declaration",      # Java
    "arrow_function",          # JS/TS
    "func_literal",            # Go
    "function_item",           # Rust
    "fn_expression",           # Rust
    "function",                # PHP, SQL
    "sub",                     # Perl / Ruby
}

_NAME_CHILD_TYPES = {"identifier", "name", "field_identifier"}


def _list_functions(repo_root: Path, rel_path: str) -> str:
    full = (repo_root / rel_path).resolve()
    source = full.read_bytes()
    tree, lang = _parse_file(repo_root, rel_path)

    results: list[tuple[int, str]] = []
    _walk_functions(tree.root_node, source, results, depth=0)
    if not results:
        return f"No function definitions found in {rel_path}"
    lines = [f"Line {ln}: {name}" for ln, name in sorted(results)]
    return f"Functions in {rel_path} (language: {lang}):\n" + "\n".join(lines)


def _walk_functions(node: Any, source: bytes, results: list[tuple[int, str]], depth: int) -> None:
    if node.type in _FUNCTION_NODE_TYPES:
        # Try to extract the name from a named child.
        name = "<anonymous>"
        for child in node.children:
            if child.type in _NAME_CHILD_TYPES:
                name = _node_text(child, source)
                break
        line = node.start_point[0] + 1
        results.append((line, name))
    for child in node.children:
        _walk_functions(child, source, results, depth + 1)


def _run_query(repo_root: Path, rel_path: str, query_string: str, max_results: int) -> str:
    from tree_sitter_language_pack import get_language  # type: ignore[import]
    full = (repo_root / rel_path).resolve()
    source = full.read_bytes()
    tree, lang = _parse_file(repo_root, rel_path)

    ts_lang = get_language(lang)
    query = ts_lang.query(query_string)
    captures = query.captures(tree.root_node)

    # tree-sitter captures can be dict {name: [node,...]} or list of (node, name)
    rows: list[str] = []
    if isinstance(captures, dict):
        for cap_name, nodes in captures.items():
            for node in nodes:
                if len(rows) >= max_results:
                    break
                text = _node_text(node, source)
                rows.append(f"[{cap_name}] line {node.start_point[0]+1}: {text[:120]}")
    else:
        for node, cap_name in captures:
            if len(rows) >= max_results:
                break
            text = _node_text(node, source)
            rows.append(f"[{cap_name}] line {node.start_point[0]+1}: {text[:120]}")

    if not rows:
        return "No captures found."
    if len(rows) >= max_results:
        rows.append(f"... (truncated at {max_results} results)")
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: tree_sitter_server.py <repo_root>", file=sys.stderr)
        sys.exit(1)

    repo_root = Path(sys.argv[1]).resolve()
    if not repo_root.is_dir():
        print(f"Error: {repo_root} is not a directory", file=sys.stderr)
        sys.exit(1)

    app = build_server(repo_root)
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
