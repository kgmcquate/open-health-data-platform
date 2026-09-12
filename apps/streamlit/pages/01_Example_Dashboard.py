"""Worked example — copy this pattern for a new AI-authored dashboard: one
file per dashboard under pages/, Streamlit picks it up automatically.
"""

from __future__ import annotations

import streamlit as st
from lib.snowflake_client import get_connection

st.title("Example dashboard")

query = st.text_area(
    "SQL against a Snowflake mart (CURATED database — see ADR-0013)",
    "SELECT CURRENT_DATABASE(), CURRENT_TIMESTAMP()",
)

if st.button("Run"):
    with get_connection().cursor() as cur:
        cur.execute(query)
        df = cur.fetch_pandas_all()
    st.dataframe(df)
