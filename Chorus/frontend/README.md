# Chorus Frontend

Next.js 16 (App Router) + React 19 UI for the Chorus distributed LLM swarm.

The one-click **Deploy to Vercel** button lives at the
[repo root README](../../README.md). Vercel's Root Directory **must** be set to
`Chorus/frontend` since this app is nested inside a monorepo.

## Local development

```bash
npm install
npm run dev
```

Then open http://localhost:3000. For LAN multi-device testing, set
`NEXT_ALLOWED_DEV_ORIGINS` in `.env.local` (see below).

## Environment variables

Copy [`.env.example`](./.env.example) to `.env.local` and fill in what you
need. Summary:

| Key                               | Scope    | Purpose                                                                   |
| --------------------------------- | -------- | ------------------------------------------------------------------------- |
| `NEXT_PUBLIC_ORCHESTRATOR_BASE_URL` | public   | HTTP(S) base of the Python signaling backend. Users can override at `/join`. |
| `NEXT_PUBLIC_DISTLM_CHAT_MODEL`   | public   | Default suggested model name on the join page (e.g. `qwen2.5:0.5b`).      |
| `NEXT_PUBLIC_AGENT_BASE_URL`      | public   | Default single Ollama/OpenAI completion base used in mock flows.          |
| `NEXT_PUBLIC_AGENT_BASE_URLS`     | public   | Comma-separated list version of the above.                                |
| `NEXT_LAN_CHAT_PROXY`             | server   | Set `0` to disable the `/api/local-chat-completions` LAN proxy route.     |
| `NEXT_ALLOWED_DEV_ORIGINS`        | dev only | LAN hostnames/origins allowed by Next's HMR check. Ignored on Vercel.     |
| `NEXT_CHAT_PROXY_EXTRA_HOSTS`     | server   | Extra allowlisted hostnames for the LAN chat proxy.                       |

## Deploying

See the repo-root [`README.md`](../../README.md) for the Vercel deploy button
and instructions. In short: push to GitHub, click Deploy, set Root Directory
to `Chorus/frontend`.

## Scripts

- `npm run dev` - start the Next dev server
- `npm run build` - production build
- `npm start` - run the production build locally
- `npm run lint` - ESLint
