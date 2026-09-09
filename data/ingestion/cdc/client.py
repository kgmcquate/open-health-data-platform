"""CDC SODA client. Stub — dataset id and schema contract to be chosen for M0."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

from ohdp_ingestion.base import Source, http_client

_BASE_URL = "https://data.cdc.gov"


class CDCSodaSource(Source):
    name = "cdc"
    raw_table = "raw_cdc_surveillance"

    def __init__(self, dataset_id: str = "", app_token: str | None = None) -> None:
        self._dataset_id = dataset_id
        self._app_token = app_token or os.environ.get("OHDP_CDC_APP_TOKEN", "")

    def fetch(self, *, since: str | None = None) -> Iterator[dict[str, Any]]:
        # TODO(M0): GET /resource/{dataset_id}.json with $where on the date column,
        # $limit/$offset paging, $order by a stable key.
        headers = {"X-App-Token": self._app_token} if self._app_token else {}
        with http_client(_BASE_URL, headers=headers) as _client:
            raise NotImplementedError("CDC SODA fetch not implemented yet — M0")
