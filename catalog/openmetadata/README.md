# OpenMetadata sync + seed

Human-browsable catalog surface (ARCHITECTURE.md §2). Also serves the discovery
MCP the chatbot uses to answer "what exists, who owns it, is it fresh, what does
this term mean" (§6).

- `sync/` — jobs run from the dbt build pod after tests pass (§3):
  - dbt manifest -> table + column lineage
  - Cube model -> Metric entities
  - freshness / row counts -> asset metadata (row counts OK; sample rows and
    file paths are NOT — §5)
- `seed/` — glossary terms, domains, custom properties. Checked in, applied
  idempotently.
