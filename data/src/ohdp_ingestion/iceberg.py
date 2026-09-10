"""One place that knows how to reach the Iceberg catalog (ADR-0010).

Production: an Apache Polaris REST catalog (``OHDP_ICEBERG_CATALOG_URI`` set).
Local dev / CI: a pyiceberg ``SqlCatalog`` on SQLite with a local warehouse dir —
same ``pyiceberg.catalog`` API, no services to run.

Used by three callers, so it lives in the ingestion layer (no dagster/dlt import):
- ``ohdp_ingestion`` — the dlt raw loader
- ``data/dbt/plugins/iceberg_write.py`` — the dbt read/write plugin
- ``ohdp_orchestration`` — the publish step
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ohdp_shared import get_logger
from ohdp_shared.settings import settings

log = get_logger(__name__)

# Medallion layers. `raw` and `clean` namespaces are suffixed with the source;
# `curated` splits into `core` and `mart_<name>`.
Layer = str  # "raw" | "clean" | "curated"


def _s3_props() -> dict[str, str]:
    """pyiceberg FileIO props for DigitalOcean Spaces."""
    if not settings.spaces_endpoint_url:
        return {}
    return {
        "s3.endpoint": settings.spaces_endpoint_url,
        "s3.access-key-id": settings.spaces_access_key_id,
        "s3.secret-access-key": settings.spaces_secret_access_key,
        "s3.region": settings.spaces_region,
        "py-io-impl": "pyiceberg.io.fsspec.FsspecFileIO",
    }


def is_local() -> bool:
    return not settings.iceberg_catalog_uri


def catalog_properties() -> dict[str, Any]:
    """The kwargs for ``pyiceberg.catalog.load_catalog`` (and dlt's
    ``iceberg_catalog_config``, which is the same shape)."""
    if is_local():
        db = Path(settings.iceberg_local_catalog_path).resolve()
        db.parent.mkdir(parents=True, exist_ok=True)
        warehouse = Path(settings.iceberg_local_warehouse).resolve()
        warehouse.mkdir(parents=True, exist_ok=True)
        return {
            "type": "sql",
            "uri": f"sqlite:///{db}",
            "warehouse": warehouse.as_uri(),
        }
    props: dict[str, Any] = {
        "type": "rest",
        "uri": settings.iceberg_catalog_uri,
        "warehouse": settings.iceberg_warehouse or settings.iceberg_catalog_name,
        "scope": settings.iceberg_scope,
    }
    if settings.iceberg_credential:
        props["credential"] = settings.iceberg_credential
    props.update(_s3_props())
    return props


def load_catalog() -> Any:
    """Return a live pyiceberg ``Catalog``."""
    from pyiceberg.catalog import load_catalog as _load

    return _load(settings.iceberg_catalog_name, **catalog_properties())


def namespace(layer: Layer, source: str | None = None) -> str:
    """Iceberg namespace for a layer.

    raw/clean -> ``<layer>_<source>`` (``raw_healthdata_gov``);
    curated   -> ``core`` or ``mart_<source>`` (source treated as the mart name).
    """
    if layer == "curated":
        return f"mart_{source}" if source else "core"
    if not source:
        raise ValueError(f"layer {layer!r} needs a source")
    return f"{layer}_{source}"


def table_location(layer: Layer, table: str, source: str | None = None) -> str:
    """Where the table's files live: ``s3://<bucket>/<layer>/<source>/<table>`` (or a
    local dir in dev). The catalog records this; callers rarely need it directly."""
    root = (
        f"s3://{settings.spaces_bucket}"
        if not is_local()
        else Path(settings.iceberg_local_warehouse).resolve().as_uri()
    )
    parts = [layer, source, table] if source else [layer, table]
    return "/".join([root, *[p for p in parts if p]])


def configure_dlt() -> None:
    """Point dlt's filesystem+iceberg destination at this catalog.

    dlt's ``get_catalog`` reads ``iceberg_catalog.*`` config (dlt.common.libs
    .pyiceberg.IcebergConfig); we set it programmatically so there is no
    ``.pyiceberg.yaml`` or brittle nested-dict env to manage.
    """
    import dlt

    dlt.config["iceberg_catalog.iceberg_catalog_name"] = settings.iceberg_catalog_name
    dlt.config["iceberg_catalog.iceberg_catalog_type"] = "sql" if is_local() else "rest"
    dlt.config["iceberg_catalog.iceberg_catalog_config"] = catalog_properties()
