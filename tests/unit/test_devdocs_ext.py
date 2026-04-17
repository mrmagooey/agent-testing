"""Unit tests for the DevDocs ToolExtension builder.

These tests do NOT start a subprocess — MCPClient and register_mcp_tools are
monkeypatched to no-ops so the suite runs without any real devdocs root
mounted and without any network access.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sec_review_framework.data.experiment import ToolExtension
from sec_review_framework.tools.registry import (
    ToolRegistry,
    _EXTENSION_BUILDERS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_target() -> Any:
    return MagicMock()


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    """The module registers its builder at import time."""

    def test_devdocs_builder_is_registered(self) -> None:
        import sec_review_framework.tools.extensions.devdocs_ext  # noqa: F401

        assert ToolExtension.DEVDOCS in _EXTENSION_BUILDERS

    def test_registered_builder_is_build_devdocs_tools(self) -> None:
        from sec_review_framework.tools.extensions.devdocs_ext import build_devdocs_tools

        assert _EXTENSION_BUILDERS[ToolExtension.DEVDOCS] is build_devdocs_tools


# ---------------------------------------------------------------------------
# Builder behaviour — root does not exist
# ---------------------------------------------------------------------------

class TestBuildDevdocsToolsRootMissing:
    """Builder raises RuntimeError with a clear message when root is absent."""

    def test_missing_root_raises_runtime_error(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.devdocs_ext import build_devdocs_tools

        absent = tmp_path / "does_not_exist"
        registry = ToolRegistry()
        target = _make_target()

        with patch.dict(os.environ, {"DEVDOCS_ROOT": str(absent)}):
            with pytest.raises(RuntimeError, match="DevDocs root not mounted"):
                build_devdocs_tools(registry, target)

    def test_error_message_includes_path(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.devdocs_ext import build_devdocs_tools

        absent = tmp_path / "missing_devdocs"
        registry = ToolRegistry()
        target = _make_target()

        with patch.dict(os.environ, {"DEVDOCS_ROOT": str(absent)}):
            with pytest.raises(RuntimeError) as exc_info:
                build_devdocs_tools(registry, target)

        assert str(absent) in str(exc_info.value)


# ---------------------------------------------------------------------------
# Builder behaviour — mocked subprocess
# ---------------------------------------------------------------------------

class TestBuildDevdocsToolsMocked:
    """Verify MCPServerSpec construction and registry mutations without a real process."""

    def test_spec_uses_docsets_root(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.devdocs_ext import build_devdocs_tools

        root = tmp_path / "devdocs"
        root.mkdir()
        registry = ToolRegistry()
        target = _make_target()

        captured_specs: list[Any] = []
        mock_client = MagicMock()

        def fake_mcp_client(spec: Any) -> MagicMock:
            captured_specs.append(spec)
            return mock_client

        with (
            patch.dict(os.environ, {"DEVDOCS_ROOT": str(root)}, clear=False),
            patch(
                "sec_review_framework.tools.extensions.devdocs_ext.MCPClient",
                side_effect=fake_mcp_client,
            ),
            patch("sec_review_framework.tools.extensions.devdocs_ext.register_mcp_tools"),
        ):
            build_devdocs_tools(registry, target)

        assert len(captured_specs) == 1
        spec = captured_specs[0]
        assert spec.name == "devdocs"
        assert "--docsets-root" in spec.args
        root_idx = spec.args.index("--docsets-root")
        assert str(root.resolve()) == spec.args[root_idx + 1]

    def test_client_start_is_called(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.devdocs_ext import build_devdocs_tools

        root = tmp_path / "devdocs"
        root.mkdir()
        registry = ToolRegistry()
        target = _make_target()
        mock_client = MagicMock()

        with (
            patch.dict(os.environ, {"DEVDOCS_ROOT": str(root)}, clear=False),
            patch(
                "sec_review_framework.tools.extensions.devdocs_ext.MCPClient",
                return_value=mock_client,
            ),
            patch("sec_review_framework.tools.extensions.devdocs_ext.register_mcp_tools"),
        ):
            build_devdocs_tools(registry, target)

        mock_client.start.assert_called_once()

    def test_closer_is_registered_with_registry(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.devdocs_ext import build_devdocs_tools

        root = tmp_path / "devdocs"
        root.mkdir()
        registry = ToolRegistry()
        target = _make_target()
        mock_client = MagicMock()

        with (
            patch.dict(os.environ, {"DEVDOCS_ROOT": str(root)}, clear=False),
            patch(
                "sec_review_framework.tools.extensions.devdocs_ext.MCPClient",
                return_value=mock_client,
            ),
            patch("sec_review_framework.tools.extensions.devdocs_ext.register_mcp_tools"),
        ):
            build_devdocs_tools(registry, target)

        assert mock_client.close in registry._closers

    def test_register_mcp_tools_called_with_doc_prefix(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.devdocs_ext import build_devdocs_tools

        root = tmp_path / "devdocs"
        root.mkdir()
        registry = ToolRegistry()
        target = _make_target()
        mock_client = MagicMock()

        with (
            patch.dict(os.environ, {"DEVDOCS_ROOT": str(root)}, clear=False),
            patch(
                "sec_review_framework.tools.extensions.devdocs_ext.MCPClient",
                return_value=mock_client,
            ),
            patch(
                "sec_review_framework.tools.extensions.devdocs_ext.register_mcp_tools",
            ) as mock_register,
        ):
            build_devdocs_tools(registry, target)

        mock_register.assert_called_once()
        _args, kwargs = mock_register.call_args
        assert kwargs.get("name_prefix") == "doc_" or (len(_args) >= 3 and _args[2] == "doc_")

    def test_allow_docsets_passed_when_env_set(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.devdocs_ext import build_devdocs_tools

        root = tmp_path / "devdocs"
        root.mkdir()
        registry = ToolRegistry()
        target = _make_target()

        captured_specs: list[Any] = []
        mock_client = MagicMock()

        def fake_mcp_client(spec: Any) -> MagicMock:
            captured_specs.append(spec)
            return mock_client

        env = {
            "DEVDOCS_ROOT": str(root),
            "DEVDOCS_ALLOW_DOCSETS": "python~3.12,javascript",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch(
                "sec_review_framework.tools.extensions.devdocs_ext.MCPClient",
                side_effect=fake_mcp_client,
            ),
            patch("sec_review_framework.tools.extensions.devdocs_ext.register_mcp_tools"),
        ):
            build_devdocs_tools(registry, target)

        spec = captured_specs[0]
        assert "--allow-docsets" in spec.args
        allow_idx = spec.args.index("--allow-docsets")
        assert "python~3.12" in spec.args[allow_idx + 1]

    def test_allow_docsets_not_passed_when_env_empty(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.devdocs_ext import build_devdocs_tools

        root = tmp_path / "devdocs"
        root.mkdir()
        registry = ToolRegistry()
        target = _make_target()

        captured_specs: list[Any] = []
        mock_client = MagicMock()

        def fake_mcp_client(spec: Any) -> MagicMock:
            captured_specs.append(spec)
            return mock_client

        env = {"DEVDOCS_ROOT": str(root)}
        # Ensure DEVDOCS_ALLOW_DOCSETS is absent
        with (
            patch.dict(os.environ, env, clear=False),
            patch(
                "sec_review_framework.tools.extensions.devdocs_ext.MCPClient",
                side_effect=fake_mcp_client,
            ),
            patch("sec_review_framework.tools.extensions.devdocs_ext.register_mcp_tools"),
        ):
            # Remove the env var if it happens to be set
            os.environ.pop("DEVDOCS_ALLOW_DOCSETS", None)
            build_devdocs_tools(registry, target)

        spec = captured_specs[0]
        assert "--allow-docsets" not in spec.args

    def test_startup_failure_propagates(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.extensions.devdocs_ext import build_devdocs_tools

        root = tmp_path / "devdocs"
        root.mkdir()
        registry = ToolRegistry()
        target = _make_target()
        mock_client = MagicMock()
        mock_client.start.side_effect = RuntimeError("server failed to start")

        with (
            patch.dict(os.environ, {"DEVDOCS_ROOT": str(root)}, clear=False),
            patch(
                "sec_review_framework.tools.extensions.devdocs_ext.MCPClient",
                return_value=mock_client,
            ),
            patch("sec_review_framework.tools.extensions.devdocs_ext.register_mcp_tools"),
        ):
            with pytest.raises(RuntimeError, match="server failed to start"):
                build_devdocs_tools(registry, target)
