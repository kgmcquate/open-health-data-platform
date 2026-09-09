# ohdp-pipeline — the Dagster code location, and the image every run Job uses.
# Built from the repo root:
#   docker build -f data/Dockerfile -t ohdp-pipeline .
#
# K8sRunLauncher launches this image for each run, so it must carry dbt and
# DuckDB as well as the Dagster definitions.

FROM ghcr.io/astral-sh/uv:0.5-python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/
COPY apps/api/pyproject.toml apps/api/
COPY data/pyproject.toml data/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-editable --package ohdp-data

COPY packages/shared/ packages/shared/
COPY data/ data/

# --no-editable is required: the default editable install leaves .pth files
# pointing at /app/data/src/... , which do not exist in the runtime stage.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --package ohdp-data


FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --gid 10001 dagster \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin dagster

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
# The dbt project is data files, not an importable package — it has to be copied.
COPY --from=builder --chown=10001:10001 /app/data/dbt /app/dbt

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DAGSTER_HOME=/opt/dagster/dagster_home \
    DBT_PROJECT_DIR=/app/dbt \
    DBT_PROFILES_DIR=/app/dbt \
    OHDP_DUCKDB_PATH=/tmp/warehouse/warehouse.duckdb

RUN mkdir -p /opt/dagster/dagster_home /tmp/warehouse \
 && chown -R 10001:10001 /opt/dagster /tmp/warehouse

USER 10001

# The chart overrides this via dagsterApiGrpcArgs; kept so the image runs
# standalone for debugging.
EXPOSE 3030
CMD ["dagster", "api", "grpc", "--host", "0.0.0.0", "--port", "3030", \
     "--module-name", "ohdp_orchestration.definitions"]
