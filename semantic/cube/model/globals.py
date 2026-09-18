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
# Primary keys: every curated mart carries a `row_sk` column -- its natural key
# hashed by macros/row_sk.sql -- and that column alone is what cube_dbt renders
# as `primary_key: true`. It needs no tag to do so: row_sk's
# `data_tests: [unique, not_null]` is one of the signals cube_dbt<0.7 (pinned
# in requirements.txt) checks per column. Model-level
# `constraints: [{type: primary_key, ...}]` is NOT one of them -- it populates
# dbt's manifest and Model.primary_key, but as_dimensions() never consults it,
# which is why the grain is declared in the model SQL rather than there.
#
# The hash exists because of what `primary_key: true` costs in Cube: it flips
# that dimension's `public` default to false, so the dimension is dropped from
# /meta and no client -- the REST API, the MCP server, a BI tool -- can group
# or filter by it. These marts key on composite *natural* keys, so marking
# those columns hid exactly the axes the semantic layer exists to slice by:
# chronic_disease__state_indicator_trend once exposed 5 of its 14 dimensions
# and no time axis at all. Hashing the key into one opaque column puts Cube's
# hidden-by-default treatment on something nobody wants to group by, and leaves
# year, week_end, state_abbr and the rest as plain public dimensions. Those
# columns carry `config: {meta: {grain: true}}`, which cube_dbt passes through
# to the dimension, so the grain is still readable through /meta.
#
# Two marts are exempt: access__treatment_sites and core__treatment_site key on
# site_id, a Socrata row id that already *is* a surrogate. They keep it as
# their primary key and Cube hides it, which is the right outcome.
#
# Column quoting (ADR-0019 fallout): curated models now land in Snowflake as
# Iceberg tables via the Horizon/Polaris catalog (catalogs.yml), and unlike
# native Snowflake tables, Iceberg tables preserve column names exactly as
# dbt wrote them -- lowercase, case-sensitive -- instead of folding an
# unquoted reference to uppercase. cube_dbt's Column.sql returns the bare
# column name, so Cube's generated SQL ends up as an unquoted `.category` /
# `.measure` / etc., which Snowflake fails to resolve ("invalid identifier").
# Patch Column.sql to always emit a quoted, {CUBE}-qualified identifier so
# every dimension as_dimensions() generates across every cube matches the
# physical column. The explicit {CUBE} prefix matters, not just the quoting:
# Cube's SQL compiler only auto-qualifies a dimension's sql with the cube's
# table alias when the value is a bare identifier; a quoted string like
# '"geography_type"' doesn't match that pattern, so Cube emits it verbatim
# with no table alias at all, which Snowflake then rejects as unresolvable
# ("invalid identifier '"geography_type"'", no cube prefix in the error).
# Hand-written `sql:` fields in cube yml files (measures, filters, joins,
# refresh_key) are outside cube_dbt and must {CUBE}-qualify + quote their own
# column refs the same way.
from cube import TemplateContext
from cube_dbt import Dbt
from cube_dbt.column import Column
from cube_dbt.dump import Dumper, SafeString
from cube_dbt.model import Model

Column.sql = property(lambda self: f'{{CUBE}}."{self._column_dict["name"]}"')

# Relation-name quoting — the same ADR-0019 fallout as the Column.sql patch
# above, but on the table side. The dbt manifest's `relation_name` is already
# double-quoted ("CURATED"."RESPIRATORY"."TABLE"), and Cube's Jinja engine
# JSON-escapes a plain `str` returned from `{{ ... }}`. So `{{ model.sql_table }}`
# in a refresh_key emitted `"\""CURATED"\".\""RESPIRATORY"\".\""TABLE"\""`, which
# Snowflake rejects ("parse error ... near '34'" — 34 is ASCII `"`). Returning a
# SafeString marks the value is_safe so Jinja inserts it verbatim, the same way
# as_cube()/as_dimensions() already do via dump(). The yaml representer below
# keeps as_cube()'s yaml.dump from serialising the SafeString as a
# `!!python/object` tag, so `sql_table` still renders as the plain quoted name.
_original_sql_table = Model.sql_table


@property
def _sql_table_safe(self) -> SafeString:
    return SafeString(_original_sql_table.fget(self))


Model.sql_table = _sql_table_safe

Dumper.add_representer(SafeString, lambda dumper, data: dumper.represent_str(str(data)))

dbt = Dbt.from_file("dbt/manifest.json").filter(paths=["curated/"])

template = TemplateContext()


@template.function("dbt_models")
def dbt_models():
    return dbt.models


@template.function("dbt_model")
def dbt_model(name):
    return dbt.model(name)
