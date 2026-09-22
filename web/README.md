# web/ — standard UI (optional)

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

- `src/lib/types.ts` — wire types, written by hand from `contracts/openapi/v0.1.yaml` and `contracts/schemas/`.
- `src/lib/api.ts` — one function per v0.1 HTTP endpoint; errors surface as `ApiError` with the RFC 9457 `Problem`.
- `src/lib/ws.ts` — lab terminal WebSocket client (`/labs/{lab_instance_id}/terminal`).
- `src/lib/layout.ts` — minimal interpreter of the pack layout document. v0.1 fixes no generic layout schema (ADR-0012); it reads only `main.modes/default_mode/components`, `side.component`, `bottom.component` and ignores unknown keys.
- `src/components/` — workspace (mission, xterm.js terminal, sequence visualization with reality mapping, concept side pane, tutor chat with highlight quotes, timeline).

The learner id and current session id are kept in `localStorage`; everything
else (attempt, lab, highlights, chat, timeline, open content, selected
visualization step) is restored from the API on reload.
