# Build from the repo root: docker build -f semantic/cube/Dockerfile .
# (matches every other image in .github/workflows/build-images.yml)

# --- Stage 1: compile the dbt manifest ---------------------------------------
# `dbt parse` needs no warehouse connection — it only reads the project's SQL
# and schema.yml files — so this stage never touches Snowflake. cube-dbt
# (semantic/cube/model/globals.py) reads the resulting manifest.json for
# dimensions and column descriptions.
FROM python:3.12-slim AS dbt-manifest
RUN pip install --no-cache-dir "dbt-snowflake>=1.8"
WORKDIR /dbt
COPY data/dbt /dbt
RUN dbt deps && dbt parse

# --- Stage 2: Cube Core --------------------------------------------------
# Pin an immutable tag here too — see platform/helm/charts/cube's fail-fast
# check on image.tag for the same rule applied to the image this produces.
FROM cubejs/cube:v1.7.37

COPY --from=dbt-manifest /dbt/target/manifest.json /cube/conf/dbt/manifest.json
COPY semantic/cube/cube.js /cube/conf/cube.js
COPY semantic/cube/model /cube/conf/model
COPY semantic/cube/requirements.txt /cube/conf/requirements.txt

# Baked in at build time instead of relying on Cube's normal
# pip-install-requirements.txt-on-startup convention, so a pod restart never
# depends on PyPI being reachable.
RUN pip3 install --no-cache-dir -r /cube/conf/requirements.txt
