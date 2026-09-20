"""Hub API package."""

import os
from pathlib import Path


def config_dir() -> Path:
    """Where `hub_api.models` and `hub_api.tool_connections` read their YAML
    config from. Defaults to the source tree's `apps/api/config/` (true for
    `uv run` and any editable install), but the Docker image installs this
    package `--no-editable` into `.venv/site-packages` *and* never bundles
    `apps/api/config/` alongside it — so that default resolves to a directory
    that doesn't exist there. The Dockerfile copies the config directory into
    the image and sets OHDP_HUB_API_CONFIG_DIR to point at it instead.
    """
    override = os.environ.get("OHDP_HUB_API_CONFIG_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent.parent / "config"
