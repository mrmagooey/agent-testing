"""Bedrock probe.

Enumerates foundation models and inference profiles across one or more AWS
regions.  Requires:
  - ``BEDROCK_PROBE_REGIONS`` (comma-separated, e.g. ``us-east-1,us-west-2``)
  - Valid AWS credentials discoverable by boto3 (IAM role, env vars, etc.)
  - ``BEDROCK_PROBE_ENABLED`` != ``"false"`` (default enabled)

Returns ``disabled`` if:
  - boto3 is not installed
  - ``BEDROCK_PROBE_ENABLED=false``
  - No AWS credentials found

Lets ``ClientError`` propagate so the catalog can mark the snapshot as
``failed`` / ``stale``.

Multi-region handling
---------------------
When multiple regions are configured and the same model id is advertised in
more than one, the snapshot collapses the duplicates to a single entry and
records the alphabetically-first region (deterministic across restarts).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sec_review_framework.models.catalog import ModelMetadata, ProviderSnapshot

logger = logging.getLogger(__name__)

try:
    import boto3
    from botocore.exceptions import ClientError  # noqa: F401 — re-exported for callers
    _BOTO3_AVAILABLE = True
except ImportError:
    boto3 = None  # type: ignore[assignment]
    _BOTO3_AVAILABLE = False


class BedrockProbe:
    provider_key = "bedrock"

    async def probe(self) -> ProviderSnapshot:
        if not _BOTO3_AVAILABLE:
            return ProviderSnapshot(
                probe_status="disabled",
                last_error="boto3 not installed",
            )

        enabled_env = os.environ.get("BEDROCK_PROBE_ENABLED", "true").strip().lower()
        if enabled_env == "false":
            return ProviderSnapshot(
                probe_status="disabled",
                last_error="BEDROCK_PROBE_ENABLED=false",
            )

        # Check credentials.
        session = boto3.Session()
        creds = session.get_credentials()
        if creds is None:
            return ProviderSnapshot(
                probe_status="disabled",
                last_error="AWS credentials not configured",
            )

        regions_raw = os.environ.get("BEDROCK_PROBE_REGIONS", "us-east-1")
        regions = sorted({r.strip() for r in regions_raw.split(",") if r.strip()})

        # Accumulate region per model id across all regions, then collapse.
        # We walk regions in sorted order so the first-seen region wins,
        # giving a deterministic representative region per model.
        per_id: dict[str, ModelMetadata] = {}

        for region in regions:
            client = boto3.client("bedrock", region_name=region)

            # Foundation models
            try:
                fm_resp = client.list_foundation_models()
                for m in fm_resp.get("modelSummaries", []):
                    raw_id = m.get("modelId", "")
                    if not raw_id:
                        continue
                    mid = f"bedrock/{raw_id}"
                    if mid in per_id:
                        continue  # first (alphabetically earliest) region wins
                    per_id[mid] = ModelMetadata(
                        id=mid,
                        display_name=m.get("modelName"),
                        region=region,
                        provider_key="bedrock",
                        raw_id=mid,
                    )
            except Exception as exc:
                logger.warning("Bedrock list_foundation_models failed in %s: %s", region, exc)
                raise

            # Inference profiles
            try:
                ip_resp = client.list_inference_profiles()
                for p in ip_resp.get("inferenceProfileSummaries", []):
                    raw_id = p.get("inferenceProfileId", "")
                    if not raw_id:
                        continue
                    mid = f"bedrock/{raw_id}"
                    if mid in per_id:
                        continue
                    per_id[mid] = ModelMetadata(
                        id=mid,
                        display_name=p.get("inferenceProfileName"),
                        region=region,
                        provider_key="bedrock",
                        raw_id=mid,
                    )
            except Exception as exc:
                logger.warning("Bedrock list_inference_profiles failed in %s: %s", region, exc)
                # Inference profiles may not be available in all regions; don't re-raise.

        return ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(per_id.keys()),
            metadata=per_id,
            fetched_at=datetime.now(timezone.utc),
        )
