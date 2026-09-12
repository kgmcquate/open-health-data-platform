{% macro dlt_load_id_as_ts() -%}
(round(_dlt_load_id, 3)::timestamp_ntz || '+00')::timestamp_tz
{%- endmacro %}