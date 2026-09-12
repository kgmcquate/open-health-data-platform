"""Turn Cube measures into OpenMetadata Metric entities.

Implemented as a Dagster asset, not here — see
``data/src/ohdp_orchestration/assets/cube_metrics_sync.py`` (asset
``openmetadata_cube_metrics_sync``, job ``openmetadata_cube_metrics_sync_job``,
schedule ``openmetadata_cube_metrics_sync_schedule``) for the real
implementation and its docstring for why it talks to Cube's REST metadata
endpoint and OpenMetadata's SDK directly rather than through
``MetadataWorkflow``.
"""
