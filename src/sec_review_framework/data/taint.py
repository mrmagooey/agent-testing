"""Taint-analysis data models for the taint_pipeline strategy.

Used by ``builtin.taint_pipeline`` and its three subagents:
- ``builtin.source_finder``       — emits :class:`Source` objects.
- ``builtin.sink_tracer``         — emits :class:`TaintPath` objects (one per source→sink pair).
- ``builtin.sanitization_checker``— emits :class:`SanitizationVerdict` objects (one per path).
"""

from __future__ import annotations

from pydantic import BaseModel


class Source(BaseModel):
    """A taint source identified by the source-finder subagent.

    Attributes
    ----------
    file_path:
        Path to the file containing the taint source.
    line:
        Line number of the source expression.
    kind:
        Short label for the source type (e.g. ``"user_input"``, ``"env_var"``,
        ``"file_read"``).
    description:
        Human-readable description of what the source is.
    """

    file_path: str
    line: int | None = None
    kind: str
    description: str


class TaintPath(BaseModel):
    """A taint propagation path from a source to a sink.

    Attributes
    ----------
    source:
        The originating taint source.
    sink_file:
        File containing the sink (dangerous operation).
    sink_line:
        Line number of the sink.
    sink_kind:
        Short label for the sink type (e.g. ``"sql_query"``, ``"exec_call"``).
    hops:
        Ordered list of intermediate function/variable names the taint passes
        through between source and sink.
    description:
        Human-readable description of how taint flows from source to sink.
    """

    source: Source
    sink_file: str
    sink_line: int | None = None
    sink_kind: str
    hops: list[str] = []
    description: str


class SanitizationVerdict(BaseModel):
    """Verdict from the sanitization-checker subagent for one taint path.

    Attributes
    ----------
    path:
        The taint path that was checked.
    sanitized:
        ``True`` if the taint is neutralised before reaching the sink.
    justification:
        Short explanation of why the path is or is not sanitized.
    """

    path: TaintPath
    sanitized: bool
    justification: str
