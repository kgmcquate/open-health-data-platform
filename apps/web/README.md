# hub-web

Next.js app for the platform hub: landing page, chat UI, embedded Superset
dashboards, and links to every tool (Dagster, OpenMetadata, Superset standalone).

Not yet scaffolded. Bring up with the App Router:

```bash
cd apps/web
npx create-next-app@latest . --ts --app --eslint --no-tailwind --no-src-dir --import-alias "@/*"
```

Constraints:
- **No `localStorage` / `sessionStorage` in embedded dashboard widgets** (ARCHITECTURE.md §9).
- Dashboards are embedded via short-lived Superset guest tokens minted by `hub-api`.
- OIDC session handled server-side; the `tier` claim drives what the UI offers.
