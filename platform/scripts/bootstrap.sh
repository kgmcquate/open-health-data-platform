#!/usr/bin/env bash
# Bring the platform up on a fresh DOKS cluster from platform/terraform.
# The external secrets (Spaces keys, source API keys, Stripe, OIDC, OM JWT) are created
# by the `secrets` step of .github/workflows/deploy-platform.yml, or by hand —
# see platform/helm/README.md. This script only does the parts that need no
# out-of-band values.
set -euo pipefail

: "${KUBECONFIG:?export KUBECONFIG from platform/terraform first}"

make -C "$(dirname "$0")/../helm" repos infra
echo
echo "Foundation up. Now create ohdp-pipeline-secrets and hub-api-secrets"
echo "(platform/helm/README.md), then: make -C platform/helm install"
