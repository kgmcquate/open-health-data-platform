# Loads the dbt manifest once per Cube process and exposes dbt_model()/dbt_models()
# to every Jinja-templated cube in this folder (cube-dbt:
# https://docs.cube.dev/reference/data-modeling/cube_dbt).
#
# manifest.json comes from `make dbt-parse` (no warehouse connection needed —
# see Makefile), mounted read-only into the Cube container at dbt/manifest.json
# (see README.md's docker run command).
#
# filter(paths=['curated/']) is ADR-0003's "every cube reads a dbt mart, never
# a staging or raw table" enforced at load time: a cube can't reference a
# model this filter excludes, because dbt_model() would return None for it.
# "curated/" is both the dbt folder and the database: models under it land in
# CURATED.CORE and CURATED.<MART> (ADR-0013), the presentation layer dashboards
# and Cube read. RAW and CLEAN are staging.
#
# Primary keys, for as_dimensions() to mark primary_key: true (required by
# Cube whenever a cube defines a join): cube_dbt<0.7 (pinned in
# requirements.txt) only reads a column's own tags/tests when rendering a
# dimension -- model-level `constraints: [{type: primary_key, ...}]` populates
# dbt's manifest and Model.primary_key, but as_dimensions() never consults it.
# So every natural-key column in the curated *_models.yml docs also carries
# `config: {tags: [primary_key]}` (single-column keys instead just use
# `data_tests: [unique, not_null]`, which cube_dbt does check per-column).
#
# Column quoting (ADR-0019 fallout): curated models now land in Snowflake as
# Iceberg tables via the Horizon/Polaris catalog (catalogs.yml), and unlike
# native Snowflake tables, Iceberg tables preserve column names exactly as
# dbt wrote them -- lowercase, case-sensitive -- instead of folding an
# unquoted reference to uppercase. cube_dbt's Column.sql returns the bare
# column name, so Cube's generated SQL ends up as an unquoted `.category` /
# `.measure` / etc., which Snowflake fails to resolve ("invalid identifier").
# Patch Column.sql to always emit a quoted identifier so every dimension
# as_dimensions() generates across every cube matches the physical column.
# Hand-written `sql:` fields in cube yml files (measures, filters, joins,
# refresh_key) are outside cube_dbt and must quote their own column refs.
from cube import TemplateContext
from cube_dbt import Dbt
from cube_dbt.column import Column

Column.sql = property(lambda self: f'"{self._column_dict["name"]}"')

dbt = Dbt.from_file("dbt/manifest.json").filter(paths=["curated/"])

template = TemplateContext()


@template.function("dbt_models")
def dbt_models():
    return dbt.models


@template.function("dbt_model")
def dbt_model(name):
    return dbt.model(name)
