#!/usr/bin/env bash
# scripts/e2e-live/port-forward.sh
#
# Forward the coordinator service port to localhost:8080.
# Runs in the foreground. Background it with &, e.g.:
#   ./scripts/e2e-live/port-forward.sh &
#
# Optional env vars:
#   RELEASE     — Helm release name    (default: sec-review-e2e)
#   NAMESPACE   — Kubernetes namespace (default: sec-review-e2e)
#   LOCAL_PORT  — local port to bind   (default: 8080)
#   REMOTE_PORT — service port         (default: 8080)

RELEASE="${RELEASE:-sec-review-e2e}"
NAMESPACE="${NAMESPACE:-sec-review-e2e}"
LOCAL_PORT="${LOCAL_PORT:-8080}"
REMOTE_PORT="${REMOTE_PORT:-8080}"

exec kubectl port-forward \
  -n "${NAMESPACE}" \
  "svc/${RELEASE}-coordinator" \
  "${LOCAL_PORT}:${REMOTE_PORT}"
