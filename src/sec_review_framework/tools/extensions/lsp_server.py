"""LSP multiplexer MCP server for security-review workloads.

One MCP server process that speaks MCP outward and manages LSP backends inward.
The multiplexer dispatches on file extension to the correct language server,
starting each backend lazily — only when a tool call names a file whose extension
maps to that language.

Exposes five MCP tools (all read-only / query-only):
  - definition       — go-to-definition for a symbol at a position
  - references       — find all references to a symbol at a position
  - hover            — hover documentation / signature for a position
  - document_symbols — hierarchical symbol list for a file
  - workspace_symbols — symbol search across the entire workspace

Launched by lsp_ext.py as a subprocess speaking MCP over stdio.
The workspace root is passed via --workspace <path>.

Usage (via MCP bridge):
    python -m sec_review_framework.tools.extensions.lsp_server --workspace /path/to/repo
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language / server mapping
# ---------------------------------------------------------------------------

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".go": "go",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
    ".jsx": "typescript",
    ".rs": "rust",
    ".c": "cpp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".h": "cpp",
    ".hpp": "cpp",
}

# (binary, args) for each language_id.
# These are the actual language-server invocations.
_LANG_SERVER_CMD: dict[str, list[str]] = {
    "python": ["pyright-langserver", "--stdio"],
    "go": ["gopls", "serve"],
    "typescript": ["typescript-language-server", "--stdio"],
    "rust": ["rust-analyzer"],
    "cpp": ["clangd"],
}


def _detect_language(file_path: str) -> str | None:
    """Return language_id for file_path based on extension, or None."""
    ext = Path(file_path).suffix.lower()
    return _EXT_TO_LANG.get(ext)


# ---------------------------------------------------------------------------
# Minimal LSP stdio JSON-RPC framer
# ---------------------------------------------------------------------------
# LSP uses HTTP-like Content-Length headers over stdio (no external dep needed).

class LspFramer:
    """Read/write LSP messages (Content-Length-framed JSON-RPC) over stdio."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self._proc = proc
        self._stdin = proc.stdin
        self._stdout = proc.stdout
        self._lock = threading.Lock()
        self._next_id = 1

    def next_id(self) -> int:
        with self._lock:
            _id = self._next_id
            self._next_id += 1
        return _id

    def send(self, msg: dict[str, Any]) -> None:
        """Send a JSON-RPC message to the language server."""
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._stdin.write(header + body)
        self._stdin.flush()

    def recv(self) -> dict[str, Any] | None:
        """Read one LSP message from the language server; returns None on EOF."""
        # Read headers
        headers: dict[str, str] = {}
        while True:
            raw = self._stdout.readline()
            if not raw:
                return None
            line = raw.decode("ascii", errors="replace").rstrip("\r\n")
            if not line:
                break  # blank line == end of headers
            if ":" in line:
                key, _, val = line.partition(":")
                headers[key.strip().lower()] = val.strip()

        length = int(headers.get("content-length", "0"))
        if length == 0:
            return None

        body = b""
        while len(body) < length:
            chunk = self._stdout.read(length - len(body))
            if not chunk:
                return None
            body += chunk

        return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# LSPSession — one language-server subprocess
# ---------------------------------------------------------------------------

class LSPSession:
    """Manages a single language-server subprocess (one per language_id)."""

    def __init__(self, language_id: str, workspace_root: Path) -> None:
        self.language_id = language_id
        self.workspace_root = workspace_root
        self._proc: subprocess.Popen | None = None
        self._framer: LspFramer | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._dead = False
        self._initialized = False
        self._open_files: set[str] = set()

    def is_alive(self) -> bool:
        if self._dead:
            return False
        if self._proc is not None and self._proc.poll() is not None:
            self._dead = True
            return False
        return self._proc is not None

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Spawn the language-server subprocess and perform LSP initialization."""
        cmd = _LANG_SERVER_CMD[self.language_id]

        # Check that the binary exists on PATH before attempting to spawn.
        if shutil.which(cmd[0]) is None:
            raise RuntimeError(
                f"LSP server binary not found on PATH: {cmd[0]!r}. "
                f"Install {self.language_id} language server to enable this language."
            )

        env = os.environ.copy()
        self._proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.workspace_root),
            env=env,
        )
        self._framer = LspFramer(self._proc)
        self._loop = loop
        self._dead = False

        # Start background reader thread.
        self._reader_thread = threading.Thread(
            target=self._read_loop,
            name=f"lsp-reader-{self.language_id}",
            daemon=True,
        )
        self._reader_thread.start()

        # Perform LSP initialize handshake synchronously (blocking).
        init_id = self._framer.next_id()
        root_uri = self.workspace_root.as_uri()

        self._framer.send({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "rootPath": str(self.workspace_root),
                "capabilities": {
                    "textDocument": {
                        "definition": {"linkSupport": False},
                        "references": {},
                        "hover": {"contentFormat": ["markdown", "plaintext"]},
                        "documentSymbol": {"hierarchicalDocumentSymbolSupport": True},
                    },
                    "workspace": {
                        "symbol": {},
                    },
                },
                "initializationOptions": None,
            },
        })

        # Wait for the initialize response (up to 30 s).
        deadline = 30
        import time
        start = time.monotonic()
        while time.monotonic() - start < deadline:
            msg = self._framer.recv()
            if msg is None:
                break
            if msg.get("id") == init_id and "result" in msg:
                break
            if msg.get("id") == init_id and "error" in msg:
                raise RuntimeError(f"LSP initialize failed for {self.language_id}: {msg['error']}")

        # Send initialized notification.
        self._framer.send({
            "jsonrpc": "2.0",
            "method": "initialized",
            "params": {},
        })
        self._initialized = True
        logger.info("[lsp] %s session initialized (pid %s)", self.language_id, self._proc.pid)

    def _read_loop(self) -> None:
        """Background thread: continuously read LSP responses and resolve futures."""
        assert self._framer is not None
        while True:
            try:
                msg = self._framer.recv()
            except Exception:
                msg = None

            if msg is None:
                # EOF or error — server died.
                self._dead = True
                # Fail all pending futures.
                if self._loop is not None and not self._loop.is_closed():
                    for fut in list(self._pending.values()):
                        if not fut.done():
                            self._loop.call_soon_threadsafe(
                                fut.set_exception,
                                RuntimeError(f"LSP server for {self.language_id} died unexpectedly"),
                            )
                self._pending.clear()
                return

            msg_id = msg.get("id")
            if msg_id is not None and msg_id in self._pending:
                fut = self._pending.pop(msg_id)
                if not fut.done():
                    if "error" in msg:
                        self._loop.call_soon_threadsafe(
                            fut.set_exception,
                            RuntimeError(f"LSP error: {msg['error']}"),
                        )
                    else:
                        self._loop.call_soon_threadsafe(
                            fut.set_result,
                            msg.get("result"),
                        )
            # Notifications (no id) are silently dropped.

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        """Send an LSP request and await the response."""
        if self._dead or not self._initialized:
            raise RuntimeError(f"LSP session for {self.language_id} is not live")

        assert self._framer is not None
        req_id = self._framer.next_id()

        fut: asyncio.Future = self._loop.create_future()
        self._pending[req_id] = fut

        self._framer.send({
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params,
        })

        try:
            return await asyncio.wait_for(fut, timeout=15.0)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise RuntimeError(f"LSP request {method!r} timed out for {self.language_id}")

    def _ensure_open(self, file_path: Path) -> None:
        """Send textDocument/didOpen if not already sent for this file."""
        uri = file_path.as_uri()
        if uri in self._open_files:
            return
        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise FileNotFoundError(f"Cannot read file for LSP: {file_path}") from e

        ext = file_path.suffix.lower()
        lang_id_map = {
            ".py": "python", ".go": "go",
            ".ts": "typescript", ".tsx": "typescriptreact",
            ".js": "javascript", ".jsx": "javascriptreact",
            ".rs": "rust",
            ".c": "c", ".h": "c",
            ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp",
        }
        lang_id = lang_id_map.get(ext, self.language_id)

        assert self._framer is not None
        self._framer.send({
            "jsonrpc": "2.0",
            "method": "textDocument/didOpen",
            "params": {
                "textDocument": {
                    "uri": uri,
                    "languageId": lang_id,
                    "version": 1,
                    "text": text,
                }
            },
        })
        self._open_files.add(uri)

    def _make_text_doc_params(self, file_path: Path, line: int, character: int) -> dict:
        return {
            "textDocument": {"uri": file_path.as_uri()},
            "position": {"line": line, "character": character},
        }

    async def definition(self, file_path: Path, line: int, character: int) -> Any:
        self._ensure_open(file_path)
        return await self._request(
            "textDocument/definition",
            self._make_text_doc_params(file_path, line, character),
        )

    async def references(self, file_path: Path, line: int, character: int, include_declaration: bool = False) -> Any:
        self._ensure_open(file_path)
        params = self._make_text_doc_params(file_path, line, character)
        params["context"] = {"includeDeclaration": include_declaration}
        return await self._request("textDocument/references", params)

    async def hover(self, file_path: Path, line: int, character: int) -> Any:
        self._ensure_open(file_path)
        return await self._request(
            "textDocument/hover",
            self._make_text_doc_params(file_path, line, character),
        )

    async def document_symbols(self, file_path: Path) -> Any:
        self._ensure_open(file_path)
        return await self._request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": file_path.as_uri()}},
        )

    async def workspace_symbols(self, query: str) -> Any:
        return await self._request("workspace/symbol", {"query": query})

    def shutdown(self) -> None:
        """Send LSP shutdown + exit to the server, then terminate."""
        if self._proc is None or self._dead:
            return
        try:
            if self._framer and self._proc.poll() is None:
                shutdown_id = self._framer.next_id()
                self._framer.send({"jsonrpc": "2.0", "id": shutdown_id, "method": "shutdown", "params": None})
                self._framer.send({"jsonrpc": "2.0", "method": "exit", "params": None})
        except Exception:
            pass
        finally:
            self._dead = True
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# LSP Multiplexer
# ---------------------------------------------------------------------------

class LSPMultiplexer:
    """Manages one LSPSession per language_id, started lazily on demand."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self._sessions: dict[str, LSPSession] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _resolve_path(self, file_path: str) -> Path:
        """Resolve file_path to an absolute path within the workspace."""
        p = Path(file_path)
        if not p.is_absolute():
            p = (self.workspace_root / p).resolve()
        else:
            p = p.resolve()
        # Security: ensure path is within workspace.
        try:
            p.relative_to(self.workspace_root.resolve())
        except ValueError:
            raise ValueError(f"Path escapes workspace root: {file_path!r}")
        if not p.exists():
            raise FileNotFoundError(f"File not found: {p}")
        return p

    def _get_or_start_session(self, language_id: str) -> LSPSession:
        """Return an existing live session, or create and start a new one."""
        session = self._sessions.get(language_id)
        if session is not None and session.is_alive():
            return session

        # Either never started or the previous session died — create a fresh one.
        new_session = LSPSession(language_id, self.workspace_root)
        new_session.start(self._loop)
        self._sessions[language_id] = new_session
        return new_session

    async def dispatch(self, method: str, file_path: str, **kwargs: Any) -> Any:
        """Dispatch an LSP call to the correct backend, starting it lazily.

        Security and availability errors (path escape, unknown extension, missing
        binary, dead session) are returned as ``{"error": "..."}`` dicts rather
        than raised exceptions so that the MCP tool surface stays stable.
        """
        lang = _detect_language(file_path)
        if lang is None:
            return {"error": f"No LSP backend for file extension: {Path(file_path).suffix!r}"}

        if lang not in _LANG_SERVER_CMD:
            return {"error": f"LSP server for {lang!r} is not configured"}

        try:
            resolved = self._resolve_path(file_path)
        except (ValueError, FileNotFoundError) as e:
            raise  # Security constraint — propagate path-escape errors to the caller.

        try:
            session = self._get_or_start_session(lang)
        except RuntimeError as e:
            return {"error": str(e)}

        try:
            if method == "definition":
                return await session.definition(resolved, kwargs["line"], kwargs["character"])
            elif method == "references":
                return await session.references(
                    resolved, kwargs["line"], kwargs["character"],
                    include_declaration=kwargs.get("include_declaration", False),
                )
            elif method == "hover":
                return await session.hover(resolved, kwargs["line"], kwargs["character"])
            elif method == "document_symbols":
                return await session.document_symbols(resolved)
            else:
                return {"error": f"Unknown method: {method!r}"}
        except RuntimeError as e:
            # Session may have died; mark dead and surface clean error.
            session._dead = True
            return {"error": str(e)}

    async def workspace_symbols(self, query: str) -> Any:
        """Query workspace symbols — picks the first live session (or Python as default)."""
        # Attempt in order of preference; return first success.
        errors = []
        for lang in ("python", "typescript", "go", "rust", "cpp"):
            if lang not in _LANG_SERVER_CMD:
                continue
            try:
                session = self._get_or_start_session(lang)
                result = await session.workspace_symbols(query)
                return result
            except RuntimeError as e:
                errors.append(str(e))
                continue

        return {"error": f"No LSP backend available for workspace symbol search. Errors: {errors}"}

    def shutdown_all(self) -> None:
        for session in self._sessions.values():
            try:
                session.shutdown()
            except Exception:
                pass
        self._sessions.clear()


# ---------------------------------------------------------------------------
# Location / symbol formatting helpers
# ---------------------------------------------------------------------------

def _format_location(loc: dict[str, Any]) -> dict[str, Any]:
    """Normalize an LSP Location to a clean dict."""
    if not isinstance(loc, dict):
        return {}
    uri = loc.get("uri", "")
    r = loc.get("range", {})
    start = r.get("start", {})
    end = r.get("end", {})
    return {
        "uri": uri,
        "start": {"line": start.get("line", 0), "character": start.get("character", 0)},
        "end": {"line": end.get("line", 0), "character": end.get("character", 0)},
    }


def _format_locations(result: Any) -> str:
    if result is None:
        return json.dumps([])
    if isinstance(result, dict):
        # Could be a single Location or an error.
        if "error" in result:
            return json.dumps(result)
        return json.dumps([_format_location(result)])
    if isinstance(result, list):
        out = []
        for item in result:
            if isinstance(item, dict):
                # Handle LocationLink too.
                if "targetUri" in item:
                    out.append({
                        "uri": item["targetUri"],
                        "start": item.get("targetSelectionRange", item.get("targetRange", {})).get("start", {}),
                        "end": item.get("targetSelectionRange", item.get("targetRange", {})).get("end", {}),
                    })
                else:
                    out.append(_format_location(item))
        return json.dumps(out)
    return json.dumps(result)


def _format_hover(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, dict):
        if "error" in result:
            return json.dumps(result)
        contents = result.get("contents", "")
        if isinstance(contents, dict):
            return contents.get("value", "")
        if isinstance(contents, list):
            parts = []
            for c in contents:
                if isinstance(c, dict):
                    parts.append(c.get("value", ""))
                elif isinstance(c, str):
                    parts.append(c)
            return "\n\n".join(parts)
        return str(contents)
    return str(result)


def _format_symbols(result: Any) -> str:
    """Recursively format document/workspace symbols."""
    if result is None:
        return json.dumps([])
    if isinstance(result, dict) and "error" in result:
        return json.dumps(result)

    def _sym(s: dict) -> dict:
        out: dict[str, Any] = {
            "name": s.get("name", ""),
            "kind": s.get("kind", 0),
        }
        loc = s.get("location")
        if loc:
            out["location"] = _format_location(loc)
        sel_range = s.get("selectionRange") or s.get("range")
        if sel_range:
            out["range"] = sel_range
        children = s.get("children")
        if children:
            out["children"] = [_sym(c) for c in children]
        container = s.get("containerName")
        if container:
            out["containerName"] = container
        return out

    if isinstance(result, list):
        return json.dumps([_sym(s) for s in result])
    return json.dumps(result)


# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

def build_server(workspace_root: Path) -> tuple[Server, LSPMultiplexer]:
    mux = LSPMultiplexer(workspace_root)
    app = Server("lsp-multiplexer")

    @app.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="definition",
                description=(
                    "Go to the definition of the symbol at the given position in a file. "
                    "Returns a list of locations (URI + range). "
                    "Supports: Python, Go, TypeScript/JavaScript, Rust, C/C++."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "File path (relative to workspace root or absolute)."},
                        "line": {"type": "integer", "description": "Zero-based line number."},
                        "character": {"type": "integer", "description": "Zero-based character offset."},
                    },
                    "required": ["file_path", "line", "character"],
                },
            ),
            types.Tool(
                name="references",
                description=(
                    "Find all references to the symbol at the given position. "
                    "Returns a list of locations. "
                    "Supports: Python, Go, TypeScript/JavaScript, Rust, C/C++."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "File path (relative to workspace root or absolute)."},
                        "line": {"type": "integer", "description": "Zero-based line number."},
                        "character": {"type": "integer", "description": "Zero-based character offset."},
                        "include_declaration": {"type": "boolean", "description": "Include the declaration site. Default false.", "default": False},
                    },
                    "required": ["file_path", "line", "character"],
                },
            ),
            types.Tool(
                name="hover",
                description=(
                    "Get hover documentation / type signature for the symbol at the given position. "
                    "Returns a markdown string. "
                    "Supports: Python, Go, TypeScript/JavaScript, Rust, C/C++."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "File path (relative to workspace root or absolute)."},
                        "line": {"type": "integer", "description": "Zero-based line number."},
                        "character": {"type": "integer", "description": "Zero-based character offset."},
                    },
                    "required": ["file_path", "line", "character"],
                },
            ),
            types.Tool(
                name="document_symbols",
                description=(
                    "List all symbols (functions, classes, variables, …) defined in a file "
                    "as a hierarchical list. "
                    "Supports: Python, Go, TypeScript/JavaScript, Rust, C/C++."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "file_path": {"type": "string", "description": "File path (relative to workspace root or absolute)."},
                    },
                    "required": ["file_path"],
                },
            ),
            types.Tool(
                name="workspace_symbols",
                description=(
                    "Search for symbols matching a query string across the entire workspace. "
                    "Returns a list of matching symbol locations. "
                    "Uses the first available language server."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Symbol name or partial name to search for."},
                    },
                    "required": ["query"],
                },
            ),
        ]

    @app.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        # Wire up the event loop the first time a tool is called — at this point
        # we're guaranteed to be inside the running asyncio event loop.
        if mux._loop is None:
            mux.set_loop(asyncio.get_running_loop())
        try:
            result_text = await _dispatch_async(name, arguments, mux)
        except Exception as exc:  # noqa: BLE001
            result_text = json.dumps({"error": str(exc)})
        return [types.TextContent(type="text", text=result_text)]

    return app, mux


async def _dispatch_async(name: str, args: dict, mux: LSPMultiplexer) -> str:
    if name == "definition":
        result = await mux.dispatch(
            "definition",
            args["file_path"],
            line=int(args["line"]),
            character=int(args["character"]),
        )
        return _format_locations(result)

    if name == "references":
        result = await mux.dispatch(
            "references",
            args["file_path"],
            line=int(args["line"]),
            character=int(args["character"]),
            include_declaration=bool(args.get("include_declaration", False)),
        )
        return _format_locations(result)

    if name == "hover":
        result = await mux.dispatch(
            "hover",
            args["file_path"],
            line=int(args["line"]),
            character=int(args["character"]),
        )
        return _format_hover(result)

    if name == "document_symbols":
        result = await mux.dispatch("document_symbols", args["file_path"])
        return _format_symbols(result)

    if name == "workspace_symbols":
        result = await mux.workspace_symbols(args["query"])
        return _format_symbols(result)

    raise ValueError(f"Unknown tool: {name!r}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="LSP multiplexer MCP server")
    parser.add_argument(
        "--workspace",
        required=True,
        help="Workspace root path (the repository root to analyse).",
    )
    argv = parser.parse_args()

    workspace_root = Path(argv.workspace).resolve()
    if not workspace_root.is_dir():
        print(f"Error: {workspace_root} is not a directory", file=sys.stderr)
        sys.exit(1)

    app, mux = build_server(workspace_root)

    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        mux.shutdown_all()


if __name__ == "__main__":
    asyncio.run(main())
