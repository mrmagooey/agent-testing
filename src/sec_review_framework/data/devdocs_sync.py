"""DevDocs docset sync utility.

Downloads DevDocs JSON docsets (index.json + db.json) from documents.devdocs.io
into a local directory. Intended to run in the dataset-builder Job, which is
the *only* component allowed to make external network calls for bootstrapping
offline resources.

Workers read the downloaded JSON files directly via devdocs_server.py —
no network access required at worker runtime.

Default docset list (opinionated selection for security-review workloads)
-------------------------------------------------------------------------
python~3.12, javascript, typescript, go, rust, cpp, ruby, php, bash,
nodejs, express, flask, django, aws, openapi, http, dom

Usage as a module
-----------------
    python -m sec_review_framework.data.devdocs_sync \\
        --root /data/devdocs \\
        --docsets python~3.12,javascript,typescript,go,rust

Testability
-----------
The top-level ``sync()`` function accepts an injectable ``downloader``
callable so tests can mock network access without patching urllib.

    from sec_review_framework.data.devdocs_sync import sync

    def fake_downloader(url):
        ...  # return a file-like object with .read()

    sync(root=Path("/tmp/test"), docsets=["python~3.12"], downloader=fake_downloader)
"""

from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEVDOCS_BASE_URL = "https://documents.devdocs.io"

# Default docsets to sync — security-review-focused set.
# Update this list when new relevant docsets become available.
DEFAULT_DOCSETS: list[str] = [
    "python~3.12",
    "javascript",
    "typescript",
    "go",
    "rust",
    "cpp",
    "ruby",
    "php",
    "bash",
    "nodejs",
    "express",
    "flask",
    "django",
    "aws",
    "openapi",
    "http",
    "dom",
]

_DOCSET_FILES = ("index.json", "db.json")

# Type alias for the downloader callable.
# Accepts a URL string; returns a file-like object supporting .read() -> bytes.
Downloader = Callable[[str], Any]


# ---------------------------------------------------------------------------
# Core sync logic
# ---------------------------------------------------------------------------

def _default_downloader(url: str) -> IO[bytes]:
    """Default downloader using urllib.request (stdlib only, no extra deps)."""
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "sec-review-devdocs-sync/1.0"},
    )
    return urllib.request.urlopen(req, timeout=120)


def _sync_docset(
    root: Path,
    slug: str,
    force: bool,
    downloader: Downloader,
) -> dict[str, Any]:
    """Download index.json and db.json for a single docset.

    Returns a status dict for the manifest.
    """
    dest_dir = root / slug
    dest_dir.mkdir(parents=True, exist_ok=True)

    downloaded: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []

    for filename in _DOCSET_FILES:
        dest = dest_dir / filename
        if dest.exists() and not force:
            logger.info("[devdocs-sync] %s/%s already exists — skipping", slug, filename)
            skipped.append(filename)
            continue

        url = f"{_DEVDOCS_BASE_URL}/{slug}/{filename}"
        logger.info("[devdocs-sync] downloading %s", url)
        try:
            response = downloader(url)
            content: bytes = response.read()
            dest.write_bytes(content)
            downloaded.append(filename)
            logger.info(
                "[devdocs-sync] %s/%s saved (%d bytes)", slug, filename, len(content)
            )
        except Exception as exc:  # noqa: BLE001
            msg = f"{slug}/{filename}: {exc}"
            logger.error("[devdocs-sync] FAILED %s", msg)
            errors.append(msg)

    return {
        "slug": slug,
        "downloaded": downloaded,
        "skipped": skipped,
        "errors": errors,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _write_manifest(root: Path, results: list[dict[str, Any]]) -> None:
    """Write (or overwrite) _manifest.json with sync results and metadata."""
    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "base_url": _DEVDOCS_BASE_URL,
        "docsets": results,
    }
    manifest_path = root / "_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    logger.info("[devdocs-sync] manifest written to %s", manifest_path)


def sync(
    root: Path,
    docsets: list[str],
    force: bool = False,
    downloader: Downloader = _default_downloader,
) -> list[dict[str, Any]]:
    """Download DevDocs docsets to *root*.

    Parameters
    ----------
    root:
        Target directory. Created if it does not exist.
    docsets:
        List of docset slugs to sync (e.g. ``["python~3.12", "javascript"]``).
    force:
        If True, re-download even if the files already exist.
    downloader:
        Callable(url: str) -> file-like-with-.read(). Override in tests to
        avoid real network access.

    Returns
    -------
    list of status dicts, one per docset.
    """
    root.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    for slug in docsets:
        result = _sync_docset(root, slug, force, downloader)
        results.append(result)

    _write_manifest(root, results)
    return results


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Download DevDocs JSON docsets for offline use."
    )
    parser.add_argument(
        "--root",
        required=True,
        help="Target directory to write docsets into (e.g. /data/devdocs).",
    )
    parser.add_argument(
        "--docsets",
        default=",".join(DEFAULT_DOCSETS),
        help=(
            "Comma-separated list of docset slugs to download. "
            f"Defaults to: {','.join(DEFAULT_DOCSETS)}"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download files even if they already exist locally.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    root = Path(args.root)
    docsets = [s.strip() for s in args.docsets.split(",") if s.strip()]

    logger.info("[devdocs-sync] syncing %d docsets to %s", len(docsets), root)
    start = time.monotonic()
    results = sync(root, docsets, force=args.force)
    elapsed = time.monotonic() - start

    ok = [r for r in results if not r["errors"]]
    failed = [r for r in results if r["errors"]]

    logger.info(
        "[devdocs-sync] done in %.1fs — %d OK, %d failed",
        elapsed, len(ok), len(failed),
    )
    if failed:
        for r in failed:
            for err in r["errors"]:
                logger.error("[devdocs-sync] error: %s", err)
        raise SystemExit(1)


if __name__ == "__main__":
    _main()
