# hub-api — FastAPI backend.
# Built from the repo root (build context = .) so ../packages/shared is present:
#   docker build -f apps/api/Dockerfile -t ohdp-hub-api .
#
# `apps/api` is a standalone uv project; `ohdp-shared` is a path dependency at
# ../../packages/shared.

FROM ghcr.io/astral-sh/uv:0.5-python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    # venv at a fixed path so console-script shebangs survive the copy to runtime
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Keep the ../../packages/shared layout the path source expects.
COPY packages/shared/ packages/shared/
COPY apps/api/pyproject.toml apps/api/uv.lock apps/api/

WORKDIR /app/apps/api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-editable

COPY apps/api/ /app/apps/api/

# --no-editable: no .pth files pointing at paths absent from the runtime stage.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable


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
