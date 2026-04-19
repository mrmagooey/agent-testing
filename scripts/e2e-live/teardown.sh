#!/usr/bin/env bash
# scripts/e2e-live/teardown.sh
#
# Delete the kind cluster used for live e2e tests.
# Idempotent — exits 0 if the cluster is already gone.
#
# Optional env vars:
#   CLUSTER   — kind cluster name (default: sec-review-e2e)

set -euo pipefail

CLUSTER="${CLUSTER:-sec-review-e2e}"

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER}"; then
  echo "Deleting kind cluster '${CLUSTER}' …"
  kind delete cluster --name "${CLUSTER}"
  echo "Cluster '${CLUSTER}' deleted."
else
  echo "kind cluster '${CLUSTER}' does not exist — nothing to do."
fi
