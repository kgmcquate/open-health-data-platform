from __future__ import annotations

import pytest

from ohdp_agent.cube import clear_meta_cache


@pytest.fixture(autouse=True)
def _fresh_meta_cache() -> None:
    """`CubeClient.list_metrics` caches `/meta` per Cube URL across instances,
    and every test points at the same fake URL — without this, one test's
    fixture catalog (and the request it did or did not make) leaks into the next."""
    clear_meta_cache()
