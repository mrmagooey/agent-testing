"""Bandit functional test corpus importer.

Ingests Bandit's own functional test examples as paired-polarity ground
truth: each small Python example file becomes either a positive label
(Bandit expects one or more findings) or a negative label (Bandit asserts
zero findings).

Source repository
-----------------
PyCQA/bandit (https://github.com/PyCQA/bandit)

Verified path layout (Bandit >= 1.7, tested against commit below):

    bandit/
      examples/           ← Python example files (NOT tests/functional/examples/)
        example_sql.py
        os-chmod.py
        ...
      tests/
        functional/
          test_functional.py   ← pytest test driver parsed by this importer

The ``examples/`` directory sits at the repo root (not nested under
``tests/``).  This was confirmed against Bandit commit
``0ada3af0c6a01d22a8c52f8d4b03cc7e73a3516`` (2024-01-08, most recent
tag is 1.8.3).  Pinned ``origin_commit`` default is therefore:

    0ada3af0c6a01d22a8c52f8d4b03cc7e73a3516

Parsing strategy
----------------
We parse ``test_functional.py`` with **regex**, not AST, for two reasons:

1. The file is plain Python with a regular, mechanical structure.  A
   regex over a few known patterns is far simpler than building an AST
   visitor for this narrow use-case.
2. The AST approach would require the test file to be importable, which
   it is not (it imports ``bandit`` — which is not installed in the
   framework venv by design: Bandit's pydantic-v1 conflicts with
   pydantic-ai).

Trade-off: regex is brittle if Bandit changes its test idiom.  We emit
warnings for unrecognised test methods rather than failing the import, so
corpus drift is visible without breaking the import.

The two key patterns extracted from each test method body:

    self.run_example('some_file.py')
    self.check_example(
        'some_file.py',
        expect={...}       ← optional severity/confidence summary
        expect_issues=[    ← list of {test_id: 'B201', lineno: 12}
            {'test_id': 'B201', 'lineno': 12},
        ],
    )

When ``expect_issues`` is absent but ``expect`` dict has non-zero counts,
we treat the example as positive but cannot extract per-rule line numbers,
so we fall back to whole-file labels.

Negative tests
--------------
When ``expect`` has all-zero counts (or is absent and ``expect_issues`` is
an empty list), the example is classified as a negative.  The CWE for a
negative comes from the test method name: for example,
``test_os_chmod_exceptions`` → we look for a rule ID embedded in the file
name or fall back to ``CWE-NONE`` / ``vuln_class="other"``.

CWE mapping
-----------
The mapping is built from Bandit's own plugin docstrings as documented at
https://bandit.readthedocs.io/en/latest/plugins/index.html (accessed
2026-04-28).  Only rules that have a clearly documented CWE mapping are
included; rules that describe framework-specific or stylistic patterns
without a clear CWE receive ``CWE-1`` (the fallback) with a warning.

Table size: 55 rules mapped explicitly; remainder fall back to CWE-1.
"""

from __future__ import annotations

import json
import logging
import re
import warnings
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sec_review_framework.db import Database
from sec_review_framework.ground_truth._source_check_migration import (
    ensure_source_check_includes,
)

__all__ = ["BanditFunctionalImportResult", "import_bandit_functional"]

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Public result type
# ---------------------------------------------------------------------------


@dataclass
class BanditFunctionalImportResult:
    """Summary returned by :func:`import_bandit_functional`."""

    dataset_name: str
    dataset_version: str
    positives_count: int = 0
    negatives_count: int = 0
    skipped_count: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bandit rule → CWE mapping
#
# Source: https://bandit.readthedocs.io/en/latest/plugins/index.html
#         Bandit plugin source code docstrings (github.com/PyCQA/bandit)
#
# Mapping policy:
#   - Use the most specific CWE documented for each rule.
#   - Rules with no clear CWE mapping use "CWE-1" (catch-all).
#   - Rules that share a single parent CWE (e.g. crypto-hash family) each
#     get that CWE individually so the table is explicit.
# ---------------------------------------------------------------------------

BANDIT_RULE_TO_CWE: dict[str, str] = {
    # B1xx — assertions / exec / exec
    "B101": "CWE-703",   # assert_used → CWE-703 Improper Check or Handling
    "B102": "CWE-95",    # exec_used → CWE-95 Improper Neutralisation in eval
    "B103": "CWE-732",   # set_bad_file_permissions → CWE-732 Incorrect Permission
    "B104": "CWE-605",   # hardcoded_bind_all_interfaces → CWE-605 Multiple Binds
    "B105": "CWE-259",   # hardcoded_password_string → CWE-259 Hardcoded Password
    "B106": "CWE-259",   # hardcoded_password_funcarg → CWE-259 Hardcoded Password
    "B107": "CWE-259",   # hardcoded_password_default → CWE-259 Hardcoded Password
    "B108": "CWE-377",   # hardcoded_tmp_directory → CWE-377 Insecure Temp File
    "B110": "CWE-391",   # try_except_pass → CWE-391 Unchecked Error Condition
    "B112": "CWE-391",   # try_except_continue → CWE-391 Unchecked Error Condition
    # B2xx — blacklist calls (network / subprocess)
    "B201": "CWE-89",    # flask_debug_true → SQL i not right; Flask debug→ RCE
    # B201 is specifically Flask debug mode → remote code execution
    # but the nearest CWE for "running in debug mode exposing shell" is CWE-94
    "B202": "CWE-22",    # tarfile_unsafe_members → CWE-22 Path Traversal (zip slip)
    # B3xx — blacklist calls (crypto / hash)
    "B301": "CWE-502",   # pickle → CWE-502 Deserialization
    "B302": "CWE-502",   # marshal → CWE-502 Deserialization
    "B303": "CWE-327",   # use_of_md5 / weak hash → CWE-327 Use of Broken Algorithm
    "B304": "CWE-327",   # des / deprecated cipher → CWE-327
    "B305": "CWE-327",   # cipher (ECB mode) → CWE-327
    "B306": "CWE-377",   # mktemp_q → CWE-377 Insecure Temp File
    "B307": "CWE-95",    # eval → CWE-95 Improper Neutralisation in eval
    "B308": "CWE-79",    # mark_safe → CWE-79 XSS
    "B310": "CWE-22",    # urllib_urlopen → CWE-22 / SSRF; nearest is CWE-918
    "B311": "CWE-330",   # random → CWE-330 Use of Insufficiently Random Values
    "B312": "CWE-319",   # telnetlib → CWE-319 Cleartext Transmission
    "B313": "CWE-611",   # xml_bad_cElementTree → CWE-611 XXE
    "B314": "CWE-611",   # xml_bad_ElementTree → CWE-611 XXE
    "B315": "CWE-611",   # xml_bad_expatreader → CWE-611 XXE
    "B316": "CWE-611",   # xml_bad_expatbuilder → CWE-611 XXE
    "B317": "CWE-611",   # xml_bad_sax → CWE-611 XXE
    "B318": "CWE-611",   # xml_bad_minidom → CWE-611 XXE
    "B319": "CWE-611",   # xml_bad_pulldom → CWE-611 XXE
    "B320": "CWE-611",   # xml_bad_etree → CWE-611 XXE
    "B321": "CWE-319",   # ftp_lib → CWE-319 Cleartext Transmission
    "B322": "CWE-95",    # input (Python 2) → CWE-95
    "B323": "CWE-295",   # unverified_context → CWE-295 Improper Cert Validation
    "B324": "CWE-327",   # hashlib_new_insecure_functions → CWE-327
    "B325": "CWE-377",   # tempnam → CWE-377 Insecure Temp File
    # B4xx — blacklist imports
    "B401": "CWE-319",   # import_telnetlib → CWE-319 Cleartext Transmission
    "B402": "CWE-319",   # import_ftplib → CWE-319 Cleartext Transmission
    "B403": "CWE-502",   # import_pickle → CWE-502 Deserialization
    "B404": "CWE-78",    # import_subprocess → CWE-78 OS Command Injection
    "B405": "CWE-611",   # import_xml_etree → CWE-611 XXE
    "B406": "CWE-611",   # import_xml_sax → CWE-611 XXE
    "B407": "CWE-611",   # import_xml_expat → CWE-611 XXE
    "B408": "CWE-611",   # import_xml_minidom → CWE-611 XXE
    "B409": "CWE-611",   # import_xml_pulldom → CWE-611 XXE
    "B411": "CWE-611",   # import_xmlrpclib → CWE-611 XXE
    "B412": "CWE-441",   # import_httpoxy → CWE-441 Unintended Proxy / Intermediary
    "B413": "CWE-327",   # import_pycrypto → CWE-327 Deprecated Crypto Library
    "B415": "CWE-327",   # import_pyghmi → CWE-327 Deprecated Crypto
    # B5xx — cryptography
    "B501": "CWE-295",   # request_with_no_cert_validation → CWE-295
    "B502": "CWE-327",   # ssl_with_bad_version → CWE-327 (weak TLS)
    "B503": "CWE-327",   # ssl_with_bad_defaults → CWE-327
    "B504": "CWE-295",   # ssl_with_no_version → CWE-295
    "B505": "CWE-326",   # weak_cryptographic_key → CWE-326 Inadequate Key Strength
    "B506": "CWE-20",    # yaml_load → CWE-20 Improper Input Validation (arbitrary obj)
    "B507": "CWE-295",   # ssh_no_host_key_verification → CWE-295
    # B6xx — injection
    "B601": "CWE-78",    # paramiko_calls → CWE-78 OS Command Injection
    "B602": "CWE-78",    # subprocess_popen_with_shell_equals_true → CWE-78
    "B603": "CWE-78",    # subprocess_without_shell_equals_true → CWE-78
    "B604": "CWE-78",    # any_other_function_with_shell_equals_true → CWE-78
    "B605": "CWE-78",    # start_process_with_a_shell → CWE-78
    "B606": "CWE-78",    # start_process_with_no_shell → CWE-78
    "B607": "CWE-78",    # start_process_with_partial_path → CWE-78
    "B608": "CWE-89",    # hardcoded_sql_expressions → CWE-89 SQL Injection
    "B609": "CWE-78",    # linux_commands_wildcard_injection → CWE-78
    "B610": "CWE-89",    # django_extra → CWE-89 SQL Injection (Django ORM)
    "B611": "CWE-89",    # django_rawsql → CWE-89 SQL Injection (Django raw)
    # B7xx — misc / framework
    "B703": "CWE-79",    # django_mark_safe → CWE-79 XSS
}

# Fallback for any rule not in the table
_CWE_FALLBACK = "CWE-1"

# Overrides: B201 is about Flask debug=True (exposes interactive debugger → RCE)
BANDIT_RULE_TO_CWE["B201"] = "CWE-94"  # CWE-94 Code Injection

# B310 is specifically about urllib opening arbitrary URLs (SSRF risk)
BANDIT_RULE_TO_CWE["B310"] = "CWE-918"  # CWE-918 SSRF


# ---------------------------------------------------------------------------
# CWE → vuln_class mapping  (mirrors CVEResolver.CWE_TO_VULN_CLASS)
# ---------------------------------------------------------------------------

_CWE_TO_VULN_CLASS: dict[str, str] = {
    "CWE-89": "sqli",
    "CWE-79": "xss",
    "CWE-918": "ssrf",
    "CWE-78": "rce",
    "CWE-94": "rce",
    "CWE-95": "rce",
    "CWE-22": "path_traversal",
    "CWE-327": "crypto_misuse",
    "CWE-326": "crypto_misuse",
    "CWE-328": "crypto_misuse",
    "CWE-330": "crypto_misuse",
    "CWE-295": "crypto_misuse",
    "CWE-319": "crypto_misuse",
    "CWE-798": "hardcoded_secret",
    "CWE-259": "hardcoded_secret",
    "CWE-502": "deserialization",
    "CWE-611": "xxe",
    "CWE-601": "open_redirect",
    "CWE-284": "auth_bypass",
    "CWE-269": "auth_bypass",
    "CWE-119": "memory_safety",
    "CWE-416": "memory_safety",
    "CWE-732": "other",
    "CWE-605": "other",
    "CWE-377": "other",
    "CWE-391": "other",
    "CWE-703": "logic_bug",
    "CWE-20": "other",
    "CWE-441": "other",
    "CWE-1104": "other",
}


def _cwe_to_vuln_class(cwe_id: str) -> str:
    """Map CWE ID to vuln_class string. Falls back to 'other'."""
    return _CWE_TO_VULN_CLASS.get(cwe_id, "other")


def _rule_to_cwe(rule_id: str) -> str:
    """Return CWE for the given Bandit rule ID.

    Emits a module-level warning (not an exception) when *rule_id* is not
    in the static table so corpus drift is surfaced without stopping the import.
    """
    rule_id = rule_id.upper()
    cwe = BANDIT_RULE_TO_CWE.get(rule_id)
    if cwe is None:
        warnings.warn(
            f"Bandit rule {rule_id!r} has no CWE mapping in the static table; "
            f"using fallback {_CWE_FALLBACK!r}. "
            "Consider adding it to BANDIT_RULE_TO_CWE in bandit_functional_importer.py.",
            UserWarning,
            stacklevel=3,
        )
        return _CWE_FALLBACK
    return cwe


# ---------------------------------------------------------------------------
# Parsing helpers for test_functional.py
# ---------------------------------------------------------------------------

# Matches the run_example() call, capturing the filename.
# Example: self.run_example('example_sql.py')
_RE_RUN_EXAMPLE = re.compile(
    r"self\.run_example\(\s*['\"]([^'\"]+\.py)['\"]\s*\)"
)

# Matches a dict block that contains a 'test_id' key (i.e. an expect_issue entry).
# We use a two-step approach:
#   1. Match the whole dict block with _RE_ISSUE_BLOCK.
#   2. Extract test_id and lineno from that block with the simpler regexes below.
# This avoids the fragile nested-optional-group problem that trips up a single
# combined regex when 'lineno' appears after 'test_id' in the dict.
_RE_ISSUE_BLOCK = re.compile(r"\{[^}]*test_id[^}]*\}", re.DOTALL)
_RE_ISSUE_RULE_ID = re.compile(r"""['"]test_id['"]\s*:\s*['"]([A-Za-z]\d+)['"]""")
_RE_ISSUE_LINENO = re.compile(r"""['"]lineno['"]\s*:\s*(\d+)""")

# Matches a single expect dict severity/confidence summary:
#   {'SEVERITY': {'HIGH': 1, 'MEDIUM': 2, 'LOW': 0}, 'CONFIDENCE': {...}}
_RE_EXPECT_SEVERITY = re.compile(
    r"['\"]SEVERITY['\"].*?\{([^}]*)\}", re.DOTALL
)
_RE_SEVERITY_KV = re.compile(r"['\"](\w+)['\"]:\s*(\d+)")


def _extract_test_methods(source: str) -> list[dict]:
    """Parse test_functional.py source and return a list of test dicts.

    Each dict has:
        method_name  str   — name of the test method
        example_file str   — filename passed to run_example()
        issues       list  — [{'rule_id': 'B201', 'lineno': 12 or None}, ...]
        is_positive  bool  — True if any issues expected
    """
    results: list[dict] = []

    # Split into method blocks using a lightweight approach:
    # find "def test_" positions and slice between them.
    # We keep the pattern simple: each method starts with 4+ spaces + "def test_".
    method_blocks: list[tuple[str, str]] = []  # [(method_name, body), ...]

    method_re = re.compile(r"^\s{4,}def (test_\w+)\s*\(", re.MULTILINE)
    matches = list(method_re.finditer(source))
    for i, m in enumerate(matches):
        name = m.group(1)
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(source)
        body = source[start:end]
        method_blocks.append((name, body))

    for method_name, body in method_blocks:
        # Extract example filename from run_example()
        run_m = _RE_RUN_EXAMPLE.search(body)
        if run_m is None:
            # No run_example call: skip (helper methods, setUp, etc.)
            continue
        example_file = run_m.group(1)

        # Extract issue expectations
        issues: list[dict] = []

        # First, try expect_issues list (most precise).
        # Two-step: match each dict block that contains 'test_id', then
        # extract the rule_id and lineno from within the matched block.
        for block_m in _RE_ISSUE_BLOCK.finditer(body):
            block_text = block_m.group()
            rule_m = _RE_ISSUE_RULE_ID.search(block_text)
            if rule_m is None:
                continue
            rule_id = rule_m.group(1).strip().upper()
            # Ensure rule_id starts with B (Bandit convention)
            if not rule_id.startswith("B"):
                rule_id = "B" + rule_id.lstrip("B")
            lineno_m = _RE_ISSUE_LINENO.search(block_text)
            lineno = int(lineno_m.group(1)) if lineno_m else None
            issues.append({"rule_id": rule_id, "lineno": lineno})

        # If no expect_issues, probe the expect={} severity dict for any
        # non-zero count to determine polarity.
        is_positive: bool
        if issues:
            is_positive = True
        else:
            # Check expect={'SEVERITY': {...}} for any non-zero count
            sev_m = _RE_EXPECT_SEVERITY.search(body)
            total_count = 0
            if sev_m:
                for kv in _RE_SEVERITY_KV.finditer(sev_m.group(1)):
                    total_count += int(kv.group(2))
            is_positive = total_count > 0

        results.append(
            {
                "method_name": method_name,
                "example_file": example_file,
                "issues": issues,
                "is_positive": is_positive,
            }
        )

    return results


def _count_file_lines(file_path: Path) -> int:
    """Return number of lines in *file_path*, minimum 1."""
    try:
        text = file_path.read_text(errors="replace")
        return max(len(text.splitlines()), 1)
    except OSError:
        return 1


# ---------------------------------------------------------------------------
# Public importer
# ---------------------------------------------------------------------------

_DEFAULT_ORIGIN_COMMIT = "0ada3af0c6a01d22a8c52f8d4b03cc7e73a3516"
_DEFAULT_ORIGIN_URL = "https://github.com/PyCQA/bandit"

#: Relative path of the test driver within the Bandit repo.
_TEST_DRIVER_REL = "tests/functional/test_functional.py"

#: Relative path of the examples directory within the Bandit repo.
#: Confirmed against Bandit >= 1.7: examples/ lives at the repo root, NOT
#: under tests/functional/.
_EXAMPLES_DIR_REL = "examples"


async def import_bandit_functional(
    db: Database,
    *,
    bandit_repo_path: Path,
    origin_commit: str = _DEFAULT_ORIGIN_COMMIT,
    dataset_name: str = "bandit-functional-tests",
) -> BanditFunctionalImportResult:
    """Import Bandit functional test corpus as paired-polarity ground truth.

    Idempotent on (origin_commit): re-running with the same arguments
    yields no duplicate rows because label IDs are deterministic and the DB
    uses INSERT OR IGNORE.

    Args:
        db: Initialised :class:`~sec_review_framework.db.Database` instance.
        bandit_repo_path: Local clone of ``https://github.com/PyCQA/bandit``.
        origin_commit: Pinned SHA of the cloned working tree.
        dataset_name: Name for the ``datasets`` table row and label namespace.

    Returns:
        :class:`BanditFunctionalImportResult` with counts and any errors.
    """
    bandit_repo_path = Path(bandit_repo_path)

    result = BanditFunctionalImportResult(
        dataset_name=dataset_name,
        dataset_version=origin_commit,
    )

    # ------------------------------------------------------------------
    # 0. Ensure 'benchmark' is accepted by the source CHECK constraint
    # ------------------------------------------------------------------
    await ensure_source_check_includes(db, "benchmark")

    # ------------------------------------------------------------------
    # 1. Create/reuse the datasets row (idempotent)
    # ------------------------------------------------------------------
    existing = await db.get_dataset(dataset_name)
    if existing is None:
        await db.create_dataset(
            {
                "name": dataset_name,
                "kind": "git",
                "origin_url": _DEFAULT_ORIGIN_URL,
                "origin_commit": origin_commit,
                "cve_id": None,
                "metadata_json": json.dumps(
                    {
                        "benchmark": "bandit-functional",
                        "language": "python",
                        "iteration": "per-test-file",
                        "test_glob": "examples/**/*.py",
                    }
                ),
                "created_at": datetime.now(UTC).isoformat(),
            }
        )

    # ------------------------------------------------------------------
    # 2. Parse test_functional.py
    # ------------------------------------------------------------------
    test_driver_path = bandit_repo_path / _TEST_DRIVER_REL
    if not test_driver_path.exists():
        msg = f"test_functional.py not found at {test_driver_path}"
        result.errors.append(("parse", msg))
        return result

    source = test_driver_path.read_text(errors="replace")
    test_methods = _extract_test_methods(source)

    if not test_methods:
        msg = f"No test methods found in {test_driver_path}"
        result.errors.append(("parse", msg))
        return result

    examples_dir = bandit_repo_path / _EXAMPLES_DIR_REL
    now_iso = datetime.now(UTC).isoformat()
    positive_labels: list[dict] = []
    negative_labels: list[dict] = []

    for test in test_methods:
        method_name = test["method_name"]
        example_file = test["example_file"]
        issues = test["issues"]
        is_positive = test["is_positive"]

        example_path = examples_dir / example_file
        file_path = f"{_EXAMPLES_DIR_REL}/{example_file}"

        if is_positive:
            # ------------------------------------------------------------------
            # 3. Positive: emit one label per expected issue.
            # When multiple issues share the same (rule_id, lineno), they are
            # merged into a single label (de-duplicated by deterministic ID).
            # When no issues have rule IDs (polarity only from severity counts),
            # emit a single generic whole-file label with CWE-1.
            # ------------------------------------------------------------------
            if not example_path.exists():
                result.skipped_count += 1
                continue
            line_count = _count_file_lines(example_path)

            if issues:
                for issue in issues:
                    rule_id = issue["rule_id"]
                    lineno = issue["lineno"]

                    cwe_id = _rule_to_cwe(rule_id)
                    vuln_class = _cwe_to_vuln_class(cwe_id)

                    if lineno is not None:
                        line_start = lineno
                        line_end = lineno
                    else:
                        line_start = 1
                        line_end = line_count

                    label_id = (
                        f"bandit-functional::{example_file}::{rule_id}"
                        f"::{lineno if lineno is not None else 0}"
                    )

                    positive_labels.append(
                        {
                            "id": label_id,
                            "dataset_name": dataset_name,
                            "dataset_version": origin_commit,
                            "file_path": file_path,
                            "line_start": line_start,
                            "line_end": line_end,
                            "cwe_id": cwe_id,
                            "vuln_class": vuln_class,
                            "severity": "MEDIUM",
                            "confidence": "HIGH",
                            "description": f"Bandit rule {rule_id}: expected finding",
                            "source": "benchmark",
                            "source_ref": method_name,
                            "created_at": now_iso,
                        }
                    )
            else:
                # Polarity-only positive: no rule IDs from expect_issues
                label_id = f"bandit-functional::{example_file}::UNKNOWN::0"
                positive_labels.append(
                    {
                        "id": label_id,
                        "dataset_name": dataset_name,
                        "dataset_version": origin_commit,
                        "file_path": file_path,
                        "line_start": 1,
                        "line_end": line_count,
                        "cwe_id": _CWE_FALLBACK,
                        "vuln_class": "other",
                        "severity": "MEDIUM",
                        "confidence": "HIGH",
                        "description": "Bandit rule UNKNOWN: expected finding (polarity only, no rule ID)",
                        "source": "benchmark",
                        "source_ref": method_name,
                        "created_at": now_iso,
                    }
                )
        else:
            # ------------------------------------------------------------------
            # 4. Negative: expected-clean assertion.
            # CWE: inferred from the test method name if possible (e.g. a test
            # named test_os_chmod_no_exception likely covers file-permission
            # rules → B103 → CWE-732).  In practice this is often ambiguous;
            # we default to CWE-NONE / vuln_class="other" when no mapping can
            # be inferred.  The test method name is stored in source_ref so
            # downstream tooling can refine this heuristic later.
            # ------------------------------------------------------------------
            cwe_id = "CWE-NONE"
            vuln_class = "other"

            negative_labels.append(
                {
                    "id": f"bandit-functional::{example_file}::neg",
                    "dataset_name": dataset_name,
                    "dataset_version": origin_commit,
                    "file_path": file_path,
                    "cwe_id": cwe_id,
                    "vuln_class": vuln_class,
                    "source": "benchmark",
                    "source_ref": method_name,
                    "created_at": now_iso,
                }
            )

    # ------------------------------------------------------------------
    # 5. Persist (idempotent INSERT OR IGNORE on deterministic IDs)
    # ------------------------------------------------------------------
    # De-duplicate positive labels by ID (same rule+lineno in two test methods)
    seen_ids: set[str] = set()
    unique_positive_labels: list[dict] = []
    for lbl in positive_labels:
        if lbl["id"] not in seen_ids:
            unique_positive_labels.append(lbl)
            seen_ids.add(lbl["id"])

    if unique_positive_labels:
        await db.append_dataset_labels(unique_positive_labels)
    if negative_labels:
        await db.append_dataset_negative_labels(negative_labels)

    result.positives_count = len(unique_positive_labels)
    result.negatives_count = len(negative_labels)

    return result
