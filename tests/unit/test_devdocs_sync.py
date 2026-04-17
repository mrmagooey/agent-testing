"""Unit tests for the DevDocs sync utility.

No real network calls are made — a fake downloader is injected in all tests.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from sec_review_framework.data.devdocs_sync import (
    DEFAULT_DOCSETS,
    _DOCSET_FILES,
    _sync_docset,
    _write_manifest,
    sync,
)


# ---------------------------------------------------------------------------
# Fake downloader
# ---------------------------------------------------------------------------

def _make_fake_downloader(content: bytes = b'[]'):
    """Return a downloader that always returns *content*."""
    recorded_urls: list[str] = []

    class FakeResponse:
        def read(self) -> bytes:
            return content

    def downloader(url: str) -> FakeResponse:
        recorded_urls.append(url)
        return FakeResponse()

    downloader.recorded_urls = recorded_urls  # type: ignore[attr-defined]
    return downloader


def _make_failing_downloader(exc: Exception = OSError("network error")):
    """Return a downloader that always raises *exc*."""
    def downloader(url: str) -> Any:
        raise exc
    return downloader


# ---------------------------------------------------------------------------
# _sync_docset
# ---------------------------------------------------------------------------

class TestSyncDocset:
    def test_creates_docset_directory(self, tmp_path: Path) -> None:
        dl = _make_fake_downloader(b'{"entries": []}')
        result = _sync_docset(tmp_path, "python~3.12", force=False, downloader=dl)
        assert (tmp_path / "python~3.12").is_dir()

    def test_downloads_both_files(self, tmp_path: Path) -> None:
        dl = _make_fake_downloader(b'[]')
        _sync_docset(tmp_path, "javascript", force=False, downloader=dl)
        assert (tmp_path / "javascript" / "index.json").exists()
        assert (tmp_path / "javascript" / "db.json").exists()

    def test_skips_existing_files_without_force(self, tmp_path: Path) -> None:
        docset_dir = tmp_path / "go"
        docset_dir.mkdir()
        (docset_dir / "index.json").write_bytes(b"original")
        (docset_dir / "db.json").write_bytes(b"original")

        dl = _make_fake_downloader(b"replaced")
        result = _sync_docset(tmp_path, "go", force=False, downloader=dl)

        assert result["skipped"] == ["index.json", "db.json"]
        assert result["downloaded"] == []
        # Files must NOT be overwritten.
        assert (docset_dir / "index.json").read_bytes() == b"original"

    def test_overwrites_existing_files_with_force(self, tmp_path: Path) -> None:
        docset_dir = tmp_path / "rust"
        docset_dir.mkdir()
        (docset_dir / "index.json").write_bytes(b"original")
        (docset_dir / "db.json").write_bytes(b"original")

        dl = _make_fake_downloader(b"replaced")
        result = _sync_docset(tmp_path, "rust", force=True, downloader=dl)

        assert result["downloaded"] == ["index.json", "db.json"]
        assert (docset_dir / "index.json").read_bytes() == b"replaced"

    def test_records_errors_on_download_failure(self, tmp_path: Path) -> None:
        dl = _make_failing_downloader(OSError("connection refused"))
        result = _sync_docset(tmp_path, "typescript", force=False, downloader=dl)
        assert len(result["errors"]) == 2  # index.json and db.json both failed

    def test_result_has_required_keys(self, tmp_path: Path) -> None:
        dl = _make_fake_downloader(b"[]")
        result = _sync_docset(tmp_path, "go", force=False, downloader=dl)
        for key in ("slug", "downloaded", "skipped", "errors", "timestamp"):
            assert key in result


# ---------------------------------------------------------------------------
# _write_manifest
# ---------------------------------------------------------------------------

class TestWriteManifest:
    def test_manifest_file_created(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path, [])
        assert (tmp_path / "_manifest.json").exists()

    def test_manifest_contains_docsets(self, tmp_path: Path) -> None:
        results = [
            {"slug": "python~3.12", "downloaded": ["index.json"], "skipped": [], "errors": [], "timestamp": "2025-01-01T00:00:00+00:00"},
        ]
        _write_manifest(tmp_path, results)
        manifest = json.loads((tmp_path / "_manifest.json").read_text())
        assert "generated_at" in manifest
        assert "base_url" in manifest
        assert len(manifest["docsets"]) == 1
        assert manifest["docsets"][0]["slug"] == "python~3.12"


# ---------------------------------------------------------------------------
# sync (top-level)
# ---------------------------------------------------------------------------

class TestSync:
    def test_sync_downloads_all_docsets(self, tmp_path: Path) -> None:
        dl = _make_fake_downloader(b'[]')
        results = sync(tmp_path, ["python~3.12", "javascript"], downloader=dl)
        assert len(results) == 2
        assert {r["slug"] for r in results} == {"python~3.12", "javascript"}

    def test_sync_creates_manifest(self, tmp_path: Path) -> None:
        dl = _make_fake_downloader(b'[]')
        sync(tmp_path, ["go"], downloader=dl)
        assert (tmp_path / "_manifest.json").exists()

    def test_sync_creates_root_if_missing(self, tmp_path: Path) -> None:
        absent = tmp_path / "new_root"
        dl = _make_fake_downloader(b'[]')
        sync(absent, ["javascript"], downloader=dl)
        assert absent.is_dir()

    def test_sync_urls_hit_devdocs_base(self, tmp_path: Path) -> None:
        dl = _make_fake_downloader(b'[]')
        sync(tmp_path, ["python~3.12"], downloader=dl)
        assert any("documents.devdocs.io" in u for u in dl.recorded_urls)

    def test_sync_url_format_is_correct(self, tmp_path: Path) -> None:
        dl = _make_fake_downloader(b'[]')
        sync(tmp_path, ["python~3.12"], downloader=dl)
        # Expect URLs like: https://documents.devdocs.io/python~3.12/index.json
        expected = {
            "https://documents.devdocs.io/python~3.12/index.json",
            "https://documents.devdocs.io/python~3.12/db.json",
        }
        assert set(dl.recorded_urls) == expected

    def test_sync_force_redownloads(self, tmp_path: Path) -> None:
        dl_first = _make_fake_downloader(b'"first"')
        sync(tmp_path, ["go"], downloader=dl_first)

        dl_second = _make_fake_downloader(b'"second"')
        sync(tmp_path, ["go"], force=True, downloader=dl_second)

        assert (tmp_path / "go" / "index.json").read_bytes() == b'"second"'

    def test_sync_returns_results_list(self, tmp_path: Path) -> None:
        dl = _make_fake_downloader(b'[]')
        results = sync(tmp_path, ["rust", "cpp"], downloader=dl)
        assert isinstance(results, list)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# DEFAULT_DOCSETS sanity
# ---------------------------------------------------------------------------

class TestDefaultDocsets:
    def test_default_docsets_is_nonempty(self) -> None:
        assert len(DEFAULT_DOCSETS) > 0

    def test_default_docsets_contains_python(self) -> None:
        assert any("python" in s for s in DEFAULT_DOCSETS)

    def test_default_docsets_contains_javascript(self) -> None:
        assert "javascript" in DEFAULT_DOCSETS
