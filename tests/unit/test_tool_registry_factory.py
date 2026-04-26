"""Tests for ToolRegistryFactory.create() — extension dispatch and backward compat."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from sec_review_framework.data.experiment import ToolExtension, ToolVariant
from sec_review_framework.tools.registry import (
    ToolRegistry,
    ToolRegistryFactory,
    _EXTENSION_BUILDERS,
    register_extension_builder,
)

# Snapshot of core tool names produced by WITH_TOOLS (no extensions).
# run_semgrep is now a SEMGREP extension tool, not a core tool.
_CORE_TOOL_NAMES = {"read_file", "list_directory", "grep", "lookup_docs"}


def _make_mock_target(tmp_path: Path) -> Any:
    target = MagicMock()
    target.repo_path = tmp_path
    return target


# ---------------------------------------------------------------------------
# Backward-compatibility: empty extensions behaves exactly as before
# ---------------------------------------------------------------------------


class TestNoExtensions:
    def test_without_tools_returns_empty_registry(self, tmp_path: Path) -> None:
        target = _make_mock_target(tmp_path)
        registry = ToolRegistryFactory.create(ToolVariant.WITHOUT_TOOLS, target)
        assert registry.tools == {}

    def test_with_tools_returns_core_tools(self, tmp_path: Path) -> None:
        target = _make_mock_target(tmp_path)
        # Real factory call — semgrep will be imported but the tool won't run.
        registry = ToolRegistryFactory.create(
            ToolVariant.WITH_TOOLS, target, tool_extensions=frozenset()
        )
        assert set(registry.tools.keys()) == _CORE_TOOL_NAMES

    def test_frozenset_and_iterable_coercion_both_work(self, tmp_path: Path) -> None:
        target = _make_mock_target(tmp_path)
        r1 = ToolRegistryFactory.create(
            ToolVariant.WITHOUT_TOOLS, target, tool_extensions=frozenset()
        )
        r2 = ToolRegistryFactory.create(
            ToolVariant.WITHOUT_TOOLS, target, tool_extensions=[]
        )
        assert r1.tools == r2.tools == {}


# ---------------------------------------------------------------------------
# Unregistered extension raises ValueError
# ---------------------------------------------------------------------------


class TestUnregisteredExtension:
    def test_unregistered_extension_raises_value_error(self, tmp_path: Path) -> None:
        # Ensure TREE_SITTER is NOT registered (Chunk 3 will register it).
        saved = _EXTENSION_BUILDERS.pop(ToolExtension.TREE_SITTER, None)
        try:
            target = _make_mock_target(tmp_path)
            with pytest.raises(ValueError, match="tree_sitter"):
                ToolRegistryFactory.create(
                    ToolVariant.WITHOUT_TOOLS,
                    target,
                    tool_extensions=frozenset([ToolExtension.TREE_SITTER]),
                )
        finally:
            if saved is not None:
                _EXTENSION_BUILDERS[ToolExtension.TREE_SITTER] = saved

    def test_error_message_names_the_extension(self, tmp_path: Path) -> None:
        saved = _EXTENSION_BUILDERS.pop(ToolExtension.LSP, None)
        try:
            target = _make_mock_target(tmp_path)
            with pytest.raises(ValueError) as exc_info:
                ToolRegistryFactory.create(
                    ToolVariant.WITHOUT_TOOLS,
                    target,
                    tool_extensions={ToolExtension.LSP},
                )
            assert "lsp" in str(exc_info.value)
        finally:
            if saved is not None:
                _EXTENSION_BUILDERS[ToolExtension.LSP] = saved


# ---------------------------------------------------------------------------
# register_extension_builder integration
# ---------------------------------------------------------------------------


class TestRegisterExtensionBuilder:
    """Verify that a stub builder registered at test time is called by create()."""

    def test_stub_builder_adds_tool(self, tmp_path: Path) -> None:
        from sec_review_framework.tools.registry import Tool

        class _NoOpTool(Tool):
            def definition(self):
                from sec_review_framework.models.base import ToolDefinition
                return ToolDefinition(name="stub_noop", description="", input_schema={})

            def invoke(self, input):
                return ""

        def stub_builder(registry: ToolRegistry, target: Any) -> None:
            tool = _NoOpTool()
            registry.tools[tool.definition().name] = tool

        # Use a real extension key temporarily.
        ext = ToolExtension.DEVDOCS
        saved = _EXTENSION_BUILDERS.pop(ext, None)
        try:
            register_extension_builder(ext, stub_builder)
            target = _make_mock_target(tmp_path)
            registry = ToolRegistryFactory.create(
                ToolVariant.WITHOUT_TOOLS,
                target,
                tool_extensions=frozenset([ext]),
            )
            assert "stub_noop" in registry.tools
        finally:
            _EXTENSION_BUILDERS.pop(ext, None)
            if saved is not None:
                _EXTENSION_BUILDERS[ext] = saved

    def test_registering_replaces_existing_builder(self) -> None:
        ext = ToolExtension.DEVDOCS
        saved = _EXTENSION_BUILDERS.pop(ext, None)
        try:
            calls: list[str] = []
            register_extension_builder(ext, lambda r, t: calls.append("first"))
            register_extension_builder(ext, lambda r, t: calls.append("second"))
            assert _EXTENSION_BUILDERS[ext] is not None
            # Only the latest should be in the table.
            _EXTENSION_BUILDERS[ext](None, None)
            assert calls == ["second"]
        finally:
            _EXTENSION_BUILDERS.pop(ext, None)
            if saved is not None:
                _EXTENSION_BUILDERS[ext] = saved


# ---------------------------------------------------------------------------
# DocLookupTool stub suppression when DEVDOCS is active
# ---------------------------------------------------------------------------


class TestDocLookupStubSuppression:
    """Confirm the lookup_docs stub is present or absent depending on DEVDOCS."""

    def test_stub_present_when_devdocs_not_in_extensions(self, tmp_path: Path) -> None:
        target = _make_mock_target(tmp_path)
        registry = ToolRegistryFactory.create(
            ToolVariant.WITH_TOOLS, target, tool_extensions=frozenset()
        )
        assert "lookup_docs" in registry.tools, (
            "DocLookupTool stub should be registered when DEVDOCS extension is absent"
        )

    def test_stub_absent_when_devdocs_in_extensions(self, tmp_path: Path) -> None:
        # Install a no-op DEVDOCS builder so the factory doesn't raise ValueError.
        saved = _EXTENSION_BUILDERS.pop(ToolExtension.DEVDOCS, None)
        try:
            register_extension_builder(ToolExtension.DEVDOCS, lambda r, t: None)
            target = _make_mock_target(tmp_path)
            registry = ToolRegistryFactory.create(
                ToolVariant.WITH_TOOLS,
                target,
                tool_extensions=frozenset([ToolExtension.DEVDOCS]),
            )
            assert "lookup_docs" not in registry.tools, (
                "DocLookupTool stub must NOT be registered when DEVDOCS extension is active"
            )
        finally:
            _EXTENSION_BUILDERS.pop(ToolExtension.DEVDOCS, None)
            if saved is not None:
                _EXTENSION_BUILDERS[ToolExtension.DEVDOCS] = saved

    def test_stub_absent_but_without_tools_still_empty(self, tmp_path: Path) -> None:
        """WITHOUT_TOOLS + DEVDOCS yields an empty core but DEVDOCS builder runs."""
        saved = _EXTENSION_BUILDERS.pop(ToolExtension.DEVDOCS, None)
        added: list[str] = []

        def tracking_builder(registry, target):
            added.append("called")

        try:
            register_extension_builder(ToolExtension.DEVDOCS, tracking_builder)
            target = _make_mock_target(tmp_path)
            registry = ToolRegistryFactory.create(
                ToolVariant.WITHOUT_TOOLS,
                target,
                tool_extensions=frozenset([ToolExtension.DEVDOCS]),
            )
            # No core tools (WITHOUT_TOOLS)
            assert "lookup_docs" not in registry.tools
            # But the extension builder was still invoked
            assert added == ["called"]
        finally:
            _EXTENSION_BUILDERS.pop(ToolExtension.DEVDOCS, None)
            if saved is not None:
                _EXTENSION_BUILDERS[ToolExtension.DEVDOCS] = saved


# ---------------------------------------------------------------------------
# SEMGREP extension behaviour
# ---------------------------------------------------------------------------


class TestSemgrepExtension:
    """run_semgrep must NOT appear in core WITH_TOOLS; only with SEMGREP extension."""

    def test_run_semgrep_absent_from_core_tools(self, tmp_path: Path) -> None:
        target = _make_mock_target(tmp_path)
        registry = ToolRegistryFactory.create(
            ToolVariant.WITH_TOOLS, target, tool_extensions=frozenset()
        )
        assert "run_semgrep" not in registry.tools, (
            "SemgrepTool must NOT be in core tools — it is now a SEMGREP extension"
        )

    def test_run_semgrep_present_when_semgrep_extension_active(self, tmp_path: Path) -> None:
        saved = _EXTENSION_BUILDERS.pop(ToolExtension.SEMGREP, None)
        try:
            register_extension_builder(
                ToolExtension.SEMGREP,
                lambda r, t: r.tools.__setitem__("run_semgrep", object()),
            )
            target = _make_mock_target(tmp_path)
            registry = ToolRegistryFactory.create(
                ToolVariant.WITH_TOOLS,
                target,
                tool_extensions=frozenset([ToolExtension.SEMGREP]),
            )
            assert "run_semgrep" in registry.tools
        finally:
            _EXTENSION_BUILDERS.pop(ToolExtension.SEMGREP, None)
            if saved is not None:
                _EXTENSION_BUILDERS[ToolExtension.SEMGREP] = saved

    def test_run_semgrep_present_without_tools_variant_when_semgrep_extension_active(
        self, tmp_path: Path
    ) -> None:
        saved = _EXTENSION_BUILDERS.pop(ToolExtension.SEMGREP, None)
        added: list[str] = []

        def tracking_builder(registry, target):
            added.append("called")

        try:
            register_extension_builder(ToolExtension.SEMGREP, tracking_builder)
            target = _make_mock_target(tmp_path)
            ToolRegistryFactory.create(
                ToolVariant.WITHOUT_TOOLS,
                target,
                tool_extensions=frozenset([ToolExtension.SEMGREP]),
            )
            assert added == ["called"]
        finally:
            _EXTENSION_BUILDERS.pop(ToolExtension.SEMGREP, None)
            if saved is not None:
                _EXTENSION_BUILDERS[ToolExtension.SEMGREP] = saved
