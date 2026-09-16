{#
    dlt stamps every raw row with `_dlt_load_id`, the load's start time as a
    string of epoch seconds. DuckDB's to_timestamp takes those seconds
    directly and yields a TIMESTAMP WITH TIME ZONE, which is what Iceberg
    stores as `timestamptz`.
#}
{% macro dlt_load_id_as_ts() -%}
to_timestamp(cast(_dlt_load_id as double))
{%- endmacro %}
