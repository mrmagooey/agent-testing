"""Tests for ReadFileTool, ListDirectoryTool, GrepTool, and ToolRegistry."""

from __future__ import annotations

from pathlib import Path

import pytest

from sec_review_framework.tools.registry import Tool, ToolDefinition, ToolRegistry
from sec_review_framework.tools.repo_access import (
    GrepTool,
    ListDirectoryTool,
    ReadFileTool,
)

_READ_SIZE_LIMIT = 50 * 1024  # 50 KB, same as source


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal repository layout with two Python files."""
    (tmp_path / "subdir").mkdir()
    (tmp_path / "hello.py").write_text("print('hello')\n")
    (tmp_path / "subdir" / "world.py").write_text("x = 1\n")
    return tmp_path


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------


def test_read_file_existing_file(repo: Path):
    tool = ReadFileTool(repo)
    result = tool.invoke({"path": "hello.py"})
    assert "print('hello')" in result


def test_read_file_not_found_returns_error_string(repo: Path):
    tool = ReadFileTool(repo)
    result = tool.invoke({"path": "nonexistent.py"})
    assert "Error" in result
    assert "nonexistent.py" in result


def test_read_file_binary_content_handled_with_replace(repo: Path):
    """Binary content that is not valid UTF-8 is decoded with 'replace'."""
    binary_file = repo / "binary.bin"
    binary_file.write_bytes(b"\x80\x81\x82hello")
    tool = ReadFileTool(repo)
    result = tool.invoke({"path": "binary.bin"})
    assert "hello" in result
    # Should not raise — replacement characters may appear but no exception.


def test_read_file_large_file_truncated(repo: Path):
    """Files larger than 50 KB are truncated and annotated."""
    big_file = repo / "big.txt"
    big_file.write_bytes(b"A" * (_READ_SIZE_LIMIT + 1000))
    tool = ReadFileTool(repo)
    result = tool.invoke({"path": "big.txt"})
    assert "[output truncated]" in result
    assert len(result) < _READ_SIZE_LIMIT + 200  # not unbounded


# ---------------------------------------------------------------------------
# ListDirectoryTool
# ---------------------------------------------------------------------------


def test_list_directory_lists_files_and_dirs(repo: Path):
    tool = ListDirectoryTool(repo)
    result = tool.invoke({"path": "."})
    assert "hello.py" in result
    assert "subdir" in result


def test_list_directory_empty_dir(repo: Path):
    empty = repo / "empty_dir"
    empty.mkdir()
    tool = ListDirectoryTool(repo)
    result = tool.invoke({"path": "empty_dir"})
    assert "empty" in result.lower()


def test_list_directory_dirs_listed_before_files(repo: Path):
    """Directories should appear before regular files in the listing."""
    tool = ListDirectoryTool(repo)
    result = tool.invoke({"path": "."})
    lines = [ln.strip() for ln in result.splitlines() if ln.strip()]
    dir_indices = [i for i, ln in enumerate(lines) if ln.startswith("/")]
    file_indices = [i for i, ln in enumerate(lines) if not ln.startswith("/")]
    if dir_indices and file_indices:
        # All dir entries should come before all file entries.
        assert max(dir_indices) < min(file_indices)


# ---------------------------------------------------------------------------
# GrepTool
# ---------------------------------------------------------------------------


def test_grep_finds_pattern_matches(repo: Path):
    tool = GrepTool(repo)
    result = tool.invoke({"pattern": "print"})
    assert "hello.py" in result
    assert "print" in result


def test_grep_no_matches_returns_message(repo: Path):
    tool = GrepTool(repo)
    result = tool.invoke({"pattern": "DEFINITELY_NOT_IN_ANY_FILE_XYZ123"})
    assert "No matches" in result or "no matches" in result.lower()


def test_grep_max_results_limit(repo: Path):
    """max_results limits the number of matching lines returned."""
    # Create a file with many matching lines.
    many = repo / "many.py"
    many.write_text("\n".join([f"match_{i}" for i in range(100)]))
    tool = GrepTool(repo)
    result = tool.invoke({"pattern": "match_", "max_results": 5})
    # Should have truncation notice or a small number of lines.
    lines = [ln for ln in result.splitlines() if "match_" in ln]
    assert len(lines) <= 5 or "[output truncated]" in result


def test_grep_repo_prefix_stripped_from_output(repo: Path):
    """Absolute repo path prefix is stripped from grep output."""
    tool = GrepTool(repo)
    result = tool.invoke({"pattern": "print"})
    # The absolute path of repo should not appear in match lines.
    assert str(repo) not in result


# ---------------------------------------------------------------------------
# ToolRegistry
# ---------------------------------------------------------------------------


class _DummyTool(Tool):
    def definition(self) -> ToolDefinition:
        return ToolDefinition(name="dummy", description="dummy", input_schema={"type": "object", "properties": {}})

    def invoke(self, input: dict) -> str:
        return "dummy_result"


def test_tool_registry_get_tool_definitions_returns_all_registered():
    registry = ToolRegistry()
    registry.tools["dummy"] = _DummyTool()
    defs = registry.get_tool_definitions()
    assert len(defs) == 1
    assert defs[0].name == "dummy"


def test_tool_registry_invoke_unknown_tool_raises_value_error():
    registry = ToolRegistry()
    with pytest.raises(ValueError, match="Unknown tool"):
        registry.invoke("nonexistent_tool", {}, "call-001")


def test_tool_registry_clone_shares_tools_fresh_audit_log():
    """clone() shares the same tool instances but creates an independent audit log."""
    tool = _DummyTool()
    registry = ToolRegistry()
    registry.tools["dummy"] = tool

    # Invoke once to populate the original log.
    registry.invoke("dummy", {}, "original-call")
    assert len(registry.audit_log.entries) == 1

    cloned = registry.clone()
    # Cloned has same tools but empty log.
    assert "dummy" in cloned.tools
    assert cloned.tools["dummy"] is tool  # shared reference
    assert len(cloned.audit_log.entries) == 0

    # Invoking on clone does not affect original's log.
    cloned.invoke("dummy", {}, "clone-call")
    assert len(registry.audit_log.entries) == 1  # original unchanged
    assert len(cloned.audit_log.entries) == 1
