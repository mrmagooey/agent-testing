"""Archive materializer: download → verify → safe-extract → synthetic git tree.

Supports ``kind='archive'`` datasets whose content is distributed as a
content-addressed tarball or zip file rather than a git repository.  The
materializer produces a working tree at *target_dir* that is indistinguishable
from a ``kind='git'`` dataset from the perspective of downstream code (label
matchers, scoring, etc.).

Entry point: :func:`materialize_archive_dataset`.

Security contract
-----------------
All tar/zip extraction is filtered for path-traversal entries before a single
byte lands on disk.  Any malicious entry causes an immediate abort with
:exc:`ArchiveSecurityError`.  We use Python 3.12's built-in
``extraction_filter='data'`` for tar (which rejects absolute paths, ``..``
components, and dangerous link targets) and an explicit member-by-member
validation loop for zip (``ZipFile.extractall`` has no safe default).

Error taxonomy
--------------
* :exc:`ArchiveHashMismatch` — downloaded bytes do not match ``archive_sha256``.
* :exc:`ArchiveSecurityError` — path-traversal or dangerous symlink detected.
* :exc:`ArchiveCorruptError` — archive is empty or otherwise un-extractable.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Literal
from urllib.error import URLError  # noqa: F401 – re-exported for callers
from urllib.request import urlretrieve

# ---------------------------------------------------------------------------
# Public exception hierarchy
# ---------------------------------------------------------------------------


class ArchiveMaterializerError(Exception):
    """Base class for all archive-materializer errors."""


class ArchiveHashMismatch(ArchiveMaterializerError):
    """SHA-256 of the downloaded archive does not match the expected digest."""

    def __init__(self, expected: str, got: str) -> None:
        self.expected = expected
        self.got = got
        super().__init__(f"SHA-256 mismatch: expected={expected!r} got={got!r}")


class ArchiveSecurityError(ArchiveMaterializerError):
    """Archive contains a path-traversal entry or dangerous link target."""


class ArchiveCorruptError(ArchiveMaterializerError):
    """Archive is empty or otherwise corrupt (zero extractable entries)."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_ArchiveFormat = Literal["tar.gz", "zip", "tar.zst"]


def _sha256_of_file(path: Path) -> str:
    """Return lowercase hex SHA-256 of *path*."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _download_to_cache(
    url: str,
    sha256: str,
    fmt: _ArchiveFormat,
    cache_dir: Path,
) -> Path:
    """Download *url* into *cache_dir*, verify hash, return the cache path.

    Uses an atomic write: downloads to ``<sha256>.<fmt>.tmp``, verifies, then
    ``os.rename`` into the final ``<sha256>.<fmt>`` path.  If the final path
    already exists and its hash is correct, the download is skipped entirely.

    Raises:
        ArchiveHashMismatch: if the downloaded content does not match *sha256*.
        urllib.error.URLError: on network failure (propagated unchanged).
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    final_path = cache_dir / f"{sha256}.{fmt}"

    # Cache hit: verify then return.
    if final_path.exists():
        actual = _sha256_of_file(final_path)
        if actual == sha256:
            return final_path
        # Cache is corrupted — remove and re-download.
        final_path.unlink()

    tmp_path = cache_dir / f"{sha256}.{fmt}.tmp"
    try:
        urlretrieve(url, tmp_path)  # noqa: S310 – URL comes from a trusted DB row
        actual = _sha256_of_file(tmp_path)
        if actual != sha256:
            raise ArchiveHashMismatch(expected=sha256, got=actual)
        os.rename(tmp_path, final_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    return final_path


def _safe_extract_tar(archive_path: Path, target_dir: Path, fmt: _ArchiveFormat) -> int:
    """Extract a tar archive to *target_dir* using Python 3.12's 'data' filter.

    The ``'data'`` filter (PEP 706) rejects:
    - Absolute paths
    - Entries with ``..`` components
    - Symlinks pointing outside the destination
    - Hard links to outside paths
    - Device files, setuid bits, etc.

    Additional guards added here on top of the filter:
    - Entries whose *name* contains a null byte (filter may not catch all).
    - Entries that are Windows-style absolute paths (e.g. ``C:\\...``).

    Returns the number of extracted members (> 0 for a healthy archive).

    Raises:
        ArchiveSecurityError: on any path-traversal or dangerous entry.
        ArchiveCorruptError: if the archive is empty.
        tarfile.TarError: on corrupt tar data (propagated).
    """
    mode_map: dict[_ArchiveFormat, str] = {
        "tar.gz": "r:gz",
        "tar.zst": "r:*",  # let tarfile auto-detect; zstandard codec via stdlib (3.14+) or falls back
    }
    open_mode = mode_map.get(fmt, "r:gz")

    # For tar.zst we need the zstandard package or Python 3.14+.  Try auto-detect first.
    if fmt == "tar.zst":
        open_mode = "r:*"

    with tarfile.open(archive_path, open_mode) as tf:
        members = tf.getmembers()
        if not members:
            raise ArchiveCorruptError(f"Archive {archive_path.name} contains zero members")

        # Pre-flight check on raw member names before the filter runs.
        for member in members:
            name = member.name
            if "\x00" in name:
                raise ArchiveSecurityError(f"Null byte in archive entry name: {name!r}")
            # Reject Windows-style drive paths (e.g. "C:/foo", "C:\\foo")
            if len(name) >= 2 and name[1] == ":" and name[0].isalpha():
                raise ArchiveSecurityError(f"Windows absolute path in archive: {name!r}")

        try:
            tf.extractall(target_dir, filter="data")
        except tarfile.FilterError as exc:
            raise ArchiveSecurityError(
                f"Path-traversal or dangerous entry rejected during extraction: {exc}"
            ) from exc

    return len(members)


def _safe_extract_zip(archive_path: Path, target_dir: Path) -> int:
    """Extract a zip archive to *target_dir* with member-by-member path validation.

    ``ZipFile.extractall()`` does NOT filter path-traversal on all Python
    versions, so we iterate members manually.

    Guards:
    - Any component equal to ``..`` → rejected.
    - Absolute paths (starts with ``/``, ``\\``, or ``X:``) → rejected.
    - Path after normalisation escaping *target_dir* → rejected.
    - Null bytes in name → rejected.

    Returns the number of extracted members.

    Raises:
        ArchiveSecurityError: on any path-traversal.
        ArchiveCorruptError: if the zip has zero members.
        zipfile.BadZipFile: on corrupt zip data (propagated).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as zf:
        names = zf.namelist()
        if not names:
            raise ArchiveCorruptError(f"Archive {archive_path.name} contains zero members")

        for name in names:
            if "\x00" in name:
                raise ArchiveSecurityError(f"Null byte in zip entry name: {name!r}")

            # Reject absolute paths in all common forms.
            # Normalise Windows separators so split works uniformly.
            normalised = name.replace("\\", "/")
            if normalised.startswith("/"):
                raise ArchiveSecurityError(f"Absolute path in zip entry: {name!r}")
            # Windows drive letter: C:/...
            if len(normalised) >= 2 and normalised[1] == ":" and normalised[0].isalpha():
                raise ArchiveSecurityError(f"Windows absolute path in zip entry: {name!r}")

            parts = normalised.split("/")
            if ".." in parts:
                raise ArchiveSecurityError(f"Path traversal in zip entry: {name!r}")

            # Resolve and confirm the final path stays within target_dir.
            dest = (target_dir / name).resolve()
            try:
                dest.relative_to(target_dir.resolve())
            except ValueError:
                raise ArchiveSecurityError(
                    f"Zip entry {name!r} would escape target directory after path resolution"
                )

            zf.extract(name, target_dir)

    return len(names)


def _git_init_synthetic_commit(target_dir: Path, sha256: str) -> None:
    """Run git init + add -A + commit in *target_dir*.

    Uses a fixed synthetic author identity so the commit is reproducible
    regardless of the host's global git config.

    Raises:
        subprocess.CalledProcessError: on git failure (propagated).
    """
    subprocess.run(
        ["git", "init", "--initial-branch=main", str(target_dir)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(target_dir), "add", "-A"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-c", "user.email=mat@local",
            "-c", "user.name=Materializer",
            "-C", str(target_dir),
            "commit",
            "-m", sha256,
        ],
        check=True,
        capture_output=True,
    )


def _is_complete_working_tree(target_dir: Path, sha256: str) -> bool:
    """Return True if *target_dir* already contains a synthetic commit for *sha256*.

    Checks for ``.git/`` presence and that the HEAD commit message matches the
    sha256 fingerprint written by :func:`_git_init_synthetic_commit`.
    """
    git_dir = target_dir / ".git"
    if not git_dir.is_dir():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(target_dir), "log", "-1", "--format=%s"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() == sha256
    except subprocess.CalledProcessError:
        return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def materialize_archive_dataset(
    dataset_row: dict,
    target_dir: Path,
    cache_dir: Path = Path.home() / ".cache" / "sec-review" / "archives",
) -> None:
    """Download → verify sha256 → safe-extract → git init synthetic commit.

    Idempotent: if *target_dir* already contains a complete working tree
    whose HEAD commit message matches ``archive_sha256``, no work is done.

    Args:
        dataset_row: The ``datasets`` DB row dict (must have ``archive_url``,
            ``archive_sha256``, and ``archive_format``).
        target_dir: Where the working tree should land.
        cache_dir: Directory for content-addressed archive cache.  Defaults to
            ``~/.cache/sec-review/archives``.

    Raises:
        ArchiveHashMismatch: if the downloaded archive's SHA-256 does not match.
        ArchiveSecurityError: if the archive contains path-traversal entries.
        ArchiveCorruptError: if the archive is empty.
        urllib.error.URLError: on network failure.
        subprocess.CalledProcessError: if the synthetic git commit fails.
    """
    url: str = dataset_row["archive_url"]
    sha256: str = dataset_row["archive_sha256"]
    fmt: _ArchiveFormat = dataset_row["archive_format"]

    # Idempotency check: if a complete working tree already exists, skip.
    if target_dir.is_dir() and _is_complete_working_tree(target_dir, sha256):
        return

    # Phase 1: download and verify (cache-aware, atomic write).
    archive_path = _download_to_cache(url, sha256, fmt, cache_dir)

    # Phase 2: extract safely into a fresh target directory.
    #
    # We extract into a sibling temp directory first, then rename it into
    # target_dir.  This ensures target_dir is never in a partial state.
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target_dir.parent, prefix=".mat-") as tmp_extract:
        tmp_path = Path(tmp_extract) / "repo"
        tmp_path.mkdir()

        if fmt in ("tar.gz", "tar.zst"):
            count = _safe_extract_tar(archive_path, tmp_path, fmt)
        elif fmt == "zip":
            count = _safe_extract_zip(archive_path, tmp_path)
        else:
            raise ValueError(f"Unknown archive format: {fmt!r}")

        if count == 0:
            raise ArchiveCorruptError(
                f"Archive extracted zero files — treating as corrupt: {archive_path.name}"
            )

        # Phase 3: synthetic git commit inside the temp dir.
        _git_init_synthetic_commit(tmp_path, sha256)

        # Phase 4: atomic rename into target_dir.
        if target_dir.exists():
            import shutil
            shutil.rmtree(target_dir)
        os.rename(tmp_path, target_dir)
