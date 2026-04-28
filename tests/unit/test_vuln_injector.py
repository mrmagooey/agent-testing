"""Unit tests for VulnInjector."""

import pytest
import yaml

from sec_review_framework.ground_truth.vuln_injector import VulnInjector


@pytest.fixture
def templates_dir(tmp_path):
    """Create a tmp directory with a sample YAML injection template."""
    tmpl_dir = tmp_path / "templates" / "sqli"
    tmpl_dir.mkdir(parents=True)
    template_data = {
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
    (tmpl_dir / "sqli_format_string_python.yaml").write_text(yaml.dump(template_data))
    return tmp_path / "templates"


@pytest.fixture
def injector(templates_dir):
    return VulnInjector(templates_root=templates_dir)


def test_template_loading(injector):
    """VulnInjector discovers YAML templates from templates directory."""
    assert len(injector.templates) == 1
    assert "sqli_format_string_python" in injector.templates


def test_template_loading_empty_dir(tmp_path):
    """Empty templates directory yields no templates."""
    inj = VulnInjector(templates_root=tmp_path / "nonexistent")
    assert len(inj.templates) == 0


def test_template_loading_malformed_skipped(tmp_path):
    """Invalid YAML files are skipped without error."""
    tmpl_dir = tmp_path / "templates"
    tmpl_dir.mkdir()
    (tmpl_dir / "bad.yaml").write_text("this is not: valid: yaml: [")
    inj = VulnInjector(templates_root=tmpl_dir)
    assert len(inj.templates) == 0


def test_preview_does_not_modify_file(injector, tmp_path):
    """preview() returns diff without modifying the source file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "views.py"
    original = "def search(request):\n    pass\n"
    source.write_text(original)

    preview = injector.preview(
        repo_path=repo,
        template_id="sqli_format_string_python",
        target_file="views.py",
    )

    assert preview is not None
    assert preview.unified_diff != ""
    assert source.read_text() == original


def test_inject_modifies_file(injector, tmp_path):
    """inject() modifies the file and returns an InjectionResult with a label."""
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "views.py"
    source.write_text("def search(request):\n    pass\n")

    result = injector.inject(
        repo_path=repo,
        template_id="sqli_format_string_python",
        target_file="views.py",
    )

    assert result is not None
    assert result.template_id == "sqli_format_string_python"
    modified = source.read_text()
    assert "SELECT * FROM users" in modified


def test_inject_label_records_patch_lines_changed(injector, tmp_path):
    """The label attached to the InjectionResult carries patch_lines_changed matching lines_added."""
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "views.py"
    source.write_text("def search(request):\n    pass\n")

    result = injector.inject(
        repo_path=repo,
        template_id="sqli_format_string_python",
        target_file="views.py",
    )

    assert result.lines_added > 0
    assert result.label.patch_lines_changed == result.lines_added
