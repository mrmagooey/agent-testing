"""Unit tests for the LSP ToolExtension builder.

These tests do NOT start a subprocess — MCPClient and register_mcp_tools are
monkeypatched to no-ops so the suite runs without any LSP server binaries
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

    def test_lsp_builder_is_registered(self) -> None:
        # Import triggers self-registration.
        import sec_review_framework.tools.extensions.lsp_ext  # noqa: F401

        assert ToolExtension.LSP in _EXTENSION_BUILDERS

    def test_registered_builder_is_build_lsp_tools(self) -> None:
        from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools

        assert _EXTENSION_BUILDERS[ToolExtension.LSP] is build_lsp_tools


# ---------------------------------------------------------------------------
# Builder behaviour (mocked subprocess)
# ---------------------------------------------------------------------------

class TestBuildLspTools:
    """Verify MCPServerSpec construction and registry mutations without spawning a process."""

    def test_spec_uses_workspace_arg_with_repo_path(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        captured_specs: list[Any] = []
        mock_client = MagicMock()

        def fake_mcp_client(spec: Any) -> MagicMock:
            captured_specs.append(spec)
            return mock_client

        with (
            patch(
                "sec_review_framework.tools.extensions.lsp_ext.MCPClient",
                side_effect=fake_mcp_client,
            ),
            patch("sec_review_framework.tools.extensions.lsp_ext.register_mcp_tools"),
        ):
            from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
            build_lsp_tools(registry, target)

        assert len(captured_specs) == 1
        spec = captured_specs[0]

        assert spec.name == "lsp"
        # --workspace <repo_path> must appear in the args list.
        assert "--workspace" in spec.args
        workspace_idx = spec.args.index("--workspace")
        assert spec.args[workspace_idx + 1] == str(tmp_path.resolve()), (
            f"Expected --workspace pointing at {tmp_path}, got args: {spec.args}"
        )

    def test_spec_cwd_is_repo_path(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        captured_specs: list[Any] = []
        mock_client = MagicMock()

        def fake_mcp_client(spec: Any) -> MagicMock:
            captured_specs.append(spec)
            return mock_client

        with (
            patch(
                "sec_review_framework.tools.extensions.lsp_ext.MCPClient",
                side_effect=fake_mcp_client,
            ),
            patch("sec_review_framework.tools.extensions.lsp_ext.register_mcp_tools"),
        ):
            from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
            build_lsp_tools(registry, target)

        assert captured_specs[0].cwd == str(tmp_path.resolve())

    def test_client_start_is_called(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        mock_client = MagicMock()

        with (
            patch(
                "sec_review_framework.tools.extensions.lsp_ext.MCPClient",
                return_value=mock_client,
            ),
            patch("sec_review_framework.tools.extensions.lsp_ext.register_mcp_tools"),
        ):
            from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
            build_lsp_tools(registry, target)

        mock_client.start.assert_called_once()

    def test_closer_is_registered_with_registry(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        mock_client = MagicMock()

        with (
            patch(
                "sec_review_framework.tools.extensions.lsp_ext.MCPClient",
                return_value=mock_client,
            ),
            patch("sec_review_framework.tools.extensions.lsp_ext.register_mcp_tools"),
        ):
            from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
            build_lsp_tools(registry, target)

        # The closer must be the client's close method.
        assert mock_client.close in registry._closers

    def test_register_mcp_tools_called_with_lsp_prefix(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        mock_client = MagicMock()

        with (
            patch(
                "sec_review_framework.tools.extensions.lsp_ext.MCPClient",
                return_value=mock_client,
            ),
            patch(
                "sec_review_framework.tools.extensions.lsp_ext.register_mcp_tools",
            ) as mock_register,
        ):
            from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
            build_lsp_tools(registry, target)

        mock_register.assert_called_once()
        _args, kwargs = mock_register.call_args
        assert kwargs.get("name_prefix") == "lsp_" or _args[2] == "lsp_"

    def test_startup_failure_propagates(self, tmp_path: Path) -> None:
        target = _make_target(tmp_path)
        registry = ToolRegistry()

        mock_client = MagicMock()
        mock_client.start.side_effect = RuntimeError("lsp server failed to start")

        with (
            patch(
                "sec_review_framework.tools.extensions.lsp_ext.MCPClient",
                return_value=mock_client,
            ),
            patch("sec_review_framework.tools.extensions.lsp_ext.register_mcp_tools"),
        ):
            from sec_review_framework.tools.extensions.lsp_ext import build_lsp_tools
            with pytest.raises(RuntimeError, match="lsp server failed to start"):
                build_lsp_tools(registry, target)

    def test_lsp_tool_filter_allows_expected_tools(self) -> None:
        from sec_review_framework.tools.extensions.lsp_ext import _lsp_tool_filter

        assert _lsp_tool_filter("definition") is True
        assert _lsp_tool_filter("references") is True
        assert _lsp_tool_filter("hover") is True
        assert _lsp_tool_filter("document_symbols") is True
        assert _lsp_tool_filter("workspace_symbols") is True

    def test_lsp_tool_filter_rejects_unknown_tools(self) -> None:
        from sec_review_framework.tools.extensions.lsp_ext import _lsp_tool_filter

        assert _lsp_tool_filter("write_file") is False
        assert _lsp_tool_filter("execute_command") is False
        assert _lsp_tool_filter("rename_symbol") is False
        assert _lsp_tool_filter("format_document") is False
