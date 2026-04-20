#!/usr/bin/env bash
# Seed the live-e2e dataset fixture into the cluster's shared PV.
#
# Accepts release/namespace/storage root via env vars or positional args.
# Env vars (from bootstrap.sh) take precedence over positional args.
#
# Env vars:
#   RELEASE        — Helm release name    (default: sec-review-e2e)
#   NAMESPACE      — K8s namespace        (default: sec-review-e2e)
#   STORAGE_ROOT   — PV mount path        (default: /data)
#
# Positional:
#   ./seed-dataset.sh [RELEASE] [NAMESPACE] [STORAGE_ROOT]

set -euo pipefail

RELEASE="${RELEASE:-${1:-sec-review-e2e}}"
NAMESPACE="${NAMESPACE:-${2:-sec-review-e2e}}"
STORAGE_ROOT="${STORAGE_ROOT:-${3:-/data}}"
CLUSTER="${CLUSTER:-sec-review-e2e}"
KUBE_CONTEXT="${KUBE_CONTEXT:-kind-${CLUSTER}}"

# Paths
FIXTURE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/tests/fixtures/live-e2e"
REMOTE_DATASET_DIR="${STORAGE_ROOT}/datasets/targets/live-e2e"

# The coordinator is a Deployment — resolve its pod name dynamically.
COORDINATOR_POD="$(kubectl --context "$KUBE_CONTEXT" get pod \
  -n "$NAMESPACE" \
  -l app.kubernetes.io/component=coordinator \
  -o jsonpath='{.items[0].metadata.name}')"

if [[ -z "$COORDINATOR_POD" ]]; then
    echo "ERROR: could not find coordinator pod in namespace $NAMESPACE" >&2
    exit 1
fi

echo "Seeding live-e2e fixture into cluster..."
echo "  Release: $RELEASE"
echo "  Namespace: $NAMESPACE"
echo "  Storage Root: $STORAGE_ROOT"
echo "  Fixture Dir: $FIXTURE_DIR"
echo "  Coordinator Pod: $COORDINATOR_POD"

# Verify fixture exists
if [[ ! -d "$FIXTURE_DIR" ]]; then
    echo "ERROR: Fixture directory not found: $FIXTURE_DIR" >&2
    exit 1
fi

if [[ ! -f "$FIXTURE_DIR/app.py" ]]; then
    echo "ERROR: Missing app.py in fixture" >&2
    exit 1
fi

if [[ ! -f "$FIXTURE_DIR/labels.jsonl" ]]; then
    echo "ERROR: Missing labels.jsonl in fixture" >&2
    exit 1
fi

# Create remote directory structure in the pod
echo "Creating remote directory structure..."
kubectl --context "$KUBE_CONTEXT" exec -n "$NAMESPACE" "$COORDINATOR_POD" -- mkdir -p "$REMOTE_DATASET_DIR/repo"

# Copy repo files
echo "Copying repo files..."
kubectl --context "$KUBE_CONTEXT" cp "$FIXTURE_DIR/app.py" "$NAMESPACE/$COORDINATOR_POD:$REMOTE_DATASET_DIR/repo/"

# Copy labels
echo "Copying labels.jsonl..."
kubectl --context "$KUBE_CONTEXT" cp "$FIXTURE_DIR/labels.jsonl" "$NAMESPACE/$COORDINATOR_POD:$REMOTE_DATASET_DIR/"

# Verify files were copied
echo "Verifying seeded files..."
kubectl --context "$KUBE_CONTEXT" exec -n "$NAMESPACE" "$COORDINATOR_POD" -- ls -la "$REMOTE_DATASET_DIR/"
kubectl --context "$KUBE_CONTEXT" exec -n "$NAMESPACE" "$COORDINATOR_POD" -- ls -la "$REMOTE_DATASET_DIR/repo/"

echo "✓ Live-e2e dataset seeded successfully"
