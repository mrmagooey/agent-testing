"""Unit tests for the archive materializer.

All archives are built synthetically in tmpdir — no real network access.
``urlretrieve`` is patched to copy a pre-built local file instead of hitting
the network.
"""

from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from sec_review_framework.ground_truth.archive_materializer import (
    ArchiveCorruptError,
    ArchiveHashMismatch,
    ArchiveSecurityError,
    materialize_archive_dataset,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_tar_gz(files: dict[str, bytes]) -> bytes:
    """Return in-memory tar.gz archive with the given filename → content mapping."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def _make_zip(files: dict[str, bytes]) -> bytes:
    """Return in-memory zip archive with the given filename → content mapping."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _make_dataset_row(*, url: str, sha256: str, fmt: str) -> dict:
    return {
        "archive_url": url,
        "archive_sha256": sha256,
        "archive_format": fmt,
    }


def _fake_urlretrieve(archive_bytes: bytes):
    """Return a urlretrieve patch that writes *archive_bytes* to the tmp path arg."""

    def _impl(url: str, filename: str) -> tuple[str, object]:  # noqa: ARG001
        Path(filename).write_bytes(archive_bytes)
        return (filename, {})

    return _impl


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dirs(tmp_path: Path):
    """Return (cache_dir, target_dir) under tmp_path."""
    cache = tmp_path / "cache"
    target = tmp_path / "repo"
    cache.mkdir()
    return cache, target


# ---------------------------------------------------------------------------
# Happy path — tar.gz
# ---------------------------------------------------------------------------


async def test_happy_path_tar_gz(tmp_dirs):
    """Valid tar.gz: extracts two files, verifies sha256, creates git tree."""
    cache_dir, target_dir = tmp_dirs
    archive_bytes = _make_tar_gz(
        {
            "hello.py": b"print('hello')\n",
            "world.py": b"print('world')\n",
        }
    )
    sha256 = _sha256(archive_bytes)
    row = _make_dataset_row(url="http://example.test/archive.tar.gz", sha256=sha256, fmt="tar.gz")

    with patch(
        "sec_review_framework.ground_truth.archive_materializer.urlretrieve",
        side_effect=_fake_urlretrieve(archive_bytes),
    ):
        await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)

    assert (target_dir / "hello.py").exists()
    assert (target_dir / "world.py").exists()
    assert (target_dir / ".git").is_dir()


# ---------------------------------------------------------------------------
# Happy path — zip
# ---------------------------------------------------------------------------


async def test_happy_path_zip(tmp_dirs):
    """Valid zip: extracts files and creates git tree."""
    cache_dir, target_dir = tmp_dirs
    archive_bytes = _make_zip(
        {
            "src/main.py": b"x = 1\n",
            "README": b"hello\n",
        }
    )
    sha256 = _sha256(archive_bytes)
    row = _make_dataset_row(url="http://example.test/archive.zip", sha256=sha256, fmt="zip")

    with patch(
        "sec_review_framework.ground_truth.archive_materializer.urlretrieve",
        side_effect=_fake_urlretrieve(archive_bytes),
    ):
        await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)

    assert (target_dir / "src" / "main.py").exists()
    assert (target_dir / ".git").is_dir()


# ---------------------------------------------------------------------------
# Hash mismatch
# ---------------------------------------------------------------------------


async def test_hash_mismatch_raises(tmp_dirs):
    """Providing a wrong sha256 causes ArchiveHashMismatch; no tmp files left."""
    cache_dir, target_dir = tmp_dirs
    archive_bytes = _make_tar_gz({"file.py": b"x = 1\n"})
    wrong_sha256 = "a" * 64  # definitely wrong

    row = _make_dataset_row(url="http://example.test/archive.tar.gz", sha256=wrong_sha256, fmt="tar.gz")

    with patch(
        "sec_review_framework.ground_truth.archive_materializer.urlretrieve",
        side_effect=_fake_urlretrieve(archive_bytes),
    ):
        with pytest.raises(ArchiveHashMismatch) as exc_info:
            await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)

    assert exc_info.value.expected == wrong_sha256
    assert exc_info.value.got == _sha256(archive_bytes)
    # Atomic write: no .tmp files left behind.
    assert list(cache_dir.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# Path traversal in tar — ../escape.txt
# ---------------------------------------------------------------------------


async def test_path_traversal_tar_rejected(tmp_dirs):
    """Tar entry with ../escape.txt is rejected as ArchiveSecurityError."""
    cache_dir, target_dir = tmp_dirs

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        evil = tarfile.TarInfo(name="../escape.txt")
        evil.size = 4
        tf.addfile(evil, io.BytesIO(b"pwnd"))
    archive_bytes = buf.getvalue()
    sha256 = _sha256(archive_bytes)

    row = _make_dataset_row(url="http://example.test/evil.tar.gz", sha256=sha256, fmt="tar.gz")

    with patch(
        "sec_review_framework.ground_truth.archive_materializer.urlretrieve",
        side_effect=_fake_urlretrieve(archive_bytes),
    ):
        with pytest.raises(ArchiveSecurityError):
            await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)

    # target_dir must not exist or must contain no regular files from the archive.
    if target_dir.exists():
        extracted_files = [p for p in target_dir.rglob("*") if p.is_file() and p.name != ".git"]
        assert extracted_files == [], f"Partial extraction leaked: {extracted_files}"


# ---------------------------------------------------------------------------
# Absolute-path symlink in tar
# ---------------------------------------------------------------------------


async def test_absolute_symlink_tar_rejected(tmp_dirs):
    """Tar entry that is a symlink to /etc/passwd is rejected."""
    cache_dir, target_dir = tmp_dirs

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        good = tarfile.TarInfo(name="good.txt")
        good.size = 4
        tf.addfile(good, io.BytesIO(b"safe"))

        link = tarfile.TarInfo(name="bad_link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        link.size = 0
        tf.addfile(link)
    archive_bytes = buf.getvalue()
    sha256 = _sha256(archive_bytes)

    row = _make_dataset_row(url="http://example.test/symlink.tar.gz", sha256=sha256, fmt="tar.gz")

    with patch(
        "sec_review_framework.ground_truth.archive_materializer.urlretrieve",
        side_effect=_fake_urlretrieve(archive_bytes),
    ):
        with pytest.raises(ArchiveSecurityError):
            await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)


# ---------------------------------------------------------------------------
# Path traversal in zip — Unix separators
# ---------------------------------------------------------------------------


async def test_path_traversal_zip_unix_rejected(tmp_dirs):
    """Zip entry named ../../escape.txt is rejected as ArchiveSecurityError."""
    cache_dir, target_dir = tmp_dirs

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../escape.txt", "pwnd")
    archive_bytes = buf.getvalue()
    sha256 = _sha256(archive_bytes)

    row = _make_dataset_row(url="http://example.test/evil.zip", sha256=sha256, fmt="zip")

    with patch(
        "sec_review_framework.ground_truth.archive_materializer.urlretrieve",
        side_effect=_fake_urlretrieve(archive_bytes),
    ):
        with pytest.raises(ArchiveSecurityError):
            await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)


# ---------------------------------------------------------------------------
# Path traversal in zip — Windows backslash separators
# ---------------------------------------------------------------------------


async def test_path_traversal_zip_windows_sep_rejected(tmp_dirs):
    """Zip entry with Windows-style backslash path traversal is rejected."""
    cache_dir, target_dir = tmp_dirs

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zi = zipfile.ZipInfo(filename="sub\\..\\..\\escape.txt")
        zf.writestr(zi, "pwnd")
    archive_bytes = buf.getvalue()
    sha256 = _sha256(archive_bytes)

    row = _make_dataset_row(url="http://example.test/evil_win.zip", sha256=sha256, fmt="zip")

    with patch(
        "sec_review_framework.ground_truth.archive_materializer.urlretrieve",
        side_effect=_fake_urlretrieve(archive_bytes),
    ):
        with pytest.raises(ArchiveSecurityError):
            await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)


# ---------------------------------------------------------------------------
# Cache hit — no re-download
# ---------------------------------------------------------------------------


async def test_cache_hit_skips_download(tmp_dirs):
    """If cache already contains the archive, urlretrieve is never called."""
    cache_dir, target_dir = tmp_dirs
    archive_bytes = _make_tar_gz({"cached.py": b"x = 1\n"})
    sha256 = _sha256(archive_bytes)

    # Pre-populate the cache.
    (cache_dir / f"{sha256}.tar.gz").write_bytes(archive_bytes)

    download_count = {"n": 0}

    def _should_not_download(url, filename):
        download_count["n"] += 1
        raise AssertionError("urlretrieve should not be called on a cache hit")

    row = _make_dataset_row(url="http://example.test/should-not-hit.tar.gz", sha256=sha256, fmt="tar.gz")

    with patch(
        "sec_review_framework.ground_truth.archive_materializer.urlretrieve",
        side_effect=_should_not_download,
    ):
        await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)

    assert download_count["n"] == 0
    assert (target_dir / "cached.py").exists()


# ---------------------------------------------------------------------------
# Empty archive — ArchiveCorruptError
# ---------------------------------------------------------------------------


async def test_empty_archive_raises(tmp_dirs):
    """An archive with zero members raises ArchiveCorruptError."""
    cache_dir, target_dir = tmp_dirs
    archive_bytes = _make_tar_gz({})  # zero entries
    sha256 = _sha256(archive_bytes)

    row = _make_dataset_row(url="http://example.test/empty.tar.gz", sha256=sha256, fmt="tar.gz")

    with patch(
        "sec_review_framework.ground_truth.archive_materializer.urlretrieve",
        side_effect=_fake_urlretrieve(archive_bytes),
    ):
        with pytest.raises(ArchiveCorruptError):
            await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)


# ---------------------------------------------------------------------------
# Synthetic git commit — valid HEAD SHA + commit message == archive sha256
# ---------------------------------------------------------------------------


async def test_synthetic_git_commit(tmp_dirs):
    """After materialization, HEAD commit message equals the archive sha256."""
    cache_dir, target_dir = tmp_dirs
    archive_bytes = _make_tar_gz({"app.py": b"pass\n"})
    sha256 = _sha256(archive_bytes)

    row = _make_dataset_row(url="http://example.test/archive.tar.gz", sha256=sha256, fmt="tar.gz")

    with patch(
        "sec_review_framework.ground_truth.archive_materializer.urlretrieve",
        side_effect=_fake_urlretrieve(archive_bytes),
    ):
        await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)

    assert (target_dir / ".git").is_dir()

    head_sha = subprocess.run(
        ["git", "-C", str(target_dir), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert len(head_sha) == 40

    commit_msg = subprocess.run(
        ["git", "-C", str(target_dir), "log", "-1", "--format=%s"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert commit_msg == sha256


# ---------------------------------------------------------------------------
# Idempotency — second call skips everything
# ---------------------------------------------------------------------------


async def test_idempotent_if_already_materialized(tmp_dirs):
    """Calling materialize twice does not re-download when tree already complete."""
    cache_dir, target_dir = tmp_dirs
    archive_bytes = _make_tar_gz({"file.py": b"x = 1\n"})
    sha256 = _sha256(archive_bytes)

    row = _make_dataset_row(url="http://example.test/archive.tar.gz", sha256=sha256, fmt="tar.gz")

    download_count = {"n": 0}

    def _counting_urlretrieve(url, filename):
        download_count["n"] += 1
        Path(filename).write_bytes(archive_bytes)
        return (filename, {})

    with patch(
        "sec_review_framework.ground_truth.archive_materializer.urlretrieve",
        side_effect=_counting_urlretrieve,
    ):
        await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)
        count_after_first = download_count["n"]
        # Second call — cache is warm, tree is complete, no re-download.
        await materialize_archive_dataset(row, target_dir, cache_dir=cache_dir)

    assert download_count["n"] == count_after_first  # no additional downloads
