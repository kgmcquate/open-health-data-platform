"""Dashboards kept in the repository, loaded from the YAML files beside this one.

A dashboard here is a *question*, not an answer: it holds Cube queries and chart
encodings, never rows, so reopening one shows current data and can never
disagree with the semantic layer (ARCHITECTURE.md §1.5).

To add one: render it in chat, copy the YAML out of the rendered card's
"Dashboard source" section, save it as `<name>.yaml` next to this file with
`name:` matching the filename, and open a PR. `test_dashboard.py` validates
every file in this directory, so a broken spec fails CI rather than 500ing in
front of a user.

They live inside the package, not at the repo root, because the runtime stage of
`apps/api/Dockerfile` copies only the built virtualenv — a top-level directory
would be reviewable in git and missing from the image.
"""

from __future__ import annotations

from importlib.resources import files

from ohdp_agent.dashboard import DashboardSpec

SUFFIX = ".yaml"


def _directory() -> object:
    return files(__name__)


def list_saved() -> list[DashboardSpec]:
    """Every saved dashboard, by name.

    A file that no longer parses is skipped rather than allowed to break the
    listing for the others — but `test_dashboard.py` asserts none of them are
    skippable, so in practice this only matters if one is edited in a running
    pod.
    """
    specs = []
    for entry in sorted(_directory().iterdir(), key=lambda p: p.name):  # type: ignore[attr-defined]
        if not entry.name.endswith(SUFFIX):
            continue
        try:
            specs.append(DashboardSpec.from_yaml(entry.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001 — one bad file must not hide the rest
            continue
    return specs


def load_saved(name: str) -> DashboardSpec:
    """One saved dashboard by `name`.

    Looked up through the parsed listing rather than by building a path from
    `name`: this argument comes from a model, and a name joined onto a directory
    is a traversal waiting to happen. Nothing here touches the filesystem with
    caller-supplied text.
    """
    for spec in list_saved():
        if spec.name == name:
            return spec
    raise KeyError(name)
