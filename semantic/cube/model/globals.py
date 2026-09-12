# Loads the dbt manifest once per Cube process and exposes dbt_model()/dbt_models()
# to every Jinja-templated cube in this folder (cube-dbt:
# https://docs.cube.dev/reference/data-modeling/cube_dbt).
#
# manifest.json comes from `make dbt-parse` (no warehouse connection needed —
# see Makefile), mounted read-only into the Cube container at dbt/manifest.json
# (see README.md's docker run command).
#
# filter(paths=['marts/']) is ADR-0003's "every cube reads a dbt mart, never a
# staging or raw table" enforced at load time: a cube can't reference a model
# this filter excludes, because dbt_model() would return None for it.
from cube import TemplateContext
from cube_dbt import Dbt

dbt = Dbt.from_file('dbt/manifest.json').filter(paths=['marts/'])

template = TemplateContext()


@template.function('dbt_models')
def dbt_models():
    return dbt.models


@template.function('dbt_model')
def dbt_model(name):
    return dbt.model(name)
