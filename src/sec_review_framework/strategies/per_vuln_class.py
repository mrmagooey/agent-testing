"""PerVulnClassStrategy — specialist subagents per vulnerability class."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sec_review_framework.data.findings import StrategyOutput, VulnClass
from sec_review_framework.strategies.base import ScanStrategy
from sec_review_framework.strategies.common import (
    FINDING_OUTPUT_FORMAT,
    FindingParser,
    build_system_prompt,
    deduplicate,
    run_subagents,
)

if TYPE_CHECKING:
    from sec_review_framework.models.base import ModelProvider
    from sec_review_framework.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Specialist system prompts — one per VulnClass
# ---------------------------------------------------------------------------

VULN_CLASS_SYSTEM_PROMPTS: dict[VulnClass, str] = {
    VulnClass.SQLI: (
        "You are a SQL injection specialist. Your sole focus is finding SQL injection "
        "vulnerabilities: classic SQLi, blind SQLi, time-based SQLi, second-order SQLi, "
        "and ORM misuse that bypasses parameterization. Ignore all other vulnerability types."
    ),
    VulnClass.XSS: (
        "You are a cross-site scripting (XSS) specialist. Your sole focus is reflected, "
        "stored, and DOM-based XSS. Pay close attention to template rendering, innerHTML "
        "assignments, and framework-specific escaping. Ignore all other vulnerability types."
    ),
    VulnClass.SSRF: (
        "You are a server-side request forgery (SSRF) specialist. Your sole focus is "
        "outbound HTTP requests triggered by user-supplied URLs or hosts, including "
        "partial SSRF via DNS rebinding and cloud metadata endpoint exposure. "
        "Ignore all other vulnerability types."
    ),
    VulnClass.RCE: (
        "You are a remote code execution (RCE) specialist. Your sole focus is code "
        "execution sinks: shell injection, template injection, dynamic code loading, "
        "and unsafe subprocess calls with user input. "
        "Ignore all other vulnerability types."
    ),
    VulnClass.IDOR: (
        "You are an insecure direct object reference (IDOR) specialist. Your sole focus "
        "is authorization failures where a user can access or modify another user's "
        "resources by manipulating IDs or references. Ignore all other vulnerability types."
    ),
    VulnClass.AUTH_BYPASS: (
        "You are an authentication bypass specialist. Your sole focus is weaknesses that "
        "allow unauthenticated or under-privileged access: broken session management, "
        "JWT algorithm confusion, missing auth checks, and logic flaws in login flows. "
        "Ignore all other vulnerability types."
    ),
    VulnClass.CRYPTO_MISUSE: (
        "You are a cryptography misuse specialist. Your sole focus is incorrect use of "
        "cryptographic primitives: weak algorithms (MD5, SHA1, DES), hard-coded keys, "
        "IV reuse, insecure random number generation, and broken TLS configurations. "
        "Ignore all other vulnerability types."
    ),
    VulnClass.HARDCODED_SECRET: (
        "You are a hardcoded secret specialist. Your sole focus is secrets embedded "
        "directly in source code: API keys, passwords, private keys, tokens, and "
        "connection strings. Ignore all other vulnerability types."
    ),
    VulnClass.PATH_TRAVERSAL: (
        "You are a path traversal specialist. Your sole focus is directory traversal "
        "vulnerabilities where user-controlled input reaches file system operations "
        "without adequate sanitization. Ignore all other vulnerability types."
    ),
    VulnClass.SUPPLY_CHAIN: (
        "You are a supply chain security specialist. Your sole focus is dependency "
        "confusion attacks, pinning weaknesses, use of known-vulnerable packages, "
        "and build pipeline injection risks. Ignore all other vulnerability types."
    ),
    VulnClass.MEMORY_SAFETY: (
        "You are a memory safety specialist. Your sole focus is buffer overflows, "
        "use-after-free, integer overflows leading to memory corruption, and unsafe "
        "pointer arithmetic. Ignore all other vulnerability types."
    ),
    VulnClass.LOGIC_BUG: (
        "You are a business logic bug specialist. Your sole focus is logical flaws "
        "in the application's intended behavior: race conditions, time-of-check/"
        "time-of-use (TOCTOU) issues, incorrect state machine transitions, and "
        "missing edge-case handling. Ignore all other vulnerability types."
    ),
    VulnClass.DESERIALIZATION: (
        "You are a deserialization specialist. Your sole focus is unsafe deserialization "
        "of untrusted data using language-native serialization formats (Python binary "
        "serialization, YAML load, Java object streams, PHP native serialization, etc.) "
        "that can lead to RCE or object injection. "
        "Ignore all other vulnerability types."
    ),
    VulnClass.XXE: (
        "You are an XML external entity (XXE) specialist. Your sole focus is XXE "
        "vulnerabilities: XML parsers that resolve external entities, DOCTYPE declarations, "
        "and SSRF via XML. Ignore all other vulnerability types."
    ),
    VulnClass.OPEN_REDIRECT: (
        "You are an open redirect specialist. Your sole focus is URL redirect sinks "
        "that accept user-controlled destinations without allowlist validation, "
        "enabling phishing and auth token leakage. Ignore all other vulnerability types."
    ),
    VulnClass.OTHER: (
        "You are a general security specialist covering vulnerability classes not "
        "addressed by other specialists. Focus on security-relevant issues that do not "
        "fit neatly into the standard categories: clickjacking, information disclosure, "
        "misconfigured security headers, insecure defaults, and similar."
    ),
}


class PerVulnClassStrategy(ScanStrategy):
    """Assign one specialist subagent per vulnerability class, scanning the full repo.

    Each subagent focuses exclusively on its assigned vulnerability class and
    scans the entire repository.  All findings are merged and deduplicated.
    Supports parallel execution via ``config["parallel"]``.
    """

    def name(self) -> str:
        return "per_vuln_class"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_repo_summary(self, target) -> str:
        """Return a text representation of the repository file tree."""
        try:
            return target.get_file_tree()
        except AttributeError:
            files = target.list_source_files()
            return "\n".join(files)

    # ------------------------------------------------------------------
    # ScanStrategy.run()
    # ------------------------------------------------------------------

    def run(
        self,
        target,
        model: "ModelProvider",
        tools: "ToolRegistry",
        config: dict,
    ) -> StrategyOutput:
        active_classes: list[VulnClass] = config.get("vuln_classes", list(VulnClass))
        repo_summary = self._build_repo_summary(target)
        experiment_id = config.get("experiment_id", "")
        max_turns_per_class = config.get("max_turns_per_class", 40)
        parallel = config.get("parallel", False)

        tasks = []
        for vuln_class in active_classes:
            base_prompt = VULN_CLASS_SYSTEM_PROMPTS[vuln_class]
            system_prompt = build_system_prompt(base_prompt, config)
            tasks.append(
                {
                    "system_prompt": system_prompt,
                    "user_message": (
                        f"You are scanning this repository for {vuln_class} vulnerabilities only.\n"
                        f"Ignore all other vulnerability types.\n\n"
                        f"Repository structure:\n{repo_summary}\n\n"
                        f"Use your tools to read any files that might contain {vuln_class} issues.\n"
                        f"{FINDING_OUTPUT_FORMAT}"
                    ),
                    "max_turns": max_turns_per_class,
                }
            )

        outputs = run_subagents(tasks, model, tools, parallel=parallel)

        all_findings = []
        for vuln_class, raw_output in zip(active_classes, outputs):
            findings = FindingParser().parse(
                raw_output,
                experiment_id=experiment_id,
                produced_by=f"specialist:{vuln_class}",
            )
            all_findings.extend(findings)

        return deduplicate(all_findings)
