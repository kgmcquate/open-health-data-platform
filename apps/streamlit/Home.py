"""Streamlit entrypoint — dashboard home page.

Direct Snowflake connection (M1 scope, same as Superset before it, ADR-0015):
Cube isn't deployed in the cluster yet (ARCHITECTURE.md M2 target). Each
dashboard is a plain Python file under pages/ — Streamlit's multipage support
picks up new ones automatically, which is the point: dashboards here are
meant to be AI-authored.
"""

from __future__ import annotations

import streamlit as st

from ohdp_shared import configure_logging, settings

configure_logging(json=settings.log_json, level=settings.log_level)

st.set_page_config(page_title="Open Health Data Platform — Dashboards", layout="wide")

st.title("Open Health Data Platform")
st.write(
    "Dashboards are Streamlit pages under `pages/` — pick one from the "
    f"sidebar. Environment: `{settings.environment}`."
)
