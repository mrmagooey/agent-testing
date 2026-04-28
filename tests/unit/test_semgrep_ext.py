"""Unit tests for the semgrep ToolExtension builder.

These tests do NOT invoke the real semgrep binary — SemgrepTool.invoke and
subprocess.run are not exercised here.  The builder is tested in isolation to
verify it registers correctly and mutates the registry as expected.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from sec_review_framework.data.experiment import ToolExtension
from sec_review_framework.tools.registry import (
    _EXTENSION_BUILDERS,
    ToolRegistry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_target(tmp_path: Path) -> Any:
    target = MagicMock()
    target.repo_path = tmp_path
    return target


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    """The module registers its builder at import time."""

    def test_semgrep_builder_is_registered(self) -> None:
        import sec_review_framework.tools.extensions.semgrep_ext  # noqa: F401

        assert ToolExtension.SEMGREP in _EXTENSION_BUILDERS

    def test_registered_builder_is_build_semgrep_tools(self) -> None:
        from sec_review_framework.tools.extensions.semgrep_ext import build_semgrep_tools

        assert _EXTENSION_BUILDERS[ToolExtension.SEMGREP] is build_semgrep_tools


# ---------------------------------------------------------------------------
# Builder behaviour
# ---------------------------------------------------------------------------

class TestBuildSemgrepTools:
    """Verify the builder adds run_semgrep to the registry without a subprocess."""

    def test_run_semgrep_added_to_registry(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        from sec_review_framework.tools.extensions.semgrep_ext import build_semgrep_tools
        build_semgrep_tools(registry, target)

        assert "run_semgrep" in registry.tools

    def test_tool_uses_target_repo_path(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        from sec_review_framework.tools.extensions.semgrep_ext import build_semgrep_tools
        build_semgrep_tools(registry, target)

        tool = registry.tools["run_semgrep"]
        assert str(tmp_path.resolve()) == str(tool._repo_path)

    def test_tool_definition_name_is_run_semgrep(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        from sec_review_framework.tools.extensions.semgrep_ext import build_semgrep_tools
        build_semgrep_tools(registry, target)

        defn = registry.tools["run_semgrep"].definition()
        assert defn.name == "run_semgrep"

    def test_no_closer_registered(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        from sec_review_framework.tools.extensions.semgrep_ext import build_semgrep_tools
        build_semgrep_tools(registry, target)

        assert registry._closers == [], (
            "SemgrepTool runs the binary in-process; no subprocess closer should be registered"
        )

    def test_target_without_repo_path_attr_uses_str(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.semgrep_ext import build_semgrep_tools

        registry = ToolRegistry()
        build_semgrep_tools(registry, str(tmp_path))

        tool = registry.tools["run_semgrep"]
        assert str(tmp_path.resolve()) == str(tool._repo_path)
