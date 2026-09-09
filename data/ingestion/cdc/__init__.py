"""CDC open data via the Socrata (SODA) API. https://data.cdc.gov

M0 source. Pick one surveillance dataset first (candidate: respiratory
virus hospitalization / ILINet-style weekly counts). No key required for
low volume; an app token raises rate limits.
"""

from ohdp_ingestion.cdc.client import CDCSodaSource

__all__ = ["CDCSodaSource"]
