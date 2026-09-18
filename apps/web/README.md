# @ohdp/web

The platform hub: landing page, news, data-source explorer, curated plots and
literature, and the AI chatbot UI.

## Stack

- **Vite + React 19 + TypeScript** — SPA, client-side routing (react-router).
- **TailwindCSS v4 + daisyUI v5** — styling. Themes are defined once in
  `src/index.css` (`ohdp` light / `ohdp-dark`); re-skin the whole app by
  editing those two blocks. The navbar theme picker lists `src/theme.ts`.
- **assistant-ui** — chat primitives (`@assistant-ui/react`,
  `@assistant-ui/react-markdown`), wired to the backend over SSE by
  `src/chat/runtime.ts`.
- **vega-embed** — renders curated plots.

## Develop

```bash
npm install
npm run dev          # http://localhost:5173, proxies /api and /auth to :8000
```

Run hub-api alongside it (`uv run uvicorn hub_api.main:app --reload` in
`apps/api`). Sign-in is built into hub-api (OIDC, `hub_api.auth`) — set
`OHDP_OIDC_CLIENT_ID`, `OHDP_OIDC_CLIENT_SECRET`, `OHDP_SESSION_SECRET_KEY`
and `OHDP_HUB_BASE_URL` to enable it; without those the site works but the
chat page asks you to sign in.

## Build

```bash
npm run build        # type-checks, then emits static files to dist/
```

## Conventions

- Pages are thin; data comes from `src/lib/api.ts` via `src/lib/useFetch.ts`.
- The chat backend owns the agent, tools, quota and token accounting; the
  client only renders the SSE event stream (`src/chat/runtime.ts`).
- Chat/Plots/Literature are lazy-loaded so vega and assistant-ui stay out of
  the landing bundle.

