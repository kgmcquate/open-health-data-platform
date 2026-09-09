.DEFAULT_GOAL := help
SHELL := /usr/bin/env bash

HELM ?= helm
VALUES := values

.PHONY: help repos lint template install opensearch openmetadata dagster proxy hub-api verify-proxy

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

repos: ## Add and update upstream chart repos
	$(HELM) repo add dagster https://dagster-io.github.io/helm
	$(HELM) repo add open-metadata https://helm.open-metadata.org
	$(HELM) repo add opensearch https://opensearch-project.github.io/helm-charts
	$(HELM) repo update

lint: ## Lint the charts we own
	$(HELM) lint charts/hub-api --set image.tag=0.0.0-dev
	$(HELM) lint charts/graphql-authz-proxy

template: ## Render our charts locally (catches template errors without a cluster)
	$(HELM) template hub-api charts/hub-api --set image.tag=0.0.0-dev >/dev/null && echo "hub-api OK"
	$(HELM) template gqlproxy charts/graphql-authz-proxy >/dev/null && echo "graphql-authz-proxy OK"

opensearch: ## Install OpenSearch (must be green before OpenMetadata)
	$(HELM) upgrade --install opensearch opensearch/opensearch \
		-n meta --create-namespace -f $(VALUES)/opensearch.yaml --wait --timeout 10m

openmetadata: ## Install OpenMetadata
	$(HELM) upgrade --install openmetadata open-metadata/openmetadata \
		-n meta --create-namespace -f $(VALUES)/openmetadata.yaml --wait --timeout 10m

dagster: ## Install Dagster
	$(HELM) upgrade --install dagster dagster/dagster \
		-n data --create-namespace -f $(VALUES)/dagster.yaml --wait --timeout 10m

proxy: ## Install the GraphQL authz proxy in front of Dagster
	$(HELM) upgrade --install gqlproxy charts/graphql-authz-proxy \
		-n data --create-namespace --wait

hub-api: ## Install the FastAPI backend (pass TAG=<immutable tag>)
	@test -n "$(TAG)" || { echo "TAG is required, e.g. make hub-api TAG=sha-abc123"; exit 1; }
	$(HELM) upgrade --install hub-api charts/hub-api \
		-n app --create-namespace --set image.tag=$(TAG) --wait

install: opensearch openmetadata dagster proxy ## Everything except hub-api (which needs a TAG)

verify-proxy: ## Assert the allowlist denies mutations. Run after every Dagster upgrade.
	@echo "port-forwarding gqlproxy..."
	@kubectl -n data port-forward svc/gqlproxy-graphql-authz-proxy 18080:80 >/dev/null 2>&1 & \
	  PF=$$!; sleep 3; \
	  MUT=$$(curl -s -o /dev/null -w '%{http_code}' localhost:18080/graphql \
	    -H 'Content-Type: application/json' \
	    -d '{"query":"mutation { terminatePipelineExecution(runId:\"x\") { __typename } }"}'); \
	  QRY=$$(curl -s -o /dev/null -w '%{http_code}' localhost:18080/graphql \
	    -H 'Content-Type: application/json' -d '{"query":"query { version }"}'); \
	  kill $$PF 2>/dev/null; \
	  echo "mutation -> $$MUT (want 403), query -> $$QRY (want 200)"; \
	  test "$$MUT" = "403" || { echo "FAIL: mutation was not denied"; exit 1; }
