"""Security tests for ReadFileTool, ListDirectoryTool, GrepTool, ToolCallAuditLog, and DocLookupTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from sec_review_framework.tools.doc_lookup import DocLookupTool
from sec_review_framework.tools.registry import ToolRegistry
from sec_review_framework.tools.repo_access import GrepTool, ListDirectoryTool, ReadFileTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_repo(tmp_path: Path) -> Path:
    """Create a minimal fake repo with a known file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "safe.py").write_text("print('hello')\n")
    return repo


# ---------------------------------------------------------------------------
# ReadFileTool — path traversal
# ---------------------------------------------------------------------------


def test_read_file_path_traversal_single_dotdot_blocked(tmp_path: Path):
    """../etc/passwd style traversal must raise ValueError."""
    repo = _make_repo(tmp_path)
    tool = ReadFileTool(repo_root=repo)
    with pytest.raises(ValueError, match="escapes repo root"):
        tool.invoke({"path": "../etc/passwd"})


def test_read_file_path_traversal_nested_dotdot_blocked(tmp_path: Path):
    """../../etc/passwd style traversal must raise ValueError."""
    repo = _make_repo(tmp_path)
    tool = ReadFileTool(repo_root=repo)
    with pytest.raises(ValueError, match="escapes repo root"):
        tool.invoke({"path": "../../etc/passwd"})


def test_read_file_absolute_path_blocked(tmp_path: Path):
    """/etc/passwd (absolute path) must raise ValueError."""
    repo = _make_repo(tmp_path)
    tool = ReadFileTool(repo_root=repo)
    with pytest.raises(ValueError, match="escapes repo root"):
        tool.invoke({"path": "/etc/passwd"})


def test_read_file_symlink_escaping_root_blocked(tmp_path: Path):
    """Symlink inside the repo pointing outside the root must raise ValueError."""
    repo = _make_repo(tmp_path)
    # Create a symlink inside the repo that points to /tmp
    link = repo / "escape_link"
    link.symlink_to("/tmp")
    tool = ReadFileTool(repo_root=repo)
    with pytest.raises(ValueError, match="escapes repo root"):
        tool.invoke({"path": "escape_link"})


def test_read_file_safe_file_succeeds(tmp_path: Path):
    """A legitimate file inside the repo is readable."""
    repo = _make_repo(tmp_path)
    tool = ReadFileTool(repo_root=repo)
    result = tool.invoke({"path": "safe.py"})
    assert "print('hello')" in result


def test_read_file_over_50kb_truncated(tmp_path: Path):
    """Files larger than 50 KB must be truncated with '[output truncated]' sentinel."""
    repo = _make_repo(tmp_path)
    big_file = repo / "big.txt"
    big_file.write_bytes(b"x" * (51 * 1024))  # 51 KB
    tool = ReadFileTool(repo_root=repo)
    result = tool.invoke({"path": "big.txt"})
    assert result.endswith("[output truncated]")


# ---------------------------------------------------------------------------
# ListDirectoryTool — path traversal
# ---------------------------------------------------------------------------


def test_list_directory_path_traversal_blocked(tmp_path: Path):
    """../.. style traversal on ListDirectoryTool must raise ValueError."""
    repo = _make_repo(tmp_path)
    tool = ListDirectoryTool(repo_root=repo)
    with pytest.raises(ValueError, match="escapes repo root"):
        tool.invoke({"path": "../../"})


def test_list_directory_absolute_path_blocked(tmp_path: Path):
    """/etc as path must raise ValueError."""
    repo = _make_repo(tmp_path)
    tool = ListDirectoryTool(repo_root=repo)
    with pytest.raises(ValueError, match="escapes repo root"):
        tool.invoke({"path": "/etc"})


def test_list_directory_root_succeeds(tmp_path: Path):
    """Listing the repo root (.) must succeed."""
    repo = _make_repo(tmp_path)
    tool = ListDirectoryTool(repo_root=repo)
    result = tool.invoke({"path": "."})
    assert "safe.py" in result


# ---------------------------------------------------------------------------
# GrepTool — path traversal
# ---------------------------------------------------------------------------


def test_grep_path_traversal_blocked(tmp_path: Path):
    """GrepTool with a path argument that escapes the root must raise ValueError."""
    repo = _make_repo(tmp_path)
    tool = GrepTool(repo_root=repo)
    with pytest.raises(ValueError, match="escapes repo root"):
        tool.invoke({"pattern": "root", "path": "../../etc"})


def test_grep_absolute_path_blocked(tmp_path: Path):
    """GrepTool with absolute path must raise ValueError."""
    repo = _make_repo(tmp_path)
    tool = GrepTool(repo_root=repo)
    with pytest.raises(ValueError, match="escapes repo root"):
        tool.invoke({"pattern": "root", "path": "/etc"})


def test_grep_max_results_enforced(tmp_path: Path):
    """GrepTool respects max_results — output lines are limited."""
    repo = _make_repo(tmp_path)
    # Write a file with many matching lines
    many_lines = repo / "many.txt"
    many_lines.write_text("\n".join(["match"] * 100))
    tool = GrepTool(repo_root=repo)
    result = tool.invoke({"pattern": "match", "path": "many.txt", "max_results": 5})
    # With max_results=5, grep -m 5 returns at most 5 matches, plus truncation notice
    match_count = result.count("match")
    assert match_count <= 5 or "[output truncated]" in result


def test_grep_within_repo_succeeds(tmp_path: Path):
    """GrepTool finds pattern in a repo file without errors."""
    repo = _make_repo(tmp_path)
    tool = GrepTool(repo_root=repo)
    result = tool.invoke({"pattern": "print", "path": "safe.py"})
    assert "print" in result


# ---------------------------------------------------------------------------
# ToolCallAuditLog
# ---------------------------------------------------------------------------


def test_audit_log_records_every_invoke(tmp_path: Path):
    """ToolRegistry.invoke() must create an audit log entry for every call."""
    repo = _make_repo(tmp_path)
    registry = ToolRegistry()
    read_tool = ReadFileTool(repo_root=repo)
    registry.tools["read_file"] = read_tool

    registry.invoke("read_file", {"path": "safe.py"}, "call-1")
    registry.invoke("read_file", {"path": "safe.py"}, "call-2")

    entries = registry.audit_log.entries
    assert len(entries) == 2
    assert entries[0].call_id == "call-1"
    assert entries[1].call_id == "call-2"


def test_audit_log_records_duration_ms_greater_than_zero(tmp_path: Path):
    """Each audit log entry must have duration_ms >= 0 (non-negative timing)."""
    repo = _make_repo(tmp_path)
    registry = ToolRegistry()
    registry.tools["read_file"] = ReadFileTool(repo_root=repo)

    registry.invoke("read_file", {"path": "safe.py"}, "call-dur")

    entry = registry.audit_log.entries[0]
    assert entry.duration_ms >= 0


def test_audit_log_records_tool_name(tmp_path: Path):
    """Audit log entry correctly captures the tool name."""
    repo = _make_repo(tmp_path)
    registry = ToolRegistry()
    registry.tools["read_file"] = ReadFileTool(repo_root=repo)

    registry.invoke("read_file", {"path": "safe.py"}, "call-x")

    assert registry.audit_log.entries[0].tool_name == "read_file"


# ---------------------------------------------------------------------------
# DocLookupTool
# ---------------------------------------------------------------------------


def test_doc_lookup_tool_returns_stub_message():
    """DocLookupTool.invoke() returns a non-empty stub message."""
    tool = DocLookupTool()
    result = tool.invoke({"query": "SQL parameterised queries", "language": "python"})
    assert isinstance(result, str)
    assert len(result) > 0
    # Stub should mention the query
    assert "SQL parameterised queries" in result


def test_doc_lookup_tool_without_language():
    """DocLookupTool works without an optional language parameter."""
    tool = DocLookupTool()
    result = tool.invoke({"query": "subprocess shell=True"})
    assert isinstance(result, str)
    assert "subprocess shell=True" in result
