"""Regression test for the circular-import bug in registry.py.

The three MCP extensions (lsp_ext, tree_sitter_ext, devdocs_ext) each import
`Tool` transitively via `mcp_bridge`.  Before the fix, the extension imports
lived at the TOP of `registry.py`, before `Tool` was defined; Python's
partial-initialisation gave `mcp_bridge` an incomplete module, causing a
false `ImportError` that was silently swallowed — all three extensions
registered as "not available".

After the fix the extension imports are deferred to AFTER `Tool` and
`register_extension_builder` are both fully defined, so the transitive import
succeeds and all three builders land in `_EXTENSION_BUILDERS`.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import warnings


# ---------------------------------------------------------------------------
# Helper: run a snippet in a fresh Python interpreter
# ---------------------------------------------------------------------------

def _run_in_subprocess(code: str) -> subprocess.CompletedProcess:
    """Execute *code* in a fresh interpreter; return the CompletedProcess."""
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# 1. All three extension builders are registered after a clean import
# ---------------------------------------------------------------------------

class TestAllExtensionBuildersRegistered:
    """Fresh subprocess — no module-cache pollution."""

    def test_lsp_builder_registered(self) -> None:
        result = _run_in_subprocess("""
            from sec_review_framework.tools.registry import _EXTENSION_BUILDERS
            from sec_review_framework.data.experiment import ToolExtension
            assert ToolExtension.LSP in _EXTENSION_BUILDERS, (
                f"LSP builder missing; registered keys: {list(_EXTENSION_BUILDERS)}"
            )
        """)
        assert result.returncode == 0, (
            f"LSP builder not registered.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_tree_sitter_builder_registered(self) -> None:
        result = _run_in_subprocess("""
            from sec_review_framework.tools.registry import _EXTENSION_BUILDERS
            from sec_review_framework.data.experiment import ToolExtension
            assert ToolExtension.TREE_SITTER in _EXTENSION_BUILDERS, (
                f"TREE_SITTER builder missing; registered keys: {list(_EXTENSION_BUILDERS)}"
            )
        """)
        assert result.returncode == 0, (
            f"TREE_SITTER builder not registered.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_devdocs_builder_registered(self) -> None:
        result = _run_in_subprocess("""
            from sec_review_framework.tools.registry import _EXTENSION_BUILDERS
            from sec_review_framework.data.experiment import ToolExtension
            assert ToolExtension.DEVDOCS in _EXTENSION_BUILDERS, (
                f"DEVDOCS builder missing; registered keys: {list(_EXTENSION_BUILDERS)}"
            )
        """)
        assert result.returncode == 0, (
            f"DEVDOCS builder not registered.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_all_three_builders_registered_together(self) -> None:
        """Single subprocess to confirm they all co-exist after one import."""
        result = _run_in_subprocess("""
            from sec_review_framework.tools.registry import _EXTENSION_BUILDERS
            from sec_review_framework.data.experiment import ToolExtension
            missing = [
                e.name for e in (ToolExtension.LSP, ToolExtension.TREE_SITTER, ToolExtension.DEVDOCS)
                if e not in _EXTENSION_BUILDERS
            ]
            assert not missing, f"Missing extension builders: {missing}"
        """)
        assert result.returncode == 0, (
            f"One or more builders missing.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# 2. Importing registry FIRST (normal worker startup order), then mcp_bridge
# ---------------------------------------------------------------------------

class TestRegistryFirstThenMcpBridge:
    """
    Normal worker startup order: registry is imported first, then mcp_bridge.
    The fix ensures this always succeeds with all three builders registered.
    """

    def test_registry_first_no_unavailability_warnings(self) -> None:
        result = _run_in_subprocess("""
            import warnings
            import logging

            # Capture log warnings emitted via the logging module
            captured_warnings = []

            class _CapturingHandler(logging.Handler):
                def emit(self, record):
                    captured_warnings.append(record.getMessage())

            handler = _CapturingHandler()
            logging.getLogger("sec_review_framework.tools.registry").addHandler(handler)
            logging.getLogger("sec_review_framework.tools.registry").setLevel(logging.WARNING)

            # Import registry first — this is the normal worker startup order
            import sec_review_framework.tools.registry  # noqa: F401
            # Now import mcp_bridge — should be fine, registry is fully loaded
            import sec_review_framework.tools.mcp_bridge  # noqa: F401

            # Any "will not be available" warning is a symptom of the old circular bug
            bad = [w for w in captured_warnings if "will not be available" in w]
            assert not bad, f"Extension availability warnings detected: {bad}"
        """)
        assert result.returncode == 0, (
            "Extension unavailability warnings emitted when registry imported first.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_registry_first_all_builders_present(self) -> None:
        """Combining the ordering check with builder-presence assertion."""
        result = _run_in_subprocess("""
            import sec_review_framework.tools.registry  # noqa: F401
            import sec_review_framework.tools.mcp_bridge  # noqa: F401
            from sec_review_framework.tools.registry import _EXTENSION_BUILDERS
            from sec_review_framework.data.experiment import ToolExtension
            missing = [
                e.name for e in (ToolExtension.LSP, ToolExtension.TREE_SITTER, ToolExtension.DEVDOCS)
                if e not in _EXTENSION_BUILDERS
            ]
            assert not missing, (
                f"Builders missing after registry-first import: {missing}"
            )
        """)
        assert result.returncode == 0, (
            f"Builders missing after registry-first import.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_mcp_bridge_first_raises_clear_circular_import_error(self) -> None:
        """
        When mcp_bridge is imported first it triggers registry which then tries to
        import the extensions, which import from mcp_bridge (partially initialized).
        The sharpened error handling in _import_extension must detect this and raise
        a clear, descriptive ImportError — NOT silently swallow it as the old code did.
        """
        result = _run_in_subprocess("""
            import sec_review_framework.tools.mcp_bridge  # noqa: F401
        """)
        # Should fail with a descriptive circular-import error, not silently succeed
        # with missing builders.
        assert result.returncode != 0, (
            "Expected ImportError when mcp_bridge imported before registry "
            "(unavoidable circular import in this order), but import succeeded."
        )
        assert "Circular import detected" in result.stderr, (
            f"Expected 'Circular import detected' in stderr but got:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# 3. In-process sanity check (uses the already-loaded module cache)
# ---------------------------------------------------------------------------

class TestInProcessRegistration:
    """
    Verify the current process's module cache has all three builders.
    This complements the subprocess tests — it will catch regressions where
    registry.py is imported before the fix is active.
    """

    def test_in_process_all_builders_present(self) -> None:
        from sec_review_framework.data.experiment import ToolExtension
        from sec_review_framework.tools.registry import _EXTENSION_BUILDERS

        missing = [
            e.name
            for e in (ToolExtension.LSP, ToolExtension.TREE_SITTER, ToolExtension.DEVDOCS)
            if e not in _EXTENSION_BUILDERS
        ]
        assert not missing, f"Extension builders not registered in-process: {missing}"

    def test_no_import_warning_from_registry(self) -> None:
        """Importing registry in-process should not emit Python-level warnings."""
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            import sec_review_framework.tools.registry  # noqa: F401

        ext_warnings = [
            str(w.message) for w in caught
            if "will not be available" in str(w.message)
        ]
        assert not ext_warnings, (
            f"Unexpected extension-availability warnings: {ext_warnings}"
        )
