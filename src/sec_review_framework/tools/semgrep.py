"""Semgrep SAST tool wrapper for the security review framework."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from sec_review_framework.models.base import ToolDefinition
from sec_review_framework.tools.registry import Tool


class SASTMatch(BaseModel):
    file_path: str
    line_start: int
    line_end: int
    rule_id: str
    message: str
    severity: str


class SemgrepTool(Tool):
    """
    Run Semgrep against the target repository using a local binary.

    No cloud API is invoked — all scanning happens via the local ``semgrep``
    binary against a pre-specified rule config.
    """

    def __init__(
        self,
        repo_path: Path,
        config: str = "p/owasp-top-ten",
    ) -> None:
        self._repo_path = repo_path
        self._config = config

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="run_semgrep",
            description=(
                "Run Semgrep static analysis on a file or directory within the repository. "
                "Returns structured findings with file path, line numbers, rule ID, "
                "message, and severity."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Path to scan, relative to the repository root. "
                            "Defaults to the entire repository."
                        ),
                        "default": ".",
                    },
                    "config": {
                        "type": "string",
                        "description": (
                            "Semgrep rule config to use (e.g. 'p/owasp-top-ten', "
                            "'p/security-audit', or a local rule file path). "
                            "Defaults to the tool's configured ruleset."
                        ),
                    },
                },
                "required": [],
            },
        )

    def invoke(self, input: dict[str, Any]) -> str:
        path_str: str = input.get("path", ".")
        config: str = input.get("config", self._config)

        scan_target = (self._repo_path / path_str).resolve()
        # Prevent path escape.
        if not str(scan_target).startswith(str(self._repo_path.resolve())):
            return f"Error: path escapes repository root: {path_str!r}"

        matches = self._run_semgrep(target=scan_target, config=config)
        if isinstance(matches, str):
            # Error string returned from _run_semgrep.
            return matches

        if not matches:
            return "Semgrep found no issues."

        lines: list[str] = []
        for m in matches:
            lines.append(
                f"[{m.severity}] {m.rule_id}\n"
                f"  {m.file_path}:{m.line_start}-{m.line_end}\n"
                f"  {m.message}"
            )
        return "\n\n".join(lines)

    def run_full_scan(self) -> list[SASTMatch]:
        """
        Run Semgrep over the entire repository and return structured matches.

        Intended for SASTFirstStrategy, which passes findings to the LLM as
        pre-computed context rather than letting the agent invoke the tool.
        """
        result = self._run_semgrep(
            target=self._repo_path.resolve(),
            config=self._config,
        )
        if isinstance(result, str):
            # Error — return empty list so the strategy can continue gracefully.
            return []
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_semgrep(
        self,
        target: Path,
        config: str,
    ) -> list[SASTMatch] | str:
        """
        Invoke the local semgrep binary and parse its JSON output.

        Returns a list of SASTMatch on success, or an error string on failure.
        """
        cmd = [
            "semgrep",
            "--json",
            "--config", config,
            "--quiet",
            str(target),
        ]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(self._repo_path),
            )
        except subprocess.TimeoutExpired:
            return "Error: semgrep timed out after 120 seconds."
        except FileNotFoundError:
            return "Error: semgrep binary not found. Install with: pip install semgrep"

        if proc.returncode not in (0, 1):
            # semgrep exits 1 when findings exist, which is normal.
            stderr = proc.stderr.strip()
            return f"Error running semgrep (exit {proc.returncode}): {stderr}"

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            return f"Error: could not parse semgrep JSON output: {exc}"

        matches: list[SASTMatch] = []
        for result in data.get("results", []):
            try:
                match = SASTMatch(
                    file_path=result["path"],
                    line_start=result["start"]["line"],
                    line_end=result["end"]["line"],
                    rule_id=result["check_id"],
                    message=result["extra"]["message"],
                    severity=result["extra"].get("severity", "unknown"),
                )
                matches.append(match)
            except (KeyError, TypeError):
                # Skip malformed entries rather than aborting the whole scan.
                continue

        return matches
