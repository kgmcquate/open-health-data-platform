.DEFAULT_GOAL := help
.PHONY: help setup lint fmt types test dbt-parse dagster-dev api-dev

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
	cd data/dbt && uv run dbt deps && uv run dbt parse --target ci

dagster-dev: ## Run the Dagster webserver against the local code location
	cd data && uv run dagster dev -m ohdp_orchestration.definitions

api-dev: ## Run the hub API locally
	cd apps/api && uv run uvicorn hub_api.main:app --reload --port 8000
