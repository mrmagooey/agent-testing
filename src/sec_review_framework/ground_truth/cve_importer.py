"""CVE pipeline: resolver, discovery, and importer for ground truth datasets."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from pydantic import BaseModel

from sec_review_framework.data.evaluation import GroundTruthLabel, GroundTruthSource
from sec_review_framework.data.findings import Severity, VulnClass

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CVESelectionCriteria(BaseModel):
    """Configurable filters for automatic CVE discovery."""

    languages: list[str] = ["python", "javascript", "go", "java", "ruby"]
    vuln_classes: list[VulnClass] | None = None
    cwe_ids: list[str] | None = None
    min_severity: Severity = Severity.MEDIUM
    max_severity: Severity = Severity.CRITICAL
    max_files_changed: int = 5
    max_lines_changed: int = 200
    min_lines_changed: int = 2
    max_repo_kloc: float = 50.0
    min_repo_kloc: float = 1.0
    published_after: str | None = None
    published_before: str | None = None
    require_github_fix: bool = True


class ResolvedCVE(BaseModel):
    """A CVE with all metadata resolved from advisory databases."""

    cve_id: str
    ghsa_id: str | None = None
    description: str
    cwe_ids: list[str]
    vuln_class: VulnClass
    severity: Severity
    cvss_score: float | None = None
    repo_url: str
    fix_commit_sha: str
    affected_files: list[str]
    lines_changed: int
    language: str
    repo_kloc: float | None = None
    published_date: str
    source: str  # "ghsa" | "nvd" | "osv"


class CVECandidate(BaseModel):
    """A CVE that passed selection criteria, ready for user review."""

    resolved: ResolvedCVE
    score: float
    score_breakdown: dict[str, float]
    importable: bool
    rejection_reason: str | None = None


class CVEImportSpec(BaseModel):
    """Full spec — used when user provides all details, or after resolution."""

    cve_id: str
    repo_url: str
    fix_commit_sha: str
    dataset_name: str
    cwe_id: str
    vuln_class: VulnClass
    severity: Severity
    description: str
    affected_files: list[str] | None = None
    patch_lines_changed: int | None = None


# ---------------------------------------------------------------------------
# CVEResolver
# ---------------------------------------------------------------------------

_SEVERITY_MAP: dict[str, Severity] = {
    "critical": Severity.CRITICAL,
    "high": Severity.HIGH,
    "medium": Severity.MEDIUM,
    "moderate": Severity.MEDIUM,
    "low": Severity.LOW,
    "info": Severity.INFO,
}


class CVEResolver:
    """Resolves a CVE ID to a ResolvedCVE with repo + fix commit info."""

    CWE_TO_VULN_CLASS: dict[str, VulnClass] = {
        "CWE-89": VulnClass.SQLI,
        "CWE-79": VulnClass.XSS,
        "CWE-918": VulnClass.SSRF,
        "CWE-78": VulnClass.RCE,
        "CWE-22": VulnClass.PATH_TRAVERSAL,
        "CWE-327": VulnClass.CRYPTO_MISUSE,
        "CWE-798": VulnClass.HARDCODED_SECRET,
        "CWE-502": VulnClass.DESERIALIZATION,
        "CWE-611": VulnClass.XXE,
        "CWE-601": VulnClass.OPEN_REDIRECT,
        "CWE-284": VulnClass.AUTH_BYPASS,
        "CWE-269": VulnClass.AUTH_BYPASS,
        "CWE-119": VulnClass.MEMORY_SAFETY,
        "CWE-416": VulnClass.MEMORY_SAFETY,
    }

    def __init__(self, github_token: str) -> None:
        self.github_token = github_token
        self.client = httpx.Client(
            timeout=30,
            headers={
                "Authorization": f"token {github_token}",
                "Accept": "application/vnd.github+json",
            },
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, cve_id: str) -> ResolvedCVE | None:
        """
        Resolution order:
        1. GitHub Advisory DB → GHSA record with repo + references
        2. OSV.dev fallback → git commit ranges
        3. NVD reference URLs → GitHub commit URLs
        Returns None if all sources fail.
        """
        try:
            # Step 1: GitHub Advisory DB
            ghsa = self._query_ghsa(cve_id)
            if ghsa:
                refs = ghsa.get("references", [])
                fix_commit = self._extract_fix_commit(refs)
                repo_url = self._extract_repo_url(refs)
                if fix_commit and repo_url:
                    return self._build_resolved(cve_id, ghsa, repo_url, fix_commit)

            # Step 2: OSV.dev fallback
            osv = self._query_osv(cve_id)
            if osv:
                fix_commit = self._extract_osv_fix(osv)
                repo_url = self._extract_osv_repo(osv)
                if fix_commit and repo_url:
                    return self._build_resolved(cve_id, osv, repo_url, fix_commit)

            # Step 3: NVD reference URLs fallback
            nvd = self._query_nvd(cve_id)
            if nvd:
                for ref in nvd.get("references", []):
                    url = ref.get("url", "")
                    if "github.com" in url and "/commit/" in url:
                        repo_url, fix_commit = self._parse_github_commit_url(url)
                        return self._build_resolved(cve_id, nvd, repo_url, fix_commit)

        except Exception:
            pass

        return None

    # ------------------------------------------------------------------
    # Data source queries
    # ------------------------------------------------------------------

    def _query_ghsa(self, cve_id: str) -> dict | None:
        """Query GitHub Advisory Database."""
        try:
            resp = self.client.get(
                "https://api.github.com/advisories",
                params={"cve_id": cve_id},
            )
            if resp.is_success:
                data = resp.json()
                if data:
                    return data[0]
        except httpx.HTTPError:
            pass
        return None

    def _query_osv(self, cve_id: str) -> dict | None:
        """Query OSV.dev."""
        try:
            resp = self.client.post(
                "https://api.osv.dev/v1/query",
                json={"id": cve_id},
            )
            if resp.is_success:
                data = resp.json()
                vulns = data.get("vulns", [])
                if vulns:
                    return vulns[0]
        except httpx.HTTPError:
            pass
        return None

    def _query_nvd(self, cve_id: str) -> dict | None:
        """Query NVD (NIST)."""
        try:
            resp = self.client.get(
                "https://services.nvd.nist.gov/rest/json/cves/2.0",
                params={"cveId": cve_id},
            )
            if resp.is_success:
                data = resp.json()
                vulns = data.get("vulnerabilities", [])
                if vulns:
                    return vulns[0].get("cve", {})
        except httpx.HTTPError:
            pass
        return None

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _extract_fix_commit(self, references: list[dict]) -> str | None:
        """Extract commit SHA from references containing '/commit/'."""
        for ref in references:
            url = ref.get("url", "")
            if "/commit/" in url:
                return url.rstrip("/").split("/")[-1]
        return None

    def _extract_repo_url(self, references: list[dict]) -> str | None:
        """Extract GitHub repo URL from references."""
        for ref in references:
            url = ref.get("url", "")
            if "github.com" not in url:
                continue
            # Parse owner/repo from URL
            import re
            m = re.match(r"(https://github\.com/[^/]+/[^/]+)", url)
            if m:
                return m.group(1)
        return None

    def _parse_github_commit_url(self, url: str) -> tuple[str, str]:
        """Parse 'https://github.com/owner/repo/commit/sha' → (repo_url, sha)."""
        parts = url.rstrip("/").split("/commit/")
        repo_url = parts[0]
        sha = parts[1].split("/")[0] if len(parts) > 1 else ""
        return repo_url, sha

    def _extract_osv_fix(self, osv: dict) -> str | None:
        """Extract fix commit from OSV affected ranges."""
        try:
            affected = osv.get("affected", [])
            if affected:
                ranges = affected[0].get("ranges", [])
                if ranges:
                    for event in ranges[0].get("events", []):
                        if "fixed" in event:
                            return event["fixed"]
        except (IndexError, KeyError, TypeError):
            pass
        return None

    def _extract_osv_repo(self, osv: dict) -> str | None:
        """Extract repo URL from OSV data."""
        try:
            # Try references first
            for ref in osv.get("references", []):
                url = ref.get("url", "")
                if "github.com" in url:
                    import re
                    m = re.match(r"(https://github\.com/[^/]+/[^/]+)", url)
                    if m:
                        return m.group(1)
            # Try affected package
            affected = osv.get("affected", [])
            if affected:
                pkg = affected[0].get("package", {})
                name = pkg.get("name", "")
                if "github.com" in name:
                    return name
        except (IndexError, KeyError, TypeError):
            pass
        return None

    # ------------------------------------------------------------------
    # Building the resolved record
    # ------------------------------------------------------------------

    def _build_resolved(
        self,
        cve_id: str,
        data: dict,
        repo_url: str,
        fix_commit: str,
    ) -> ResolvedCVE:
        """Construct a ResolvedCVE from raw advisory data."""
        # Extract CWE IDs — handle both GHSA and NVD formats
        cwe_ids: list[str] = []
        raw_cwes = data.get("cwes", [])
        for item in raw_cwes:
            if isinstance(item, dict):
                cid = item.get("cwe_id") or item.get("cweId")
                if cid:
                    cwe_ids.append(cid)
            elif isinstance(item, str):
                cwe_ids.append(item)
        # NVD format: weaknesses list
        for weakness in data.get("weaknesses", []):
            for desc in weakness.get("description", []):
                val = desc.get("value", "")
                if val.startswith("CWE-"):
                    cwe_ids.append(val)

        # Map first CWE to VulnClass
        vuln_class = VulnClass.OTHER
        for cid in cwe_ids:
            if cid in self.CWE_TO_VULN_CLASS:
                vuln_class = self.CWE_TO_VULN_CLASS[cid]
                break

        # Severity
        raw_sev = (data.get("severity") or "medium").lower()
        severity = _SEVERITY_MAP.get(raw_sev, Severity.MEDIUM)

        # Description
        description = data.get("summary") or data.get("description") or ""
        if isinstance(description, list):
            # NVD sometimes returns a list of {lang, value}
            for entry in description:
                if isinstance(entry, dict) and entry.get("lang") == "en":
                    description = entry.get("value", "")
                    break
            else:
                description = str(description)

        # Published date
        published_date = (
            data.get("published_at")
            or data.get("published")
            or data.get("datePublished")
            or ""
        )

        # Source detection
        if "ghsa_id" in data:
            source = "ghsa"
        elif "aliases" in data:
            source = "osv"
        else:
            source = "nvd"

        return ResolvedCVE(
            cve_id=cve_id,
            ghsa_id=data.get("ghsa_id"),
            description=str(description),
            cwe_ids=cwe_ids,
            vuln_class=vuln_class,
            severity=severity,
            cvss_score=None,
            repo_url=repo_url,
            fix_commit_sha=fix_commit,
            affected_files=[],
            lines_changed=0,
            language="unknown",
            repo_kloc=None,
            published_date=str(published_date),
            source=source,
        )


# ---------------------------------------------------------------------------
# CVEDiscovery
# ---------------------------------------------------------------------------


class CVEDiscovery:
    """Discovers and ranks CVE candidates matching selection criteria."""

    _ECOSYSTEM_MAP: dict[str, str] = {
        "python": "pip",
        "javascript": "npm",
        "go": "go",
        "java": "maven",
        "ruby": "rubygems",
        "rust": "crates.io",
        "php": "composer",
        "dotnet": "nuget",
    }

    def __init__(self, resolver: CVEResolver) -> None:
        self.resolver = resolver

    def discover(
        self,
        criteria: CVESelectionCriteria,
        max_results: int = 50,
    ) -> list[CVECandidate]:
        """
        1. Query GitHub Advisory DB with ecosystem + severity filters
        2. For each advisory, resolve to get repo + fix commit
        3. Filter by patch size, repo size, language
        4. Score remaining candidates
        5. Return top N ranked by score
        """
        raw_advisories = self._search_advisories(criteria)
        candidates: list[CVECandidate] = []

        for advisory in raw_advisories:
            cve_id = advisory.get("cve_id")
            if not cve_id:
                continue
            try:
                resolved = self.resolver.resolve(cve_id)
            except Exception:
                continue
            if not resolved:
                continue
            candidate = self._evaluate(resolved, criteria)
            if candidate.score > 0:
                candidates.append(candidate)

        candidates.sort(key=lambda c: c.score, reverse=True)
        return candidates[:max_results]

    def _search_advisories(self, criteria: CVESelectionCriteria) -> list[dict]:
        """Query GitHub Advisory DB for each language ecosystem."""
        ecosystems = self._languages_to_ecosystems(criteria.languages)
        results: list[dict] = []
        for ecosystem in ecosystems:
            try:
                resp = self.resolver.client.get(
                    "https://api.github.com/advisories",
                    params={
                        "ecosystem": ecosystem,
                        "severity": criteria.min_severity.value,
                        "type": "reviewed",
                        "per_page": 100,
                    },
                )
                if resp.is_success:
                    results.extend(resp.json())
            except httpx.HTTPError:
                continue
        return results

    def _evaluate(
        self, resolved: ResolvedCVE, criteria: CVESelectionCriteria
    ) -> CVECandidate:
        """Score a resolved CVE against selection criteria."""
        scores: dict[str, float] = {}
        rejection: str | None = None

        # Hard filter: require fix commit
        if criteria.require_github_fix and not resolved.fix_commit_sha:
            return CVECandidate(
                resolved=resolved,
                score=0.0,
                score_breakdown={},
                importable=False,
                rejection_reason="No fix commit found",
            )

        # Hard filters on patch size.
        # lines_changed == 0 and affected_files == [] mean "unknown" at the
        # discovery stage: _build_resolved() never fetches the diff, so these
        # fields are always zero/empty.  Treat 0 as unknown rather than
        # "literally zero" so the CVE isn't spuriously rejected here; the real
        # values are checked again during the import path (CVEImporter).
        if resolved.lines_changed > 0 and resolved.lines_changed > criteria.max_lines_changed:
            rejection = f"Patch too large ({resolved.lines_changed} lines)"
        elif resolved.lines_changed > 0 and resolved.lines_changed < criteria.min_lines_changed:
            rejection = f"Patch too small ({resolved.lines_changed} lines)"
        elif len(resolved.affected_files) > 0 and len(resolved.affected_files) > criteria.max_files_changed:
            rejection = f"Too many files changed ({len(resolved.affected_files)})"

        if rejection:
            return CVECandidate(
                resolved=resolved,
                score=0.0,
                score_breakdown={},
                importable=False,
                rejection_reason=rejection,
            )

        # Soft scores (0-1).
        # Omit patch_size entirely when lines_changed == 0 (unknown at discovery
        # stage) so the score isn't artificially inflated by a meaningless 1.0.
        if resolved.lines_changed > 0:
            patch_score = 1.0 - (resolved.lines_changed / criteria.max_lines_changed)
            scores["patch_size"] = max(0.0, patch_score)

        scores["language"] = 1.0 if resolved.language in criteria.languages else 0.0

        if criteria.vuln_classes:
            scores["vuln_class"] = (
                1.0 if resolved.vuln_class in criteria.vuln_classes else 0.3
            )
        else:
            scores["vuln_class"] = 1.0

        severity_order: dict[Severity, float] = {
            Severity.CRITICAL: 1.0,
            Severity.HIGH: 0.8,
            Severity.MEDIUM: 0.5,
            Severity.LOW: 0.2,
        }
        scores["severity"] = severity_order.get(resolved.severity, 0.3)

        if resolved.repo_kloc is not None:
            if criteria.min_repo_kloc <= resolved.repo_kloc <= criteria.max_repo_kloc:
                scores["repo_size"] = 1.0
            else:
                scores["repo_size"] = 0.3

        total = sum(scores.values()) / len(scores) if scores else 0.0
        return CVECandidate(
            resolved=resolved,
            score=total,
            score_breakdown=scores,
            importable=True,
        )

    @staticmethod
    def _languages_to_ecosystems(languages: list[str]) -> list[str]:
        mapping = {
            "python": "pip",
            "javascript": "npm",
            "go": "go",
            "java": "maven",
            "ruby": "rubygems",
            "rust": "crates.io",
            "php": "composer",
            "dotnet": "nuget",
        }
        return [mapping[lang] for lang in languages if lang in mapping]


# ---------------------------------------------------------------------------
# CVEImporter
# ---------------------------------------------------------------------------


class CVEImporter:
    """Imports a CVE as a labeled ground truth dataset."""

    def __init__(self, datasets_root: Path, resolver: CVEResolver) -> None:
        self.datasets_root = Path(datasets_root)
        self.resolver = resolver

    def import_from_id(
        self, cve_id: str, dataset_name: str | None = None
    ) -> list[GroundTruthLabel]:
        """Resolve-and-import: user provides only the CVE ID."""
        resolved = self.resolver.resolve(cve_id)
        if not resolved:
            raise ValueError(
                f"Could not resolve {cve_id} to a GitHub repo + fix commit"
            )
        spec = CVEImportSpec(
            cve_id=resolved.cve_id,
            repo_url=resolved.repo_url,
            fix_commit_sha=resolved.fix_commit_sha,
            dataset_name=dataset_name
            or f"cve-{cve_id.lower().replace('-', '_')}",
            cwe_id=resolved.cwe_ids[0] if resolved.cwe_ids else "CWE-unknown",
            vuln_class=resolved.vuln_class,
            severity=resolved.severity,
            description=resolved.description,
            affected_files=resolved.affected_files or None,
            patch_lines_changed=resolved.lines_changed or None,
        )
        return self.import_from_spec(spec)

    def import_from_spec(self, spec: CVEImportSpec) -> list[GroundTruthLabel]:
        """
        Full-spec import:
        1. Clone repo at fix_commit_sha~1 (the vulnerable state).
        2. Parse diff to extract file paths.
        3. Create GroundTruthLabel records.
        4. Install repo and labels.
        """
        repo_path = self._clone_at_parent(spec.repo_url, spec.fix_commit_sha)
        affected = spec.affected_files or self._parse_diff(
            repo_path, spec.fix_commit_sha
        )
        if spec.patch_lines_changed is None:
            spec = spec.model_copy(
                update={
                    "patch_lines_changed": self._count_diff_lines(
                        repo_path, spec.fix_commit_sha
                    )
                }
            )
        labels = self._build_labels(spec, affected)
        self._install_repo(repo_path, spec.dataset_name)
        return labels

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _clone_at_parent(self, repo_url: str, fix_sha: str) -> Path:
        """Clone the repo and check out the commit before the fix."""
        tmpdir = tempfile.mkdtemp()
        repo_name = repo_url.rstrip("/").split("/")[-1]
        repo_path = Path(tmpdir) / repo_name
        subprocess.run(
            ["git", "clone", "--depth=10", repo_url, str(repo_path)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", f"{fix_sha}~1"],
            cwd=repo_path,
            check=True,
            capture_output=True,
        )
        return repo_path

    def _parse_diff(self, repo_path: Path, fix_sha: str) -> list[str]:
        """Return list of files changed in the fix commit."""
        result = subprocess.run(
            ["git", "diff", f"{fix_sha}~1", fix_sha, "--name-only"],
            cwd=repo_path,
            capture_output=True,
            text=True,
        )
        return [line for line in result.stdout.splitlines() if line.strip()]

    def _count_diff_lines(self, repo_path: Path, fix_sha: str) -> int | None:
        """Return total insertions + deletions in the fix commit, or None on failure."""
        try:
            result = subprocess.run(
                ["git", "diff", f"{fix_sha}~1", fix_sha, "--numstat"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.SubprocessError:
            return None
        total = 0
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            added, removed = parts[0], parts[1]
            if added == "-" or removed == "-":
                continue  # binary file
            try:
                total += int(added) + int(removed)
            except ValueError:
                continue
        return total if total > 0 else None

    def _build_labels(
        self, spec: CVEImportSpec, affected_files: list[str]
    ) -> list[GroundTruthLabel]:
        """Create one GroundTruthLabel per affected file."""
        labels: list[GroundTruthLabel] = []
        for file_path in affected_files:
            label = GroundTruthLabel(
                id=str(uuid4()),
                dataset_version="v1",
                file_path=file_path,
                line_start=1,
                line_end=1,
                cwe_id=spec.cwe_id,
                vuln_class=spec.vuln_class,
                severity=spec.severity,
                description=spec.description,
                source=GroundTruthSource.CVE_PATCH,
                source_ref=spec.cve_id,
                confidence="confirmed",
                created_at=datetime.now(UTC).replace(tzinfo=None),
                patch_lines_changed=spec.patch_lines_changed,
            )
            labels.append(label)
        return labels

    def _install_repo(self, repo_path: Path, dataset_name: str) -> None:
        """Copy the cloned repo into the datasets directory."""
        dest = self.datasets_root / "targets" / dataset_name / "repo"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(repo_path, dest, dirs_exist_ok=True)
