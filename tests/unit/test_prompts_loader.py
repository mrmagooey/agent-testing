"""Unit tests for sec_review_framework.prompts.loader."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from sec_review_framework.prompts.loader import load_system_prompt, load_user_prompt


# ---------------------------------------------------------------------------
# load_system_prompt — happy path
# ---------------------------------------------------------------------------


class TestLoadSystemPromptHappy:
    def test_returns_string(self):
        result = load_system_prompt("single_agent.txt")
        assert isinstance(result, str)

    def test_nonempty(self):
        result = load_system_prompt("single_agent.txt")
        assert len(result) > 0

    def test_stripped_leading_trailing_whitespace(self, tmp_path):
        prompt_dir = tmp_path / "system"
        prompt_dir.mkdir()
        (prompt_dir / "ws.txt").write_text("  hello world  \n\n", encoding="utf-8")

        import sec_review_framework.prompts.loader as loader
        original = loader._SYSTEM_DIR
        loader._SYSTEM_DIR = prompt_dir
        try:
            result = load_system_prompt("ws.txt")
        finally:
            loader._SYSTEM_DIR = original

        assert result == "hello world"

    def test_multipart_path(self):
        result = load_system_prompt("per_vuln_class", "sqli.txt")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_known_content_substring(self):
        result = load_system_prompt("single_agent.txt")
        assert "security" in result.lower()

    def test_no_trailing_newline(self):
        result = load_system_prompt("single_agent.txt")
        assert not result.endswith("\n")

    def test_no_leading_whitespace(self):
        result = load_system_prompt("single_agent.txt")
        assert result == result.lstrip()


# ---------------------------------------------------------------------------
# load_system_prompt — unicode safety
# ---------------------------------------------------------------------------


class TestLoadSystemPromptUnicode:
    def test_unicode_roundtrip(self, tmp_path):
        prompt_dir = tmp_path / "system"
        prompt_dir.mkdir()
        content = "You are a security reviewer. 中文 élève \U0001f512"
        (prompt_dir / "unicode.txt").write_text(content, encoding="utf-8")

        import sec_review_framework.prompts.loader as loader
        original = loader._SYSTEM_DIR
        loader._SYSTEM_DIR = prompt_dir
        try:
            result = load_system_prompt("unicode.txt")
        finally:
            loader._SYSTEM_DIR = original

        assert result == content

    def test_emoji_preserved(self, tmp_path):
        prompt_dir = tmp_path / "system"
        prompt_dir.mkdir()
        content = "\U0001f6e1️ secure this"
        (prompt_dir / "emoji.txt").write_text(content, encoding="utf-8")

        import sec_review_framework.prompts.loader as loader
        original = loader._SYSTEM_DIR
        loader._SYSTEM_DIR = prompt_dir
        try:
            result = load_system_prompt("emoji.txt")
        finally:
            loader._SYSTEM_DIR = original

        assert "\U0001f6e1" in result


# ---------------------------------------------------------------------------
# load_system_prompt — error paths
# ---------------------------------------------------------------------------


class TestLoadSystemPromptErrors:
    def test_missing_file_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_system_prompt("does_not_exist_xyz.txt")

    def test_missing_nested_file_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_system_prompt("per_vuln_class", "no_such_class.txt")

    def test_missing_directory_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_system_prompt("no_such_dir", "file.txt")


# ---------------------------------------------------------------------------
# load_user_prompt — happy path
# ---------------------------------------------------------------------------


class TestLoadUserPromptHappy:
    def test_returns_string(self):
        result = load_user_prompt("single_agent.txt")
        assert isinstance(result, str)

    def test_nonempty(self):
        result = load_user_prompt("single_agent.txt")
        assert len(result) > 0

    def test_stripped_leading_trailing_whitespace(self, tmp_path):
        prompt_dir = tmp_path / "user"
        prompt_dir.mkdir()
        (prompt_dir / "t.txt").write_text("\n\nReview {repo_summary}.\n\n", encoding="utf-8")

        import sec_review_framework.prompts.loader as loader
        original = loader._USER_DIR
        loader._USER_DIR = prompt_dir
        try:
            result = load_user_prompt("t.txt")
        finally:
            loader._USER_DIR = original

        assert result == "Review {repo_summary}."

    def test_multipart_path(self):
        result = load_user_prompt("per_vuln_class", "specialist.txt")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_no_trailing_newline(self):
        result = load_user_prompt("single_agent.txt")
        assert not result.endswith("\n")


# ---------------------------------------------------------------------------
# load_user_prompt — template placeholder substitution
# ---------------------------------------------------------------------------


class TestLoadUserPromptSubstitution:
    def test_single_agent_placeholders_present(self):
        template = load_user_prompt("single_agent.txt")
        placeholders = re.findall(r"\{(\w+)\}", template)
        assert len(placeholders) > 0

    def test_single_agent_required_placeholders_fillable(self):
        template = load_user_prompt("single_agent.txt")
        placeholders = set(re.findall(r"\{(\w+)\}", template))
        kwargs = {p: f"<{p}>" for p in placeholders}
        rendered = template.format(**kwargs)
        for placeholder in placeholders:
            assert f"<{placeholder}>" in rendered

    def test_missing_placeholder_raises_key_error(self):
        template = load_user_prompt("single_agent.txt")
        placeholders = set(re.findall(r"\{(\w+)\}", template))
        assert placeholders, "expected at least one placeholder in single_agent.txt"
        with pytest.raises(KeyError):
            template.format()

    def test_extra_kwargs_silently_ignored(self):
        template = load_user_prompt("single_agent.txt")
        placeholders = set(re.findall(r"\{(\w+)\}", template))
        kwargs = {p: f"<{p}>" for p in placeholders}
        kwargs["extra_unused_kwarg"] = "ignored"
        rendered = template.format(**kwargs)
        assert isinstance(rendered, str)

    def test_double_braces_not_treated_as_placeholders(self, tmp_path):
        prompt_dir = tmp_path / "user"
        prompt_dir.mkdir()
        (prompt_dir / "double_brace.txt").write_text(
            "Template with {{escaped}} and {real} placeholder.", encoding="utf-8"
        )

        import sec_review_framework.prompts.loader as loader
        original = loader._USER_DIR
        loader._USER_DIR = prompt_dir
        try:
            template = load_user_prompt("double_brace.txt")
        finally:
            loader._USER_DIR = original

        assert "{{escaped}}" in template

    def test_per_vuln_class_specialist_substitution(self):
        template = load_user_prompt("per_vuln_class", "specialist.txt")
        placeholders = set(re.findall(r"\{(\w+)\}", template))
        assert placeholders
        kwargs = {p: f"<{p}>" for p in placeholders}
        rendered = template.format(**kwargs)
        for placeholder in placeholders:
            assert f"<{placeholder}>" in rendered

    def test_custom_template_substitution(self, tmp_path):
        prompt_dir = tmp_path / "user"
        prompt_dir.mkdir()
        (prompt_dir / "custom.txt").write_text(
            "Review {target} for {vuln_class} issues.", encoding="utf-8"
        )

        import sec_review_framework.prompts.loader as loader
        original = loader._USER_DIR
        loader._USER_DIR = prompt_dir
        try:
            template = load_user_prompt("custom.txt")
        finally:
            loader._USER_DIR = original

        rendered = template.format(target="myapp", vuln_class="sqli")
        assert rendered == "Review myapp for sqli issues."


# ---------------------------------------------------------------------------
# load_user_prompt — unicode safety
# ---------------------------------------------------------------------------


class TestLoadUserPromptUnicode:
    def test_unicode_in_template_preserved(self, tmp_path):
        prompt_dir = tmp_path / "user"
        prompt_dir.mkdir()
        content = "Analysez {repo_summary} pour des vulnérabilités."
        (prompt_dir / "fr.txt").write_text(content, encoding="utf-8")

        import sec_review_framework.prompts.loader as loader
        original = loader._USER_DIR
        loader._USER_DIR = prompt_dir
        try:
            template = load_user_prompt("fr.txt")
        finally:
            loader._USER_DIR = original

        assert "vulnérabilités" in template
        rendered = template.format(repo_summary="mon-projet")
        assert "mon-projet" in rendered

    def test_unicode_substitution_value(self, tmp_path):
        prompt_dir = tmp_path / "user"
        prompt_dir.mkdir()
        (prompt_dir / "u.txt").write_text("Scan {target}.", encoding="utf-8")

        import sec_review_framework.prompts.loader as loader
        original = loader._USER_DIR
        loader._USER_DIR = prompt_dir
        try:
            template = load_user_prompt("u.txt")
        finally:
            loader._USER_DIR = original

        rendered = template.format(target="中文代码库")
        assert "中文代码库" in rendered


# ---------------------------------------------------------------------------
# load_user_prompt — error paths
# ---------------------------------------------------------------------------


class TestLoadUserPromptErrors:
    def test_missing_file_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_user_prompt("does_not_exist_xyz.txt")

    def test_missing_nested_file_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_user_prompt("per_vuln_class", "no_such_specialist.txt")

    def test_missing_directory_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            load_user_prompt("no_such_dir", "file.txt")
