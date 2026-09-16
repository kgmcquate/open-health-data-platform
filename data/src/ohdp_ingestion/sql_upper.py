"""A dlt naming convention that upper-cases every identifier.

dlt ships ``snake_case`` (the default) and ``sql_ci_v1``, both of which lower
case; there is no upper-casing convention in the box. We need one because
Snowflake is the Iceberg catalog (ADR-0019): an external engine reaching it
through the Horizon REST catalog addresses namespaces and tables in all
capitals, and everything else in this project — dbt's
``generate_schema_name``/``generate_alias_name``, ``ohdp_ingestion.naming`` —
is upper case for the same reason. A raw table dlt created as
``nndss_weekly_data`` would be the one lower-case name in the lakehouse, and
the one relying on some engine in the chain matching case-insensitively.

Subclasses dlt's case-sensitive SQL convention rather than reimplementing it,
so the sanitizing rules (ASCII-only, leading digits, collapsed underscores)
stay dlt's. Selected through ``dlt.config["schema.naming"]`` — see
``ohdp_ingestion.socrata.source.configure_catalog``.

Column names are upper-cased too, which is Snowflake's own convention for
unquoted identifiers and invisible to the dbt models: DuckDB resolves column
references case-insensitively, so ``select socrata_id`` still finds
``SOCRATA_ID``.
"""

from __future__ import annotations

from dlt.common.normalizers.naming.sql_cs_v1 import NamingConvention as SqlCsNamingConvention


class NamingConvention(SqlCsNamingConvention):
    """A variant of sql_cs which upper cases all identifiers."""

    def normalize_identifier(self, identifier: str) -> str:
        return super().normalize_identifier(identifier).upper()

    @property
    def is_case_sensitive(self) -> bool:
        # Upper casing collapses `Foo` and `foo` onto one name, exactly as
        # sql_ci_v1's lower casing does — so this convention is not
        # case-sensitive either, whatever the base class says.
        return False
