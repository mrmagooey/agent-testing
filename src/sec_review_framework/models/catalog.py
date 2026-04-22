"""ProviderCatalog: in-process cache of reachable models per provider.

The catalog runs background probes on a TTL schedule. Callers read snapshots
non-blocking via ``snapshot()``; the catalog never raises into callers.

Usage::

    catalog = ProviderCatalog(probes=build_probes(), ttl_seconds=600)
    await catalog.start()
    snap = catalog.snapshot()          # dict[str, ProviderSnapshot]
    await catalog.stop()

The ``snapshot()`` return value is a shallow copy of the internal dict but
the ``ProviderSnapshot`` objects themselves are *not* copied.  Callers must
not mutate them.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

ProbeStatus = Literal["fresh", "stale", "failed", "disabled"]


@dataclass(frozen=True)
class ModelMetadata:
    id: str
    display_name: str | None = None
    context_length: int | None = None
    region: str | None = None      # Bedrock
    pricing: dict | None = None    # OpenRouter


@dataclass
class ProviderSnapshot:
    probe_status: ProbeStatus
    model_ids: frozenset[str] = frozenset()
    metadata: dict[str, ModelMetadata] = field(default_factory=dict)
    fetched_at: datetime | None = None
    last_error: str | None = None


class ProviderProbe(Protocol):
    provider_key: str  # "openai", "openrouter", "bedrock", etc.

    async def probe(self) -> ProviderSnapshot: ...


class ProviderCatalog:
    """In-process cache of reachable models per provider.

    Parameters
    ----------
    probes:
        One probe object per logical provider.
    ttl_seconds:
        How long a snapshot is considered "fresh".  After this interval the
        background task re-runs all probes concurrently.
    probe_enabled:
        When False, ``start()`` does nothing and every probe's snapshot is
        ``disabled`` with empty ``model_ids``.
    """

    def __init__(
        self,
        *,
        probes: list[ProviderProbe],
        ttl_seconds: int = 600,
        probe_enabled: bool = True,
        max_stale_seconds: int | None = None,
    ) -> None:
        self._probes = probes
        self._ttl_seconds = ttl_seconds
        self._probe_enabled = probe_enabled
        # Default: allow stale data for at most 6× the TTL (1 h for the
        # default 10-min TTL).  After that, return probe_status="failed".
        self._max_stale_seconds: int = (
            max_stale_seconds if max_stale_seconds is not None else ttl_seconds * 6
        )
        # Initialise every probe to "disabled" so snapshot() is always safe.
        self._snapshots: dict[str, ProviderSnapshot] = {
            p.provider_key: ProviderSnapshot(probe_status="disabled")
            for p in probes
        }
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Kick off initial probe + background refresh task."""
        if not self._probe_enabled:
            # Leave all snapshots as "disabled".
            return
        # Run first probe immediately so callers get data without waiting.
        await self._run_probes()
        self._task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        """Cancel the background task cleanly."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def snapshot(self) -> dict[str, ProviderSnapshot]:
        """Non-blocking read of current snapshots.

        Returns a shallow copy of the internal mapping so the caller cannot
        accidentally replace entries.  The individual ``ProviderSnapshot``
        objects are *not* copied — callers must not mutate them.
        """
        return dict(self._snapshots)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _refresh_loop(self) -> None:
        """Sleep ttl_seconds, then re-probe; repeat until cancelled."""
        try:
            while True:
                await asyncio.sleep(self._ttl_seconds)
                await self._run_probes()
        except asyncio.CancelledError:
            raise

    async def _run_probes(self) -> None:
        """Run all probes concurrently; update snapshots; swallow errors."""
        results = await asyncio.gather(
            *[self._safe_probe(p) for p in self._probes],
            return_exceptions=True,
        )
        for probe, result in zip(self._probes, results):
            if isinstance(result, BaseException):
                # Should not happen because _safe_probe swallows, but guard.
                logger.warning(
                    "Unexpected exception from _safe_probe for %s: %s",
                    probe.provider_key,
                    result,
                )
            else:
                self._snapshots[probe.provider_key] = result

    async def _safe_probe(self, probe: ProviderProbe) -> ProviderSnapshot:
        """Run probe; handle errors without propagating."""
        try:
            snap = await probe.probe()
            return snap
        except Exception as exc:
            err_msg = str(exc)
            logger.warning(
                "Probe %s failed: %s", probe.provider_key, err_msg
            )
            prior = self._snapshots.get(probe.provider_key)
            if prior is not None and prior.probe_status not in ("disabled", "failed"):
                # Check whether the stale data is still within the allowed age.
                if prior.fetched_at is not None:
                    age = (
                        datetime.now(timezone.utc) - prior.fetched_at
                    ).total_seconds()
                    if age > self._max_stale_seconds:
                        logger.warning(
                            "Stale snapshot for %s is %.0f s old (max %d s) — "
                            "discarding and returning failed",
                            probe.provider_key,
                            age,
                            self._max_stale_seconds,
                        )
                        return ProviderSnapshot(
                            probe_status="failed",
                            last_error=err_msg,
                        )
                # Keep previous good data, mark stale.
                return ProviderSnapshot(
                    probe_status="stale",
                    model_ids=prior.model_ids,
                    metadata=prior.metadata,
                    fetched_at=prior.fetched_at,
                    last_error=err_msg,
                )
            # No usable prior snapshot.
            return ProviderSnapshot(
                probe_status="failed",
                last_error=err_msg,
            )
