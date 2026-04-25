"""Classification data models for the classifier_dispatch strategy.

Used by ``builtin.classifier_dispatch`` and its ``builtin.classifier`` subagent.
The classifier emits a list of :class:`ClassifierJudgement` objects — one per
file/code region — that the parent uses to decide which specialist subagents to
dispatch.
"""

from __future__ import annotations

from pydantic import BaseModel

from sec_review_framework.data.findings import VulnClass


class ClassifierJudgement(BaseModel):
    """A single classification judgement from the classifier subagent.

    Attributes
    ----------
    file_path:
        Path to the file or region that was classified.
    vuln_class:
        The predicted vulnerability class for this file/region.
    confidence:
        Classifier confidence in [0.0, 1.0].  Values below 0.5 are typically
        treated as low-confidence and may be skipped by the parent.
    """

    file_path: str
    vuln_class: VulnClass
    confidence: float
