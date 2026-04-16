"""Built-in review profiles and profile registry."""

from pydantic import BaseModel

from sec_review_framework.data.experiment import ReviewProfileName


class ReviewProfile(BaseModel):
    name: ReviewProfileName
    description: str
    system_prompt_modifier: str


BUILTIN_PROFILES: dict[ReviewProfileName, ReviewProfile] = {
    ReviewProfileName.DEFAULT: ReviewProfile(
        name=ReviewProfileName.DEFAULT,
        description=(
            "Balanced security review. Standard confidence thresholds. "
            "Report all vulnerability classes at all severity levels."
        ),
        system_prompt_modifier="",
    ),
    ReviewProfileName.STRICT: ReviewProfile(
        name=ReviewProfileName.STRICT,
        description=(
            "High evidence bar. Only report findings you are near-certain about. "
            "Every finding must include a specific file:line citation and a concrete "
            "explanation of how the vulnerability could be exploited."
        ),
        system_prompt_modifier="""\
REVIEW INSTRUCTIONS — STRICT MODE:
- Only report findings where you have high confidence (0.8+) the vulnerability is real
- Every finding MUST include specific file:line citations in the source code
- Every finding MUST explain the concrete exploitation path — not just \
"this could be vulnerable" but exactly how an attacker would exploit it
- If you cannot trace the data flow from user input to the vulnerable sink, \
do not report the finding
- Prefer fewer, higher-quality findings over comprehensive coverage
- Do not report potential issues based on naming conventions alone""",
    ),
    ReviewProfileName.COMPREHENSIVE: ReviewProfile(
        name=ReviewProfileName.COMPREHENSIVE,
        description=(
            "Cast a wide net. Lower confidence threshold. Report anything suspicious "
            "including potential issues that need manual review."
        ),
        system_prompt_modifier="""\
REVIEW INSTRUCTIONS — COMPREHENSIVE MODE:
- Report all potential security issues, even at lower confidence levels (0.3+)
- Include findings that might be false positives but warrant human review
- Flag code patterns that are not vulnerable today but could become vulnerable \
if assumptions change (e.g., input validation removed upstream)
- Report defense-in-depth concerns (missing but not strictly required mitigations)
- When uncertain, report the finding with a clear note about what you're unsure about""",
    ),
    ReviewProfileName.OWASP_FOCUSED: ReviewProfile(
        name=ReviewProfileName.OWASP_FOCUSED,
        description=(
            "Focus exclusively on OWASP Top 10 2021 vulnerability categories. "
            "Ignore all other vulnerability classes."
        ),
        system_prompt_modifier="""\
REVIEW INSTRUCTIONS — OWASP TOP 10 FOCUS:
You MUST limit your analysis to the OWASP Top 10 2021 categories only:
- A01:2021 Broken Access Control
- A02:2021 Cryptographic Failures
- A03:2021 Injection
- A04:2021 Insecure Design
- A05:2021 Security Misconfiguration
- A06:2021 Vulnerable and Outdated Components
- A07:2021 Identification and Authentication Failures
- A08:2021 Software and Data Integrity Failures
- A09:2021 Security Logging and Monitoring Failures
- A10:2021 Server-Side Request Forgery (SSRF)
Do not report issues that fall outside these categories.
Map each finding to its OWASP category in the description.""",
    ),
    ReviewProfileName.QUICK_SCAN: ReviewProfile(
        name=ReviewProfileName.QUICK_SCAN,
        description=(
            "Triage mode. Only report critical and high severity issues. "
            "Skip detailed explanations in favor of fast, actionable output."
        ),
        system_prompt_modifier="""\
REVIEW INSTRUCTIONS — QUICK SCAN / TRIAGE MODE:
- Only report Critical and High severity vulnerabilities
- Skip Medium, Low, and Informational findings entirely
- Keep descriptions brief — one or two sentences explaining the risk
- Focus on issues that represent immediate, exploitable risk
- Prioritize: RCE > SQLi > Auth Bypass > SSRF > XSS > everything else
- Aim for speed over completeness — flag the worst issues first""",
    ),
}


class ProfileRegistry:
    def get(self, name: ReviewProfileName) -> ReviewProfile:
        profile = BUILTIN_PROFILES.get(name)
        if profile is None:
            raise ValueError(f"Unknown review profile: {name}")
        return profile

    def list_all(self) -> list[ReviewProfile]:
        return list(BUILTIN_PROFILES.values())
