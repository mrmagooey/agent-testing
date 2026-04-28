"""Unit tests for ground_truth/models.py — file walker and _SKIP_DIRS behaviour."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sec_review_framework.ground_truth.models import TargetCodebase, _SKIP_DIRS


# ---------------------------------------------------------------------------
# _SKIP_DIRS membership
# ---------------------------------------------------------------------------


class TestSkipDirsMembership:
    """Verify the expected directories are in _SKIP_DIRS."""

    def test_standard_vcs_dir(self) -> None:
        assert ".git" in _SKIP_DIRS

    def test_node_modules(self) -> None:
        assert "node_modules" in _SKIP_DIRS

    def test_pycache(self) -> None:
        assert "__pycache__" in _SKIP_DIRS

    def test_maven_target(self) -> None:
        assert "target" in _SKIP_DIRS

    def test_gradle_build(self) -> None:
        assert "build" in _SKIP_DIRS

    def test_gradle_cache(self) -> None:
        assert ".gradle" in _SKIP_DIRS


# ---------------------------------------------------------------------------
# list_source_files — Java build artefact directories are pruned
# ---------------------------------------------------------------------------


class TestListSourceFilesJavaBuildDirsSkipped:
    """Build a minimal tmp repo with Java sources and build artefacts; assert
    list_source_files() only returns the real source files."""

    def _make_file(self, root: Path, rel: str, content: str = "x") -> None:
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)

    def test_target_dir_excluded(self, tmp_path: Path) -> None:
        self._make_file(tmp_path, "src/main/java/com/example/Foo.java", "class Foo {}")
        self._make_file(tmp_path, "target/Foo.class", "\xca\xfe\xba\xbe")  # binary-ish

        codebase = TargetCodebase(tmp_path)
        files = codebase.list_source_files()

        java_files = [f for f in files if f.endswith(".java")]
        target_files = [f for f in files if f.startswith("target" + os.sep) or f.startswith("target/")]
        assert len(java_files) == 1
        assert java_files[0].endswith("Foo.java")
        assert target_files == [], f"Expected no target/ files but got: {target_files}"

    def test_build_dir_excluded(self, tmp_path: Path) -> None:
        self._make_file(tmp_path, "app/Main.java", "class Main {}")
        self._make_file(tmp_path, "build/classes/Main.class", "")

        codebase = TargetCodebase(tmp_path)
        files = codebase.list_source_files()

        build_files = [f for f in files if f.startswith("build" + os.sep) or f.startswith("build/")]
        assert build_files == [], f"Expected no build/ files but got: {build_files}"

    def test_gradle_dir_excluded(self, tmp_path: Path) -> None:
        self._make_file(tmp_path, "app/Util.java", "class Util {}")
        self._make_file(tmp_path, ".gradle/caches/something.lock", "")

        codebase = TargetCodebase(tmp_path)
        files = codebase.list_source_files()

        gradle_files = [f for f in files if ".gradle" in f]
        assert gradle_files == [], f"Expected no .gradle files but got: {gradle_files}"

    def test_java_source_survives(self, tmp_path: Path) -> None:
        """Verify Java source files are returned when not inside a skip dir."""
        self._make_file(tmp_path, "src/Foo.java", "class Foo {}")
        self._make_file(tmp_path, "src/Bar.java", "class Bar {}")
        self._make_file(tmp_path, "target/Foo.class", "")

        codebase = TargetCodebase(tmp_path)
        files = codebase.list_source_files()

        java_files = sorted(f for f in files if f.endswith(".java"))
        assert java_files == [
            os.path.join("src", "Bar.java"),
            os.path.join("src", "Foo.java"),
        ]

    def test_mixed_project_real_sources_only(self, tmp_path: Path) -> None:
        """A realistic project layout: source, resources, and build artefacts."""
        # Real source files
        self._make_file(tmp_path, "src/main/java/App.java", "class App {}")
        self._make_file(tmp_path, "src/test/java/AppTest.java", "class AppTest {}")
        # Build artefacts — must all be pruned
        self._make_file(tmp_path, "target/classes/App.class", "")
        self._make_file(tmp_path, "target/test-classes/AppTest.class", "")
        self._make_file(tmp_path, "build/tmp/compileJava/App.class", "")
        self._make_file(tmp_path, ".gradle/7.0/executionHistory.bin", "")

        codebase = TargetCodebase(tmp_path)
        files = codebase.list_source_files()

        # Only .java files should appear
        java_files = [f for f in files if f.endswith(".java")]
        assert len(java_files) == 2

        # No artefact dirs
        for artefact in ("target", "build", ".gradle"):
            contaminated = [f for f in files if f.startswith(artefact)]
            assert contaminated == [], f"Expected no {artefact}/ files but got: {contaminated}"
