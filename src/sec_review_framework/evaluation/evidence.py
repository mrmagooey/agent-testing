"""Evidence quality assessment — heuristic and LLM-judge variants."""

import json
import re

from sec_review_framework.data.evaluation import EvidenceQuality, GroundTruthLabel
from sec_review_framework.data.findings import Finding, VulnClass

JUDGE_PROMPT = """
You are evaluating whether a security scanner correctly identified a vulnerability.

## Finding description (what the scanner reported)
{description}

## Source code excerpt
```
{source_excerpt}
```

## Labeled vulnerability
- Type: {vuln_class}
- Expected location: lines {line_start}-{line_end}

Answer ONLY with a JSON object:
{{
  "accurate": true|false,
  "identifies_mechanism": true|false,
  "cites_correct_code": true|false,
  "reason": "<one sentence>"
}}
"""


class EvidenceQualityAssessor:
    """
    Assesses the quality of a true positive finding's explanation.

    A finding can be correctly located (TP) but have poor reasoning.
    This matters for production use — human reviewers need actionable
    explanations.

    Scoring:
      +1  description cites specific file:line references
      +1  description mentions the correct vulnerability mechanism
      +1  finding line range overlaps the label line range
      >=3 -> STRONG, >=2 -> ADEQUATE, else WEAK
    """

    def assess(self, finding: Finding, label: GroundTruthLabel) -> EvidenceQuality:
        score = 0

        # Does the description cite specific file:line references?
        has_line_citations = bool(re.findall(r"\b\w+\.\w+:\d+", finding.description))
        if has_line_citations:
            score += 1

        # Does the description mention the correct vulnerability mechanism?
        mechanism_keywords = self._get_mechanism_keywords(label.vuln_class)
        mechanism_mentioned = any(kw in finding.description.lower() for kw in mechanism_keywords)
        if mechanism_mentioned:
            score += 1

        # Does the finding have line overlap with the label?
        if finding.line_start and finding.line_end and not (
            finding.line_end < label.line_start or finding.line_start > label.line_end
        ):
            score += 1

        if score >= 3:
            return EvidenceQuality.STRONG
        elif score >= 2:
            return EvidenceQuality.ADEQUATE
        else:
            return EvidenceQuality.WEAK

    def _get_mechanism_keywords(self, vuln_class: VulnClass) -> list[str]:
        return {
            VulnClass.SQLI: ["sql", "query", "injection", "parameterized", "prepared statement"],
            VulnClass.XSS: ["script", "html", "escape", "sanitize", "cross-site"],
            VulnClass.SSRF: ["url", "request", "fetch", "redirect", "internal"],
            VulnClass.CRYPTO_MISUSE: ["key", "encrypt", "hash", "random", "iv", "nonce"],
            VulnClass.RCE: ["exec", "command", "shell", "subprocess", "code execution"],
            VulnClass.IDOR: ["object", "reference", "authorization", "access control", "id"],
            VulnClass.AUTH_BYPASS: ["authentication", "bypass", "token", "session", "credential"],
            VulnClass.HARDCODED_SECRET: ["secret", "password", "api key", "hardcoded", "credential"],
            VulnClass.PATH_TRAVERSAL: ["path", "traversal", "directory", "file", "../"],
            VulnClass.SUPPLY_CHAIN: ["dependency", "package", "supply chain", "third-party"],
            VulnClass.MEMORY_SAFETY: ["buffer", "overflow", "use-after-free", "memory", "heap"],
            VulnClass.LOGIC_BUG: ["logic", "condition", "validation", "check", "flow"],
            VulnClass.DESERIALIZATION: ["deserializ", "marshal", "untrusted data", "object graph"],
            VulnClass.XXE: ["xml", "entity", "external", "xxe", "dtd"],
            VulnClass.OPEN_REDIRECT: ["redirect", "url", "location", "forward", "open redirect"],
        }.get(vuln_class, [])


class LLMEvidenceAssessor:
    """
    Uses an LLM to judge evidence quality.

    More accurate than heuristics, but adds cost — use for final
    evaluation, not exploratory batches.
    """

    def __init__(self, model, target) -> None:
        """
        Parameters
        ----------
        model:
            A ModelProvider instance used to call the judge LLM.
        target:
            A TargetCodebase instance with a read_file_excerpt() method.
        """
        self.model = model
        self.target = target

    def assess(self, finding: Finding, label: GroundTruthLabel) -> EvidenceQuality:
        from sec_review_framework.models.base import Message

        excerpt = self.target.read_file_excerpt(
            finding.file_path, label.line_start, context_lines=20
        )
        prompt = JUDGE_PROMPT.format(
            description=finding.description,
            source_excerpt=excerpt,
            vuln_class=label.vuln_class,
            line_start=label.line_start,
            line_end=label.line_end,
        )
        response = self.model.complete(
            [Message(role="user", content=prompt)], max_tokens=256
        )
        try:
            verdict = json.loads(response.content)
        except json.JSONDecodeError:
            return EvidenceQuality.NOT_ASSESSED

        score = sum([
            verdict.get("accurate", False),
            verdict.get("identifies_mechanism", False),
            verdict.get("cites_correct_code", False),
        ])
        if score >= 3:
            return EvidenceQuality.STRONG
        elif score >= 2:
            return EvidenceQuality.ADEQUATE
        else:
            return EvidenceQuality.WEAK
