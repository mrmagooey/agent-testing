"""Tests for sec_review_framework.secrets.fernet."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

import sec_review_framework.secrets.fernet as fernet_mod

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KEY_A = "-TzdFD_37R4K4X08iwJ9vXs08da9GngSAIdgJ2zRVnI="
_KEY_B = "5675kij6IGS0vPDbuZj_Ax-Hyg1w3WJqh5fvbRHRt40="
_KEY_C = "kHrtxOS0HfaVQCucltWktNu3BuLiToprwjjfPlO9Nsk="


def _make_multi(*keys: str) -> MultiFernet:
    return MultiFernet([Fernet(k.encode()) for k in keys])


# ---------------------------------------------------------------------------
# Round-trip: encrypt -> decrypt restores original plaintext
# ---------------------------------------------------------------------------


def test_round_trip_simple(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    ct = fernet_mod.encrypt_str("hello world")
    assert fernet_mod.decrypt_bytes(ct) == "hello world"


def test_round_trip_empty_plaintext(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    ct = fernet_mod.encrypt_str("")
    assert fernet_mod.decrypt_bytes(ct) == ""


def test_round_trip_non_ascii(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    payload = "日本語テスト non-ascii caf\xe9"
    ct = fernet_mod.encrypt_str(payload)
    assert fernet_mod.decrypt_bytes(ct) == payload


def test_round_trip_large_plaintext(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    payload = "x" * 100_000
    ct = fernet_mod.encrypt_str(payload)
    assert fernet_mod.decrypt_bytes(ct) == payload


def test_encrypt_returns_bytes(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    ct = fernet_mod.encrypt_str("any")
    assert isinstance(ct, bytes)


def test_decrypt_returns_str(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    ct = fernet_mod.encrypt_str("any")
    result = fernet_mod.decrypt_bytes(ct)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Non-determinism: same plaintext yields different ciphertext each time
# ---------------------------------------------------------------------------


def test_encrypt_nondeterministic(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    ct1 = fernet_mod.encrypt_str("same message")
    ct2 = fernet_mod.encrypt_str("same message")
    assert ct1 != ct2


# ---------------------------------------------------------------------------
# Key derivation: _load_fernet is deterministic given the same env var value
# ---------------------------------------------------------------------------


def test_load_fernet_deterministic_key_derivation(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ENCRYPTION_KEY", _KEY_A)
    f1 = fernet_mod._load_fernet()
    f2 = fernet_mod._load_fernet()
    payload = b"test payload"
    ct = f1.encrypt(payload)
    assert f2.decrypt(ct) == payload


def test_load_fernet_multiple_keys_first_is_write_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ENCRYPTION_KEY", f"{_KEY_A},{_KEY_B}")
    f = fernet_mod._load_fernet()
    ct = f.encrypt(b"data")
    assert Fernet(_KEY_A.encode()).decrypt(ct) == b"data"


# ---------------------------------------------------------------------------
# Rotated keys: encrypt with A, decrypt succeeds with [A] or [A,B], fails with [B]
# ---------------------------------------------------------------------------


def test_rotated_keys_encrypt_A_decrypt_with_A_succeeds(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    ct = fernet_mod.encrypt_str("secret")
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    assert fernet_mod.decrypt_bytes(ct) == "secret"


def test_rotated_keys_encrypt_A_decrypt_with_A_and_B_succeeds(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    ct = fernet_mod.encrypt_str("secret")
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A, _KEY_B))
    assert fernet_mod.decrypt_bytes(ct) == "secret"


def test_rotated_keys_encrypt_A_decrypt_with_B_only_fails(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    ct = fernet_mod.encrypt_str("secret")
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_B))
    with pytest.raises(InvalidToken):
        fernet_mod.decrypt_bytes(ct)


def test_rotated_keys_encrypt_B_decrypt_with_A_only_fails(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_B))
    ct = fernet_mod.encrypt_str("secret")
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    with pytest.raises(InvalidToken):
        fernet_mod.decrypt_bytes(ct)


def test_rotated_keys_old_ciphertext_decryptable_after_rotation(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    old_ct = fernet_mod.encrypt_str("old secret")
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_B, _KEY_A))
    assert fernet_mod.decrypt_bytes(old_ct) == "old secret"


def test_rotated_keys_new_ciphertext_not_decryptable_with_old_key_only(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_B, _KEY_A))
    new_ct = fernet_mod.encrypt_str("new secret")
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    with pytest.raises(InvalidToken):
        fernet_mod.decrypt_bytes(new_ct)


# ---------------------------------------------------------------------------
# Error paths: bad ciphertext, malformed input
# ---------------------------------------------------------------------------


def test_decrypt_bad_ciphertext_raises_invalid_token(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    with pytest.raises(InvalidToken):
        fernet_mod.decrypt_bytes(b"not-a-fernet-token")


def test_decrypt_empty_bytes_raises_invalid_token(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    with pytest.raises(InvalidToken):
        fernet_mod.decrypt_bytes(b"")


def test_decrypt_truncated_ciphertext_raises_invalid_token(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    ct = fernet_mod.encrypt_str("truncation test")
    with pytest.raises(InvalidToken):
        fernet_mod.decrypt_bytes(ct[:20])


def test_decrypt_bit_flipped_ciphertext_raises_invalid_token(monkeypatch):
    monkeypatch.setattr(fernet_mod, "_fernet", _make_multi(_KEY_A))
    ct = bytearray(fernet_mod.encrypt_str("flip test"))
    ct[-1] ^= 0xFF
    with pytest.raises(InvalidToken):
        fernet_mod.decrypt_bytes(bytes(ct))


# ---------------------------------------------------------------------------
# Error paths: missing or malformed key material at load time
# ---------------------------------------------------------------------------


def test_load_fernet_missing_env_var_raises_runtime_error(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="LLM_PROVIDER_ENCRYPTION_KEY"):
        fernet_mod._load_fernet()


def test_load_fernet_empty_env_var_raises_runtime_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ENCRYPTION_KEY", "")
    with pytest.raises(RuntimeError, match="LLM_PROVIDER_ENCRYPTION_KEY"):
        fernet_mod._load_fernet()


def test_load_fernet_whitespace_only_env_var_raises_runtime_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ENCRYPTION_KEY", "   ")
    with pytest.raises(RuntimeError, match="LLM_PROVIDER_ENCRYPTION_KEY"):
        fernet_mod._load_fernet()


def test_load_fernet_malformed_key_raises_runtime_error(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ENCRYPTION_KEY", "not-a-valid-fernet-key")
    with pytest.raises(RuntimeError, match="malformed"):
        fernet_mod._load_fernet()


def test_load_fernet_second_key_malformed_raises_runtime_error(monkeypatch):
    monkeypatch.setenv(
        "LLM_PROVIDER_ENCRYPTION_KEY", f"{_KEY_A},not-valid"
    )
    with pytest.raises(RuntimeError, match="position 1"):
        fernet_mod._load_fernet()


def test_load_fernet_valid_single_key_succeeds(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ENCRYPTION_KEY", _KEY_A)
    f = fernet_mod._load_fernet()
    assert isinstance(f, MultiFernet)


def test_load_fernet_valid_multi_key_succeeds(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ENCRYPTION_KEY", f"{_KEY_A},{_KEY_B},{_KEY_C}")
    f = fernet_mod._load_fernet()
    assert isinstance(f, MultiFernet)


def test_load_fernet_keys_trimmed_of_whitespace(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER_ENCRYPTION_KEY", f"  {_KEY_A}  ,  {_KEY_B}  ")
    f = fernet_mod._load_fernet()
    ct = f.encrypt(b"trimmed")
    assert Fernet(_KEY_A.encode()).decrypt(ct) == b"trimmed"


# ---------------------------------------------------------------------------
# InvalidToken is re-exported from the module
# ---------------------------------------------------------------------------


def test_invalid_token_exported():
    from cryptography.fernet import InvalidToken as CryptoInvalidToken

    from sec_review_framework.secrets.fernet import InvalidToken as ModuleInvalidToken
    assert ModuleInvalidToken is CryptoInvalidToken
