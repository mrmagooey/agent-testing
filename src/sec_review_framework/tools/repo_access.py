"""Repository file-access tools: read, list, and grep within a sandboxed repo root."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from sec_review_framework.models.base import ToolDefinition
from sec_review_framework.tools.registry import Tool

_READ_SIZE_LIMIT = 50 * 1024  # 50 KB


def _validate_path(repo_root: Path, path: str) -> Path:
    """
    Resolve *path* relative to *repo_root* and confirm it stays inside the root.

    Raises
    ------
    ValueError
        If the resolved path escapes the repo root (e.g. via ``..`` traversal).
    """
    resolved = (repo_root / path).resolve()
    if not str(resolved).startswith(str(repo_root.resolve())):
        raise ValueError(
            f"Path escapes repo root: {path!r} resolves to {resolved}"
        )
    return resolved


class ReadFileTool(Tool):
    """Read a single file from the repository, with a 50 KB size cap."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description=(
                "Read the contents of a file in the repository. "
                "Returns up to 50 KB; larger files are truncated."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the file, relative to the repository root."
                        ),
                    },
                },
                "required": ["path"],
            },
        )

    def invoke(self, input: dict[str, Any]) -> str:
        path_str: str = input["path"]
        resolved = _validate_path(self._repo_root, path_str)

        if not resolved.exists():
            return f"Error: file not found: {path_str}"
        if not resolved.is_file():
            return f"Error: path is not a file: {path_str}"

        raw = resolved.read_bytes()
        truncated = len(raw) > _READ_SIZE_LIMIT
        content_bytes = raw[:_READ_SIZE_LIMIT]

        try:
            text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text = content_bytes.decode("utf-8", errors="replace")

        if truncated:
            return text + "\n[output truncated]"
        return text


class ListDirectoryTool(Tool):
    """List the contents of a directory inside the repository."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_directory",
            description=(
                "List the files and subdirectories within a directory of the repository."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to the directory, relative to the repository root. "
                            "Defaults to the repository root itself."
                        ),
                        "default": ".",
                    },
                },
                "required": [],
            },
        )

    def invoke(self, input: dict[str, Any]) -> str:
        path_str: str = input.get("path", ".")
        resolved = _validate_path(self._repo_root, path_str)

        if not resolved.exists():
            return f"Error: directory not found: {path_str}"
        if not resolved.is_dir():
            return f"Error: path is not a directory: {path_str}"

        entries = sorted(resolved.iterdir(), key=lambda p: (p.is_file(), p.name))
        lines: list[str] = []
        for entry in entries:
            indicator = "/" if entry.is_dir() else " "
            lines.append(f"{indicator} {entry.name}")

        if not lines:
            return f"(empty directory: {path_str})"
        return "\n".join(lines)


class GrepTool(Tool):
    """Search for a pattern across files in the repository using grep."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="grep",
            description=(
                "Search for a regular-expression pattern across the repository "
                "using grep. Returns matching lines with file name and line number."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Regular expression pattern to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Restrict the search to this path (file or directory), "
                            "relative to the repository root. Defaults to the entire repo."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return.",
                        "default": 50,
                    },
                },
                "required": ["pattern"],
            },
        )

    def invoke(self, input: dict[str, Any]) -> str:
        pattern: str = input["pattern"]
        path_str: str | None = input.get("path")
        max_results: int = int(input.get("max_results", 50))

        if path_str is not None:
            search_path = _validate_path(self._repo_root, path_str)
        else:
            search_path = self._repo_root.resolve()

        cmd = [
            "grep",
            "-rn",
            "--include=*",
            "-m", str(max_results),
            pattern,
            str(search_path),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self._repo_root),
            )
        except subprocess.TimeoutExpired:
            return "Error: grep timed out after 30 seconds."
        except FileNotFoundError:
            return "Error: grep binary not found on this system."

        output = proc.stdout.strip()
        if not output and proc.returncode == 1:
            # grep exits 1 when no matches found — not an error.
            return f"No matches found for pattern: {pattern!r}"
        if proc.returncode > 1:
            return f"Error running grep: {proc.stderr.strip()}"

        # Strip the repo root prefix from file paths for cleaner output.
        repo_prefix = str(search_path) + "/"
        cleaned_lines = [
            line[len(repo_prefix):] if line.startswith(repo_prefix) else line
            for line in output.splitlines()
        ]
        result = "\n".join(cleaned_lines)

        if len(cleaned_lines) >= max_results:
            result += "\n[output truncated]"

        return result
