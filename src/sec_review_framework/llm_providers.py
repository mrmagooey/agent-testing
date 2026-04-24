"""FastAPI router for user-configurable LLM providers and app settings.

Endpoints
---------
GET  /llm-providers                  → { builtin: ProviderDTO[], custom: ProviderDTO[] }
POST /llm-providers                  → ProviderDTO  (201)
PATCH /llm-providers/{id}            → ProviderDTO
DELETE /llm-providers/{id}           → 204
POST /llm-providers/{id}/probe       → ProviderDTO
GET  /settings/defaults              → AppSettingsDTO
PATCH /settings/defaults             → AppSettingsDTO

All endpoints are mounted under /api/ by the coordinator's HTTP middleware.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator, model_validator

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# API-key scrubbing for probe error messages
# ---------------------------------------------------------------------------

# Patterns that may embed raw API keys in exception messages.
_SCRUB_PATTERNS: list[re.Pattern] = [
    re.compile(r"sk-[A-Za-z0-9_-]+", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9_.\-]+", re.IGNORECASE),
    re.compile(r"key=[^\s,]+", re.IGNORECASE),
    re.compile(r"api[_-]?key[=:]\s*[^\s,]+", re.IGNORECASE),
]

_SCRUB_MAX_LEN = 200


def _scrub_error(raw: str) -> str:
    """Remove API-key patterns from an error string and truncate."""
    scrubbed = raw
    for pattern in _SCRUB_PATTERNS:
        scrubbed = pattern.sub("[REDACTED]", scrubbed)
    return scrubbed[:_SCRUB_MAX_LEN]

router = APIRouter()

# ---------------------------------------------------------------------------
# Slug validation
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,31}$")


def _validate_slug(name: str) -> str:
    if not name or not _SLUG_RE.match(name):
        raise ValueError(
            "name must be lowercase letters, digits, and dashes; max 32 chars; "
            "must start with a letter or digit"
        )
    return name


# ---------------------------------------------------------------------------
# Pydantic request/response models
# ---------------------------------------------------------------------------


class ProviderCreateRequest(BaseModel):
    name: str
    display_name: str
    adapter: str
    model_id: str
    api_base: str | None = None
    api_key: str | None = None
    auth_type: str = "api_key"
    region: str | None = None

    @field_validator("name")
    @classmethod
    def _name_slug(cls, v: str) -> str:
        return _validate_slug(v)

    @field_validator("adapter")
    @classmethod
    def _adapter_valid(cls, v: str) -> str:
        allowed = {"openai_compat", "anthropic_compat", "bedrock", "litellm"}
        if v not in allowed:
            raise ValueError(f"adapter must be one of {sorted(allowed)}")
        return v

    @field_validator("auth_type")
    @classmethod
    def _auth_type_valid(cls, v: str) -> str:
        allowed = {"api_key", "aws", "none"}
        if v not in allowed:
            raise ValueError(f"auth_type must be one of {sorted(allowed)}")
        return v


class ProviderPatchRequest(BaseModel):
    display_name: str | None = None
    adapter: str | None = None
    model_id: str | None = None
    api_base: str | None = None
    api_key: str | None = None
    auth_type: str | None = None
    region: str | None = None
    enabled: bool | None = None

    @field_validator("adapter")
    @classmethod
    def _adapter_valid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"openai_compat", "anthropic_compat", "bedrock", "litellm"}
        if v not in allowed:
            raise ValueError(f"adapter must be one of {sorted(allowed)}")
        return v

    @field_validator("auth_type")
    @classmethod
    def _auth_type_valid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"api_key", "aws", "none"}
        if v not in allowed:
            raise ValueError(f"auth_type must be one of {sorted(allowed)}")
        return v


class AppSettingsPatchRequest(BaseModel):
    allow_unavailable_models: bool | None = None
    evidence_assessor: str | None = None
    evidence_judge_model: str | None = None

    @field_validator("evidence_assessor")
    @classmethod
    def _assessor_valid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        allowed = {"heuristic", "llm_judge"}
        if v not in allowed:
            raise ValueError(f"evidence_assessor must be one of {sorted(allowed)}")
        return v


# ---------------------------------------------------------------------------
# DTO helpers
# ---------------------------------------------------------------------------

_NETWORK_RELEVANT_FIELDS = frozenset({"adapter", "model_id", "api_base", "auth_type", "region", "api_key_ciphertext"})


def _mask_key(ciphertext: bytes | None) -> str | None:
    if not ciphertext:
        return None
    try:
        from sec_review_framework.secrets.fernet import decrypt_bytes
        plaintext = decrypt_bytes(ciphertext)
        last4 = plaintext[-4:] if len(plaintext) >= 4 else plaintext
        return "•" * 8 + last4
    except Exception:
        return "•" * 8 + "????"


def _row_to_dto(row: dict, source: str) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "display_name": row["display_name"],
        "adapter": row["adapter"],
        "model_id": row["model_id"],
        "api_base": row.get("api_base"),
        "auth_type": row["auth_type"],
        "region": row.get("region"),
        "enabled": bool(row.get("enabled", True)),
        "api_key_masked": _mask_key(row.get("api_key_ciphertext")),
        "last_probe_at": row.get("last_probe_at"),
        "last_probe_status": row.get("last_probe_status"),
        "last_probe_error": row.get("last_probe_error"),
        "source": source,
    }


def _builtin_dto(provider_key: str, snap: Any, source: str = "builtin") -> dict:
    """Build a DTO for a built-in provider from a ProviderSnapshot."""
    return {
        "id": f"builtin:{provider_key}",
        "name": provider_key,
        "display_name": provider_key.replace("_", " ").title(),
        "adapter": "litellm",
        "model_id": "",
        "api_base": None,
        "auth_type": "api_key",
        "region": None,
        "enabled": snap.probe_status not in ("disabled",),
        "api_key_masked": None,
        "last_probe_at": snap.fetched_at.isoformat() if snap.fetched_at else None,
        "last_probe_status": snap.probe_status,
        "last_probe_error": snap.last_error,
        "source": source,
    }


# ---------------------------------------------------------------------------
# Probe helper for custom providers
# ---------------------------------------------------------------------------

_PROBE_TIMEOUT_S = 15


async def _probe_custom_provider(row: dict) -> dict:
    """Run a connectivity probe against a custom provider row.

    Returns updated fields: last_probe_at, last_probe_status, last_probe_error.
    """
    now_iso = datetime.now(UTC).isoformat()
    try:
        api_key: str | None = None
        if row.get("api_key_ciphertext"):
            from sec_review_framework.secrets.fernet import decrypt_bytes
            api_key = decrypt_bytes(row["api_key_ciphertext"])

        adapter = row["adapter"]
        model_id = row["model_id"]
        api_base = row.get("api_base")

        import litellm  # lazy import

        # Map adapter → litellm custom_llm_provider
        _adapter_to_provider = {
            "openai_compat": "openai",
            "anthropic_compat": "anthropic",
            "bedrock": "bedrock",
            "litellm": None,  # let litellm auto-detect
        }
        custom_provider = _adapter_to_provider.get(adapter)

        kwargs: dict = {
            "model": model_id,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1,
        }
        if api_key:
            kwargs["api_key"] = api_key
        if api_base:
            kwargs["api_base"] = api_base
        if custom_provider:
            kwargs["custom_llm_provider"] = custom_provider
        if row.get("region") and adapter == "bedrock":
            kwargs["aws_region_name"] = row["region"]

        await asyncio.wait_for(
            asyncio.to_thread(litellm.completion, **kwargs),
            timeout=_PROBE_TIMEOUT_S,
        )
        return {
            "last_probe_at": now_iso,
            "last_probe_status": "fresh",
            "last_probe_error": None,
        }
    except Exception as exc:
        full_err = str(exc)
        logger.warning(
            "Custom provider probe failed for %s: %s",
            row.get("name"),
            full_err,
        )
        scrubbed_err = _scrub_error(full_err)
        return {
            "last_probe_at": now_iso,
            "last_probe_status": "failed",
            "last_probe_error": scrubbed_err,
        }


def _invalidate_catalog_for_custom(name: str, catalog: Any) -> None:
    """Remove custom provider's snapshot from catalog so it re-probes next cycle."""
    provider_key = f"custom:{name}"
    if catalog is not None and provider_key in catalog._snapshots:
        from sec_review_framework.models.catalog import ProviderSnapshot
        catalog._snapshots[provider_key] = ProviderSnapshot(probe_status="disabled")
        catalog.snapshot_version += 1


def _inject_custom_into_catalog(row: dict, catalog: Any) -> None:
    """Inject a single custom provider row into the catalog snapshot map."""
    if catalog is None:
        return
    from sec_review_framework.models.catalog import ModelMetadata, ProviderSnapshot

    provider_key = f"custom:{row['name']}"
    model_id = f"custom:{row['name']}/{row['model_id']}"

    probe_status = row.get("last_probe_status") or "disabled"
    fetched_at_str = row.get("last_probe_at")
    fetched_at = None
    if fetched_at_str:
        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
        except Exception:
            pass

    if probe_status not in ("fresh", "stale", "failed", "disabled"):
        probe_status = "disabled"

    catalog._snapshots[provider_key] = ProviderSnapshot(
        probe_status=probe_status,  # type: ignore[arg-type]
        model_ids=frozenset([model_id]) if probe_status in ("fresh", "stale") else frozenset(),
        metadata={
            model_id: ModelMetadata(
                id=model_id,
                display_name=row["display_name"],
                provider_key=provider_key,
                raw_id=row["model_id"],
                region=row.get("region"),
            )
        } if probe_status in ("fresh", "stale") else {},
        fetched_at=fetched_at,
        last_error=row.get("last_probe_error"),
    )
    catalog.snapshot_version += 1


# ---------------------------------------------------------------------------
# Dependency injection helpers — coordinator/db pulled from coordinator module
# ---------------------------------------------------------------------------

def _get_coordinator():
    from sec_review_framework.coordinator import coordinator as _coord
    return _coord


def _get_db():
    return _get_coordinator().db


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/llm-providers")
async def list_llm_providers() -> dict:
    """Return builtin and custom provider lists."""
    db = _get_db()
    coord = _get_coordinator()

    # Built-ins from catalog
    snapshots = coord.catalog.snapshot() if coord.catalog is not None else {}
    builtins = [
        _builtin_dto(key, snap)
        for key, snap in snapshots.items()
        if not key.startswith("custom:")
    ]

    # Custom from DB
    rows = await db.list_llm_providers()
    customs = [_row_to_dto(r, "custom") for r in rows]

    return {"builtin": builtins, "custom": customs}


@router.post("/llm-providers", status_code=201)
async def create_llm_provider(body: ProviderCreateRequest) -> dict:
    """Create a new custom LLM provider."""
    db = _get_db()
    coord = _get_coordinator()

    # Unique-name check
    existing = await db.get_llm_provider_by_name(body.name)
    if existing:
        raise HTTPException(status_code=409, detail=f"Provider name '{body.name}' already exists")

    now_iso = datetime.now(UTC).isoformat()
    provider_id = str(uuid.uuid4())

    api_key_ciphertext: bytes | None = None
    if body.api_key:
        from sec_review_framework.secrets.fernet import encrypt_str
        api_key_ciphertext = encrypt_str(body.api_key)

    row: dict = {
        "id": provider_id,
        "name": body.name,
        "display_name": body.display_name,
        "adapter": body.adapter,
        "model_id": body.model_id,
        "api_base": body.api_base,
        "api_key_ciphertext": api_key_ciphertext,
        "auth_type": body.auth_type,
        "region": body.region,
        "enabled": 1,
        "last_probe_at": None,
        "last_probe_status": None,
        "last_probe_error": None,
        "created_at": now_iso,
        "updated_at": now_iso,
    }

    await db.create_llm_provider(row)

    # Run probe asynchronously and update row
    probe_result = await _probe_custom_provider(row)
    await db.update_llm_provider(provider_id, {**probe_result, "updated_at": datetime.now(UTC).isoformat()})
    row.update(probe_result)

    # Inject into catalog
    _inject_custom_into_catalog(row, coord.catalog)

    fresh_row = await db.get_llm_provider(provider_id)
    return _row_to_dto(fresh_row, "custom")


@router.patch("/llm-providers/{provider_id}")
async def patch_llm_provider(provider_id: str, body: ProviderPatchRequest) -> dict:
    """Partial update of a custom provider."""
    db = _get_db()
    coord = _get_coordinator()

    existing = await db.get_llm_provider(provider_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Provider not found")

    update_fields: dict = {"updated_at": datetime.now(UTC).isoformat()}
    network_changed = False

    if body.display_name is not None:
        update_fields["display_name"] = body.display_name
    if body.adapter is not None:
        update_fields["adapter"] = body.adapter
        network_changed = True
    if body.model_id is not None:
        update_fields["model_id"] = body.model_id
        network_changed = True
    if body.api_base is not None:
        update_fields["api_base"] = body.api_base
        network_changed = True
    if body.auth_type is not None:
        update_fields["auth_type"] = body.auth_type
        network_changed = True
    if body.region is not None:
        update_fields["region"] = body.region
        network_changed = True
    if body.enabled is not None:
        update_fields["enabled"] = int(body.enabled)
    if body.api_key is not None:
        from sec_review_framework.secrets.fernet import encrypt_str
        update_fields["api_key_ciphertext"] = encrypt_str(body.api_key)
        network_changed = True

    await db.update_llm_provider(provider_id, update_fields)

    if network_changed:
        merged = {**existing, **update_fields}
        probe_result = await _probe_custom_provider(merged)
        await db.update_llm_provider(provider_id, {**probe_result, "updated_at": datetime.now(UTC).isoformat()})
        _invalidate_catalog_for_custom(existing["name"], coord.catalog)
        fresh_row = await db.get_llm_provider(provider_id)
        _inject_custom_into_catalog(fresh_row, coord.catalog)
    else:
        fresh_row = await db.get_llm_provider(provider_id)
        _inject_custom_into_catalog(fresh_row, coord.catalog)

    return _row_to_dto(fresh_row, "custom")


@router.delete("/llm-providers/{provider_id}", status_code=204)
async def delete_llm_provider(provider_id: str) -> None:
    """Hard delete a custom provider."""
    db = _get_db()
    coord = _get_coordinator()

    existing = await db.get_llm_provider(provider_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Provider not found")

    await db.delete_llm_provider(provider_id)

    # Remove from catalog
    _invalidate_catalog_for_custom(existing["name"], coord.catalog)


@router.post("/llm-providers/{provider_id}/probe")
async def probe_llm_provider(provider_id: str) -> dict:
    """Force a fresh probe of a custom provider."""
    db = _get_db()
    coord = _get_coordinator()

    row = await db.get_llm_provider(provider_id)
    if not row:
        raise HTTPException(status_code=404, detail="Provider not found")

    probe_result = await _probe_custom_provider(row)
    await db.update_llm_provider(provider_id, {**probe_result, "updated_at": datetime.now(UTC).isoformat()})

    fresh_row = await db.get_llm_provider(provider_id)
    _inject_custom_into_catalog(fresh_row, coord.catalog)

    return _row_to_dto(fresh_row, "custom")


@router.get("/settings/defaults")
async def get_app_settings() -> dict:
    """Return current app settings."""
    db = _get_db()
    return await db.get_app_settings()


@router.patch("/settings/defaults")
async def patch_app_settings(body: AppSettingsPatchRequest) -> dict:
    """Partial update of app settings."""
    db = _get_db()
    fields: dict = {}
    if body.allow_unavailable_models is not None:
        fields["allow_unavailable_models"] = body.allow_unavailable_models
    if body.evidence_assessor is not None:
        fields["evidence_assessor"] = body.evidence_assessor
    if body.evidence_judge_model is not None:
        fields["evidence_judge_model"] = body.evidence_judge_model
    return await db.update_app_settings(fields)
