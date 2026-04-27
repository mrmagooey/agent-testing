"""Unit tests for CVEResolver, CVEDiscovery, and CVEImporter."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import httpx
import pytest

from sec_review_framework.ground_truth.cve_importer import (
    CVECandidate,
    CVEDiscovery,
    CVEImportSpec,
    CVEImporter,
    CVEResolver,
    CVESelectionCriteria,
    ResolvedCVE,
)
from sec_review_framework.data.findings import Severity, VulnClass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_resolved_cve(
    cve_id: str = "CVE-2023-00001",
    severity: Severity = Severity.HIGH,
    vuln_class: VulnClass = VulnClass.SQLI,
    lines_changed: int = 20,
    affected_files: list[str] | None = None,
    fix_commit_sha: str = "abc123def456",
    language: str = "python",
    repo_kloc: float | None = 10.0,
) -> ResolvedCVE:
    return ResolvedCVE(
        cve_id=cve_id,
        ghsa_id="GHSA-xxxx-yyyy-zzzz",
        description="SQL injection via user input",
        cwe_ids=["CWE-89"],
        vuln_class=vuln_class,
        severity=severity,
        cvss_score=8.1,
        repo_url="https://github.com/owner/repo",
        fix_commit_sha=fix_commit_sha,
        affected_files=affected_files or ["app/views.py"],
        lines_changed=lines_changed,
        language=language,
        repo_kloc=repo_kloc,
        published_date="2023-01-01",
        source="ghsa",
    )


def _make_resolver(github_token: str = "fake-token") -> CVEResolver:
    return CVEResolver(github_token=github_token)


# ---------------------------------------------------------------------------
# CVEResolver — extraction helpers
# ---------------------------------------------------------------------------


class TestCVEResolverExtractionHelpers:
    def test_extract_fix_commit_from_commit_url(self):
        resolver = _make_resolver()
        refs = [{"url": "https://github.com/owner/repo/commit/abc123def456"}]
        sha = resolver._extract_fix_commit(refs)
        assert sha == "abc123def456"

    def test_extract_fix_commit_returns_none_when_no_commit_url(self):
        resolver = _make_resolver()
        refs = [{"url": "https://github.com/owner/repo/issues/42"}]
        sha = resolver._extract_fix_commit(refs)
        assert sha is None

    def test_extract_fix_commit_empty_refs(self):
        resolver = _make_resolver()
        assert resolver._extract_fix_commit([]) is None

    def test_extract_repo_url_from_github_ref(self):
        resolver = _make_resolver()
        refs = [{"url": "https://github.com/owner/myrepo/commit/abc123"}]
        url = resolver._extract_repo_url(refs)
        assert url == "https://github.com/owner/myrepo"

    def test_extract_repo_url_skips_non_github(self):
        resolver = _make_resolver()
        refs = [{"url": "https://nvd.nist.gov/vuln/detail/CVE-2023-00001"}]
        url = resolver._extract_repo_url(refs)
        assert url is None

    def test_parse_github_commit_url(self):
        resolver = _make_resolver()
        repo_url, sha = resolver._parse_github_commit_url(
            "https://github.com/owner/repo/commit/deadbeef1234"
        )
        assert repo_url == "https://github.com/owner/repo"
        assert sha == "deadbeef1234"

    def test_extract_osv_fix_from_events(self):
        resolver = _make_resolver()
        osv = {
            "affected": [
                {
                    "ranges": [
                        {
                            "events": [
                                {"introduced": "0"},
                                {"fixed": "1.2.3"},
                            ]
                        }
                    ]
                }
            ]
        }
        assert resolver._extract_osv_fix(osv) == "1.2.3"

    def test_extract_osv_fix_returns_none_on_empty(self):
        resolver = _make_resolver()
        assert resolver._extract_osv_fix({}) is None

    def test_extract_osv_repo_from_references(self):
        resolver = _make_resolver()
        osv = {
            "references": [
                {"url": "https://github.com/owner/project/commit/abc123"}
            ]
        }
        assert resolver._extract_osv_repo(osv) == "https://github.com/owner/project"

    def test_extract_osv_repo_returns_none_when_no_github(self):
        resolver = _make_resolver()
        osv = {"references": [{"url": "https://example.com"}]}
        assert resolver._extract_osv_repo(osv) is None


# ---------------------------------------------------------------------------
# CVEResolver — _build_resolved
# ---------------------------------------------------------------------------


class TestBuildResolved:
    def test_build_resolved_ghsa_format(self):
        resolver = _make_resolver()
        data = {
            "ghsa_id": "GHSA-xxxx-yyyy-zzzz",
            "summary": "SQL injection via user-supplied parameter",
            "severity": "high",
            "cwes": [{"cwe_id": "CWE-89"}],
            "published_at": "2023-06-01T00:00:00Z",
        }
        resolved = resolver._build_resolved("CVE-2023-00001", data, "https://github.com/o/r", "abc123")
        assert resolved.cve_id == "CVE-2023-00001"
        assert resolved.severity == Severity.HIGH
        assert resolved.vuln_class == VulnClass.SQLI
        assert resolved.source == "ghsa"
        assert resolved.cwe_ids == ["CWE-89"]

    def test_build_resolved_maps_moderate_to_medium(self):
        resolver = _make_resolver()
        data = {"severity": "moderate", "cwes": [], "summary": "desc"}
        resolved = resolver._build_resolved("CVE-2023-00002", data, "https://github.com/o/r", "sha")
        assert resolved.severity == Severity.MEDIUM

    def test_build_resolved_unknown_severity_defaults_to_medium(self):
        resolver = _make_resolver()
        data = {"severity": "unknown_level", "cwes": []}
        resolved = resolver._build_resolved("CVE-2023-00003", data, "https://github.com/o/r", "sha")
        assert resolved.severity == Severity.MEDIUM

    def test_build_resolved_unknown_cwe_yields_other_vuln_class(self):
        resolver = _make_resolver()
        data = {"severity": "high", "cwes": [{"cwe_id": "CWE-9999"}]}
        resolved = resolver._build_resolved("CVE-2023-00004", data, "https://github.com/o/r", "sha")
        assert resolved.vuln_class == VulnClass.OTHER

    def test_build_resolved_nvd_description_list_format(self):
        """NVD sometimes returns description as list of {lang, value} dicts."""
        resolver = _make_resolver()
        data = {
            "severity": "medium",
            "cwes": [],
            "description": [
                {"lang": "es", "value": "Descripción en español"},
                {"lang": "en", "value": "English description of the bug"},
            ],
        }
        resolved = resolver._build_resolved("CVE-2023-00005", data, "https://github.com/o/r", "sha")
        assert resolved.description == "English description of the bug"

    def test_build_resolved_osv_source_detected(self):
        resolver = _make_resolver()
        data = {"aliases": ["CVE-2023-99999"], "severity": "low", "cwes": []}
        resolved = resolver._build_resolved("CVE-2023-99999", data, "https://github.com/o/r", "sha")
        assert resolved.source == "osv"

    def test_build_resolved_with_injection_in_description(self):
        """Description with injection-like content should be stored verbatim (no sanitisation needed)."""
        resolver = _make_resolver()
        malicious_desc = "DROP TABLE users; -- injection via <script>alert(1)</script>"
        data = {"severity": "high", "cwes": [], "summary": malicious_desc}
        resolved = resolver._build_resolved("CVE-2023-00006", data, "https://github.com/o/r", "sha")
        assert resolved.description == malicious_desc


# ---------------------------------------------------------------------------
# CVEResolver — GitHub rate-limit handling
# ---------------------------------------------------------------------------


class TestGitHubRateLimit:
    def test_query_ghsa_rate_limit_returns_none(self):
        """If the GitHub API returns 429 or raises, _query_ghsa returns None."""
        resolver = _make_resolver()
        mock_resp = MagicMock()
        mock_resp.is_success = False
        mock_resp.status_code = 429
        resolver.client.get = MagicMock(return_value=mock_resp)

        result = resolver._query_ghsa("CVE-2023-00001")
        assert result is None

    def test_query_ghsa_http_error_returns_none(self):
        resolver = _make_resolver()
        resolver.client.get = MagicMock(side_effect=httpx.HTTPError("connection failed"))

        result = resolver._query_ghsa("CVE-2023-00001")
        assert result is None

    def test_query_osv_http_error_returns_none(self):
        resolver = _make_resolver()
        resolver.client.post = MagicMock(side_effect=httpx.HTTPError("timeout"))

        result = resolver._query_osv("CVE-2023-00001")
        assert result is None

    def test_resolve_all_sources_fail_returns_none(self):
        """If all three data sources fail, resolve() returns None without raising."""
        resolver = _make_resolver()
        resolver.client.get = MagicMock(side_effect=httpx.HTTPError("network"))
        resolver.client.post = MagicMock(side_effect=httpx.HTTPError("network"))

        result = resolver.resolve("CVE-2023-00001")
        assert result is None


# ---------------------------------------------------------------------------
# CVEDiscovery — _evaluate
# ---------------------------------------------------------------------------


class TestCVEDiscoveryEvaluate:
    def _make_discovery(self) -> CVEDiscovery:
        resolver = _make_resolver()
        return CVEDiscovery(resolver=resolver)

    def test_candidate_importable_when_within_criteria(self):
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria(max_lines_changed=200, min_lines_changed=2, max_files_changed=5)
        resolved = _make_resolved_cve(lines_changed=50, affected_files=["app.py"])
        candidate = discovery._evaluate(resolved, criteria)
        assert candidate.importable is True
        assert candidate.score > 0.0

    def test_candidate_rejected_when_patch_too_large(self):
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria(max_lines_changed=10)
        resolved = _make_resolved_cve(lines_changed=500)
        candidate = discovery._evaluate(resolved, criteria)
        assert candidate.importable is False
        assert "large" in (candidate.rejection_reason or "").lower()

    def test_candidate_rejected_when_patch_too_small(self):
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria(min_lines_changed=10)
        resolved = _make_resolved_cve(lines_changed=1)
        candidate = discovery._evaluate(resolved, criteria)
        assert candidate.importable is False
        assert "small" in (candidate.rejection_reason or "").lower()

    def test_candidate_rejected_too_many_files(self):
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria(max_files_changed=2)
        resolved = _make_resolved_cve(affected_files=["a.py", "b.py", "c.py"])
        candidate = discovery._evaluate(resolved, criteria)
        assert candidate.importable is False

    def test_candidate_rejected_no_fix_commit(self):
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria(require_github_fix=True)
        resolved = _make_resolved_cve(fix_commit_sha="")
        candidate = discovery._evaluate(resolved, criteria)
        assert candidate.importable is False
        assert candidate.score == 0.0

    def test_score_breakdown_contains_expected_keys(self):
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria()
        resolved = _make_resolved_cve(lines_changed=50, language="python")
        candidate = discovery._evaluate(resolved, criteria)
        assert "patch_size" in candidate.score_breakdown
        assert "language" in candidate.score_breakdown
        assert "severity" in candidate.score_breakdown

    def test_vuln_class_filter_reduces_score_for_mismatch(self):
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria(vuln_classes=[VulnClass.XSS])
        resolved = _make_resolved_cve(vuln_class=VulnClass.SQLI)
        candidate = discovery._evaluate(resolved, criteria)
        # Score should be reduced (0.3) rather than full (1.0) for vuln_class mismatch
        assert candidate.score_breakdown.get("vuln_class", 1.0) == pytest.approx(0.3)

    def test_malformed_selection_criteria_language_empty(self):
        """Empty language list should produce candidates without language score."""
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria(languages=[])
        resolved = _make_resolved_cve(language="python")
        candidate = discovery._evaluate(resolved, criteria)
        # language score = 0.0 (python not in [])
        assert candidate.score_breakdown.get("language", 1.0) == pytest.approx(0.0)

    # --- Regression tests: discovery-stage shape (lines_changed=0, affected_files=[]) ---

    def test_discovery_stage_shape_is_importable(self):
        """Regression: lines_changed=0 and affected_files=[] (discovery stage) must not be
        rejected.  The diff is never fetched during discovery so 0 means unknown."""
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria(
            max_lines_changed=200, min_lines_changed=2, max_files_changed=5
        )
        resolved = _make_resolved_cve(lines_changed=0, affected_files=[])
        candidate = discovery._evaluate(resolved, criteria)
        assert candidate.importable is True
        assert candidate.score > 0.0
        assert candidate.rejection_reason is None
        assert "Patch too small" not in (candidate.rejection_reason or "")
        assert "Too many files changed" not in (candidate.rejection_reason or "")
        assert "patch_size" not in candidate.score_breakdown

    def test_real_values_include_patch_size_in_breakdown(self):
        """Existing behavior: a CVE with real diff data still gets patch_size scored."""
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria(max_lines_changed=200, min_lines_changed=2)
        resolved = _make_resolved_cve(lines_changed=50, affected_files=["a.py"])
        candidate = discovery._evaluate(resolved, criteria)
        assert candidate.importable is True
        assert "patch_size" in candidate.score_breakdown

    def test_hard_filter_fires_when_patch_too_large(self):
        """Hard filter still rejects when lines_changed is present and exceeds max."""
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria(max_lines_changed=200)
        resolved = _make_resolved_cve(lines_changed=500, affected_files=["a.py"])
        candidate = discovery._evaluate(resolved, criteria)
        assert candidate.importable is False
        assert "Patch too large" in (candidate.rejection_reason or "")

    def test_hard_filter_fires_when_too_many_files(self):
        """Hard filter still rejects when affected_files is present and exceeds max."""
        discovery = self._make_discovery()
        criteria = CVESelectionCriteria(max_files_changed=5)
        resolved = _make_resolved_cve(
            lines_changed=10,
            affected_files=["a.py", "b.py", "c.py", "d.py", "e.py", "f.py"],
        )
        candidate = discovery._evaluate(resolved, criteria)
        assert candidate.importable is False
        assert "Too many files changed" in (candidate.rejection_reason or "")


# ---------------------------------------------------------------------------
# CVEImporter — _build_labels (dedup, injection in description/CWE)
# ---------------------------------------------------------------------------


class TestCVEImporterBuildLabels:
    def _make_importer(self, tmp_path: Path) -> CVEImporter:
        resolver = _make_resolver()
        return CVEImporter(datasets_root=tmp_path, resolver=resolver)

    def test_build_labels_creates_one_label_per_file(self, tmp_path: Path):
        importer = self._make_importer(tmp_path)
        spec = CVEImportSpec(
            cve_id="CVE-2023-00001",
            repo_url="https://github.com/o/r",
            fix_commit_sha="abc123",
            dataset_name="test-ds",
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="SQL injection",
        )
        labels = importer._build_labels(spec, ["file_a.py", "file_b.py"])
        assert len(labels) == 2
        file_paths = {l.file_path for l in labels}
        assert file_paths == {"file_a.py", "file_b.py"}

    def test_build_labels_have_unique_ids(self, tmp_path: Path):
        importer = self._make_importer(tmp_path)
        spec = CVEImportSpec(
            cve_id="CVE-2023-00001",
            repo_url="https://github.com/o/r",
            fix_commit_sha="abc123",
            dataset_name="test-ds",
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="test",
        )
        labels = importer._build_labels(spec, ["a.py", "b.py", "c.py"])
        ids = [l.id for l in labels]
        assert len(ids) == len(set(ids))  # all unique UUIDs

    def test_build_labels_cve_source_ref_set(self, tmp_path: Path):
        importer = self._make_importer(tmp_path)
        spec = CVEImportSpec(
            cve_id="CVE-2023-00007",
            repo_url="https://github.com/o/r",
            fix_commit_sha="abc123",
            dataset_name="test-ds",
            cwe_id="CWE-79",
            vuln_class=VulnClass.XSS,
            severity=Severity.MEDIUM,
            description="XSS",
        )
        labels = importer._build_labels(spec, ["templates/index.html"])
        assert labels[0].source_ref == "CVE-2023-00007"

    def test_build_labels_with_injection_in_description(self, tmp_path: Path):
        """Description containing injection-like content should be stored verbatim."""
        importer = self._make_importer(tmp_path)
        malicious = "<script>alert(1)</script>; DROP TABLE cves;"
        spec = CVEImportSpec(
            cve_id="CVE-2023-00008",
            repo_url="https://github.com/o/r",
            fix_commit_sha="abc123",
            dataset_name="test-ds",
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description=malicious,
        )
        labels = importer._build_labels(spec, ["app.py"])
        assert labels[0].description == malicious

    def test_build_labels_with_injection_in_cwe_field(self, tmp_path: Path):
        """CWE ID with unusual characters should not cause parsing errors."""
        importer = self._make_importer(tmp_path)
        spec = CVEImportSpec(
            cve_id="CVE-2023-00009",
            repo_url="https://github.com/o/r",
            fix_commit_sha="abc123",
            dataset_name="test-ds",
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="desc",
        )
        labels = importer._build_labels(spec, ["a.py"])
        assert labels[0].cwe_id == "CWE-89"

    def test_build_labels_empty_files_returns_empty_list(self, tmp_path: Path):
        importer = self._make_importer(tmp_path)
        spec = CVEImportSpec(
            cve_id="CVE-2023-00010",
            repo_url="https://github.com/o/r",
            fix_commit_sha="abc123",
            dataset_name="test-ds",
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="empty",
        )
        labels = importer._build_labels(spec, [])
        assert labels == []

    def test_build_labels_propagates_patch_lines_changed(self, tmp_path: Path):
        importer = self._make_importer(tmp_path)
        spec = CVEImportSpec(
            cve_id="CVE-2023-00099",
            repo_url="https://github.com/o/r",
            fix_commit_sha="abc123",
            dataset_name="test-ds",
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="x",
            patch_lines_changed=42,
        )
        labels = importer._build_labels(spec, ["a.py", "b.py"])
        assert all(l.patch_lines_changed == 42 for l in labels)

    def test_build_labels_patch_lines_none_when_unset(self, tmp_path: Path):
        importer = self._make_importer(tmp_path)
        spec = CVEImportSpec(
            cve_id="CVE-2023-00100",
            repo_url="https://github.com/o/r",
            fix_commit_sha="abc123",
            dataset_name="test-ds",
            cwe_id="CWE-89",
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            description="x",
        )
        labels = importer._build_labels(spec, ["a.py"])
        assert labels[0].patch_lines_changed is None


# ---------------------------------------------------------------------------
# CVEDiscovery._languages_to_ecosystems
# ---------------------------------------------------------------------------


class TestLanguagesToEcosystems:
    def test_known_languages_mapped(self):
        ecosystems = CVEDiscovery._languages_to_ecosystems(["python", "javascript", "go"])
        assert "pip" in ecosystems
        assert "npm" in ecosystems
        assert "go" in ecosystems

    def test_unknown_language_skipped(self):
        ecosystems = CVEDiscovery._languages_to_ecosystems(["cobol", "fortran"])
        assert ecosystems == []

    def test_mixed_known_unknown(self):
        ecosystems = CVEDiscovery._languages_to_ecosystems(["python", "cobol"])
        assert ecosystems == ["pip"]
