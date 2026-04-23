"""Provider resolution — single source of truth for (model_id → provider_key)
and (provider_key → env var).

Background
----------
Historically every probe had its own ad-hoc mapping between LiteLLM routing
strings and our logical provider keys (``openai``, ``anthropic``, ``gemini``,
``mistral``, ``cohere``, ``openrouter``, ``bedrock``, ``local_llm``).  This
module collapses those maps into one place.

Design
------
* ``provider_key_for_model(raw_id)`` first asks ``litellm.get_llm_provider``;
  if LiteLLM disagrees with our grouping (e.g. it classifies ``gemini-2.0-flash``
  as ``vertex_ai`` instead of ``gemini``), a deterministic prefix table wins.
* ``ENV_VAR_FOR_PROVIDER`` is the canonical env-var per provider group.
  Probes and availability both read from here.

The table is intentionally narrow — only providers this framework actually
enumerates.  Unknown providers default to the raw LiteLLM key.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# Canonical env var per provider group.  Bedrock has no single env var (boto3
# discovers credentials) — None here; UI treats missing ``last_error`` env-var
# hint specially.
ENV_VAR_FOR_PROVIDER: dict[str, str | None] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "bedrock": None,  # uses AWS credential chain
    "local_llm": "LOCAL_LLM_BASE_URL",
}


# LiteLLM-provider-name → our-provider-key overrides.  Applied before returning
# the litellm-native answer so we don't leak ``vertex_ai`` or ``cohere_chat``
# into the catalog.
_LITELLM_TO_GROUP: dict[str, str] = {
    "vertex_ai": "gemini",          # LiteLLM routes Gemini via Vertex
    "gemini": "gemini",
    "cohere_chat": "cohere",
    "cohere": "cohere",
    "openai": "openai",
    "anthropic": "anthropic",
    "mistral": "mistral",
    "openrouter": "openrouter",
    "bedrock": "bedrock",
}


# Prefix table used when ``litellm.get_llm_provider`` raises (e.g. on
# unprefixed ``claude-3-5-sonnet-latest`` or ``mistral-large-latest``).  Order
# matters: longer/more specific prefixes are checked first.
_PREFIX_TABLE: tuple[tuple[str, str], ...] = (
    ("openrouter/",  "openrouter"),
    ("bedrock/",     "bedrock"),
    ("anthropic/",   "anthropic"),
    ("gemini/",      "gemini"),
    ("mistral/",     "mistral"),
    ("cohere/",      "cohere"),
    ("openai/",      "openai"),
    # Bare-name prefixes (matched after slashed routes):
    ("claude",       "anthropic"),
    ("gpt",          "openai"),
    ("o1",           "openai"),
    ("o3",           "openai"),
    ("o4",           "openai"),
    ("gemini",       "gemini"),
    ("command",      "cohere"),
    ("mistral",      "mistral"),
    ("codestral",    "mistral"),
)


def provider_key_for_model(raw_id: str) -> str:
    """Return the logical provider group key for a LiteLLM routing string.

    Resolution order:
      1. ``litellm.get_llm_provider`` (native), remapped through
         ``_LITELLM_TO_GROUP`` so known aliases collapse to our group names.
      2. Prefix match on ``_PREFIX_TABLE``.
      3. Fallback: first ``/``-or-``-`` segment of the id, lowercased.

    Never raises.
    """
    if not raw_id:
        return "unknown"

    # Step 1 — ask LiteLLM
    try:
        import litellm  # imported lazily so non-worker unit tests stay fast
        _, litellm_provider, _, _ = litellm.get_llm_provider(raw_id)
        if litellm_provider:
            mapped = _LITELLM_TO_GROUP.get(litellm_provider)
            if mapped is not None:
                return mapped
            # Unknown-to-us provider but LiteLLM was confident — pass through.
            return litellm_provider
    except Exception:
        # LiteLLM couldn't classify (common for unprefixed non-openai ids).
        pass

    # Step 2 — prefix table
    for prefix, group in _PREFIX_TABLE:
        if raw_id.startswith(prefix):
            return group

    # Step 3 — fallback
    head = raw_id.split("/", 1)[0].split("-", 1)[0].lower()
    return head or "unknown"
