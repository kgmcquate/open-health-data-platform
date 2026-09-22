"""openFDA config-driven ingestion.

``component.py`` defines one component: ``OpenFDADataset`` — one instance per
openFDA REST endpoint, one ``---`` document in ``datasets/defs.yaml``
(hand-maintained — openFDA has no multi-dataset catalog to scrape, unlike
Socrata/CMS). Each emits a source ``AssetSpec`` under the ``sources/`` prefix
and, when ``enabled``, a ``dlt``-backed table asset under ``ingestion/`` plus
a ``lakehouse/raw/`` label that receives the load's materialization events —
the same shape ADR-0008/0018/0026 established for Socrata and CMS sources.

The three cadence asset jobs + schedules need no per-instance config, so they
are plain code, not a component — see ``ohdp_orchestration.jobs.openfda`` /
``ohdp_orchestration.schedules.openfda``.
"""
