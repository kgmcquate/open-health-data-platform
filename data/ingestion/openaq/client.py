"""OpenAQ v3 client. Stub — pagination and schema contract to be filled in for M0."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from ohdp_ingestion.base import Source, http_client

_BASE_URL = "https://api.openaq.org/v3"


class OpenAQSource(Source):
    name = "openaq"
    raw_table = "raw_openaq_measurements"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("OHDP_OPENAQ_API_KEY", "")

    def fetch(self, *, since: str | None = None) -> Iterator[dict[str, Any]]:
        # TODO(M0): page /measurements filtered by datetime_from=since, yield rows.
        headers = {"X-API-Key": self._api_key} if self._api_key else {}
        with http_client(_BASE_URL, headers=headers) as _client:
            raise NotImplementedError("OpenAQ fetch not implemented yet — M0")
