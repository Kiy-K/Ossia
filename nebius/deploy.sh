#!/usr/bin/env bash
set -euo pipefail

# One-command deploy script for Ossia on Nebius Serverless.
# Usage: ./nebius/deploy.sh
# Requires: docker, kubectl, envsubst (gettext), and NEBIUS_PROJECT_ID set.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if ! command -v envsubst >/dev/null 2>&1; then
    echo "ERROR: 'envsubst' is required (install gettext/gettext-base)." >&2
    exit 1
fi
if [[ -z "${NEBIUS_PROJECT_ID:-}" ]]; then
    echo "ERROR: NEBIUS_PROJECT_ID must be set before deploying." >&2
    exit 1
fi

REGISTRY="cr.nebius.ai/${NEBIUS_PROJECT_ID}/ossia"
IMAGE_TAG="${IMAGE_TAG:-v0.1.0}"

echo "=== Building Ossia container image ==="
docker build -t "${REGISTRY}:${IMAGE_TAG}" -f "${SCRIPT_DIR}/docker/Dockerfile" "${PROJECT_ROOT}"

echo "=== Pushing image to Nebius Container Registry ==="
docker push "${REGISTRY}:${IMAGE_TAG}"

echo "=== Deploying Serverless Endpoints ==="
# Deploy candidate model endpoint (vLLM) and embedder endpoint.
for endpoint in "${SCRIPT_DIR}"/endpoints/*.yaml; do
    kubectl apply -f "${endpoint}"
done

echo "=== Deploying evaluation Jobs ==="
# Job manifests use ${NEBIUS_PROJECT_ID} for the registry; substitute at apply time.
for job in "${SCRIPT_DIR}"/jobs/*.yaml; do
    envsubst < "${job}" | kubectl apply -f -
done

echo "=== Deploy complete ==="
echo "Monitor endpoints with: kubectl get endpoints -n ossia"
echo "Monitor jobs with: kubectl get jobs -n ossia"
