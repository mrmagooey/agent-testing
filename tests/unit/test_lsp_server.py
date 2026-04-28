"""Unit tests for the LSP multiplexer server helpers.

Tests focus on the pure-Python helpers (language detection, location/symbol
formatting) that don't require starting a subprocess. The JSON-RPC framer
is tested via a mock process object.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

class TestDetectLanguage:
    def setup_method(self) -> None:
        from sec_review_framework.tools.extensions.lsp_server import _detect_language
        self._detect = _detect_language

    def test_python(self) -> None:
        assert self._detect("src/auth.py") == "python"

    def test_go(self) -> None:
        assert self._detect("cmd/main.go") == "go"

    def test_typescript(self) -> None:
        assert self._detect("app.ts") == "typescript"
        assert self._detect("component.tsx") == "typescript"

    def test_javascript(self) -> None:
        assert self._detect("index.js") == "typescript"
        assert self._detect("helper.jsx") == "typescript"

    def test_rust(self) -> None:
        assert self._detect("src/lib.rs") == "rust"

    def test_c_variants(self) -> None:
        assert self._detect("main.c") == "cpp"
        assert self._detect("util.h") == "cpp"
        assert self._detect("impl.cpp") == "cpp"
        assert self._detect("impl.cc") == "cpp"
        assert self._detect("impl.hpp") == "cpp"

    def test_java(self) -> None:
        assert self._detect("src/Main.java") == "java"
        assert self._detect("com/example/Foo.java") == "java"

    def test_unknown_returns_none(self) -> None:
        assert self._detect("README.md") is None
        assert self._detect("Makefile") is None
        assert self._detect("data.json") is None


# ---------------------------------------------------------------------------
# Java language-server command
# ---------------------------------------------------------------------------

class TestJavaLangServerCmd:
    def test_java_cmd_lookup(self) -> None:
        from sec_review_framework.tools.extensions.lsp_server import _LANG_SERVER_CMD

        assert "java" in _LANG_SERVER_CMD
        cmd = _LANG_SERVER_CMD["java"]
        assert cmd[0] == "jdtls"
        assert "-data" in cmd
        assert "/tmp/jdtls-workspace" in cmd

    def test_java_lang_id_in_ensure_open_map(self) -> None:
        """The lang_id_map inside _ensure_open must include .java → 'java'."""
        import inspect
        from sec_review_framework.tools.extensions import lsp_server as mod

        src = inspect.getsource(mod.LSPSession._ensure_open)
        assert '".java": "java"' in src or "'.java': 'java'" in src

    def test_java_in_workspace_symbols_fallback(self) -> None:
        """'java' must appear in the workspace_symbols fallback language sequence."""
        import inspect
        from sec_review_framework.tools.extensions import lsp_server as mod

        src = inspect.getsource(mod.LSPMultiplexer.workspace_symbols)
        assert '"java"' in src or "'java'" in src


# ---------------------------------------------------------------------------
# Location formatting
# ---------------------------------------------------------------------------

class TestFormatLocations:
    def setup_method(self) -> None:
        from sec_review_framework.tools.extensions.lsp_server import _format_locations
        self._fmt = _format_locations

    def test_none_returns_empty_list(self) -> None:
        result = json.loads(self._fmt(None))
        assert result == []

    def test_single_location_dict(self) -> None:
        loc = {
            "uri": "file:///repo/src/auth.py",
            "range": {
                "start": {"line": 10, "character": 4},
                "end": {"line": 10, "character": 15},
            },
        }
        result = json.loads(self._fmt(loc))
        assert len(result) == 1
        assert result[0]["uri"] == "file:///repo/src/auth.py"
        assert result[0]["start"]["line"] == 10

    def test_list_of_locations(self) -> None:
        locs = [
            {
                "uri": "file:///repo/a.py",
                "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 5}},
            },
            {
                "uri": "file:///repo/b.py",
                "range": {"start": {"line": 5, "character": 0}, "end": {"line": 5, "character": 5}},
            },
        ]
        result = json.loads(self._fmt(locs))
        assert len(result) == 2
        assert result[0]["uri"] == "file:///repo/a.py"
        assert result[1]["uri"] == "file:///repo/b.py"

    def test_location_link_with_target_uri(self) -> None:
        loc_link = {
            "originSelectionRange": {"start": {"line": 2, "character": 4}, "end": {"line": 2, "character": 10}},
            "targetUri": "file:///repo/target.py",
            "targetRange": {"start": {"line": 20, "character": 0}, "end": {"line": 25, "character": 0}},
            "targetSelectionRange": {"start": {"line": 20, "character": 4}, "end": {"line": 20, "character": 10}},
        }
        result = json.loads(self._fmt([loc_link]))
        assert len(result) == 1
        assert result[0]["uri"] == "file:///repo/target.py"

    def test_error_dict_passthrough(self) -> None:
        error = {"error": "LSP server for cpp not available"}
        result = json.loads(self._fmt(error))
        assert "error" in result


# ---------------------------------------------------------------------------
# Hover formatting
# ---------------------------------------------------------------------------

class TestFormatHover:
    def setup_method(self) -> None:
        from sec_review_framework.tools.extensions.lsp_server import _format_hover
        self._fmt = _format_hover

    def test_none_returns_empty_string(self) -> None:
        assert self._fmt(None) == ""

    def test_marked_string_content(self) -> None:
        result = self._fmt({"contents": {"kind": "markdown", "value": "```python\ndef foo(): ...\n```"}})
        assert "def foo" in result

    def test_list_of_strings(self) -> None:
        result = self._fmt({"contents": ["first", "second"]})
        assert "first" in result
        assert "second" in result

    def test_list_of_marked_strings(self) -> None:
        result = self._fmt({"contents": [{"language": "python", "value": "def bar(): ..."}, "Some doc"]})
        assert "def bar" in result
        assert "Some doc" in result

    def test_error_passthrough(self) -> None:
        result = self._fmt({"error": "timed out"})
        assert "timed out" in result

    def test_plain_string_contents(self) -> None:
        result = self._fmt({"contents": "plain text doc"})
        assert result == "plain text doc"


# ---------------------------------------------------------------------------
# Symbol formatting
# ---------------------------------------------------------------------------

class TestFormatSymbols:
    def setup_method(self) -> None:
        from sec_review_framework.tools.extensions.lsp_server import _format_symbols
        self._fmt = _format_symbols

    def test_none_returns_empty_list(self) -> None:
        result = json.loads(self._fmt(None))
        assert result == []

    def test_flat_symbols(self) -> None:
        symbols = [
            {"name": "helper", "kind": 12, "location": {
                "uri": "file:///repo/app.py",
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 0}},
            }},
            {"name": "caller", "kind": 12, "location": {
                "uri": "file:///repo/app.py",
                "range": {"start": {"line": 3, "character": 0}, "end": {"line": 4, "character": 0}},
            }},
        ]
        result = json.loads(self._fmt(symbols))
        names = {s["name"] for s in result}
        assert "helper" in names
        assert "caller" in names

    def test_hierarchical_symbols_with_children(self) -> None:
        symbols = [
            {
                "name": "MyClass",
                "kind": 5,
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 10, "character": 0}},
                "selectionRange": {"start": {"line": 0, "character": 6}, "end": {"line": 0, "character": 13}},
                "children": [
                    {
                        "name": "__init__",
                        "kind": 6,
                        "range": {"start": {"line": 1, "character": 4}, "end": {"line": 3, "character": 0}},
                        "selectionRange": {"start": {"line": 1, "character": 8}, "end": {"line": 1, "character": 16}},
                    }
                ],
            }
        ]
        result = json.loads(self._fmt(symbols))
        assert result[0]["name"] == "MyClass"
        assert len(result[0]["children"]) == 1
        assert result[0]["children"][0]["name"] == "__init__"

    def test_error_passthrough(self) -> None:
        result = json.loads(self._fmt({"error": "not available"}))
        assert "error" in result


# ---------------------------------------------------------------------------
# LspFramer — JSON-RPC encoding/decoding with a mock process
# ---------------------------------------------------------------------------

class TestLspFramer:
    """Test the Content-Length framer against a mock stdin/stdout."""

    def _make_proc(self, response_bytes: bytes) -> MagicMock:
        """Create a mock Popen process with a pre-filled stdout buffer."""
        proc = MagicMock()
        proc.stdin = io.BytesIO()
        proc.stdout = io.BytesIO(response_bytes)
        return proc

    def _encode_lsp(self, msg: dict) -> bytes:
        body = json.dumps(msg).encode("utf-8")
        return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body

    def test_recv_parses_content_length_message(self) -> None:
        from sec_review_framework.tools.extensions.lsp_server import LspFramer

        response = {"jsonrpc": "2.0", "id": 1, "result": {"capabilities": {}}}
        proc = self._make_proc(self._encode_lsp(response))
        framer = LspFramer(proc)

        msg = framer.recv()
        assert msg is not None
        assert msg["id"] == 1
        assert "result" in msg

    def test_recv_returns_none_on_eof(self) -> None:
        from sec_review_framework.tools.extensions.lsp_server import LspFramer

        proc = self._make_proc(b"")
        framer = LspFramer(proc)

        msg = framer.recv()
        assert msg is None

    def test_send_writes_content_length_header(self) -> None:
        from sec_review_framework.tools.extensions.lsp_server import LspFramer

        buf = io.BytesIO()
        proc = MagicMock()
        proc.stdin = buf
        proc.stdout = io.BytesIO(b"")
        framer = LspFramer(proc)

        framer.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})

        written = buf.getvalue()
        assert b"Content-Length:" in written
        assert b"jsonrpc" in written

    def test_next_id_increments(self) -> None:
        from sec_review_framework.tools.extensions.lsp_server import LspFramer

        proc = MagicMock()
        proc.stdin = io.BytesIO()
        proc.stdout = io.BytesIO(b"")
        framer = LspFramer(proc)

        id1 = framer.next_id()
        id2 = framer.next_id()
        id3 = framer.next_id()
        assert id1 < id2 < id3

    def test_recv_multiple_messages(self) -> None:
        from sec_review_framework.tools.extensions.lsp_server import LspFramer

        msg1 = {"jsonrpc": "2.0", "id": 1, "result": "first"}
        msg2 = {"jsonrpc": "2.0", "id": 2, "result": "second"}
        raw = self._encode_lsp(msg1) + self._encode_lsp(msg2)
        proc = self._make_proc(raw)
        framer = LspFramer(proc)

        r1 = framer.recv()
        r2 = framer.recv()
        assert r1["id"] == 1
        assert r2["id"] == 2


# ---------------------------------------------------------------------------
# LSPMultiplexer — language dispatch / error handling (no real subprocess)
# ---------------------------------------------------------------------------

class TestLspMultiplexerDispatch:
    def test_unknown_extension_returns_error(self, tmp_path: Path) -> None:
        import asyncio

        from sec_review_framework.tools.extensions.lsp_server import LSPMultiplexer

        mux = LSPMultiplexer(tmp_path)
        mux.set_loop(asyncio.new_event_loop())

        # "data.json" has no LSP backend.
        result = asyncio.get_event_loop().run_until_complete(
            mux.dispatch("definition", "data.json", line=0, character=0)
        ) if False else None

        # Synchronous check via a small helper coroutine.
        async def _check():
            return await mux.dispatch("definition", "data.json", line=0, character=0)

        loop = asyncio.new_event_loop()
        mux.set_loop(loop)
        result = loop.run_until_complete(_check())
        loop.close()

        assert isinstance(result, dict)
        assert "error" in result

    def test_path_escape_raises(self, tmp_path: Path) -> None:
        """A file path with a recognized extension that escapes the workspace root
        must raise ValueError rather than silently serving the file."""
        import asyncio

        from sec_review_framework.tools.extensions.lsp_server import LSPMultiplexer

        mux = LSPMultiplexer(tmp_path)

        async def _check():
            # Use a .py extension so language detection succeeds, but traverse
            # above the workspace root to trigger the security check.
            return await mux.dispatch("document_symbols", "../../sneaky.py")

        loop = asyncio.new_event_loop()
        mux.set_loop(loop)
        try:
            with pytest.raises(ValueError, match="escapes workspace root"):
                loop.run_until_complete(_check())
        finally:
            loop.close()

    def test_binary_not_on_path_returns_error(self, tmp_path: Path) -> None:
        """If a language-server binary is missing, the error is surfaced cleanly."""
        import asyncio

        import sec_review_framework.tools.extensions.lsp_server as lsp_mod
        from sec_review_framework.tools.extensions.lsp_server import LSPMultiplexer

        py_file = tmp_path / "main.py"
        py_file.write_text("def foo(): pass\n")

        mux = LSPMultiplexer(tmp_path)

        async def _check():
            # Patch shutil at module level in lsp_server so which() returns None.
            with patch.object(lsp_mod.shutil, "which", return_value=None):
                return await mux.dispatch("document_symbols", "main.py")

        loop = asyncio.new_event_loop()
        mux.set_loop(loop)
        result = loop.run_until_complete(_check())
        loop.close()

        assert isinstance(result, dict)
        assert "error" in result
