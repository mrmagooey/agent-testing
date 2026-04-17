"""Unit tests for the DevDocs MCP server logic.

Tests exercise the server functions directly against a small on-disk fixture
(tests/fixtures/devdocs/) — no subprocess is started, no network access.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Fixture directory relative to the tests package root.
_FIXTURES_ROOT = Path(__file__).parent.parent / "fixtures" / "devdocs"


# ---------------------------------------------------------------------------
# Import the server module under test
# ---------------------------------------------------------------------------

from sec_review_framework.tools.extensions.devdocs_server import (
    _dispatch,
    _discover_docsets,
    _html_to_text,
    _load_index,
    _score_entry,
    _tool_fetch,
    _tool_list_docsets,
    _tool_search,
    _validate_path,
    _validate_slug,
    _INDEX_CACHE,
)


@pytest.fixture(autouse=True)
def _clear_index_cache():
    """Clear the module-level index cache before each test for isolation."""
    _INDEX_CACHE.clear()
    yield
    _INDEX_CACHE.clear()


# ---------------------------------------------------------------------------
# Security validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_slug_with_dotdot_raises(self):
        with pytest.raises(ValueError, match="Unsafe"):
            _validate_slug("../etc/passwd")

    def test_slug_with_leading_slash_raises(self):
        with pytest.raises(ValueError, match="Unsafe"):
            _validate_slug("/abs/path")

    def test_slug_with_shell_metachar_raises(self):
        with pytest.raises(ValueError, match="Unsafe"):
            _validate_slug("python;rm -rf /")

    def test_slug_with_backtick_raises(self):
        with pytest.raises(ValueError, match="Unsafe"):
            _validate_slug("`cmd`")

    def test_valid_slug_passes(self):
        _validate_slug("python~3.12")
        _validate_slug("javascript")
        _validate_slug("go")
        _validate_slug("cpp")

    def test_path_with_dotdot_raises(self):
        with pytest.raises(ValueError, match="Unsafe"):
            _validate_path("../secret")

    def test_path_with_leading_slash_raises(self):
        with pytest.raises(ValueError, match="Unsafe"):
            _validate_path("/etc/passwd")

    def test_valid_path_passes(self):
        _validate_path("subprocess#subprocess.Popen")
        _validate_path("global_objects/eval")


# ---------------------------------------------------------------------------
# HTML to text
# ---------------------------------------------------------------------------

class TestHtmlToText:
    def test_strips_tags(self):
        html = "<p>Hello <strong>world</strong></p>"
        text = _html_to_text(html)
        assert "Hello" in text
        assert "world" in text
        assert "<" not in text

    def test_truncates_long_content(self):
        html = "<p>" + ("x" * 15_000) + "</p>"
        text = _html_to_text(html)
        assert len(text) <= 10_240 + 200  # allow for marker length
        assert "truncated" in text

    def test_skips_script_content(self):
        html = "<p>visible</p><script>alert('xss')</script><p>also visible</p>"
        text = _html_to_text(html)
        assert "visible" in text
        assert "alert" not in text

    def test_empty_input(self):
        assert _html_to_text("") == ""

    def test_plain_text_passthrough(self):
        text = _html_to_text("just plain text")
        assert text == "just plain text"


# ---------------------------------------------------------------------------
# Score entry
# ---------------------------------------------------------------------------

class TestScoreEntry:
    def test_exact_match_highest_score(self):
        assert _score_entry("subprocess", "subprocess") == 100

    def test_prefix_match_high_score(self):
        assert _score_entry("subprocess.Popen", "subprocess") >= 60

    def test_no_match_zero(self):
        assert _score_entry("hashlib.sha256", "subprocess") == 0

    def test_case_insensitive(self):
        assert _score_entry("Subprocess.Popen", "subprocess") > 0

    def test_word_boundary_match(self):
        # "run" appears as a word boundary in "subprocess.run"
        score = _score_entry("subprocess.run", "run")
        assert score > 0


# ---------------------------------------------------------------------------
# doc_list_docsets
# ---------------------------------------------------------------------------

class TestDocListDocsets:
    def test_lists_both_fixture_docsets(self):
        result = json.loads(_tool_list_docsets(_FIXTURES_ROOT, None))
        slugs = {d["slug"] for d in result}
        assert "python~3.12" in slugs
        assert "javascript" in slugs

    def test_result_includes_name_version_count(self):
        result = json.loads(_tool_list_docsets(_FIXTURES_ROOT, None))
        py = next(d for d in result if d["slug"] == "python~3.12")
        assert py["name"] == "python"
        assert py["version"] == "3.12"
        assert py["doc_count"] > 0

    def test_allow_docsets_filters_results(self):
        allow = frozenset({"javascript"})
        result = json.loads(_tool_list_docsets(_FIXTURES_ROOT, allow))
        slugs = {d["slug"] for d in result}
        assert "javascript" in slugs
        assert "python~3.12" not in slugs

    def test_empty_root_returns_empty_list(self, tmp_path):
        result = json.loads(_tool_list_docsets(tmp_path, None))
        assert result == []


# ---------------------------------------------------------------------------
# doc_search
# ---------------------------------------------------------------------------

class TestDocSearch:
    def test_search_finds_subprocess_in_python(self):
        result = json.loads(
            _tool_search(_FIXTURES_ROOT, None, "subprocess", None, 20)
        )
        names = {r["name"] for r in result}
        assert any("subprocess" in n.lower() for n in names)

    def test_search_respects_docset_filter(self):
        result = json.loads(
            _tool_search(_FIXTURES_ROOT, None, "eval", "javascript", 20)
        )
        assert all(r["docset"] == "javascript" for r in result)

    def test_search_returns_empty_for_no_match(self):
        result = json.loads(
            _tool_search(_FIXTURES_ROOT, None, "zzznomatch", None, 20)
        )
        assert result == []

    def test_search_respects_limit(self):
        # "s" matches many entries across both docsets
        result = json.loads(
            _tool_search(_FIXTURES_ROOT, None, "s", None, 3)
        )
        assert len(result) <= 3

    def test_search_disallowed_docset_returns_error(self):
        allow = frozenset({"javascript"})
        result = json.loads(
            _tool_search(_FIXTURES_ROOT, allow, "subprocess", "python~3.12", 20)
        )
        assert "error" in result

    def test_search_sorted_by_score_descending(self):
        result = json.loads(
            _tool_search(_FIXTURES_ROOT, None, "subprocess", None, 20)
        )
        scores = [r["score"] for r in result]
        assert scores == sorted(scores, reverse=True)

    def test_search_unsafe_docset_slug_raises(self):
        with pytest.raises(ValueError, match="Unsafe"):
            _tool_search(_FIXTURES_ROOT, None, "subprocess", "../etc", 20)


# ---------------------------------------------------------------------------
# doc_fetch
# ---------------------------------------------------------------------------

class TestDocFetch:
    def test_fetch_known_entry(self):
        result = json.loads(
            _tool_fetch(
                _FIXTURES_ROOT, None,
                docset="python~3.12",
                path="subprocess#subprocess.Popen",
            )
        )
        assert result["docset"] == "python~3.12"
        assert result["path"] == "subprocess#subprocess.Popen"
        assert "subprocess" in result["html"].lower()
        assert "subprocess" in result["text"].lower()
        assert "<" not in result["text"]  # text is stripped

    def test_fetch_returns_both_html_and_text(self):
        result = json.loads(
            _tool_fetch(
                _FIXTURES_ROOT, None,
                docset="javascript",
                path="global_objects/eval",
            )
        )
        assert "html" in result
        assert "text" in result
        assert "<h2>" in result["html"]
        assert "<h2>" not in result["text"]

    def test_fetch_missing_entry_returns_error(self):
        result = json.loads(
            _tool_fetch(
                _FIXTURES_ROOT, None,
                docset="python~3.12",
                path="nonexistent#path",
            )
        )
        assert "error" in result

    def test_fetch_missing_docset_returns_error(self):
        result = json.loads(
            _tool_fetch(
                _FIXTURES_ROOT, None,
                docset="nonexistent_docset",
                path="something",
            )
        )
        assert "error" in result

    def test_fetch_disallowed_docset_returns_error(self):
        allow = frozenset({"javascript"})
        result = json.loads(
            _tool_fetch(
                _FIXTURES_ROOT, allow,
                docset="python~3.12",
                path="subprocess#subprocess.Popen",
            )
        )
        assert "error" in result

    def test_fetch_path_traversal_raises(self):
        with pytest.raises(ValueError, match="Unsafe"):
            _tool_fetch(_FIXTURES_ROOT, None, docset="../etc", path="passwd")

    def test_fetch_path_with_dotdot_raises(self):
        with pytest.raises(ValueError, match="Unsafe"):
            _tool_fetch(_FIXTURES_ROOT, None, docset="python~3.12", path="../secret")


# ---------------------------------------------------------------------------
# _dispatch routing
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_dispatch_list_docsets(self):
        result = _dispatch("list_docsets", {}, _FIXTURES_ROOT, None)
        data = json.loads(result)
        assert isinstance(data, list)

    def test_dispatch_search(self):
        result = _dispatch(
            "search", {"query": "subprocess"}, _FIXTURES_ROOT, None
        )
        data = json.loads(result)
        assert isinstance(data, list)

    def test_dispatch_fetch(self):
        result = _dispatch(
            "fetch",
            {"docset": "python~3.12", "path": "hashlib#hashlib.sha256"},
            _FIXTURES_ROOT,
            None,
        )
        data = json.loads(result)
        assert "html" in data or "error" in data

    def test_dispatch_unknown_tool_raises(self):
        with pytest.raises(ValueError, match="Unknown tool"):
            _dispatch("nonexistent_tool", {}, _FIXTURES_ROOT, None)


# ---------------------------------------------------------------------------
# Lazy index cache
# ---------------------------------------------------------------------------

class TestIndexCache:
    def test_cache_populated_after_first_load(self):
        assert "python~3.12" not in _INDEX_CACHE
        _load_index(_FIXTURES_ROOT, "python~3.12")
        assert "python~3.12" in _INDEX_CACHE

    def test_second_load_uses_cache(self, tmp_path):
        # Prime the cache with a manually inserted entry.
        _INDEX_CACHE["fake_slug"] = [{"name": "CachedEntry", "path": "x", "type": "t"}]
        result = _load_index(tmp_path, "fake_slug")
        assert result[0]["name"] == "CachedEntry"
