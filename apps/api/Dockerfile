# hub-api — FastAPI backend.
# Built from the repo root so the uv workspace (packages/shared) is in context:
#   docker build -f apps/api/Dockerfile -t ohdp-hub-api .

FROM ghcr.io/astral-sh/uv:0.5-python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Resolve dependencies first so a source-only change does not re-resolve.
COPY pyproject.toml uv.lock ./
COPY packages/shared/pyproject.toml packages/shared/
COPY apps/api/pyproject.toml apps/api/
COPY data/pyproject.toml data/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-editable --package ohdp-api

COPY packages/shared/ packages/shared/
COPY apps/api/ apps/api/

# --no-editable is required: the default editable install leaves .pth files
# pointing at /app/packages/... , which do not exist in the runtime stage.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --package ohdp-api


FROM python:3.12-slim-bookworm AS runtime

# The chart runs this with runAsNonRoot + readOnlyRootFilesystem (§4, §5).
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10001
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"

CMD ["uvicorn", "hub_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
