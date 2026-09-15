# Open WebUI, plus the `openwebui-token-tracking` package (ADR-0022) baked in
# at build time rather than installed at pod startup via a Function's
# `requirements:` frontmatter. The upstream image already runs as a non-root
# user with no writable site-packages, and a startup-time `pip install` would
# refetch from PyPI on every pod restart — on a node already at its memory
# ceiling (values/open-webui.yaml, ARCHITECTURE.md §4) that is a cost worth
# avoiding, and it is the one custom-code path this deployment couldn't
# otherwise reproduce from git alone (ADR-0017's "not reproducible from git"
# consequence — this doesn't need to be a second instance of it).
#
#   docker build -f apps/open-webui/Dockerfile -t ohdp-open-webui .
#
# Base tag must match the `open-webui/open-webui` chart version pinned in
# platform/helm/Makefile (OPENWEBUI_VERSION) — check that chart's appVersion
# before bumping either one on its own.
FROM ghcr.io/open-webui/open-webui:0.11.3

# Exact version pinned deliberately: this package's own bundled pricing table
# (src/openwebui_token_tracking/resources/models.json) does not know about
# every model we run, which is why the init Job also upserts pricing for
# z-ai/glm-5.3-flash explicitly (platform/helm/manifests/open-webui-token-tracking-init-job.yaml).
# This version is also where `pipes/openai.py` (OpenAITrackedPipe, with its
# API_BASE_URL/PROVIDER Valves) comes from — ADR-0023 routes OpenRouter through
# it, so don't drop below it.
RUN pip install --no-cache-dir openwebui-token-tracking==0.1.10
