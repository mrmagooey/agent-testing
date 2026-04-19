#!/usr/bin/env bash
# scripts/e2e-live/bootstrap.sh
#
# Stand up a kind cluster and deploy the sec-review stack for live e2e tests.
# Does NOT run tests — callers should port-forward then invoke make kind-e2e-pytest
# and make kind-e2e-playwright.
#
# Required env vars:
#   OPENROUTER_TEST_KEY   — OpenRouter API key (live, will consume tokens)
#
# Optional env vars:
#   RELEASE     — Helm release name     (default: sec-review-e2e)
#   NAMESPACE   — Kubernetes namespace  (default: sec-review-e2e)
#   CLUSTER     — kind cluster name     (default: sec-review-e2e)

set -euo pipefail

RELEASE="${RELEASE:-sec-review-e2e}"
NAMESPACE="${NAMESPACE:-sec-review-e2e}"
CLUSTER="${CLUSTER:-sec-review-e2e}"
CHART_DIR="$(cd "$(dirname "$0")/../../helm/sec-review" && pwd)"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# ---------------------------------------------------------------------------
# 1. Require OPENROUTER_TEST_KEY
# ---------------------------------------------------------------------------
if [[ -z "${OPENROUTER_TEST_KEY:-}" ]]; then
  echo "ERROR: OPENROUTER_TEST_KEY is not set. Export it before running this script." >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# 2. Create kind cluster if not already present
# ---------------------------------------------------------------------------
if kind get clusters 2>/dev/null | grep -qx "${CLUSTER}"; then
  echo "kind cluster '${CLUSTER}' already exists — skipping creation."
else
  echo "Creating kind cluster '${CLUSTER}' …"
  kind create cluster --name "${CLUSTER}" --wait 60s
fi

# ---------------------------------------------------------------------------
# 3. Build Docker images
# ---------------------------------------------------------------------------
echo "Building coordinator image …"
docker build -f "${REPO_ROOT}/Dockerfile.coordinator" \
  -t sec-review-coordinator:e2e \
  "${REPO_ROOT}"

echo "Building worker image …"
docker build -f "${REPO_ROOT}/Dockerfile.worker" \
  -t sec-review-worker:e2e \
  "${REPO_ROOT}"

# ---------------------------------------------------------------------------
# 4. Load images into kind
# ---------------------------------------------------------------------------
echo "Loading coordinator image into kind cluster '${CLUSTER}' …"
kind load docker-image sec-review-coordinator:e2e --name "${CLUSTER}"

echo "Loading worker image into kind cluster '${CLUSTER}' …"
kind load docker-image sec-review-worker:e2e --name "${CLUSTER}"

# ---------------------------------------------------------------------------
# 5. Helm upgrade --install
# ---------------------------------------------------------------------------
echo "Deploying Helm chart (release: ${RELEASE}, namespace: ${NAMESPACE}) …"
helm upgrade --install "${RELEASE}" "${CHART_DIR}" \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  --values "${CHART_DIR}/values-e2e.yaml" \
  --set image.coordinator.tag=e2e \
  --set image.worker.tag=e2e \
  --set-string secrets.apiKeys.openrouter="${OPENROUTER_TEST_KEY}" \
  --wait \
  --timeout 5m

# ---------------------------------------------------------------------------
# 6. Wait for coordinator pod
# ---------------------------------------------------------------------------
echo "Waiting for coordinator pod to become ready …"
kubectl wait \
  --for=condition=ready pod \
  -l app.kubernetes.io/component=coordinator \
  -n "${NAMESPACE}" \
  --timeout=120s

# ---------------------------------------------------------------------------
# 7. Seed dataset
# ---------------------------------------------------------------------------
echo "Seeding dataset …"
RELEASE="${RELEASE}" NAMESPACE="${NAMESPACE}" \
  bash "$(dirname "$0")/seed-dataset.sh"

# ---------------------------------------------------------------------------
# 8. Print port-forward command
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Bootstrap complete."
echo " Run the following to forward the coordinator port:"
echo ""
echo "   kubectl port-forward -n ${NAMESPACE} svc/${RELEASE}-coordinator 8080:8080"
echo ""
echo " Or use the helper script:"
echo "   ./scripts/e2e-live/port-forward.sh"
echo "============================================================"
