"""Finding data models — the core output of all scan strategies."""

from enum import Enum
from typing import NamedTuple

from pydantic import BaseModel


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class VulnClass(str, Enum):
    SQLI = "sqli"
    XSS = "xss"
    SSRF = "ssrf"
    RCE = "rce"
    IDOR = "idor"
    AUTH_BYPASS = "auth_bypass"
    CRYPTO_MISUSE = "crypto_misuse"
    HARDCODED_SECRET = "hardcoded_secret"
    PATH_TRAVERSAL = "path_traversal"
    SUPPLY_CHAIN = "supply_chain"
    MEMORY_SAFETY = "memory_safety"
    LOGIC_BUG = "logic_bug"
    DESERIALIZATION = "deserialization"
    XXE = "xxe"
    OPEN_REDIRECT = "open_redirect"
    OTHER = "other"


LINE_BUCKET_SIZE = 10


class FindingIdentity(NamedTuple):
    """Canonical key for deduplication and cross-run matching.

    Line numbers are bucketed to 10-line windows so minor formatting
    changes don't produce false negatives.
    """

    file_path: str
    vuln_class: str
    line_bucket: int

    @classmethod
    def from_finding(cls, finding: "Finding") -> "FindingIdentity":
        bucket = (finding.line_start or 0) // LINE_BUCKET_SIZE
        return cls(file_path=finding.file_path, vuln_class=finding.vuln_class, line_bucket=bucket)

    def __str__(self) -> str:
        lo = self.line_bucket * LINE_BUCKET_SIZE
        return f"{self.file_path}:{self.vuln_class}:L{lo}-{lo + LINE_BUCKET_SIZE - 1}"


class Finding(BaseModel):
    """A single security finding produced by a scan strategy."""

    id: str
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    vuln_class: VulnClass
    cwe_ids: list[str] = []
    severity: Severity
    title: str
    description: str
    recommendation: str | None = None
    confidence: float  # 0.0-1.0, self-reported by model
    raw_llm_output: str
    produced_by: str  # strategy name + subagent id if applicable
    experiment_id: str

    # Verification fields — populated by the verification pass
    verified: bool | None = None
    verification_evidence: str | None = None
    verification_rejected_reason: str | None = None


class DedupEntry(BaseModel):
    """Record of a deduplication merge."""

    kept_finding_id: str
    merged_finding_ids: list[str]
    reason: str


class StrategyOutput(BaseModel):
    """Returned by strategies — captures both findings and dedup metadata."""

    findings: list[Finding]
    pre_dedup_count: int
    post_dedup_count: int
    dedup_log: list[DedupEntry]
    system_prompt: str | None = None
    user_message: str | None = None
    # Populated when the parent strategy declares output_type_name != "finding_list".
    # In that case `findings` is empty and the structured output lives here.
    non_finding_output: object | None = None
    # Fraction of expected subagent dispatches that actually occurred (0.0–1.0).
    # None when the dispatch validator did not run for this strategy.
    dispatch_completeness: float | None = None
