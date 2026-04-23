"""Legacy model-id aliases.

Old short opaque ids like ``bedrock-claude-3-5-sonnet`` and
``openrouter-llama-3.1-8b`` map to the full LiteLLM routing string
(``bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0``,
``openrouter/meta-llama/llama-3.1-8b-instruct``) that the probe-driven
catalog uses as the stable id.

The mapping lets rows persisted in the database (and references in older
``experiments.yaml`` files) continue to resolve. ``rewrite_legacy_id()`` is
the single entry point, and emits a one-shot deprecation warning per id
per process so operators can find and update stale references without log
flooding.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# old_id → new_id.  Keep alphabetical so diffs stay readable.
LEGACY_ID_ALIASES: dict[str, str] = {
    # Bedrock
    "bedrock-claude-3-5-haiku":  "bedrock/anthropic.claude-3-5-haiku-20241022-v1:0",
    "bedrock-claude-3-5-sonnet": "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
    "bedrock-llama-3-1-70b":     "bedrock/meta.llama3-1-70b-instruct-v1:0",
    "bedrock-nova-lite":         "bedrock/amazon.nova-lite-v1:0",
    "bedrock-nova-pro":          "bedrock/amazon.nova-pro-v1:0",
    # OpenRouter
    "openrouter-llama-3.1-8b":   "openrouter/meta-llama/llama-3.1-8b-instruct",
    "openrouter-llama-3.2-3b":   "openrouter/meta-llama/llama-3.2-3b-instruct",
}


# Track which aliases we have already warned about in this process so the log
# does not flood when the same legacy id shows up across many DB rows.
_warned: set[str] = set()


def rewrite_legacy_id(model_id: str) -> str:
    """Return the canonical id for ``model_id``, or ``model_id`` unchanged.

    Emits a deprecation warning exactly once per legacy id per process.
    Safe to call in hot paths (submit-time validation, enrichment, UI list).
    """
    new_id = LEGACY_ID_ALIASES.get(model_id)
    if new_id is None:
        return model_id
    if model_id not in _warned:
        _warned.add(model_id)
        logger.warning(
            "Legacy model id %r is deprecated; use %r. "
            "Update experiments.yaml / DB rows to silence this warning.",
            model_id,
            new_id,
        )
    return new_id


def rewrite_legacy_ids(model_ids: list[str]) -> list[str]:
    """Vectorised form of ``rewrite_legacy_id`` that preserves order + dupes."""
    return [rewrite_legacy_id(m) for m in model_ids]


def _reset_warnings_for_tests() -> None:
    """Clear the process-local warning set.  Test-only helper."""
    _warned.clear()
