"""Unit tests for the LLM providers feature.

Covers:
 - Fernet encrypt/decrypt roundtrip
 - Fernet key rotation scenario
 - Slug validation
 - Unique-name constraint (409)
 - AppSettings DB helpers
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fernet unit tests
# ---------------------------------------------------------------------------

def _fernet_module(keys: list[str]):
    """Return a fresh fernet module loaded with the given key list."""
    key_str = ",".join(keys)
    # Temporarily set env var, reload module
    env_backup = os.environ.get("LLM_PROVIDER_ENCRYPTION_KEY")
    os.environ["LLM_PROVIDER_ENCRYPTION_KEY"] = key_str
    # Remove cached module so _load_fernet() runs fresh
    mod_name = "sec_review_framework.secrets.fernet"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    try:
        mod = importlib.import_module(mod_name)
    finally:
        if env_backup is None:
            os.environ.pop("LLM_PROVIDER_ENCRYPTION_KEY", None)
        else:
            os.environ["LLM_PROVIDER_ENCRYPTION_KEY"] = env_backup
    return mod


def _generate_fernet_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def test_fernet_roundtrip():
    """Encrypting and then decrypting a string returns the original."""
    key = _generate_fernet_key()
    mod = _fernet_module([key])
    plaintext = "sk-super-secret-key-12345"
    ciphertext = mod.encrypt_str(plaintext)
    assert isinstance(ciphertext, bytes)
    assert ciphertext != plaintext.encode()
    assert mod.decrypt_bytes(ciphertext) == plaintext


def test_fernet_ciphertext_differs_from_plaintext():
    """The ciphertext bytes must not equal the UTF-8 plaintext."""
    key = _generate_fernet_key()
    mod = _fernet_module([key])
    plaintext = "my-api-key"
    ciphertext = mod.encrypt_str(plaintext)
    assert ciphertext != plaintext.encode("utf-8")


def test_fernet_key_rotation():
    """Ciphertext encrypted with old_key can be decrypted when both keys are present."""
    old_key = _generate_fernet_key()
    new_key = _generate_fernet_key()

    # Encrypt with old key only
    old_mod = _fernet_module([old_key])
    ciphertext = old_mod.encrypt_str("rotate-me")

    # Decrypt with new_key + old_key (rotation scenario)
    new_mod = _fernet_module([new_key, old_key])
    assert new_mod.decrypt_bytes(ciphertext) == "rotate-me"


def test_fernet_wrong_key_raises():
    """Decrypting with wrong key raises InvalidToken."""
    from cryptography.fernet import InvalidToken

    key1 = _generate_fernet_key()
    key2 = _generate_fernet_key()
    mod1 = _fernet_module([key1])
    mod2 = _fernet_module([key2])
    ciphertext = mod1.encrypt_str("secret")
    with pytest.raises(InvalidToken):
        mod2.decrypt_bytes(ciphertext)


def test_fernet_missing_env_raises():
    """Module fails fast when env var is missing."""
    backup = os.environ.pop("LLM_PROVIDER_ENCRYPTION_KEY", None)
    mod_name = "sec_review_framework.secrets.fernet"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    try:
        with pytest.raises(RuntimeError, match="LLM_PROVIDER_ENCRYPTION_KEY"):
            importlib.import_module(mod_name)
    finally:
        if backup is not None:
            os.environ["LLM_PROVIDER_ENCRYPTION_KEY"] = backup
        if mod_name in sys.modules:
            del sys.modules[mod_name]


def test_fernet_malformed_key_raises():
    """Module fails fast when a key is malformed base64."""
    backup = os.environ.get("LLM_PROVIDER_ENCRYPTION_KEY")
    os.environ["LLM_PROVIDER_ENCRYPTION_KEY"] = "not-a-valid-fernet-key"
    mod_name = "sec_review_framework.secrets.fernet"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    try:
        with pytest.raises(RuntimeError, match="malformed"):
            importlib.import_module(mod_name)
    finally:
        if backup is None:
            os.environ.pop("LLM_PROVIDER_ENCRYPTION_KEY", None)
        else:
            os.environ["LLM_PROVIDER_ENCRYPTION_KEY"] = backup
        if mod_name in sys.modules:
            del sys.modules[mod_name]


# ---------------------------------------------------------------------------
# Slug validation unit tests
# ---------------------------------------------------------------------------

def test_slug_valid_names():
    from sec_review_framework.llm_providers import _validate_slug
    assert _validate_slug("openai-staging") == "openai-staging"
    assert _validate_slug("my-provider-1") == "my-provider-1"
    assert _validate_slug("a") == "a"
    assert _validate_slug("a" * 32) == "a" * 32


def test_slug_rejects_uppercase():
    from sec_review_framework.llm_providers import _validate_slug
    with pytest.raises(ValueError):
        _validate_slug("Bad-Name")


def test_slug_rejects_spaces():
    from sec_review_framework.llm_providers import _validate_slug
    with pytest.raises(ValueError):
        _validate_slug("Bad Name")


def test_slug_rejects_empty():
    from sec_review_framework.llm_providers import _validate_slug
    with pytest.raises(ValueError):
        _validate_slug("")


def test_slug_rejects_too_long():
    from sec_review_framework.llm_providers import _validate_slug
    with pytest.raises(ValueError):
        _validate_slug("a" * 33)


def test_slug_rejects_leading_dash():
    from sec_review_framework.llm_providers import _validate_slug
    with pytest.raises(ValueError):
        _validate_slug("-bad")


# ---------------------------------------------------------------------------
# DB layer unit tests (async)
# ---------------------------------------------------------------------------

@pytest.fixture()
async def db_with_key(tmp_path: Path):
    """Database initialised with Fernet key set in env."""
    key = _generate_fernet_key()
    os.environ["LLM_PROVIDER_ENCRYPTION_KEY"] = key

    from sec_review_framework.db import Database
    db = Database(tmp_path / "test.db")
    await db.init()
    yield db, key

    os.environ.pop("LLM_PROVIDER_ENCRYPTION_KEY", None)
    mod_name = "sec_review_framework.secrets.fernet"
    if mod_name in sys.modules:
        del sys.modules[mod_name]


async def test_db_llm_provider_crud(db_with_key):
    db, _ = db_with_key
    from datetime import UTC, datetime

    row = {
        "id": "test-id-001",
        "name": "my-provider",
        "display_name": "My Provider",
        "adapter": "openai_compat",
        "model_id": "gpt-4-test",
        "api_base": "https://api.example.com",
        "api_key_ciphertext": None,
        "auth_type": "api_key",
        "region": None,
        "enabled": 1,
        "last_probe_at": None,
        "last_probe_status": None,
        "last_probe_error": None,
        "created_at": datetime.now(UTC).isoformat(),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await db.create_llm_provider(row)

    fetched = await db.get_llm_provider("test-id-001")
    assert fetched is not None
    assert fetched["name"] == "my-provider"

    by_name = await db.get_llm_provider_by_name("my-provider")
    assert by_name is not None

    rows = await db.list_llm_providers()
    assert len(rows) == 1

    await db.update_llm_provider("test-id-001", {"display_name": "Updated Name"})
    updated = await db.get_llm_provider("test-id-001")
    assert updated["display_name"] == "Updated Name"

    deleted = await db.delete_llm_provider("test-id-001")
    assert deleted is True
    assert await db.get_llm_provider("test-id-001") is None


async def test_db_llm_provider_unique_name_constraint(db_with_key):
    """Inserting two rows with the same name raises a DB error."""
    import aiosqlite

    db, _ = db_with_key
    from datetime import UTC, datetime

    def _row(suffix: str) -> dict:
        return {
            "id": f"id-{suffix}",
            "name": "same-name",
            "display_name": "Dupe",
            "adapter": "litellm",
            "model_id": "m",
            "api_base": None,
            "api_key_ciphertext": None,
            "auth_type": "none",
            "region": None,
            "enabled": 1,
            "last_probe_at": None,
            "last_probe_status": None,
            "last_probe_error": None,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }

    await db.create_llm_provider(_row("1"))
    with pytest.raises(aiosqlite.IntegrityError):
        await db.create_llm_provider(_row("2"))


async def test_db_app_settings_defaults(db_with_key):
    db, _ = db_with_key
    settings = await db.get_app_settings()
    assert settings["evidence_assessor"] == "heuristic"
    assert settings["allow_unavailable_models"] is False
    assert settings["evidence_judge_model"] is None


async def test_db_app_settings_patch(db_with_key):
    db, _ = db_with_key
    updated = await db.update_app_settings({
        "allow_unavailable_models": True,
        "evidence_assessor": "llm_judge",
        "evidence_judge_model": "gpt-4o",
    })
    assert updated["allow_unavailable_models"] is True
    assert updated["evidence_assessor"] == "llm_judge"
    assert updated["evidence_judge_model"] == "gpt-4o"

    # Verify persistence
    fetched = await db.get_app_settings()
    assert fetched["allow_unavailable_models"] is True


# ---------------------------------------------------------------------------
# Coordinator startup — fernet must be loaded eagerly (finding #2)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Probe error scrubbing (finding #4)
# ---------------------------------------------------------------------------

def test_scrub_error_removes_sk_key():
    """_scrub_error must remove sk-… style API keys."""
    from sec_review_framework.llm_providers import _scrub_error
    raw = "AuthenticationError: Invalid key sk-abc123verylongkey for model gpt-4"
    result = _scrub_error(raw)
    assert "abc123verylongkey" not in result
    assert "[REDACTED]" in result


def test_scrub_error_removes_bearer():
    from sec_review_framework.llm_providers import _scrub_error
    raw = "Request failed: Authorization: Bearer mySecretToken123"
    result = _scrub_error(raw)
    assert "mySecretToken123" not in result
    assert "[REDACTED]" in result


def test_scrub_error_removes_key_equals():
    from sec_review_framework.llm_providers import _scrub_error
    raw = "Request failed: key=super_secret_value, status=401"
    result = _scrub_error(raw)
    assert "super_secret_value" not in result


def test_scrub_error_removes_api_key_colon():
    from sec_review_framework.llm_providers import _scrub_error
    raw = "Error: api_key: sk-proj-mysecretapikey123"
    result = _scrub_error(raw)
    assert "mysecretapikey123" not in result


def test_scrub_error_truncates_to_200_chars():
    from sec_review_framework.llm_providers import _scrub_error
    raw = "x" * 500
    result = _scrub_error(raw)
    assert len(result) <= 200


def test_scrub_error_preserves_non_secret_text():
    from sec_review_framework.llm_providers import _scrub_error
    raw = "Connection timed out after 15s"
    result = _scrub_error(raw)
    assert "Connection timed out" in result


def test_fernet_imported_eagerly_in_lifespan():
    """Importing the fernet module with a missing env var must raise RuntimeError.

    The coordinator's lifespan handler imports fernet unconditionally so that
    a missing LLM_PROVIDER_ENCRYPTION_KEY causes a startup failure rather than
    a runtime crash on the first DB write.
    """
    backup = os.environ.pop("LLM_PROVIDER_ENCRYPTION_KEY", None)
    mod_name = "sec_review_framework.secrets.fernet"
    if mod_name in sys.modules:
        del sys.modules[mod_name]
    try:
        with pytest.raises(RuntimeError, match="LLM_PROVIDER_ENCRYPTION_KEY"):
            importlib.import_module(mod_name)
    finally:
        if backup is not None:
            os.environ["LLM_PROVIDER_ENCRYPTION_KEY"] = backup
        if mod_name in sys.modules:
            del sys.modules[mod_name]
