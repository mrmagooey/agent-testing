"""Shared utilities for scan strategies.

Provides:
- FINDING_OUTPUT_FORMAT: format instructions injected into every strategy prompt
- build_system_prompt(): injects review profile modifier
- run_agentic_loop(): standard tool-use loop (kept for verifier.py)
- deduplicate(): merges overlapping findings, returns StrategyOutput with dedup log
- ModelProviderCache: per-run cache of ModelProvider instances keyed by model_id
- filter_tools(): returns a ToolRegistry clone limited to allowed tool names

Removed in Phase 4 (replaced by runner.py / pydantic-ai):
- run_subagents()
- FindingParser
- _resolve_task_fields()

Note: run_agentic_loop() is kept because verification/verifier.py depends on it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sec_review_framework.data.findings import (
    DedupEntry,
    Finding,
    StrategyOutput,
)
from sec_review_framework.models.base import Message

if TYPE_CHECKING:
    from sec_review_framework.models.base import ModelProvider
    from sec_review_framework.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Output format constant — injected at the end of every strategy prompt
# ---------------------------------------------------------------------------

FINDING_OUTPUT_FORMAT = """
At the end of your analysis, output your findings as a JSON array in a ```json block.
Each finding must be a JSON object with these fields:
{
  "file_path": "relative/path/to/file.py",
  "line_start": 42,
  "line_end": 47,
  "vuln_class": "sqli",
  "cwe_ids": ["CWE-89"],
  "severity": "high",
  "title": "SQL Injection in user login endpoint",
  "description": "...",
  "recommendation": "...",
  "confidence": 0.9
}
Include all genuine findings. Do not include false alarms you are uncertain about.
"""


# ---------------------------------------------------------------------------
# System prompt construction (kept for external callers)
# ---------------------------------------------------------------------------

def build_system_prompt(base_prompt: str, config: dict) -> str:
    """Append the review profile's system_prompt_modifier if present.

    Args:
        base_prompt: The strategy's base system prompt.
        config: Strategy config dict; may contain a ``review_profile`` key
                whose value has a ``system_prompt_modifier`` attribute.

    Returns:
        The combined system prompt string.
    """
    profile = config.get("review_profile")
    if profile is not None and getattr(profile, "system_prompt_modifier", None):
        return f"{base_prompt}\n\n{profile.system_prompt_modifier}"
    return base_prompt


# ---------------------------------------------------------------------------
# Agentic loop (kept for verification/verifier.py)
# ---------------------------------------------------------------------------

def run_agentic_loop(
    model: ModelProvider,
    tools: ToolRegistry,
    system_prompt: str,
    initial_user_message: str,
    max_turns: int = 50,
) -> str:
    """Run a standard tool-use loop until the model returns a non-tool response.

    Sends messages to the model, handles tool calls by invoking the tool
    registry, and loops until the model returns a response with no tool calls
    or ``max_turns`` is exceeded.

    Note: Strategy execution uses the pydantic-ai runner (runner.py) since
    Phase 3. This function is retained for verification/verifier.py which runs
    a separate synchronous verification pass outside the runner context.

    Args:
        model: The model provider to call.
        tools: The tool registry used to invoke tool calls.
        system_prompt: System prompt for the model.
        initial_user_message: The first user turn.
        max_turns: Maximum number of model calls before raising RuntimeError.

    Returns:
        The final text response from the model.

    Raises:
        RuntimeError: If max_turns is exceeded without a terminal response.
    """
    messages: list[Message] = [Message(role="user", content=initial_user_message)]
    tool_defs = tools.get_tool_definitions()

    for _ in range(max_turns):
        response = model.complete(messages, tools=tool_defs, system_prompt=system_prompt)
        if not response.tool_calls:
            return response.content

        messages.append(Message(role="assistant", content=response.content))
        for call in response.tool_calls:
            result = tools.invoke(call["name"], call["input"], call["id"])
            messages.append(
                Message(role="tool", content=result, tool_call_id=call["id"])
            )

    raise RuntimeError(f"Exceeded max_turns={max_turns} in agentic loop")


# ---------------------------------------------------------------------------
# Model provider cache
# ---------------------------------------------------------------------------


class ModelProviderCache:
    """Per-run cache of ModelProvider instances keyed by model_id.

    Prevents redundant re-instantiation when multiple subagents within the
    same run use different models.

    Usage::

        cache = ModelProviderCache(factory)
        provider = cache.get("claude-opus-4-5")
    """

    def __init__(self, factory: ModelProviderFactory | None = None) -> None:
        self._factory = factory
        self._cache: dict[str, ModelProvider] = {}

    def get(self, model_id: str) -> ModelProvider:
        """Return (and cache) a ModelProvider for *model_id*.

        Raises ValueError if no factory was provided at construction time and
        the model_id is not already cached.
        """
        if model_id not in self._cache:
            if self._factory is None:
                raise ValueError(
                    f"ModelProviderCache has no factory; cannot create provider "
                    f"for model_id={model_id!r}."
                )
            self._cache[model_id] = self._factory(model_id)
        return self._cache[model_id]

    def put(self, model_id: str, provider: ModelProvider) -> None:
        """Explicitly store a pre-constructed provider (useful for testing)."""
        self._cache[model_id] = provider

    def __contains__(self, model_id: str) -> bool:
        return model_id in self._cache

    def providers(self) -> list[ModelProvider]:
        """Return all cached providers, in insertion order."""
        return list(self._cache.values())


# Type alias kept for forward compatibility
ModelProviderFactory = None  # noqa: F841 — used only in annotations above


# ---------------------------------------------------------------------------
# Tool filtering
# ---------------------------------------------------------------------------


def filter_tools(tools: ToolRegistry, allowed: frozenset[str]) -> ToolRegistry:
    """Return a clone of *tools* containing only tools whose names are in *allowed*.

    Args:
        tools: The source ToolRegistry.
        allowed: Set of tool names to retain.

    Returns:
        A new ToolRegistry with a fresh audit log and only the allowed tools.
    """
    clone = tools.clone()
    clone.tools = {name: tool for name, tool in clone.tools.items() if name in allowed}
    return clone


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate(findings: list[Finding]) -> StrategyOutput:
    """Deduplicate findings and return a StrategyOutput with tracking metadata.

    Two findings are considered duplicates when they share the same
    (file_path, vuln_class) and their line ranges overlap within 5 lines of
    each other.  When duplicates are merged, the finding with the highest
    confidence is kept.

    Args:
        findings: All findings from one or more subagents.

    Returns:
        StrategyOutput with deduplicated findings and a full dedup log.
    """
    pre_count = len(findings)
    dedup_log: list[DedupEntry] = []
    kept: list[Finding] = []

    # Group by (file_path, vuln_class)
    groups: dict[tuple[str, str], list[Finding]] = {}
    for finding in findings:
        key = (finding.file_path, finding.vuln_class)
        groups.setdefault(key, []).append(finding)

    MERGE_WINDOW = 5  # lines — findings within this distance are considered same issue

    for (file_path, vuln_class), group in groups.items():
        # Sort by line_start so we can do a single sweep
        group_sorted = sorted(
            group,
            key=lambda f: (f.line_start if f.line_start is not None else 0),
        )

        # Greedy merge: cluster findings whose line ranges are within MERGE_WINDOW
        clusters: list[list[Finding]] = []
        for finding in group_sorted:
            merged = False
            for cluster in clusters:
                # Check if this finding is within MERGE_WINDOW of any member
                for member in cluster:
                    f_start = finding.line_start if finding.line_start is not None else 0
                    m_start = member.line_start if member.line_start is not None else 0
                    f_end = finding.line_end if finding.line_end is not None else f_start
                    m_end = member.line_end if member.line_end is not None else m_start
                    # Overlap check: ranges overlap or are within MERGE_WINDOW
                    if (
                        f_start <= m_end + MERGE_WINDOW
                        and m_start <= f_end + MERGE_WINDOW
                    ):
                        cluster.append(finding)
                        merged = True
                        break
                if merged:
                    break
            if not merged:
                clusters.append([finding])

        for cluster in clusters:
            # Keep highest-confidence finding
            best = max(cluster, key=lambda f: f.confidence)
            kept.append(best)
            if len(cluster) > 1:
                merged_ids = [f.id for f in cluster if f.id != best.id]
                dedup_log.append(
                    DedupEntry(
                        kept_finding_id=best.id,
                        merged_finding_ids=merged_ids,
                        reason=(
                            f"Merged {len(cluster)} overlapping {vuln_class} findings "
                            f"in {file_path} (within {MERGE_WINDOW}-line window); "
                            f"kept highest confidence ({best.confidence:.2f})"
                        ),
                    )
                )

    return StrategyOutput(
        findings=kept,
        pre_dedup_count=pre_count,
        post_dedup_count=len(kept),
        dedup_log=dedup_log,
    )
