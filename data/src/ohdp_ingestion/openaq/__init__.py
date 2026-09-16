"""OpenAQ — global air quality measurements. https://docs.openaq.org

M0 source. Requires an API key (header `X-API-Key`), free tier.
"""

from ohdp_ingestion.openaq.client import OpenAQSource

__all__ = ["OpenAQSource"]
