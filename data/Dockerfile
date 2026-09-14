# ohdp-pipeline — the Dagster code location, and the image every run Job uses.
# Built from the repo root (build context = .):
#   docker build -f data/Dockerfile -t ohdp-pipeline .
#
# `data/` is a standalone uv project; `ohdp-shared` is a path dependency at
# ../packages/shared. K8sRunLauncher launches this image for each run, so it
# carries dbt as well as the Dagster definitions.

FROM ghcr.io/astral-sh/uv:0.5-python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    # venv at a fixed path so console-script shebangs survive the copy to runtime
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Keep the ../packages/shared layout that data/pyproject.toml's path source
# expects. Resolve deps first so a source-only change does not re-resolve.
COPY packages/shared/ packages/shared/
COPY data/pyproject.toml data/uv.lock data/

WORKDIR /app/data
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-editable

# dbt deps hits the network (dbt Hub); dbt parse is offline — no Snowflake
# credentials needed, the catalog is only attached on a real run. Copy just the dbt project
# first so ordinary source changes elsewhere in data/ don't force a re-fetch of
# dbt packages: this manifest.json build for the `lakehouse` dbt assets
# (ADR-0019) only reruns when the dbt project itself changes.
COPY data/dbt/ dbt/
RUN /app/.venv/bin/dbt deps --project-dir dbt \
 && /app/.venv/bin/dbt parse --project-dir dbt --profiles-dir dbt

# Bake DuckDB's extensions in. The profile lists them (data/dbt/profiles.yml)
# and DuckDB would fetch them on first connect, but a run pod that has to reach
# extensions.duckdb.org before it can build anything is a needless dependency —
# and the failure mode is an opaque "could not install extension" mid-run.
# Installed into the venv's DuckDB home, which the runtime stage copies.
ENV DUCKDB_HOME=/app/.duckdb
RUN mkdir -p "$DUCKDB_HOME" \
 && /app/.venv/bin/python -c "\
import duckdb; con = duckdb.connect(); \
[con.execute(f'INSTALL {e}') for e in ('httpfs', 'aws', 'iceberg')]"

COPY data/ /app/data/

# --no-editable: the default editable install leaves .pth files pointing at
# /app/data/src/... which do not exist in the runtime stage (ADR-0004).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable


FROM python:3.12-slim-bookworm AS runtime

RUN groupadd --gid 10001 dagster \
 && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin dagster

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
# The dbt project (with the built manifest) is data files, not an importable
# package — copy it whole.
COPY --from=builder --chown=10001:10001 /app/data/dbt /app/dbt
# DuckDB's pre-installed extensions (see the builder stage).
COPY --from=builder --chown=10001:10001 /app/.duckdb /app/.duckdb

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DAGSTER_HOME=/opt/dagster/dagster_home \
    DBT_PROJECT_DIR=/app/dbt \
    DBT_PROFILES_DIR=/app/dbt \
    DUCKDB_HOME=/app/.duckdb

RUN mkdir -p /opt/dagster/dagster_home \
 && chown -R 10001:10001 /opt/dagster

USER 10001

# The chart overrides this via dagsterApiGrpcArgs; kept so the image runs
# standalone for debugging.
EXPOSE 3030
CMD ["dagster", "api", "grpc", "--host", "0.0.0.0", "--port", "3030", \
     "--module-name", "ohdp_orchestration.definitions"]
