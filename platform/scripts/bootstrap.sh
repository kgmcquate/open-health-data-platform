#!/usr/bin/env bash
# Bring the platform up on a fresh DOKS cluster from platform/terraform.
# See platform/helm/README.md for the full sequence.
set -euo pipefail

: "${KUBECONFIG:?export KUBECONFIG from platform/terraform first}"
: "${DOPPLER_TOKEN:?a Doppler service token is required}"

kubectl create namespace external-secrets --dry-run=client -o yaml | kubectl apply -f -
kubectl -n external-secrets create secret generic doppler-token \
  --from-literal=dopplerToken="$DOPPLER_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

make -C "$(dirname "$0")/../helm" repos infra install
