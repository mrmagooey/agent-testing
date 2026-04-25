"""Tests for SemgrepTool and SASTMatch (tools/semgrep.py)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sec_review_framework.tools.semgrep import SASTMatch, SemgrepBinaryNotFoundError, SemgrepTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _semgrep_json(results: list[dict]) -> str:
    """Produce semgrep --json output with the given results list."""
    return json.dumps({"results": results, "errors": []})


def _make_result(
    path: str = "app/views.py",
    line_start: int = 10,
    line_end: int = 12,
    check_id: str = "python.lang.security.sqli",
    message: str = "SQL injection risk",
    severity: str = "ERROR",
) -> dict:
    return {
        "path": path,
        "start": {"line": line_start, "col": 1},
        "end": {"line": line_end, "col": 10},
        "check_id": check_id,
        "extra": {"message": message, "severity": severity},
    }


def _mock_proc(stdout: str = "", returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = ""
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# SemgrepTool.invoke
# ---------------------------------------------------------------------------


def test_invoke_parses_json_output_into_formatted_text(tmp_path: Path):
    tool = SemgrepTool(repo_path=tmp_path)
    payload = _semgrep_json([_make_result()])

    with patch("subprocess.run", return_value=_mock_proc(stdout=payload, returncode=0)):
        result = tool.invoke({"path": "."})

    assert "python.lang.security.sqli" in result
    assert "SQL injection risk" in result


def test_invoke_no_findings_returns_message(tmp_path: Path):
    tool = SemgrepTool(repo_path=tmp_path)
    payload = _semgrep_json([])

    with patch("subprocess.run", return_value=_mock_proc(stdout=payload, returncode=0)):
        result = tool.invoke({"path": "."})

    assert "no issues" in result.lower() or "no findings" in result.lower() or "Semgrep found no issues" in result


def test_invoke_path_escape_blocked(tmp_path: Path):
    tool = SemgrepTool(repo_path=tmp_path)
    result = tool.invoke({"path": "../../../etc/passwd"})
    assert "Error" in result
    assert "escapes" in result


def test_invoke_returns_error_string_when_binary_missing(tmp_path: Path):
    """invoke() must return an error string (not raise) when the semgrep binary is absent.

    The Tool.invoke() contract requires a str return so the LLM sees a clean
    tool-result message rather than an unhandled exception aborting the run.
    """
    tool = SemgrepTool(repo_path=tmp_path)
    with patch("subprocess.run", side_effect=FileNotFoundError):
        result = tool.invoke({"path": "."})
    assert isinstance(result, str), "invoke() must return str, not raise"
    assert "not found" in result.lower() or "unavailable" in result.lower()


def test_semgrep_binary_not_found_error_is_runtime_error(tmp_path: Path):
    """SemgrepBinaryNotFoundError is a RuntimeError subclass."""
    err = SemgrepBinaryNotFoundError("msg")
    assert isinstance(err, RuntimeError)


def test_run_full_scan_raises_domain_error_when_binary_missing(tmp_path: Path):
    """run_full_scan propagates SemgrepBinaryNotFoundError when binary absent."""
    tool = SemgrepTool(repo_path=tmp_path)
    with patch("subprocess.run", side_effect=FileNotFoundError):
        with pytest.raises(SemgrepBinaryNotFoundError):
            tool.run_full_scan()


def test_timeout_handled_gracefully(tmp_path: Path):
    tool = SemgrepTool(repo_path=tmp_path)
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="semgrep", timeout=120)):
        result = tool.invoke({"path": "."})
    assert "timed out" in result.lower() or "Error" in result


# ---------------------------------------------------------------------------
# SemgrepTool.run_full_scan
# ---------------------------------------------------------------------------


def test_run_full_scan_returns_list_of_sast_match(tmp_path: Path):
    tool = SemgrepTool(repo_path=tmp_path)
    payload = _semgrep_json([
        _make_result(path="app/a.py", line_start=1, line_end=3),
        _make_result(path="app/b.py", line_start=5, line_end=7),
    ])

    with patch("subprocess.run", return_value=_mock_proc(stdout=payload, returncode=0)):
        matches = tool.run_full_scan()

    assert isinstance(matches, list)
    assert len(matches) == 2
    assert all(isinstance(m, SASTMatch) for m in matches)
    assert matches[0].file_path == "app/a.py"
    assert matches[1].file_path == "app/b.py"
