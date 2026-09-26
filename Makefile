.DEFAULT_GOAL := help
.PHONY: help setup lint fmt types test dbt-parse dbt-build dagster-dev api-dev cube-dev motherduck-bootstrap \
        act-list act-preflight act-build act-plan images web-setup web-dev web-build

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## Install the Python workspace
	uv sync --all-packages --dev

lint: ## Ruff lint
	uv run ruff check .

fmt: ## Ruff format
	uv run ruff format .

types: ## mypy
	uv run mypy packages apps data

test: ## pytest
	uv run pytest

dbt-parse: ## Parse dbt models (no catalog connection required)
	cd data/dbt && uv run dbt deps && uv run dbt parse

dbt-build: ## Build the lakehouse (needs OHDP_SNOWFLAKE_* — see .env.example)
	cd data/dbt && uv run dbt build

dagster-dev: ## Run the Dagster webserver against the local code location
	cd data && uv run dagster dev -m ohdp_orchestration.definitions

api-dev: ## Run the hub API locally
	cd apps/api && uv run uvicorn hub_api.main:app --reload --port 8000

web-setup: ## Install hub UI dependencies
	cd apps/web && npm install

web-dev: ## Run the hub UI dev server (proxies /api and /auth to :8000)
	cd apps/web && npm run dev

web-build: ## Type-check and build the hub UI into apps/web/dist
	cd apps/web && npm run build

cube-dev: dbt-parse ## Run Cube Core locally, wired to the dbt manifest + MotherDuck
	cd semantic/cube && docker run -p 4000:4000 \
	  -v "$$PWD:/cube/conf" \
	  -v "$$PWD/../../data/dbt/target:/cube/conf/dbt:ro" \
	  -e CUBEJS_DEV_MODE=true \
	  -e CUBEJS_API_SECRET=$$OHDP_CUBE_API_SECRET \
	  -e CUBEJS_DB_TYPE=duckdb \
	  -e CUBEJS_DB_DUCKDB_DATABASE_PATH=md:cache \
	  -e motherduck_token=$$MOTHERDUCK_TOKEN \
	  cubejs/cube:v1.7.46

motherduck-bootstrap: ## Attach Horizon's CURATED Iceberg catalog to MotherDuck (ADR-0029)
	uv run platform/scripts/motherduck_bootstrap.py

# --- Deploy (see docs/deploying.md) ----------------------------------------

act-list: ## List workflows and jobs act can see
	act -l

act-preflight: ## Validate charts, policy, values and manifests — no cluster needed
	act workflow_dispatch -W .github/workflows/deploy-platform.yml

act-plan: ## terraform plan via act
	act workflow_dispatch -W .github/workflows/deploy-infra.yml --input action=plan

act-build: ## Build images via act without pushing
	act workflow_dispatch -W .github/workflows/build-images.yml --input push=false

images: ## Build all images directly with docker (faster than act)
	docker build -f apps/api/Dockerfile -t ohdp-hub-api:dev .
	docker build -f data/Dockerfile -t ohdp-pipeline:dev .
	docker build -f semantic/cube/Dockerfile -t ohdp-cube:dev .
