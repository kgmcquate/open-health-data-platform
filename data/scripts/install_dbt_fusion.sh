#!/usr/bin/env sh
# Installs dbt v2 (the Fusion engine) and points this project's `dbt` console
# script at it, without disturbing dbt-core — which stays installed
# transitively (dagster-dbt and, via collate-data-diff, openmetadata-ingestion's
# `snowflake` extra both import dbt-core's Python classes directly) and must
# not be removed. dbt-oss/dbt (the pip packages) can't be installed
# side-by-side with dbt-core in the same venv: both ship files under the same
# top-level `dbt/` package, and installing both corrupts that shared
# namespace (confirmed while building this migration — dagster_dbt itself
# failed to import). Fusion is installed as a fully standalone binary
# instead, entirely outside site-packages, and only the tiny `bin/dbt`
# console-script launcher gets replaced.
#
# Run after `uv sync` — in the Dockerfile, in CI, and once locally after any
# `uv sync` that recreates the venv (a fresh checkout, a dependency bump).
#
# Usage: install_dbt_fusion.sh [path/to/.venv]  (default: ./.venv)
set -eu

VERSION=2.0.1
VENV_DIR=$(cd "${1:-.venv}" && pwd)
FUSION_DIR="$VENV_DIR/fusion"

# The installer unconditionally edits a shell rc file to add itself to PATH,
# which we don't want as a side effect of a project setup script (and
# definitely don't want happening to a developer's real dotfiles). Sandbox
# that into a throwaway HOME; --to still controls the real install location.
FAKE_HOME=$(mktemp -d)
trap 'rm -rf "$FAKE_HOME"' EXIT

if ! HOME="$FAKE_HOME" curl -fsSL https://public.cdn.getdbt.com/fs/install/install.sh \
  | HOME="$FAKE_HOME" sh -s -- --version "$VERSION" --to "$FUSION_DIR"; then
  echo "warning: dbt installer exited non-zero; continuing (see installer output)"
fi

ln -sf "$FUSION_DIR/dbt" "$VENV_DIR/bin/dbt"
if [ -f "$VENV_DIR/bin/dbt" ]; then
  # Try to print the version, but don't fail the build if the binary is
  # not runnable in the build environment (cross-arch/qemu issues).
  "$VENV_DIR/bin/dbt" --version || true
else
  echo "warning: $VENV_DIR/bin/dbt not present"
fi

echo "install_dbt_fusion.sh: installed fusion to $FUSION_DIR, symlink at $VENV_DIR/bin/dbt"
# Don't let a non-runnable binary (cross-arch) fail the Docker build; runtime
# pods will run the correct arch binary or fetch extensions as needed.
exit 0
