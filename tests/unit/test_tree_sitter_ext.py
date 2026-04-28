"""Unit tests for the tree-sitter ToolExtension builder.

These tests do NOT start a subprocess — MCPClient and register_mcp_tools are
monkeypatched to no-ops so the suite runs without tree-sitter-language-pack
installed and without any network access.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

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

    def test_tree_sitter_builder_is_registered(self) -> None:
        # Import triggers self-registration.
        import sec_review_framework.tools.extensions.tree_sitter_ext  # noqa: F401

        assert ToolExtension.TREE_SITTER in _EXTENSION_BUILDERS

    def test_registered_builder_is_build_tree_sitter_tools(self) -> None:
        from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools

        assert _EXTENSION_BUILDERS[ToolExtension.TREE_SITTER] is build_tree_sitter_tools


# ---------------------------------------------------------------------------
# Builder behaviour (mocked subprocess)
# ---------------------------------------------------------------------------

class TestBuildTreeSitterTools:
    """Verify MCPServerSpec construction and registry mutations without spawning a process."""

    def test_spec_uses_repo_path_as_cwd_and_arg(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        captured_specs: list[Any] = []

        mock_client = MagicMock()

        def fake_mcp_client(spec: Any) -> MagicMock:
            captured_specs.append(spec)
            return mock_client

        with (
            patch(
                "sec_review_framework.tools.extensions.tree_sitter_ext.MCPClient",
                side_effect=fake_mcp_client,
            ),
            patch("sec_review_framework.tools.extensions.tree_sitter_ext.register_mcp_tools"),
        ):
            from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools
            build_tree_sitter_tools(registry, target)

        assert len(captured_specs) == 1
        spec = captured_specs[0]

        assert spec.name == "tree-sitter"
        assert str(tmp_path.resolve()) in spec.args, (
            f"repo_path {tmp_path} not found in args {spec.args}"
        )
        assert str(tmp_path.resolve()) == spec.cwd

    def test_client_start_is_called(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        mock_client = MagicMock()

        with (
            patch(
                "sec_review_framework.tools.extensions.tree_sitter_ext.MCPClient",
                return_value=mock_client,
            ),
            patch("sec_review_framework.tools.extensions.tree_sitter_ext.register_mcp_tools"),
        ):
            from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools
            build_tree_sitter_tools(registry, target)

        mock_client.start.assert_called_once()

    def test_closer_is_registered_with_registry(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        mock_client = MagicMock()

        with (
            patch(
                "sec_review_framework.tools.extensions.tree_sitter_ext.MCPClient",
                return_value=mock_client,
            ),
            patch("sec_review_framework.tools.extensions.tree_sitter_ext.register_mcp_tools"),
        ):
            from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools
            build_tree_sitter_tools(registry, target)

        # The closer must be the client's close method.
        assert mock_client.close in registry._closers

    def test_register_mcp_tools_called_with_ts_prefix(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        mock_client = MagicMock()

        with (
            patch(
                "sec_review_framework.tools.extensions.tree_sitter_ext.MCPClient",
                return_value=mock_client,
            ),
            patch(
                "sec_review_framework.tools.extensions.tree_sitter_ext.register_mcp_tools",
            ) as mock_register,
        ):
            from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools
            build_tree_sitter_tools(registry, target)

        mock_register.assert_called_once()
        _args, kwargs = mock_register.call_args
        assert kwargs.get("name_prefix") == "ts_" or _args[2] == "ts_"

    def test_startup_failure_propagates(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        mock_client = MagicMock()
        mock_client.start.side_effect = RuntimeError("server failed to start")

        with (
            patch(
                "sec_review_framework.tools.extensions.tree_sitter_ext.MCPClient",
                return_value=mock_client,
            ),
            patch("sec_review_framework.tools.extensions.tree_sitter_ext.register_mcp_tools"),
        ):
            from sec_review_framework.tools.extensions.tree_sitter_ext import build_tree_sitter_tools
            with pytest.raises(RuntimeError, match="server failed to start"):
                build_tree_sitter_tools(registry, target)

    def test_name_filter_excludes_non_security_tools(self, tmp_path: Path) -> None:
        """The name_filter should reject tools not in the allowed set."""
        from sec_review_framework.tools.extensions.tree_sitter_ext import _security_filter

        assert _security_filter("find_symbol") is True
        assert _security_filter("get_ast") is True
        assert _security_filter("list_functions") is True
        assert _security_filter("query") is True

        # Hypothetical write-capable or project-management tools must be rejected.
        assert _security_filter("write_file") is False
        assert _security_filter("register_project") is False
        assert _security_filter("clear_cache") is False
        assert _security_filter("diagnose_config") is False
