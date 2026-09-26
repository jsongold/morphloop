# apps/swe/web/ — standard UI (optional)

The SDK's minimal learner UI (ADR-0015). It talks to the harness only through
the HTTP/WebSocket contract in `contracts/` with a hand-written client
(ADR-0017); it never imports harness code. Any custom UI that follows the same
contract can replace it.

## Run

```bash
pnpm install
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 pnpm dev   # default API URL shown
```

Checks: `pnpm typecheck && pnpm lint && pnpm build`.

## Layout

- `src/app/v2/page.tsx` — the workspace; `/` redirects to `/v2`.
- `src/v2/` — hand-written client for `contracts/openapi/v0.2/` (`api.ts`, `types.ts`), workspace state, and one directory per pane under `panes/`.
- `src/components/TerminalPane.tsx` — xterm.js terminal used by the artifact pane.
- `src/lib/websocket.ts` — lab terminal WebSocket client (`contracts/schemas/websocket/`); `src/lib/types.ts` holds its wire types.

The session and workspace ids live in the URL query; everything else is
restored from the API on reload.
