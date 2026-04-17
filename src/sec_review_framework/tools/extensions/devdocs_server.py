"""DevDocs offline documentation MCP server for security-review workloads.

Reads pre-rendered DevDocs JSON files (index.json + db.json) directly from
a local directory — no Ruby/Rack server required, no network access at runtime.

Exposes three MCP tools:
  - doc_list_docsets  — list all available docsets under the docsets root
  - doc_search        — case-insensitive substring search across docset indexes
  - doc_fetch         — fetch and return a single documentation entry

The docsets root is expected to be populated by the dataset-builder Job
(via ``devdocs_sync``) before workers start. The directory layout must be::

    <docsets_root>/
      python~3.12/
        index.json   — [{"name": "...", "path": "...", "type": "..."}, ...]
        db.json      — {"path": "<html body>", ...}
      javascript/
        index.json
        db.json
      _manifest.json  — written by devdocs_sync (informational, not required)

Launched by devdocs_ext.py as a subprocess speaking MCP over stdio.

Usage (via MCP bridge):
    python -m sec_review_framework.tools.extensions.devdocs_server \\
        --docsets-root /data/devdocs \\
        [--allow-docsets python~3.12,javascript]
"""

from __future__ import annotations

import argparse
import asyncio
import html.parser
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

# Characters that must not appear in docset slugs or entry paths.
_SHELL_METACHARS = re.compile(r'[;&|`$<>\\!*?()\[\]{}\'"]')

_TEXT_TRUNCATION_LIMIT = 10_240  # 10 KB
_TEXT_TRUNCATION_MARKER = "\n\n[text truncated — fetch raw html for the full entry]"


def _validate_slug(slug: str) -> None:
    """Raise ValueError if slug is not safe for filesystem use."""
    if ".." in slug or slug.startswith("/"):
        raise ValueError(f"Unsafe docset slug: {slug!r}")
    if _SHELL_METACHARS.search(slug):
        raise ValueError(f"Unsafe characters in docset slug: {slug!r}")


def _validate_path(path: str) -> None:
    """Raise ValueError if entry path is not safe."""
    if ".." in path or path.startswith("/"):
        raise ValueError(f"Unsafe entry path: {path!r}")
    if _SHELL_METACHARS.search(path):
        raise ValueError(f"Unsafe characters in entry path: {path!r}")


# ---------------------------------------------------------------------------
# HTML-to-text stripper (stdlib only — html.parser)
# ---------------------------------------------------------------------------

class _HTMLStripper(html.parser.HTMLParser):
    """Minimal HTML-to-text converter using the stdlib HTMLParser."""

    _BLOCK_TAGS = frozenset({
        "p", "div", "li", "br", "h1", "h2", "h3", "h4", "h5", "h6",
        "pre", "blockquote", "td", "th", "tr", "section", "article",
        "header", "footer", "aside", "main",
    })
    _SKIP_TAGS = frozenset({"script", "style", "head", "nav", "aside"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth: int = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS and self._skip_depth == 0:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        # Collapse excessive blank lines.
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def _html_to_text(html_content: str) -> str:
    """Strip HTML tags and return plain text. Truncates at 10 KB."""
    stripper = _HTMLStripper()
    try:
        stripper.feed(html_content)
        text = stripper.get_text()
    except Exception:  # noqa: BLE001
        # Fall back to crude regex strip on any parser error.
        text = re.sub(r"<[^>]+>", " ", html_content)
        text = re.sub(r"\s+", " ", text).strip()

    if len(text) > _TEXT_TRUNCATION_LIMIT:
        return text[:_TEXT_TRUNCATION_LIMIT] + _TEXT_TRUNCATION_MARKER
    return text


# ---------------------------------------------------------------------------
# Docset index cache (lazy load, held in memory)
# ---------------------------------------------------------------------------

# {slug: [{"name": ..., "path": ..., "type": ...}, ...]}
_INDEX_CACHE: dict[str, list[dict[str, str]]] = {}


def _load_index(docsets_root: Path, slug: str) -> list[dict[str, str]]:
    """Load (and cache) the index.json for a docset slug."""
    if slug in _INDEX_CACHE:
        return _INDEX_CACHE[slug]

    index_path = docsets_root / slug / "index.json"
    try:
        raw = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not load index for %r: %s", slug, exc)
        return []

    # DevDocs index.json is either a list of entry dicts directly, or a dict
    # with an "entries" key (older export format).
    if isinstance(raw, dict):
        entries: list[dict[str, str]] = raw.get("entries", [])
    elif isinstance(raw, list):
        entries = raw
    else:
        entries = []

    _INDEX_CACHE[slug] = entries
    return entries


def _discover_docsets(docsets_root: Path, allow_docsets: frozenset[str] | None) -> list[str]:
    """Return all valid docset slugs under docsets_root."""
    slugs: list[str] = []
    if not docsets_root.is_dir():
        return slugs
    for child in sorted(docsets_root.iterdir()):
        if not child.is_dir():
            continue
        slug = child.name
        if slug.startswith("_"):
            continue  # skip _manifest.json etc.
        if not (child / "index.json").exists() or not (child / "db.json").exists():
            continue
        if allow_docsets is not None and slug not in allow_docsets:
            continue
        slugs.append(slug)
    return slugs


# ---------------------------------------------------------------------------
# Search logic
# ---------------------------------------------------------------------------

def _score_entry(entry_name: str, query_lower: str) -> int:
    """Return a relevance score (higher = more relevant). 0 = no match."""
    name_lower = entry_name.lower()
    if query_lower not in name_lower:
        return 0
    # Exact full match
    if name_lower == query_lower:
        return 100
    # Starts with query
    if name_lower.startswith(query_lower):
        return 80
    # Word boundary match
    if re.search(r'\b' + re.escape(query_lower) + r'\b', name_lower):
        return 60
    # Substring match
    return 20


def _search_docset(
    docsets_root: Path,
    slug: str,
    query_lower: str,
    results: list[dict[str, Any]],
    limit: int,
) -> None:
    """Append matching entries from a single docset to results."""
    entries = _load_index(docsets_root, slug)
    for entry in entries:
        if len(results) >= limit:
            break
        name: str = entry.get("name", "")
        score = _score_entry(name, query_lower)
        if score > 0:
            results.append({
                "docset": slug,
                "name": name,
                "path": entry.get("path", ""),
                "type": entry.get("type", ""),
                "score": score,
            })


# ---------------------------------------------------------------------------
# MCP server definition
# ---------------------------------------------------------------------------

def build_server(
    docsets_root: Path,
    allow_docsets: frozenset[str] | None = None,
) -> Server:
    """Construct and return the MCP Server object."""
    app = Server("devdocs")

    @app.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name="list_docsets",
                description=(
                    "List all available offline documentation docsets. "
                    "Returns slug, name, version, and entry count for each."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            ),
            types.Tool(
                name="search",
                description=(
                    "Search documentation entries by keyword. "
                    "Optionally restrict to a single docset slug. "
                    "Returns matching entries sorted by relevance score."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (case-insensitive substring match).",
                        },
                        "docset": {
                            "type": "string",
                            "description": (
                                "Optional docset slug to restrict search "
                                "(e.g. 'python~3.12', 'javascript'). "
                                "Omit to search all docsets."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results to return (default 20).",
                            "default": 20,
                        },
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="fetch",
                description=(
                    "Fetch the full documentation for a specific entry. "
                    "Returns both raw HTML and plain-text representations. "
                    "Use doc_search first to find the docset and path."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "docset": {
                            "type": "string",
                            "description": "Docset slug (e.g. 'python~3.12').",
                        },
                        "path": {
                            "type": "string",
                            "description": "Entry path as returned by doc_search.",
                        },
                    },
                    "required": ["docset", "path"],
                },
            ),
        ]

    @app.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        try:
            result = _dispatch(name, arguments, docsets_root, allow_docsets)
        except Exception as exc:  # noqa: BLE001
            result = json.dumps({"error": str(exc)})
        return [types.TextContent(type="text", text=result)]

    return app


def _dispatch(
    name: str,
    args: dict,
    docsets_root: Path,
    allow_docsets: frozenset[str] | None,
) -> str:
    if name == "list_docsets":
        return _tool_list_docsets(docsets_root, allow_docsets)
    if name == "search":
        return _tool_search(
            docsets_root,
            allow_docsets,
            query=args["query"],
            docset=args.get("docset"),
            limit=int(args.get("limit", 20)),
        )
    if name == "fetch":
        return _tool_fetch(
            docsets_root,
            allow_docsets,
            docset=args["docset"],
            path=args["path"],
        )
    raise ValueError(f"Unknown tool: {name!r}")


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _tool_list_docsets(
    docsets_root: Path,
    allow_docsets: frozenset[str] | None,
) -> str:
    slugs = _discover_docsets(docsets_root, allow_docsets)
    result: list[dict[str, Any]] = []
    for slug in slugs:
        entries = _load_index(docsets_root, slug)
        # Parse name/version from slug format: "name~version" or just "name"
        if "~" in slug:
            doc_name, _, version = slug.partition("~")
        else:
            doc_name = slug
            version = ""
        result.append({
            "slug": slug,
            "name": doc_name,
            "version": version,
            "doc_count": len(entries),
        })
    return json.dumps(result)


def _tool_search(
    docsets_root: Path,
    allow_docsets: frozenset[str] | None,
    query: str,
    docset: str | None,
    limit: int,
) -> str:
    query_lower = query.lower()
    results: list[dict[str, Any]] = []

    if docset is not None:
        _validate_slug(docset)
        if allow_docsets is not None and docset not in allow_docsets:
            return json.dumps({"error": f"Docset {docset!r} is not in the allowed list"})
        _search_docset(docsets_root, docset, query_lower, results, limit)
    else:
        slugs = _discover_docsets(docsets_root, allow_docsets)
        for slug in slugs:
            if len(results) >= limit:
                break
            _search_docset(docsets_root, slug, query_lower, results, limit - len(results))

    # Sort by score descending, then name for determinism.
    results.sort(key=lambda r: (-r["score"], r["name"]))
    return json.dumps(results)


def _tool_fetch(
    docsets_root: Path,
    allow_docsets: frozenset[str] | None,
    docset: str,
    path: str,
) -> str:
    _validate_slug(docset)
    _validate_path(path)

    if allow_docsets is not None and docset not in allow_docsets:
        return json.dumps({"error": f"Docset {docset!r} is not in the allowed list"})

    db_path = docsets_root / docset / "db.json"
    if not db_path.exists():
        return json.dumps({"error": f"Docset {docset!r} not found at {db_path}"})

    try:
        db: dict[str, str] = json.loads(db_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return json.dumps({"error": f"Could not read db.json for {docset!r}: {exc}"})

    html_content = db.get(path)
    if html_content is None:
        # DevDocs sometimes stores paths without fragment; try stripping fragment.
        base_path = path.split("#")[0] if "#" in path else None
        if base_path:
            html_content = db.get(base_path)
    if html_content is None:
        return json.dumps({
            "error": f"Entry {path!r} not found in docset {docset!r}",
        })

    return json.dumps({
        "docset": docset,
        "path": path,
        "html": html_content,
        "text": _html_to_text(html_content),
    })


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    parser = argparse.ArgumentParser(description="DevDocs offline documentation MCP server")
    parser.add_argument(
        "--docsets-root",
        default="/data/devdocs",
        help="Path to the directory containing downloaded DevDocs docsets.",
    )
    parser.add_argument(
        "--allow-docsets",
        default="",
        help="Comma-separated list of allowed docset slugs. Empty = allow all.",
    )
    argv = parser.parse_args()

    docsets_root = Path(argv.docsets_root).resolve()
    if not docsets_root.exists():
        print(
            f"Error: docsets root does not exist: {docsets_root}",
            file=sys.stderr,
        )
        sys.exit(1)

    allow_docsets: frozenset[str] | None = None
    if argv.allow_docsets.strip():
        allow_docsets = frozenset(
            s.strip() for s in argv.allow_docsets.split(",") if s.strip()
        )

    app = build_server(docsets_root, allow_docsets)
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
