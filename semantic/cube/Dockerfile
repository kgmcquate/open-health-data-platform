# Build from the repo root: docker build -f semantic/cube/Dockerfile .
# (matches every other image in .github/workflows/build-images.yml)

# --- Stage 1: compile the dbt manifest ---------------------------------------
# `dbt parse` attaches no catalog — it only reads the project's SQL and
# schema.yml files — so this stage needs no AWS credentials. cube-dbt
# (semantic/cube/model/globals.py) reads the resulting manifest.json for
# dimensions and column descriptions.
#
# dbt v2 (Fusion), not dbt-duckdb: this project's catalogs.yml is v2
# (flags.use_catalogs_v2 in dbt_project.yml, ADR-0019), and dbt-duckdb v1
# can't parse it ("Adapter 'duckdb' does not support catalogs.yml v2 yet").
# Installed the same way as the pipeline image (data/Dockerfile) — Fusion is
# a standalone binary, not a pip package, so it's fetched via the shared
# install script rather than `pip install`. The manifest's relation names
# (`CURATED.<SCHEMA>.<TABLE>`) resolve in Snowflake too, because Snowflake
# *is* the catalog those tables live in — see platform/terraform/snowflake.tf.
FROM python:3.12-slim AS dbt-manifest
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates && \
    rm -rf /var/lib/apt/lists/*
COPY data/scripts/install_dbt_fusion.sh /tmp/install_dbt_fusion.sh
RUN mkdir -p /opt/dbt-fusion/bin && /tmp/install_dbt_fusion.sh /opt/dbt-fusion
ENV PATH="/opt/dbt-fusion/bin:${PATH}"

WORKDIR /dbt
COPY data/dbt /dbt
RUN dbt deps && dbt parse

# --- Stage 2: Cube Core --------------------------------------------------
# Pin an immutable tag here too — see platform/helm/charts/cube's fail-fast
# check on image.tag for the same rule applied to the image this produces.
FROM cubejs/cube:v1.7.46

COPY --from=dbt-manifest /dbt/target/manifest.json /cube/conf/dbt/manifest.json
COPY semantic/cube/cube.js /cube/conf/cube.js
COPY semantic/cube/model /cube/conf/model
COPY semantic/cube/requirements.txt /cube/conf/requirements.txt

# The image ships python3 (Jinja/Python data models need it) but not pip —
# Debian trixie's python3.13 install omits it. Installed system-wide with
# --break-system-packages (PEP 668): this container only ever runs Cube, so
# there's no other Python app on it to conflict with.
#
# Baked in at build time instead of relying on Cube's normal
# pip-install-requirements.txt-on-startup convention, so a pod restart never
# depends on PyPI being reachable.
# No --auto-remove on the purge: it drags python3 itself out too, since
# installing python3-pip is what flips python3's apt mark from
# manually-installed to auto-installed.
RUN apt-get update && \
    apt-get install -y --no-install-recommends python3-pip && \
    pip3 install --no-cache-dir --break-system-packages -r /cube/conf/requirements.txt && \
    apt-get purge -y python3-pip && \
    rm -rf /var/lib/apt/lists/*
