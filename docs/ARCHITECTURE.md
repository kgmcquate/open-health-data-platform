# Open Health Data Platform — Architecture

A self-hosted SaaS platform over public health data. Free and paid ($5/mo) tiers.
Every platform component is intentionally user-visible: this is a portfolio project as
much as a product.

**Audience for this document:** the implementing agent. It defines target
architecture, repo layout, and build order. It is not a tutorial.

---

## 1. Design principles

These drive most decisions below. Read them before changing anything.

1. **The data is public and identical for every user.** There is no PHI, no
   per-tenant data, no HIPAA exposure. Authorization is about *features and
   quotas*, not rows. Do not build row-level security on the health data.
2. **Materialize once, serve many.** Compute cost scales with query volume, not
   user count. Everything is precomputed by a batch pipeline and served read-only.
3. **Cost ceiling is ~$35/month of infrastructure.** One VM, one Postgres, free
   tiers everywhere else. Any design that requires managed Kubernetes, a cloud
   warehouse, or a second always-on database is out of budget.
4. **Low availability is acceptable.** Single replica, brief downtime during
   rollouts and upgrades is fine. Do not add HA complexity.
5. **One metric definition, three consumers.** Dashboards, chatbot, and alerting
   all read from the semantic layer. They must never disagree about what a number means.
6. **The agent never writes raw SQL against tables.** It selects from a bounded
   set of measures and dimensions. This is the primary safety control.

---

## 2. System architecture

```mermaid
flowchart TB
    subgraph external["Public data sources"]
        S1["OpenAQ"]
        S2["CDC SODA"]
        S3["openFDA"]
        S4["CMS"]
        S5["WHO GHO"]
    end

    subgraph orchestration["Orchestration"]
        DAG["Dagster<br/>webserver + daemon"]
        BUILD["dbt build pod<br/>ephemeral"]
    end

    subgraph storage["Object storage — Cloudflare R2"]
        ART["Versioned DuckDB snapshots<br/>+ current.json pointer"]
    end

    subgraph serving["Serving"]
        REPL["DuckDB read replica<br/>read-only, local NVMe"]
        CUBE["Cube Core<br/>semantic layer"]
    end

    subgraph consumers["Consumers"]
        SUP["Apache Superset"]
        BOT["Chat orchestrator"]
        ALERT["Alerting jobs"]
    end

    subgraph meta["Metadata"]
        OM["OpenMetadata<br/>+ OpenSearch"]
    end

    APP["Hub app<br/>Next.js + FastAPI"]
    IDP["Hosted IdP<br/>OIDC"]
    PG[("Postgres<br/>4 databases")]

    S1 & S2 & S3 & S4 & S5 --> BUILD
    DAG -->|launches| BUILD
    BUILD -->|uploads snapshot| ART
    ART -->|init container pulls| REPL
    REPL --> CUBE
    CUBE --> SUP
    CUBE --> BOT
    CUBE --> ALERT
    ALERT -->|SMS / email| APP
    BOT --> APP
    SUP -->|embedded via guest token| APP
    DAG -.->|GraphQL status| APP
    OM -.->|catalog context| BOT
    BUILD -->|pushes lineage + metrics| OM
    CUBE -->|metric definitions| OM
    IDP -.->|OIDC| APP
    IDP -.->|OIDC| OM
    IDP -.->|OIDC| SUP
    DAG --- PG
    SUP --- PG
    OM --- PG
    APP --- PG
```

### What each component owns

| Component | Responsibility | Notes |
|---|---|---|
| Hub app | Signup, billing, chat UI, embedded dashboards, links to every tool | The only thing most users see first |
| Dagster | Ingestion, dbt orchestration, snapshot publishing, ML inference, alert checks | Publicly visible, hardened (§5) |
| dbt | SQL transformation and tests against DuckDB | Source of truth for models |
| DuckDB | Compute and storage | Publish-and-replicate, never shared read-write |
| Cube Core | Semantic layer: measures, dimensions, access control, MCP endpoint | Single definition of every metric |
| Superset | Dashboards, embedded and standalone | Queries Cube, not DuckDB |
| OpenMetadata | Catalog, lineage, glossary, metric directory, discovery MCP | Human-browsable surface |
| Postgres | App metadata for Dagster, Superset, OpenMetadata, hub app | One instance, four databases |
| R2 | Versioned snapshots and Parquet | Zero egress fees |

---

## 3. Data lifecycle

The core pattern. Multiple processes may open a DuckDB file read-only **only when
no process holds it read-write**, so the writer and readers must never share a file.

```mermaid
sequenceDiagram
    participant Sched as Dagster schedule
    participant Build as dbt build pod
    participant R2 as Cloudflare R2
    participant K8s as k3s API
    participant Repl as DuckDB replica
    participant OM as OpenMetadata

    Sched->>Build: launch run (concurrency 1)
    Build->>Build: ingest sources to raw tables
    Build->>Build: dbt build (models + tests)
    alt tests fail
        Build-->>Sched: fail run, publish nothing
    else tests pass
        Build->>R2: upload warehouse-{ts}.duckdb
        Build->>R2: update current.json pointer
        Build->>OM: upsert lineage, metrics, freshness
        Build->>K8s: patch Deployment annotation
        K8s->>Repl: rolling restart
        Repl->>R2: init container pulls current snapshot
        Repl->>Repl: open read-only, serve
    end
```

**Rollback is a pointer change.** Snapshots are immutable and retained for N versions.

**Do not publish on failed tests.** The dbt test gate is the only thing standing
between a broken upstream API and a wrong number on a clinician's dashboard.

---

## 4. Deployment topology

Single VM. Hetzner CX53 (16 vCPU, 32 GB, 320 GB NVMe), k3s, Germany or Finland.
Do not use EKS: the control plane alone exceeds the entire infrastructure budget.

```mermaid
flowchart TB
    CF["Cloudflare<br/>DNS, TLS, WAF, rate limiting"]

    subgraph vm["Single VM — k3s"]
        ING["Traefik ingress + cert-manager"]

        subgraph nsapp["namespace: app"]
            A1["hub-web"]
            A2["hub-api"]
        end

        subgraph nsdata["namespace: data"]
            D1["dagster-webserver"]
            D2["dagster-daemon"]
            D3["graphql-authz-proxy"]
            D4["oauth2-proxy"]
            D5["duckdb-replica"]
            D6["cube"]
        end

        subgraph nsbi["namespace: bi"]
            B1["superset"]
            B2["redis"]
        end

        subgraph nsmeta["namespace: meta"]
            M1["openmetadata-server"]
            M2["opensearch<br/>single node, 2GB heap"]
        end

        subgraph nsinfra["namespace: infra"]
            P1[("postgres")]
        end
    end

    R2["Cloudflare R2"]
    GC["Grafana Cloud<br/>free tier"]

    CF --> ING
    ING --> A1
    ING --> D4
    ING --> B1
    ING --> M1
    D4 --> D3
    D3 --> D1
    D5 --> R2
    vm -.->|metrics, logs| GC
```

### Resource budget

Steady state ~13 GB, burst ~15 GB during a build.

| Workload | Memory request | Notes |
|---|---|---|
| opensearch | 3 GB | Largest single consumer; single node, 1 shard, 0 replicas |
| openmetadata-server | 2 GB | JVM |
| superset | 1 GB | Celery worker and beat deferred to phase 3 |
| duckdb-replica | 1.5 GB | Bounded worker pool, see §6 |
| postgres | 1 GB | 4 databases: dagster, superset, openmetadata, app |
| dagster-webserver + daemon | 1 GB | |
| cube | 0.5 GB | No Cube Store, no pre-aggregations initially |
| hub-web + hub-api | 0.5 GB | |
| ingress, cert-manager, oauth2-proxy, graphql-proxy | 0.3 GB | |
| k3s system | 1 GB | |
| dbt build pod | 1.5 GB | Burst only, concurrency capped at 1 |

Set memory **limits** on every pod. An unbounded DuckDB query will otherwise take
down the node.

### Cost

| Item | Monthly |
|---|---|
| Hetzner CX53 + IPv4 | ~$33 |
| R2 (10 GB free tier), Cloudflare, hosted IdP, Grafana Cloud, Resend | $0 |
| Domain amortized | ~$1 |
| LLM inference | Variable — quota-gated |

---

## 5. Auth and authorization

One hosted OIDC provider (Clerk, Auth0, or WorkOS free tier). Self-hosted Keycloak
was considered and rejected: ~1 GB of RAM for no user-visible benefit at this scale.

Tokens carry a `tier` claim (`free` | `paid`).

| Surface | Authn | Authz |
|---|---|---|
| Hub app | OIDC session | Entitlement checks in hub-api against `tier` |
| Superset | OIDC via Flask-AppBuilder; embedded uses guest tokens | Role mapped from IdP group claim |
| OpenMetadata | Native OIDC | Default viewer role for all authenticated users |
| Dagster | oauth2-proxy gates the hostname | GraphQL allowlist proxy enforces read-only |
| Cube | Service token from hub-api | `queryRewrite` applies tier limits |

### Dagster hardening — non-negotiable

Dagster is publicly exposed. Its own read-only mode is a UI-level concern only;
the GraphQL proxy is what actually enforces it.

- **Allowlist, not denylist.** Permit read operations (`runsOrError`, `assetNodes`,
  `assetsLatestInfo`, `pipelineRunsOrError`, `instigationStatesOrError`) and reject
  everything else by default. A denylist silently opens up on every Dagster upgrade.
- **Scrub logs.** Run logs are world-readable. Add a redaction filter to the logging
  config. No secrets in tracebacks, no connection strings in dbt output.
- **No secrets in run config or tags.** These render in the UI. Reference Kubernetes
  secrets by env var name only.
- **Curate asset metadata.** Row counts are fine; sample rows and file paths are not.
- **Rate limit at Cloudflare.** The Dagster UI polls GraphQL continuously; a few
  hundred idle viewers generate real load on the daemon and Postgres.

---

## 6. Chatbot safety model

The agent has two MCP connections and no direct database access:

- **OpenMetadata MCP** (`{OM_URL}/mcp`) for discovery: what exists, who owns it,
  is it fresh, what does this term mean.
- **Cube MCP** for execution: select measures and dimensions from the semantic model.

Controls:

- No raw SQL tool is exposed to the agent. Ever.
- Hard row limit and statement timeout on every generated query.
- Monthly query quota per tier, enforced in hub-api before the agent is invoked.
- Generated query and result shape are always shown to the user. Clinicians will not
  trust an unattributed number, and they shouldn't.
- Every question, plan, and result logged to Postgres to build an eval set.

**Serving-side worker pool.** Route every query through a bounded pool. Cap concurrent
queries and their summed memory expectation; queue or reject excess rather than letting
each request start a full scan. Record queue wait, duration, peak memory, spill bytes,
input bytes, and output rows. This is both the OOM guard and the observability story.

### Ticket creation

Chatbot requests for new data sources or metrics open GitHub Projects issues.
Before creating: embed the request, similarity-search open issues, and upvote a
duplicate rather than filing a new one. Rate limit per user per month. Label by tier.

---

## 7. Monorepo layout

```
health-data-platform/
├── apps/
│   ├── web/                    # Next.js hub: landing, chat UI, embedded dashboards
│   └── api/                    # FastAPI: chat orchestration, entitlements, Stripe webhooks
│
├── data/
│   ├── ingestion/              # One module per source; typed clients, schema contracts
│   │   ├── openaq/
│   │   ├── cdc/
│   │   ├── openfda/
│   │   ├── cms/
│   │   └── who/
│   ├── dagster/
│   │   ├── definitions.py      # Single Definitions object
│   │   ├── assets/             # Grouped by domain
│   │   ├── jobs/
│   │   ├── schedules/
│   │   ├── sensors/
│   │   └── resources/          # R2, DuckDB, Cube, OpenMetadata, k8s clients
│   ├── dbt/
│   │   ├── models/
│   │   │   ├── staging/
│   │   │   ├── intermediate/
│   │   │   └── marts/
│   │   ├── tests/
│   │   └── dbt_project.yml
│   └── ml/
│       ├── anomaly/            # EARS C1-C3, Farrington, STL + robust z-score
│       ├── forecast/           # FluSight-style quantile forecasts, WIS scoring
│       └── eval/               # Baselines must beat naive before shipping
│
├── semantic/
│   └── cube/
│       ├── model/              # Cubes: measures, dimensions, joins
│       └── cube.js             # queryRewrite for tier limits
│
├── catalog/
│   └── openmetadata/
│       ├── sync/               # Cube meta -> Metric entities; dbt -> lineage
│       └── seed/               # Glossary terms, domains, custom properties
│
├── platform/
│   ├── k3s/
│   │   ├── base/               # Namespaces, ingress, cert-manager
│   │   ├── charts/             # Helm values per component
│   │   └── overlays/
│   ├── graphql-proxy/          # Dagster GraphQL allowlist proxy
│   └── scripts/                # Bootstrap, backup, restore
│
├── packages/
│   └── shared/                 # Shared Python config, types, logging with redaction
│
├── docs/
│   ├── ARCHITECTURE.md         # This file
│   ├── decisions/              # ADRs — see §9
│   └── runbook.md
│
└── .github/workflows/          # CI: lint, dbt parse, image build, deploy
```

Rationale for a monorepo: dbt models, Cube definitions, and OpenMetadata sync must
change together or metrics drift. A single PR that touches all three is the point.

---

## 8. Build order

Each milestone should be independently demoable. Do not start the next until the
previous is green for a week.

**M0 — Pipeline spine**
Two sources (OpenAQ, one CDC surveillance dataset). dbt against local DuckDB.
Dagster running the build. No auth, no UI, no catalog. Snapshot published to R2 and
pulled by a read replica. Goal: prove the publish-and-replicate loop stays green.

**M1 — Platform on k3s**
Provision the VM, k3s, Postgres, ingress, TLS. Deploy Dagster with oauth2-proxy and
the GraphQL allowlist proxy. Deploy Superset pointed at the read replica. Everything
public, still free, still no chatbot.

**M2 — Semantic layer and catalog**
Cube Core with the first five metrics. Repoint Superset at Cube. Deploy OpenMetadata
and the sync job so metrics and lineage appear in the catalog. Hub app shell with OIDC
and one embedded dashboard.

**M3 — Chatbot**
Read-only Q&A over Cube MCP plus OpenMetadata MCP. Log everything. Quota enforcement.
No dashboard creation, no ticket creation yet.

**M4 — Monetization**
Stripe, tier claims, quota tiers. Alerting via email. Dashboard generation from a
validated chart spec. GitHub ticket flow with dedup.

**M5 — ML**
Classical anomaly detection baselines first (EARS, Farrington), then compare an ML
approach against them. LLM writes the explanation; it never does the detection.
Forecasting scored against a naive baseline with WIS.

---

## 9. Constraints for the implementer

Things that will look like reasonable improvements and are not:

- **Do not add managed Kubernetes.** The control plane cost exceeds the total budget.
- **Do not adopt DuckDB's Quack client-server protocol yet.** It is promoted to stable
  in DuckDB 2.0, which had no release candidate date as of August 2026. Evaluate it,
  document the evaluation, but keep the serving path on publish-and-replicate.
- **Do not use the dbt Semantic Layer's serving APIs.** They require dbt Cloud at
  roughly $100/user/month. MetricFlow itself is open source; the serving tier is not.
- **Do not give the agent a raw SQL tool** on the grounds that it would be more flexible.
- **Do not split Postgres into separate instances** for isolation.
- **Do not add HA, multi-replica, or autoscaling.** Single replica is the design.
- **Do not use browser localStorage or sessionStorage** in embedded dashboard widgets.
- **Do not put anything in the Dagster UI you would not put on a public webpage.**

---

## 10. Open decisions

Flag these rather than deciding unilaterally:

1. **Hosting region.** Germany/Finland is cheapest; US (Ashburn) has better latency for
   the likely audience at higher cost. Needs a price check.
2. **Positioning and disclaimers.** "For clinicians" plus AI-generated insight edges
   toward clinical decision support. Framing must stay explicitly population-level and
   non-clinical, with disclaimers on every generated output. Worth a lawyer's hour
   before launch.
3. **Free-tier chat quota.** Starting proposal: 20 questions/month free, 500 paid.
4. **Snapshot retention.** Starting proposal: keep 14 daily snapshots in R2.
5. **Which persona leads.** Clinicians and business users want different defaults from
   the same data; this affects the metrics modeled first.

---

## 11. Backups

The warehouse is fully reproducible from public sources plus git, so it is not backed up.

Backed up: nightly `pg_dump` of the single Postgres instance to R2, covering Dagster run
history, Superset dashboards, OpenMetadata annotations, and user records. Restore is
documented in `docs/runbook.md` and must be tested before M4.

Skip provider snapshot backups — roughly 20% of server cost for data that rebuilds itself.
