"""Cached Snowflake connection shared by every dashboard page.

Queries the Iceberg lakehouse through Snowflake's catalog-linked database
`LAKEHOUSE` (ADR-0019) — the same tables dbt-duckdb builds, the same relation
names the dbt manifest carries.

Uses the shared `OHDP_PIPELINE` role/key (`ohdp_shared.settings`) rather than a
dedicated one. Since ADR-0019 that role is read-only over the lakehouse, so
this is now sharing a read credential with Cube rather than the privilege
trade-off docs/decisions/0015-streamlit-over-superset.md described.
"""

from __future__ import annotations

import streamlit as st
from cryptography.hazmat.primitives import serialization
from snowflake.connector import SnowflakeConnection, connect

from ohdp_shared import settings


def _private_key_der() -> bytes:
    """snowflake-connector-python's `private_key` param wants DER/PKCS8 bytes;
    the stored secret is PEM (Terraform's `tls_private_key` output)."""
    key = serialization.load_pem_private_key(settings.snowflake_private_key.encode(), password=None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


@st.cache_resource
def get_connection() -> SnowflakeConnection:
    return connect(
        account=settings.snowflake_account,
        user=settings.snowflake_user,
        private_key=_private_key_der(),
        role=settings.snowflake_role,
        warehouse=settings.snowflake_warehouse,
    )
