"""Reusable Dagster component base classes.

Deliberately **outside** ``ohdp_orchestration.defs``: that package is autoloaded
by ``dagster.components.load_defs`` and should hold only the concrete component
subclasses and their ``defs.yaml`` instances. An abstract base with no instances
has no business being walked by the loader.
"""
