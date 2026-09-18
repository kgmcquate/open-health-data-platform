# hub-web

Next.js app for the platform hub: landing page, chat UI, and links to every
tool (Dagster, OpenMetadata).

Not yet scaffolded. Bring up with the App Router:

```bash
cd apps/web
npx create-next-app@latest . --ts --app --eslint --no-tailwind --no-src-dir --import-alias "@/*"
```

Constraints:
- Dashboards are authored in chat (ADR-0025), not a standalone BI app.
- OIDC session handled server-side; the `tier` claim drives what the UI offers.
