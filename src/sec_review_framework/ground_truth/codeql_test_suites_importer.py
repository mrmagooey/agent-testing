"""CodeQL test-suite importer.

Ingests GitHub's open-source CodeQL query test fixtures as paired-polarity
ground truth. Each CodeQL query has paired test fixtures under::

    <lang>/ql/test/query-tests/Security/CWE-NNN/<query-name>/

containing:

- A ``.ql`` query file (used for naming only — NOT stored).
- One or more sample input files (``*.<lang-ext>``) — both vulnerable and clean.
- A ``<query-name>.expected`` file listing which lines the query SHOULD flag.

Source repository
-----------------
``github/codeql`` (https://github.com/github/codeql).

License compliance
------------------
The ``github/codeql`` repository is MIT-licensed. This importer:

- **Reads** the local clone's contents at import time.
- **Stores label metadata only**: file paths, line numbers, CWE IDs, query
  names. It does NOT store source code content from any test-fixture file.
- **Never copies** test-fixture files, query ``.ql`` files, or sample source
  into this repository. The dataset's working tree is the operator's local
  clone of ``github/codeql``, governed by the upstream MIT license.

The MIT license requires the copyright notice and license text to accompany
redistributions. Since this importer stores *pointers* (metadata), not the
content itself, no redistribution occurs. The upstream MIT LICENSE file
remains in the operator's clone. Reference: MIT License §1 ("The above
copyright notice and this permission notice shall be included in all copies
or **substantial portions** of the Software"). Metadata rows of file paths
and line numbers are not a substantial portion of the software.

Parsing strategy
----------------
``.expected`` files use a CodeQL-specific diff-output format. Lines are
parsed with a robust line-by-line regex strategy that handles:

- Standard ``file.ext:line:col:message`` tuples.
- ``|`` continuation lines (multi-line message bodies — skipped).
- Empty files (explicit negatives — no flagged lines).
- Comment lines starting with ``#`` or ``//`` — skipped.
- Lines with quoted file paths (paths containing spaces).
- Unrecognised lines — counted but do not abort; if > 50 % of non-comment
  lines are unrecognised, the query directory is skipped with
  ``error_unparseable_expected``.

CWE mapping
-----------
The importer reads ``config/vuln_classes.yaml`` at import time to build a
``CWE-NNN → vuln_class`` lookup. CWEs not present in the YAML fall back to
``"other"`` without raising an error.

Idempotency
-----------
Label IDs are deterministic::

    f"codeql::{lang}::{cwe_id}::{query_name}::{relative_file_path}::{lineno}"

``INSERT OR IGNORE`` handles deduplication. Re-running with the same
``origin_commit`` is safe and updates nothing (idempotent).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

from sec_review_framework.db import Database
from sec_review_framework.ground_truth._source_check_migration import (
    ensure_negative_source_check_includes,
    ensure_source_check_includes,
)

__all__ = ["CodeQLImportResult", "import_codeql_test_suites"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Language → file extensions mapping
# ---------------------------------------------------------------------------

_LANG_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "java": (".java",),
    "python": (".py",),
    "javascript": (".js", ".ts", ".jsx", ".tsx", ".mjs"),
    "cpp": (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp"),
    "csharp": (".cs",),
    "go": (".go",),
    "ruby": (".rb",),
    "swift": (".swift",),
}

# ---------------------------------------------------------------------------
# Known languages in the codeql repo (checked under <lang>/ql/test/query-tests/Security/)
# ---------------------------------------------------------------------------

_KNOWN_LANGUAGES: tuple[str, ...] = (
    "java",
    "python",
    "javascript",
    "cpp",
    "csharp",
    "go",
    "ruby",
    "swift",
)

# ---------------------------------------------------------------------------
# Origin URL
# ---------------------------------------------------------------------------

_ORIGIN_URL = "https://github.com/github/codeql"

# ---------------------------------------------------------------------------
# .expected file parsing
# ---------------------------------------------------------------------------

# A result tuple line looks like one of:
#
#   TestVulnerable.java:10:5:10:15:Some message text
#   "File With Spaces.java":7:1:7:20:Alert message
#   | continuation of previous message
#   #comment
#   //comment
#
# The canonical format from CodeQL's test runner is:
#   <file>:<start-line>:<start-col>:<end-line>:<end-col>:<message>
# Some queries emit simpler:
#   <file>:<line>:<col>:<message>
#
# We accept both. The file name may be quoted.

# Matches: optional_quote FILE optional_quote : LINE : REST
_RE_RESULT_LINE = re.compile(
    r'^(?P<quote>"?)'            # optional opening quote
    r'(?P<file>[^":\n]+?)'       # file name (non-greedy, no quote/colon/newline)
    r'(?P=quote)'                # matching closing quote
    r':(?P<line>\d+)'            # :line_number
    r'(?::\d+)?'                 # optional :col
    r'(?::\d+:\d+)?'             # optional :end_line:end_col
    r'(?::.*)?$'                 # optional :message
)

# Pipe continuation lines
_RE_CONTINUATION = re.compile(r'^\s*\|')

# Comment lines
_RE_COMMENT = re.compile(r'^\s*(?:#|//)')


def _parse_expected_file(
    expected_path: Path,
) -> tuple[dict[str, list[int]], bool, str | None]:
    """Parse a ``.expected`` file and return per-file hit line numbers.

    Returns a 3-tuple:
        hits        dict[filename, list[line_number]]  (may be empty)
        parseable   bool — False if the file is too malformed to trust
        error_msg   str | None — human-readable parse problem description
    """
    try:
        text = expected_path.read_text(errors="replace")
    except OSError as exc:
        return {}, False, f"Cannot read {expected_path}: {exc}"

    lines = text.splitlines()
    hits: dict[str, list[int]] = {}
    unrecognised = 0
    total_significant = 0  # non-comment, non-continuation, non-blank

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped:
            continue
        if _RE_COMMENT.match(stripped):
            continue
        if _RE_CONTINUATION.match(stripped):
            continue

        total_significant += 1

        m = _RE_RESULT_LINE.match(stripped)
        if m:
            fname = m.group("file").strip()
            lineno = int(m.group("line"))
            hits.setdefault(fname, []).append(lineno)
        else:
            unrecognised += 1
            logger.debug("Unrecognised .expected line: %r", stripped)

    # If more than half of significant lines are unrecognised, declare unparseable
    if total_significant > 0 and unrecognised / total_significant > 0.5:
        return (
            {},
            False,
            f"{unrecognised}/{total_significant} lines unrecognised in {expected_path}",
        )

    return hits, True, None


# ---------------------------------------------------------------------------
# CWE → vuln_class mapping (loaded from config/vuln_classes.yaml)
# ---------------------------------------------------------------------------


def _build_cwe_to_vuln_class(config_dir: Path) -> dict[str, str]:
    """Build CWE → vuln_class mapping from ``config/vuln_classes.yaml``."""
    yaml_path = config_dir / "vuln_classes.yaml"
    mapping: dict[str, str] = {}
    try:
        with yaml_path.open() as fh:
            data = yaml.safe_load(fh)
        for vuln_class, info in (data.get("vuln_classes") or {}).items():
            for cwe_id in info.get("cwe_ids") or []:
                mapping[cwe_id] = vuln_class
    except Exception as exc:
        logger.warning("Could not load vuln_classes.yaml: %s", exc)
    return mapping


# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class CodeQLImportResult:
    """Summary returned by :func:`import_codeql_test_suites`."""

    dataset_name: str
    dataset_version: str
    positives_count: int = 0
    negatives_count: int = 0
    queries_processed: int = 0
    skipped_count: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public importer
# ---------------------------------------------------------------------------


async def import_codeql_test_suites(
    db: Database,
    *,
    codeql_repo_path: Path,
    origin_commit: str,
    languages: list[str] | None = None,
    cwe_filter: list[str] | None = None,
) -> CodeQLImportResult:
    """Import CodeQL test-suite corpus as paired-polarity ground truth.

    Idempotent on (origin_commit). Re-running updates labels in place (new
    labels added; existing labels untouched by INSERT OR IGNORE).

    Args:
        db: Initialised :class:`~sec_review_framework.db.Database` instance.
        codeql_repo_path: Local clone of ``https://github.com/github/codeql``.
        origin_commit: Pinned SHA of the cloned working tree.
        languages: Languages to import. ``None`` means all known languages.
            Example: ``["java", "python"]``.
        cwe_filter: CWE directory names to include. ``None`` means all.
            Example: ``["CWE-079", "CWE-089"]``.

    Returns:
        :class:`CodeQLImportResult` with counts, skipped, and errors.
    """
    codeql_repo_path = Path(codeql_repo_path)

    # Find the config directory (two levels up from this file: src/sec_review_framework/ground_truth → config)
    _here = Path(__file__).parent
    _config_dir = _here.parent.parent.parent / "config"
    cwe_to_vuln_class = _build_cwe_to_vuln_class(_config_dir)

    # ------------------------------------------------------------------
    # 0. Ensure 'codeql' is accepted by both source CHECK constraints
    # ------------------------------------------------------------------
    await ensure_source_check_includes(db, "codeql")
    await ensure_negative_source_check_includes(db, "codeql")

    # ------------------------------------------------------------------
    # 1. Determine which languages to process
    # ------------------------------------------------------------------
    target_languages: list[str] = (
        list(languages) if languages is not None else list(_KNOWN_LANGUAGES)
    )

    now_iso = datetime.now(UTC).isoformat()

    # Aggregate result across all languages
    total_result = CodeQLImportResult(
        dataset_name="codeql-test-suites",
        dataset_version=origin_commit,
    )

    for lang in target_languages:
        lang_result = await _import_language(
            db=db,
            codeql_repo_path=codeql_repo_path,
            lang=lang,
            origin_commit=origin_commit,
            cwe_filter=cwe_filter,
            cwe_to_vuln_class=cwe_to_vuln_class,
            now_iso=now_iso,
        )
        total_result.positives_count += lang_result.positives_count
        total_result.negatives_count += lang_result.negatives_count
        total_result.queries_processed += lang_result.queries_processed
        total_result.skipped_count += lang_result.skipped_count
        total_result.errors.extend(lang_result.errors)

    # Update aggregate name to reflect multi-language import
    if len(target_languages) == 1:
        total_result.dataset_name = (
            f"codeql-test-suites-{target_languages[0]}-{origin_commit[:8]}"
        )

    return total_result


async def _import_language(
    *,
    db: Database,
    codeql_repo_path: Path,
    lang: str,
    origin_commit: str,
    cwe_filter: list[str] | None,
    cwe_to_vuln_class: dict[str, str],
    now_iso: str,
) -> CodeQLImportResult:
    """Import a single language's CodeQL test suites."""
    dataset_name = f"codeql-test-suites-{lang}-{origin_commit[:8]}"
    lang_exts = _LANG_EXTENSIONS.get(lang, ())

    result = CodeQLImportResult(
        dataset_name=dataset_name,
        dataset_version=origin_commit,
    )

    # ------------------------------------------------------------------
    # 1. Locate the Security test directory for this language
    # ------------------------------------------------------------------
    security_dir = codeql_repo_path / lang / "ql" / "test" / "query-tests" / "Security"
    if not security_dir.is_dir():
        logger.debug(
            "No Security test directory for language %r at %s — skipping",
            lang,
            security_dir,
        )
        return result

    # ------------------------------------------------------------------
    # 2. Create/reuse the datasets row (idempotent)
    # ------------------------------------------------------------------
    existing = await db.get_dataset(dataset_name)
    if existing is None:
        test_glob = f"{lang}/ql/test/query-tests/Security/**/*"
        if lang_exts:
            # e.g. {*.java} or {*.py,*.pyi}
            ext_pat = ",".join(f"*{e}" for e in lang_exts)
            test_glob = f"{lang}/ql/test/query-tests/Security/**/{{{ext_pat}}}"

        await db.create_dataset(
            {
                "name": dataset_name,
                "kind": "git",
                "origin_url": _ORIGIN_URL,
                "origin_commit": origin_commit,
                "cve_id": None,
                "metadata_json": json.dumps(
                    {
                        "benchmark": "codeql-test-suites",
                        "language": lang,
                        "iteration": "per-test-file",
                        "test_glob": test_glob,
                    }
                ),
                "created_at": now_iso,
            }
        )

    # ------------------------------------------------------------------
    # 3. Walk CWE directories
    # ------------------------------------------------------------------
    positive_labels: list[dict] = []
    negative_labels: list[dict] = []

    cwe_dirs = sorted(security_dir.iterdir())
    for cwe_dir in cwe_dirs:
        if not cwe_dir.is_dir():
            continue
        cwe_name = cwe_dir.name  # e.g. "CWE-079"

        # Normalise for filter matching (case-insensitive, with/without leading zero)
        if cwe_filter is not None:
            normalised_name = cwe_name.upper()
            normalised_filter = [c.upper() for c in cwe_filter]
            if normalised_name not in normalised_filter:
                continue

        # Extract CWE ID (numeric part only, strip leading zeros)
        cwe_id = _normalise_cwe_id(cwe_name)
        vuln_class = cwe_to_vuln_class.get(cwe_id, "other")

        # Walk query subdirectories
        for query_dir in sorted(cwe_dir.iterdir()):
            if not query_dir.is_dir():
                continue
            query_name = query_dir.name

            _pos, _neg, _skip, _errors = _process_query_dir(
                query_dir=query_dir,
                lang=lang,
                lang_exts=lang_exts,
                cwe_name=cwe_name,
                cwe_id=cwe_id,
                vuln_class=vuln_class,
                query_name=query_name,
                dataset_name=dataset_name,
                origin_commit=origin_commit,
                now_iso=now_iso,
            )
            positive_labels.extend(_pos)
            negative_labels.extend(_neg)
            result.queries_processed += 1
            result.skipped_count += _skip
            result.errors.extend(_errors)

    # ------------------------------------------------------------------
    # 4. Persist (idempotent INSERT OR IGNORE on deterministic IDs)
    # ------------------------------------------------------------------
    # De-duplicate by ID
    seen_pos: set[str] = set()
    unique_pos: list[dict] = []
    for lbl in positive_labels:
        if lbl["id"] not in seen_pos:
            unique_pos.append(lbl)
            seen_pos.add(lbl["id"])

    seen_neg: set[str] = set()
    unique_neg: list[dict] = []
    for lbl in negative_labels:
        if lbl["id"] not in seen_neg:
            unique_neg.append(lbl)
            seen_neg.add(lbl["id"])

    if unique_pos:
        await db.append_dataset_labels(unique_pos)
    if unique_neg:
        await db.append_dataset_negative_labels(unique_neg)

    result.positives_count = len(unique_pos)
    result.negatives_count = len(unique_neg)

    logger.info(
        "CodeQL import: lang=%r dataset=%r positives=%d negatives=%d skipped=%d errors=%d",
        lang,
        dataset_name,
        result.positives_count,
        result.negatives_count,
        result.skipped_count,
        len(result.errors),
    )

    return result


def _process_query_dir(
    *,
    query_dir: Path,
    lang: str,
    lang_exts: tuple[str, ...],
    cwe_name: str,
    cwe_id: str,
    vuln_class: str,
    query_name: str,
    dataset_name: str,
    origin_commit: str,
    now_iso: str,
) -> tuple[list[dict], list[dict], int, list[tuple[str, str]]]:
    """Process a single query directory.

    Returns:
        (positive_labels, negative_labels, skipped_count, errors)
    """
    positives: list[dict] = []
    negatives: list[dict] = []
    errors: list[tuple[str, str]] = []
    skipped = 0

    # Find the .expected file
    expected_files = list(query_dir.glob("*.expected"))
    if not expected_files:
        # No .expected file — can't determine polarity
        logger.debug("No .expected file in %s — skipping query dir", query_dir)
        skipped += 1
        return positives, negatives, skipped, errors

    # If multiple .expected files, take the one matching query_name, else first
    expected_path: Path | None = None
    for ef in expected_files:
        if ef.stem == query_name:
            expected_path = ef
            break
    if expected_path is None:
        expected_path = expected_files[0]

    # Parse the .expected file
    hits, parseable, parse_error = _parse_expected_file(expected_path)
    if not parseable:
        msg = parse_error or f"Unparseable .expected: {expected_path}"
        errors.append(("error_unparseable_expected", msg))
        logger.warning("Skipping unparseable .expected in %s: %s", query_dir, msg)
        return positives, negatives, skipped, errors

    # Find sample input files in this directory
    sample_files = _find_sample_files(query_dir, lang_exts)
    if not sample_files:
        # Some query dirs contain only .ql + .expected with no sample source
        logger.debug("No sample source files in %s — skipping", query_dir)
        skipped += 1
        return positives, negatives, skipped, errors

    for sample_path in sample_files:
        rel_name = sample_path.name  # filename only, relative to query dir

        # Look for hits using both the bare name and a qualified version
        # (CodeQL .expected sometimes references files by path segment)
        file_hits: list[int] = (
            hits.get(rel_name)
            or hits.get(sample_path.stem)
            or []
        )

        # Build relative path from query_dir parent (cwe_dir → up to lang/)
        # stored as: <lang>/ql/test/query-tests/Security/<CWE>/<query>/<file>
        try:
            rel_file_path = str(sample_path.relative_to(query_dir.parent.parent.parent.parent.parent))
        except ValueError:
            rel_file_path = f"{lang}/ql/test/query-tests/Security/{cwe_name}/{query_name}/{rel_name}"

        if file_hits:
            # Positive: one label per flagged line
            for lineno in file_hits:
                label_id = (
                    f"codeql::{lang}::{cwe_id}::{query_name}"
                    f"::{rel_file_path}::{lineno}"
                )
                positives.append(
                    {
                        "id": label_id,
                        "dataset_name": dataset_name,
                        "dataset_version": origin_commit,
                        "file_path": rel_file_path,
                        "line_start": lineno,
                        "line_end": lineno,
                        "cwe_id": cwe_id,
                        "vuln_class": vuln_class,
                        "severity": "MEDIUM",
                        "confidence": "HIGH",
                        "description": (
                            f"CodeQL query {query_name}: expected finding at line {lineno}"
                        ),
                        "source": "codeql",
                        "source_ref": query_name,
                        "created_at": now_iso,
                        "notes": None,
                        "introduced_in_diff": None,
                        "patch_lines_changed": None,
                    }
                )
        else:
            # Negative: .expected exists but no hits for this file
            label_id = (
                f"codeql::{lang}::{cwe_id}::{query_name}"
                f"::{rel_file_path}::neg"
            )
            negatives.append(
                {
                    "id": label_id,
                    "dataset_name": dataset_name,
                    "dataset_version": origin_commit,
                    "file_path": rel_file_path,
                    "cwe_id": cwe_id,
                    "vuln_class": vuln_class,
                    "source": "codeql",
                    "source_ref": query_name,
                    "created_at": now_iso,
                    "notes": None,
                }
            )

    return positives, negatives, skipped, errors


def _find_sample_files(query_dir: Path, lang_exts: tuple[str, ...]) -> list[Path]:
    """Return all sample source files directly in query_dir (not recursive).

    If lang_exts is empty, return all non-.ql, non-.expected, non-.qll files.
    """
    results: list[Path] = []
    skip_exts = {".ql", ".qll", ".expected", ".qlref"}
    for p in sorted(query_dir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix in skip_exts:
            continue
        if lang_exts:
            if p.suffix in lang_exts:
                results.append(p)
        else:
            results.append(p)
    return results


def _normalise_cwe_id(cwe_name: str) -> str:
    """Convert directory name like ``CWE-079`` to ``CWE-79``.

    Strips leading zeros from the numeric part so it matches vuln_classes.yaml
    (which uses e.g. ``CWE-79`` not ``CWE-079``).
    """
    upper = cwe_name.upper()
    if upper.startswith("CWE-"):
        try:
            num = int(upper[4:])
            return f"CWE-{num}"
        except ValueError:
            pass
    return upper
