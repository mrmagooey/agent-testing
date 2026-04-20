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
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-${CLUSTER}}"
CHART_DIR="$(cd "$(dirname "$0")/../../helm/sec-review" && pwd)"
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

# ---------------------------------------------------------------------------
# 1. Require a backend configuration
#
# Two modes are supported:
#   (a) OpenRouter:            OPENROUTER_TEST_KEY=<key>
#   (b) OpenAI-compatible:     LIVE_TEST_API_BASE=<url>  LIVE_TEST_API_KEY=<key>
#                              (the worker reads OPENAI_BASE_URL / OPENAI_API_KEY)
# ---------------------------------------------------------------------------
HELM_BACKEND_ARGS=()
if [[ -n "${OPENROUTER_TEST_KEY:-}" ]]; then
  echo "Backend: OpenRouter"
  HELM_BACKEND_ARGS+=(--set-string "secrets.apiKeys.openrouter=${OPENROUTER_TEST_KEY}")
elif [[ -n "${LIVE_TEST_API_BASE:-}" && -n "${LIVE_TEST_API_KEY:-}" ]]; then
  echo "Backend: OpenAI-compatible at ${LIVE_TEST_API_BASE}"
  HELM_BACKEND_ARGS+=(--set-string "secrets.apiKeys.openai=${LIVE_TEST_API_KEY}")
  HELM_BACKEND_ARGS+=(--set-string "secrets.baseUrls.openai=${LIVE_TEST_API_BASE}")
else
  echo "ERROR: set either OPENROUTER_TEST_KEY, or LIVE_TEST_API_BASE + LIVE_TEST_API_KEY." >&2
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
  --kube-context "${KUBE_CONTEXT}" \
  --namespace "${NAMESPACE}" \
  --create-namespace \
  --values "${CHART_DIR}/values-e2e.yaml" \
  --set image.coordinator.tag=e2e \
  --set image.worker.tag=e2e \
  "${HELM_BACKEND_ARGS[@]}" \
  --wait \
  --timeout 5m

# ---------------------------------------------------------------------------
# 6. Wait for coordinator pod
# ---------------------------------------------------------------------------
echo "Waiting for coordinator pod to become ready …"
kubectl --context "${KUBE_CONTEXT}" wait \
  --for=condition=ready pod \
  -l app.kubernetes.io/component=coordinator \
  -n "${NAMESPACE}" \
  --timeout=120s

# ---------------------------------------------------------------------------
# 7. Seed dataset
# ---------------------------------------------------------------------------
echo "Seeding dataset …"
RELEASE="${RELEASE}" NAMESPACE="${NAMESPACE}" KUBE_CONTEXT="${KUBE_CONTEXT}" \
  bash "$(dirname "$0")/seed-dataset.sh"

# ---------------------------------------------------------------------------
# 8. Print port-forward command
# ---------------------------------------------------------------------------
echo ""
echo "============================================================"
echo " Bootstrap complete."
echo " Run the following to forward the coordinator port:"
echo ""
echo "   kubectl --context ${KUBE_CONTEXT} port-forward -n ${NAMESPACE} svc/${RELEASE}-coordinator 8080:8080"
echo ""
echo " Or use the helper script:"
echo "   ./scripts/e2e-live/port-forward.sh"
echo "============================================================"
