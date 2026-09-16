# hub-web

Next.js app for the platform hub: landing page, chat UI, and links to every
tool (Dagster, OpenMetadata, Streamlit dashboards).

Not yet scaffolded. Bring up with the App Router:

```bash
cd apps/web
npx create-next-app@latest . --ts --app --eslint --no-tailwind --no-src-dir --import-alias "@/*"
```

Constraints:
- Dashboards are Streamlit, linked as a standalone destination — not embedded
  (ADR-0015, no guest-token equivalent exists for Streamlit).
- OIDC session handled server-side; the `tier` claim drives what the UI offers.
