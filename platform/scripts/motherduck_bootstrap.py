# /// script
# requires-python = ">=3.12"
# dependencies = ["duckdb~=1.4.1"]
# ///
"""Attach Snowflake's CURATED Iceberg catalog to MotherDuck, server-side (ADR-0029).

Cube queries MotherDuck, and MotherDuck reads the curated marts straight out of
the same Horizon Iceberg REST catalog the pipeline writes (ADR-0019). Snowflake
stays the catalog; only the compute that serves Cube moves.

Three MotherDuck objects, all persistent in the workspace:

  ohdp_horizon  an ICEBERG secret stored IN MOTHERDUCK, holding the pipeline PAT
  CURATED       a read-only ICEBERG database over Horizon's CURATED catalog
  cache         a native MotherDuck database holding Cube's pre-aggregations

The database is named CURATED on purpose: the cube models' `sql_table` comes
from the dbt manifest as "CURATED"."<SCHEMA>"."<TABLE>", which then resolves in
MotherDuck unchanged.

The secret is replaced on every run, so rotating the PAT (snowflake.tf) is just
a redeploy. The database is only created when missing: MotherDuck can't ALTER
its endpoint/warehouse/read_only options, so pass --recreate after changing
any of those below.

Horizon quirks carried over from data/dbt/profiles.yml, where they are
explained: CLIENT_ID must be empty, and OAUTH2_SCOPE must name the role.

Run: uv run platform/scripts/motherduck_bootstrap.py [--recreate]
Env: MOTHERDUCK_TOKEN, OHDP_SNOWFLAKE_PAT, OHDP_SNOWFLAKE_ACCOUNT, OHDP_SNOWFLAKE_ROLE
"""

import os
import sys

import duckdb

SECRET = "ohdp_horizon"
DATABASE = "CURATED"
CACHE_DATABASE = "cache"


def sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    recreate = "--recreate" in sys.argv[1:]
    token = os.environ["MOTHERDUCK_TOKEN"]
    pat = os.environ["OHDP_SNOWFLAKE_PAT"]
    account = os.environ["OHDP_SNOWFLAKE_ACCOUNT"]
    role = os.environ["OHDP_SNOWFLAKE_ROLE"]
    endpoint = f"https://{account}.snowflakecomputing.com/polaris/api/catalog"

    con = duckdb.connect(f"md:?motherduck_token={token}")

    con.execute(f"""
        CREATE OR REPLACE SECRET {SECRET} IN MOTHERDUCK (
            TYPE ICEBERG,
            CLIENT_ID '',
            CLIENT_SECRET {sql_str(pat)},
            OAUTH2_SERVER_URI {sql_str(endpoint + "/v1/oauth/tokens")},
            OAUTH2_SCOPE {sql_str(f"session:role:{role}")}
        )
    """)
    print(f"secret {SECRET}: replaced")

    exists = con.execute(
        "SELECT count(*) FROM duckdb_databases() WHERE lower(database_name) = lower(?)",
        [DATABASE],
    ).fetchone()[0]
    if exists and recreate:
        con.execute(f"DROP DATABASE {DATABASE}")
        exists = 0
        print(f"database {DATABASE}: dropped (--recreate)")
    if exists:
        print(f"database {DATABASE}: already attached")
    else:
        # default_schema must already exist in the catalog; CURATED.CORE always
        # does (snowflake.tf's snowflake_namespaces).
        con.execute(f"""
            CREATE DATABASE {DATABASE} (
                TYPE ICEBERG,
                "secret" {SECRET},
                endpoint {sql_str(endpoint)},
                warehouse {sql_str(DATABASE)},
                default_schema 'CORE',
                access_delegation_mode 'vended_credentials',
                read_only true
            )
        """)
        print(f"database {DATABASE}: attached")

    # Native MotherDuck storage for Cube's originalSql pre-aggregations
    # (cube/values.yaml connects Cube to md:cache, making this its default
    # database, so they land in cache.prod_pre_aggregations).
    con.execute(f"CREATE DATABASE IF NOT EXISTS {CACHE_DATABASE};")
    print(f"database {CACHE_DATABASE}: present")

    # Smoke test: fail the deploy here rather than in Cube's readiness probe.
    schemas = con.execute(
        "SELECT DISTINCT schema_name FROM duckdb_schemas() WHERE lower(database_name) = lower(?)",
        [DATABASE],
    ).fetchall()
    print(f"database {DATABASE}: schemas {sorted(s for (s,) in schemas)}")


if __name__ == "__main__":
    main()
