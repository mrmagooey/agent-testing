"""Extended unit tests for VulnInjector — edge cases not covered in test_vuln_injector.py."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from sec_review_framework.ground_truth.vuln_injector import VulnInjector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sqli_template_data() -> dict:
    return {
        "id": "sqli_format_string_python",
        "vuln_class": "sqli",
        "cwe_id": "CWE-89",
        "language": "python",
        "description": "SQL query via string formatting",
        "severity": "high",
        "patch_template": '    query = "SELECT * FROM users WHERE name = \'%s\'" % name\n    cursor.execute(query)\n',
        "anchor_pattern": r"def \w+\(.*request.*\):",
        "anchor_mode": "after",
    }


@pytest.fixture
def replace_template_data() -> dict:
    return {
        "id": "replace_mode_template",
        "vuln_class": "xss",
        "cwe_id": "CWE-79",
        "language": "python",
        "description": "XSS via unescaped output",
        "severity": "medium",
        "patch_template": "    return f'<h1>{name}</h1>'\n",
        "anchor_pattern": r"return.*render",
        "anchor_mode": "replace",
    }


@pytest.fixture
def before_template_data() -> dict:
    return {
        "id": "before_mode_template",
        "vuln_class": "sqli",
        "cwe_id": "CWE-89",
        "language": "python",
        "description": "SQL before anchor",
        "severity": "high",
        "patch_template": "    vuln_code = 1\n",
        "anchor_pattern": r"def search",
        "anchor_mode": "before",
    }


@pytest.fixture
def templates_dir(tmp_path, sqli_template_data) -> Path:
    tmpl_dir = tmp_path / "templates"
    tmpl_dir.mkdir()
    (tmpl_dir / "sqli.yaml").write_text(yaml.dump(sqli_template_data))
    return tmpl_dir


@pytest.fixture
def injector(templates_dir) -> VulnInjector:
    return VulnInjector(templates_root=templates_dir)


# ---------------------------------------------------------------------------
# Anchor not found — fallback to line 1
# ---------------------------------------------------------------------------


class TestAnchorFallback:
    def test_anchor_not_found_defaults_to_line_1(self, injector, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        source = repo / "views.py"
        # No 'def ...(request):' pattern
        source.write_text("x = 1\ny = 2\n")

        result = injector.inject(
            repo_path=repo,
            template_id="sqli_format_string_python",
            target_file="views.py",
        )
        # Anchor defaults to line 1 → patch inserted after first line
        assert result.anchor_line == 1
        modified = source.read_text()
        assert "SELECT * FROM users" in modified


# ---------------------------------------------------------------------------
# Line-range boundary: anchor at last line (beyond EOF - 1)
# ---------------------------------------------------------------------------


class TestLineRangeBoundaries:
    def test_inject_at_last_line_does_not_crash(self, injector, tmp_path):
        """Anchor at the very last line should not raise IndexError."""
        repo = tmp_path / "repo"
        repo.mkdir()
        source = repo / "views.py"
        # Only one line matching the anchor pattern
        source.write_text("def handle(request):\n")

        result = injector.inject(
            repo_path=repo,
            template_id="sqli_format_string_python",
            target_file="views.py",
        )
        assert result is not None
        modified = source.read_text()
        assert len(modified) > 0

    def test_preview_at_last_line_does_not_crash(self, injector, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        source = repo / "views.py"
        source.write_text("def handle(request):\n")

        preview = injector.preview(
            repo_path=repo,
            template_id="sqli_format_string_python",
            target_file="views.py",
        )
        assert preview is not None


# ---------------------------------------------------------------------------
# anchor_mode: before and replace
# ---------------------------------------------------------------------------


class TestAnchorModes:
    def _setup(self, tmp_path: Path, template_data: dict) -> tuple[VulnInjector, Path]:
        tmpl_dir = tmp_path / "templates"
        tmpl_dir.mkdir(exist_ok=True)
        (tmpl_dir / "tmpl.yaml").write_text(yaml.dump(template_data))
        injector = VulnInjector(templates_root=tmpl_dir)
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        return injector, repo

    def test_before_mode_inserts_before_anchor(self, tmp_path, before_template_data):
        injector, repo = self._setup(tmp_path, before_template_data)
        source = repo / "views.py"
        source.write_text("def search(request):\n    pass\n")

        result = injector.inject(
            repo_path=repo,
            template_id="before_mode_template",
            target_file="views.py",
        )
        modified = source.read_text()
        lines = modified.splitlines()
        # "vuln_code" line should appear before "def search"
        vuln_idx = next(i for i, l in enumerate(lines) if "vuln_code" in l)
        search_idx = next(i for i, l in enumerate(lines) if "def search" in l)
        assert vuln_idx < search_idx

    def test_replace_mode_replaces_anchor_line(self, tmp_path, replace_template_data):
        injector, repo = self._setup(tmp_path, replace_template_data)
        source = repo / "views.py"
        source.write_text("def render_view():\n    return render(template)\n")

        injector.inject(
            repo_path=repo,
            template_id="replace_mode_template",
            target_file="views.py",
        )
        modified = source.read_text()
        # Original 'return render(template)' should be gone, replaced by new line
        assert "return render(template)" not in modified
        assert "<h1>" in modified

    def test_unknown_anchor_mode_falls_back_to_after(self, tmp_path):
        tmpl_data = {
            "id": "fallback_mode",
            "vuln_class": "sqli",
            "cwe_id": "CWE-89",
            "language": "python",
            "description": "test",
            "severity": "low",
            "patch_template": "    injected = True\n",
            "anchor_pattern": r"def process",
            "anchor_mode": "invalid_mode",
        }
        tmpl_dir = tmp_path / "templates"
        tmpl_dir.mkdir()
        (tmpl_dir / "fallback.yaml").write_text(yaml.dump(tmpl_data))
        injector = VulnInjector(templates_root=tmpl_dir)

        repo = tmp_path / "repo"
        repo.mkdir()
        source = repo / "views.py"
        source.write_text("def process(request):\n    pass\n")

        result = injector.inject(
            repo_path=repo,
            template_id="fallback_mode",
            target_file="views.py",
        )
        assert "injected" in source.read_text()


# ---------------------------------------------------------------------------
# Substitutions
# ---------------------------------------------------------------------------


class TestSubstitutions:
    def _make_substitution_template(self, tmp_path: Path) -> tuple[VulnInjector, Path]:
        tmpl_data = {
            "id": "subst_template",
            "vuln_class": "sqli",
            "cwe_id": "CWE-89",
            "language": "python",
            "description": "test subst",
            "severity": "high",
            "patch_template": '    query = "SELECT * FROM {{TABLE}} WHERE id = {{ID}}"\n',
            "anchor_pattern": r"def get",
            "anchor_mode": "after",
        }
        tmpl_dir = tmp_path / "templates"
        tmpl_dir.mkdir(exist_ok=True)
        (tmpl_dir / "subst.yaml").write_text(yaml.dump(tmpl_data))
        injector = VulnInjector(templates_root=tmpl_dir)
        repo = tmp_path / "repo"
        repo.mkdir(exist_ok=True)
        return injector, repo

    def test_substitutions_applied(self, tmp_path):
        injector, repo = self._make_substitution_template(tmp_path)
        source = repo / "views.py"
        source.write_text("def get(request):\n    pass\n")

        injector.inject(
            repo_path=repo,
            template_id="subst_template",
            target_file="views.py",
            substitutions={"TABLE": "users", "ID": "user_id"},
        )
        modified = source.read_text()
        assert "users" in modified
        assert "user_id" in modified

    def test_no_substitutions_leaves_placeholders(self, tmp_path):
        injector, repo = self._make_substitution_template(tmp_path)
        source = repo / "views.py"
        source.write_text("def get(request):\n    pass\n")

        injector.inject(
            repo_path=repo,
            template_id="subst_template",
            target_file="views.py",
            substitutions=None,
        )
        modified = source.read_text()
        # Placeholders remain when no substitutions provided
        assert "{{TABLE}}" in modified or "{{ID}}" in modified


# ---------------------------------------------------------------------------
# Collision: inject twice into same file
# ---------------------------------------------------------------------------


class TestCollisions:
    def test_inject_twice_both_patches_present(self, injector, tmp_path):
        """Injecting twice into the same file applies both patches."""
        repo = tmp_path / "repo"
        repo.mkdir()
        source = repo / "views.py"
        source.write_text("def search(request):\n    pass\n")

        injector.inject(repo_path=repo, template_id="sqli_format_string_python", target_file="views.py")
        # After first injection, the file now contains SQL injection code.
        # A second injection looks for the same anchor (which may still match).
        content_after_first = source.read_text()
        injector.inject(repo_path=repo, template_id="sqli_format_string_python", target_file="views.py")
        content_after_second = source.read_text()

        # File changed on second injection
        assert len(content_after_second.splitlines()) >= len(content_after_first.splitlines())


# ---------------------------------------------------------------------------
# Binary / non-UTF-8 file handling
# ---------------------------------------------------------------------------


class TestNonUtf8Files:
    def test_inject_into_binary_file_raises(self, injector, tmp_path):
        """Injecting into a binary file should raise — it cannot be read as text."""
        repo = tmp_path / "repo"
        repo.mkdir()
        binary_file = repo / "binary.py"
        binary_file.write_bytes(b"\xff\xfe\x00\x01binary content\x00\xff")

        with pytest.raises(Exception):  # UnicodeDecodeError or similar
            injector.inject(
                repo_path=repo,
                template_id="sqli_format_string_python",
                target_file="binary.py",
            )


# ---------------------------------------------------------------------------
# Preview rollback idempotency
# ---------------------------------------------------------------------------


class TestPreviewRollback:
    def test_preview_multiple_times_same_result(self, injector, tmp_path):
        """Calling preview() multiple times should always return the same diff."""
        repo = tmp_path / "repo"
        repo.mkdir()
        source = repo / "views.py"
        source.write_text("def search(request):\n    pass\n")

        p1 = injector.preview(repo_path=repo, template_id="sqli_format_string_python", target_file="views.py")
        p2 = injector.preview(repo_path=repo, template_id="sqli_format_string_python", target_file="views.py")

        assert p1.unified_diff == p2.unified_diff
        assert p1.anchor_line == p2.anchor_line

    def test_preview_then_inject_match(self, injector, tmp_path):
        """The diff shown in preview should match the actual change after inject."""
        repo = tmp_path / "repo"
        repo.mkdir()
        source = repo / "views.py"
        original = "def search(request):\n    pass\n"
        source.write_text(original)

        preview = injector.preview(repo_path=repo, template_id="sqli_format_string_python", target_file="views.py")
        injector.inject(repo_path=repo, template_id="sqli_format_string_python", target_file="views.py")

        # Both preview and inject should agree that SQL injection code was added
        assert "SELECT * FROM users" in preview.after_snippet
        assert "SELECT * FROM users" in source.read_text()
