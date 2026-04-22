"""Unit tests for ProviderCatalog."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from sec_review_framework.models.catalog import (
    ModelMetadata,
    ProviderCatalog,
    ProviderSnapshot,
)


# ---------------------------------------------------------------------------
# Stub probes
# ---------------------------------------------------------------------------


def _fresh_snapshot(provider_key: str) -> ProviderSnapshot:
    return ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset([f"{provider_key}/model-1"]),
        metadata={f"{provider_key}/model-1": ModelMetadata(id=f"{provider_key}/model-1")},
        fetched_at=datetime.now(timezone.utc),
    )


class _OkProbe:
    def __init__(self, provider_key: str) -> None:
        self.provider_key = provider_key
        self.call_count = 0

    async def probe(self) -> ProviderSnapshot:
        self.call_count += 1
        return _fresh_snapshot(self.provider_key)


class _FailingProbe:
    def __init__(self, provider_key: str) -> None:
        self.provider_key = provider_key
        self.call_count = 0

    async def probe(self) -> ProviderSnapshot:
        self.call_count += 1
        raise RuntimeError("upstream error")


class _DisabledProbe:
    def __init__(self, provider_key: str) -> None:
        self.provider_key = provider_key
        self.call_count = 0

    async def probe(self) -> ProviderSnapshot:
        self.call_count += 1
        return ProviderSnapshot(probe_status="disabled")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_cold_start_all_fresh():
    """After start(), every probe key has a fresh snapshot."""
    probes = [_OkProbe("openai"), _OkProbe("anthropic")]
    catalog = ProviderCatalog(probes=probes, ttl_seconds=60, probe_enabled=True)
    await catalog.start()
    try:
        snap = catalog.snapshot()
        assert set(snap.keys()) == {"openai", "anthropic"}
        for key, s in snap.items():
            assert s.probe_status == "fresh", f"{key} not fresh"
            assert len(s.model_ids) > 0
    finally:
        await catalog.stop()


async def test_snapshot_returns_all_probe_keys():
    """snapshot() has an entry for every probe even before start()."""
    probes = [_OkProbe("openai"), _OkProbe("openrouter")]
    catalog = ProviderCatalog(probes=probes, ttl_seconds=600, probe_enabled=True)
    # Before start, still accessible — status is disabled (initial).
    snap = catalog.snapshot()
    assert set(snap.keys()) == {"openai", "openrouter"}


async def test_ttl_expiry_triggers_refresh():
    """With a very short TTL, the background task re-probes."""
    probe = _OkProbe("openai")
    catalog = ProviderCatalog(probes=[probe], ttl_seconds=1, probe_enabled=True)
    await catalog.start()
    initial_count = probe.call_count
    # Wait for at least one refresh cycle (TTL=1s).
    await asyncio.sleep(1.2)
    await catalog.stop()
    assert probe.call_count > initial_count, "Expected at least one refresh"


async def test_probe_raises_cold_gives_failed():
    """A probe that raises on first call → failed status."""
    probe = _FailingProbe("openai")
    catalog = ProviderCatalog(probes=[probe], ttl_seconds=60, probe_enabled=True)
    await catalog.start()
    try:
        snap = catalog.snapshot()
        s = snap["openai"]
        assert s.probe_status == "failed"
        assert s.last_error is not None
        assert len(s.model_ids) == 0
    finally:
        await catalog.stop()


async def test_probe_raises_after_success_gives_stale():
    """A probe that succeeds once then raises → stale status, prior data kept."""

    class _FlipProbe:
        provider_key = "openai"
        _calls = 0

        async def probe(self) -> ProviderSnapshot:
            self._calls += 1
            if self._calls == 1:
                return _fresh_snapshot("openai")
            raise RuntimeError("transient error")

    probe = _FlipProbe()
    catalog = ProviderCatalog(probes=[probe], ttl_seconds=1, probe_enabled=True)
    await catalog.start()
    # Wait for refresh cycle.
    await asyncio.sleep(1.3)
    await catalog.stop()

    snap = catalog.snapshot()
    s = snap["openai"]
    assert s.probe_status == "stale"
    assert "openai/model-1" in s.model_ids
    assert s.last_error is not None


async def test_probe_enabled_false_all_disabled():
    """probe_enabled=False → all snapshots are disabled, probe never called."""
    probes = [_OkProbe("openai"), _OkProbe("openrouter")]
    catalog = ProviderCatalog(probes=probes, ttl_seconds=60, probe_enabled=False)
    await catalog.start()
    snap = catalog.snapshot()
    for key, s in snap.items():
        assert s.probe_status == "disabled", f"{key} should be disabled"
    for p in probes:
        assert p.call_count == 0, f"{p.provider_key} probe should not have been called"
    await catalog.stop()


async def test_stop_cancels_cleanly():
    """stop() resolves without pending-task warnings."""
    probe = _OkProbe("openai")
    catalog = ProviderCatalog(probes=[probe], ttl_seconds=600, probe_enabled=True)
    await catalog.start()
    # Should complete without raising.
    await catalog.stop()
    # Second stop is a no-op.
    await catalog.stop()


async def test_snapshot_is_shallow_copy():
    """snapshot() returns a new dict each time but references same snapshots."""
    probe = _OkProbe("openai")
    catalog = ProviderCatalog(probes=[probe], ttl_seconds=60, probe_enabled=True)
    await catalog.start()
    try:
        s1 = catalog.snapshot()
        s2 = catalog.snapshot()
        assert s1 is not s2
        assert s1["openai"] is s2["openai"]
    finally:
        await catalog.stop()


async def test_multiple_probes_independent_failure():
    """One probe failing does not affect other probes' snapshots."""
    ok = _OkProbe("anthropic")
    bad = _FailingProbe("openai")
    catalog = ProviderCatalog(probes=[ok, bad], ttl_seconds=60, probe_enabled=True)
    await catalog.start()
    try:
        snap = catalog.snapshot()
        assert snap["anthropic"].probe_status == "fresh"
        assert snap["openai"].probe_status == "failed"
    finally:
        await catalog.stop()


async def test_stale_snapshot_expires_after_max_stale_seconds():
    """Sequence: fresh → stale → failed once max_stale_seconds is exceeded.

    Uses ttl_seconds=0.01 and max_stale_seconds=0.05 so the full cycle
    completes in under a second.
    """

    class _FlipProbe:
        provider_key = "openai"
        _calls = 0

        async def probe(self) -> ProviderSnapshot:
            self._calls += 1
            if self._calls == 1:
                return _fresh_snapshot("openai")
            raise RuntimeError("downstream error")

    probe = _FlipProbe()
    catalog = ProviderCatalog(
        probes=[probe],
        ttl_seconds=1,
        probe_enabled=True,
        max_stale_seconds=2,
    )
    await catalog.start()

    # After first probe the snapshot should be fresh.
    snap_fresh = catalog.snapshot()["openai"]
    assert snap_fresh.probe_status == "fresh"

    # Wait for one TTL to trigger a failure → stale transition.
    await asyncio.sleep(1.3)
    snap_stale = catalog.snapshot()["openai"]
    assert snap_stale.probe_status == "stale"
    assert snap_stale.last_error is not None

    # Wait until past max_stale_seconds from the original fetch.
    # The next refresh cycle will discard the stale snapshot.
    await asyncio.sleep(1.5)  # total ~2.8 s > max_stale_seconds=2
    snap_failed = catalog.snapshot()["openai"]
    assert snap_failed.probe_status == "failed"
    assert snap_failed.last_error is not None

    await catalog.stop()
