#!/usr/bin/env python3
"""Regression guard on the Dagster authz policy.

The allowlist in graphql-authz-proxy is the only thing making a publicly exposed
Dagster UI read-only (ARCHITECTURE.md §5). It is YAML in a values file, so it is
one careless edit away from being wide open and nothing else would notice.

Run from platform/helm:  python3 .ci/assert_authz_policy.py
"""

from __future__ import annotations

import subprocess
import sys

import yaml

CHART = "charts/graphql-authz-proxy"
FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def main() -> int:
    rendered = subprocess.run(
        ["helm", "template", "gqlproxy", CHART],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    docs = [d for d in yaml.safe_load_all(rendered) if d]

    configmaps = [d for d in docs if d["kind"] == "ConfigMap"]
    check(len(configmaps) == 1, "expected exactly one authz ConfigMap")
    groups = yaml.safe_load(configmaps[0]["data"]["groups.yaml"])
    by_name = {g["name"]: g for g in groups["groups"]}

    # Unrecognised users must land somewhere, and that somewhere must not be admin.
    defaults = groups.get("default_groups") or []
    check(bool(defaults), "default_groups is empty: anonymous users would get a 403")
    check("admin" not in defaults, "admin must never be a default group")

    for name in defaults:
        group = by_name.get(name)
        check(group is not None, f"default group {name!r} is not defined")
        if group is None:
            continue

        mutations = group["permissions"].get("mutations", {})
        check(
            mutations.get("effect") == "deny",
            f"{name}: mutations must be denied, got {mutations.get('effect')!r}",
        )
        check(
            {"field_name": "*"} in mutations.get("fields", []),
            f"{name}: mutation denial must cover the '*' wildcard",
        )

        queries = group["permissions"].get("queries", {})
        check(
            queries.get("effect") == "allow",
            f"{name}: queries must use an allowlist, got {queries.get('effect')!r}",
        )
        fields = [f["field_name"] for f in queries.get("fields", [])]
        check(
            "*" not in fields,
            f"{name}: query allowlist contains '*' — that grants every root field",
        )
        check(bool(fields), f"{name}: query allowlist is empty")

    # A floating tag on a security control is not acceptable (ADR-0006).
    deployment = next(d for d in docs if d["kind"] == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    check("@sha256:" in container["image"], "proxy image must be pinned by digest")

    env = {e["name"]: e.get("value") for e in container["env"]}
    check(
        env.get("HOST") == "0.0.0.0",
        "HOST must be 0.0.0.0 or gunicorn binds to loopback and the Service cannot reach it",
    )
    check("memory" in container.get("resources", {}).get("limits", {}), "proxy needs a memory limit")

    if FAILURES:
        print("authz policy assertions FAILED:", file=sys.stderr)
        for failure in FAILURES:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print("authz policy OK: default-deny, no wildcard query allowance, digest-pinned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
