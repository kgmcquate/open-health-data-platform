.DEFAULT_GOAL := help
.PHONY: help setup lint fmt types test dbt-parse dagster-dev api-dev cube-dev \
        act-list act-preflight act-build act-plan images

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

dbt-parse: ## Parse dbt models (no warehouse required)
	cd data/dbt && uv run dbt deps && uv run dbt parse

dagster-dev: ## Run the Dagster webserver against the local code location
	cd data && uv run dagster dev -m ohdp_orchestration.definitions

api-dev: ## Run the hub API locally
	cd apps/api && uv run uvicorn hub_api.main:app --reload --port 8000

cube-dev: dbt-parse ## Run Cube Core locally, wired to the dbt manifest + Snowflake
	cd semantic/cube && docker run -p 4000:4000 \
	  -v "$$PWD:/cube/conf" \
	  -v "$$PWD/../../data/dbt/target:/cube/conf/dbt:ro" \
	  -e CUBEJS_DEV_MODE=true \
	  -e CUBEJS_DB_TYPE=snowflake \
	  -e CUBEJS_DB_SNOWFLAKE_ACCOUNT=$$OHDP_SNOWFLAKE_ACCOUNT \
	  -e CUBEJS_DB_USER=$$OHDP_SNOWFLAKE_USER \
	  -e CUBEJS_DB_SNOWFLAKE_PRIVATE_KEY=$$OHDP_SNOWFLAKE_PRIVATE_KEY \
	  -e CUBEJS_DB_SNOWFLAKE_ROLE=$$OHDP_SNOWFLAKE_ROLE \
	  -e CUBEJS_DB_SNOWFLAKE_WAREHOUSE=$$OHDP_SNOWFLAKE_WAREHOUSE \
	  -e CUBEJS_DB_SNOWFLAKE_AUTHENTICATOR=SNOWFLAKE_JWT \
	  cubejs/cube:latest

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
	docker build -f apps/streamlit/Dockerfile -t ohdp-streamlit:dev .
	docker build -f data/Dockerfile -t ohdp-pipeline:dev .
	docker build -f semantic/cube/Dockerfile -t ohdp-cube:dev .
