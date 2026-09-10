.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

HELM ?= helm
KUBECTL ?= kubectl
VALUES := values
CHARTS := charts

# Image tag for dagster + hub-api. Defaults to the current commit — the same
# `sha-<12>` tag build-images.yml pushes on merge to main. Override to roll
# back to an older build: `make dagster TAG=sha-0123456789ab`.
TAG ?= $(shell git rev-parse HEAD 2>/dev/null | cut -c1-12 | grep . | sed 's/^/sha-/')

# Pin every upstream chart. `helm upgrade --install` otherwise silently pulls
# whatever is newest — OpenMetadata 2.0.x restructured its secret/config layout
# and will not deploy against these values. Re-resolve deliberately after
# reading the upstream changelog (same rule as the proxy image digest).
OPENSEARCH_VERSION      ?= 3.8.0
OPENMETADATA_VERSION    ?= 1.13.5
DAGSTER_VERSION         ?= 1.13.21
TRAEFIK_VERSION         ?= 41.5.0
CERT_MANAGER_VERSION    ?= v1.21.1
OAUTH2_PROXY_VERSION    ?= 10.7.0

.PHONY: help repos lint template base cluster-issuer traefik cert-manager infra \
        install opensearch openmetadata dagster proxy hub-api

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

repos: ## Add and update upstream chart repos
	$(HELM) repo add dagster https://dagster-io.github.io/helm
	$(HELM) repo add open-metadata https://helm.open-metadata.org
	$(HELM) repo add opensearch https://opensearch-project.github.io/helm-charts
	$(HELM) repo add traefik https://traefik.github.io/charts
	$(HELM) repo add jetstack https://charts.jetstack.io
	$(HELM) repo add oauth2-proxy https://oauth2-proxy.github.io/manifests
	$(HELM) repo update

lint: ## Lint the charts we own
	$(HELM) lint $(CHARTS)/platform-base
	$(HELM) lint $(CHARTS)/hub-api --set image.tag=0.0.0-dev
	$(HELM) lint $(CHARTS)/graphql-authz-proxy

template: ## Render the charts we own locally (catches template errors without a cluster)
	$(HELM) template platform-base $(CHARTS)/platform-base >/dev/null && echo "platform-base OK"
	$(HELM) template hub-api $(CHARTS)/hub-api --set image.tag=0.0.0-dev >/dev/null && echo "hub-api OK"
	$(HELM) template gqlproxy $(CHARTS)/graphql-authz-proxy >/dev/null && echo "graphql-authz-proxy OK"

# --------------------------------------------------------------------------
# Cluster foundation
# --------------------------------------------------------------------------
base: ## Namespaces, generated internal secrets, and the shared Postgres StatefulSet
	$(HELM) upgrade --install platform-base $(CHARTS)/platform-base \
		-n infra --create-namespace --wait --timeout 5m

traefik: ## Install the Traefik ingress controller (provisions a DO load balancer)
	$(HELM) upgrade --install traefik traefik/traefik --version $(TRAEFIK_VERSION) \
		-n infra --create-namespace -f $(VALUES)/traefik.yaml --wait --timeout 5m

cert-manager: ## Install cert-manager (HTTP-01 solver for Traefik)
	$(HELM) upgrade --install cert-manager jetstack/cert-manager --version $(CERT_MANAGER_VERSION) \
		-n infra --create-namespace -f $(VALUES)/cert-manager.yaml --wait --timeout 5m

cluster-issuer: ## Create the letsencrypt-prod ClusterIssuer (re-renders platform-base once the CRDs exist)
	$(HELM) upgrade platform-base $(CHARTS)/platform-base -n infra --wait --timeout 5m

infra: base traefik cert-manager cluster-issuer ## Cluster foundation: platform-base, ingress, cert-manager, issuer

# The external secrets (Spaces keys, source API keys, Stripe, OIDC, OM ingestion
# JWT) are injected by .github/workflows/deploy-platform.yml from repo secrets —
# not by a make target. For a manual/local deploy, see platform/helm/README.md.

# --------------------------------------------------------------------------
# Data platform
# --------------------------------------------------------------------------
opensearch: ## Install OpenSearch (must be green before OpenMetadata)
	$(HELM) upgrade --install opensearch opensearch/opensearch --version $(OPENSEARCH_VERSION) \
		-n meta --create-namespace -f $(VALUES)/opensearch.yaml --wait --timeout 10m

openmetadata: ## Install OpenMetadata
	$(HELM) upgrade --install openmetadata open-metadata/openmetadata --version $(OPENMETADATA_VERSION) \
		-n meta --create-namespace -f $(VALUES)/openmetadata.yaml --wait --timeout 10m

dagster: ## Install Dagster (TAG defaults to the current commit's sha-<12>)
	@test -n "$(TAG)" || { echo "TAG is empty and HEAD is unknown — pass TAG=sha-abc123def456"; exit 1; }
	$(HELM) upgrade --install dagster dagster/dagster --version $(DAGSTER_VERSION) \
		-n data --create-namespace -f $(VALUES)/dagster.yaml \
		--set "dagster-user-deployments.deployments[0].image.tag=$(TAG)" \
		--set "runLauncher.config.k8sRunLauncher.image.tag=$(TAG)" \
		--wait --timeout 10m

proxy: ## Install the GraphQL authz proxy in front of Dagster
	$(HELM) upgrade --install gqlproxy $(CHARTS)/graphql-authz-proxy \
		-n data --create-namespace --wait

oauth2-proxy: ## Install the Google login wall in front of the authz proxy (ADR-0007)
	$(HELM) upgrade --install oauth2-proxy oauth2-proxy/oauth2-proxy --version $(OAUTH2_PROXY_VERSION) \
		-n data --create-namespace -f $(VALUES)/oauth2-proxy.yaml --wait --timeout 5m

hub-api: ## Install the FastAPI backend (TAG defaults to the current commit's sha-<12>)
	@test -n "$(TAG)" || { echo "TAG is empty and HEAD is unknown — pass TAG=sha-abc123def456"; exit 1; }
	$(HELM) upgrade --install hub-api $(CHARTS)/hub-api \
		-n app --create-namespace --set image.tag=$(TAG) --wait

install: opensearch openmetadata proxy dagster ## The data platform (hub-api is deployed on its own)
