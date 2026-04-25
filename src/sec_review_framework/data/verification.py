"""Verification data models for the verifier_wrapping strategy.

Used by ``builtin.single_agent_with_verifier`` to represent the verdict from
the ``builtin.verifier`` subagent for each candidate finding.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class FileLine(BaseModel):
    """A reference to a specific file and line range."""

    file_path: str
    line_start: int | None = None
    line_end: int | None = None


class VerifierVerdict(BaseModel):
    """Structured output from the verifier subagent.

    Attributes
    ----------
    status:
        - ``"confirmed"`` — the finding is real and exploitable.
        - ``"refuted"`` — no actual vulnerability at this location.
        - ``"inconclusive"`` — insufficient context to decide.
    evidence:
        Short prose explanation of the verdict.
    citation:
        Optional file/line reference supporting the verdict.
    """

    status: Literal["confirmed", "refuted", "inconclusive"]
    evidence: str
    citation: FileLine | None = None
