# contracts/schemas/websocket/

JSON Schema for the WebSocket connection that carries (a) the xterm.js
terminal ↔ backend PTY bridge for a learner's lab container and (b)
server→client push notifications, per `docs/ARCHITECTURE.md`'s API sketch
(`WS /labs/{lab_id}/terminal`). See `contracts/README.md` for the overall
`contracts/` layout and `$id` convention, which this directory follows.

## Layout

- `envelope/fields.json` — shared envelope fields (open; never validate
  against it directly).
- `envelope/message.json` — a full message: envelope fields + payload.
  Closed. Validate real messages against this.
- `dispatch.json` — maps `type` to its payload schema and applies per-type
  envelope rules (fixed `idempotency_key` requirement for message types
  that cause a stored event). Mirrors
  `contracts/schemas/events/dispatch.json`.
- `payloads/<type>.json` — one closed payload schema per message type.

Deviation from `contracts/schemas/events/`: there is no `append.json` vs
`stored.json` split (WebSocket messages are not themselves persisted) and
no per-type `event_version`/major-version subdirectory under `payloads/`.
Versioning here is a single envelope field, `protocol_version`, covering
the whole message catalog at once: a breaking change to any message type
bumps it. This is deliberately different from the event contract, where
each stored payload is individually versioned forever — WebSocket messages
are live/ephemeral, not an immutable append-only log, so there is nothing
to keep upcasting old readers for.

## Message catalog

Client → server:

- `terminal.input` — keystroke/input data to write to the PTY.
- `terminal.resize` — cols/rows changed (xterm.js fit).
- `ping` — liveness check.

Server → client:

- `terminal.output` — one chunk of PTY output.
- `terminal.exit` — the PTY process for this connection ended.
- `lab.status` — the lab instance's lifecycle state changed.
- `event.appended` — a new event was appended to the session's timeline
  (any type except `terminal.output`; see below).
- `error` — a connection-level error.
- `pong` — reply to `ping`.

## Key decisions

### Authentication: `?ticket=` (v0.4)

A browser cannot send an `Authorization` header on a WebSocket upgrade, and
a bearer token in the URL would leak into logs. So a socket authenticates
with a ticket: the client calls `POST /v2/auth/socket-tickets` with its
bearer token, gets `{ticket, expires_at}`, and opens the socket with
`?ticket=<ticket>`. The ticket is single-use, short-lived and bound to the
caller; a missing, unknown, expired or already-used ticket is rejected
before the upgrade completes (HTTP 401, Problem `unauthorized`). No message
in the catalog carries a token.

### Connection scoping: per lab instance, not per attempt

The connection is opened at `/labs/{lab_instance_id}/terminal`
(`docs/ARCHITECTURE.md`), so it is scoped to one `LabInstance`, not to an
`ActivityAttempt`. v0.1 gives each lab instance exactly one terminal;
`terminal_id` is assigned server-side and delivered to the client in the
first `lab.status` message (`status: "ready"`) rather than being a path
parameter, so message payloads never need to repeat `lab_instance_id` —
the URL already scopes the whole connection. A lab reset (`lab.reset`)
replaces the `LabInstance`, so the client must reconnect (or the server
must close the socket) rather than reuse a connection across a reset;
`lab.status` announces this transition (`resetting` → new connection).

### Text vs base64 for terminal data

- `terminal.input` (client → server) is UTF-8 text only, no `encoding`
  field. xterm.js's `onData` callback always hands the browser a complete,
  valid Unicode string — the browser assembles keystrokes (including IME
  composition) into full scalar values before calling it, so there is no
  way for a WebSocket text frame to arrive mid-codepoint on this side.
- `terminal.output` (server → client) carries `data` **and** `encoding`
  (`"utf-8"` or `"base64"`), mirroring
  `contracts/schemas/events/payloads/terminal.output/1.json` exactly. A
  PTY streams raw bytes with no regard for UTF-8 boundaries, so a chunk
  boundary can legally split a multibyte sequence; when that happens the
  bridge must not emit invalid UTF-8 as a JSON string, so it base64-encodes
  that chunk. Reusing the event payload's `data`/`encoding` shape means the
  terminal bridge can push the exact bytes it also appends as the
  `terminal.output` event's payload, with no re-encoding between the two.

### `event.appended` is a thin notification, not the stored event

`event.appended` carries `event_id`, `event_type`, `event_version`,
`position`, `occurred_at` and attempt scoping — never the full stored event
(`contracts/schemas/events/envelope/stored.json`). Two reasons:

1. **Terminal output already has its own direct, high-frequency push**
   (`terminal.output`). Announcing the same content again through
   `event.appended` would double-deliver every chunk over the same
   connection; `event.appended`'s payload schema forbids
   `event_type: "terminal.output"` to make this a checked contract rule,
   not just a convention.
2. **Keeping the generic path thin** avoids growing the WebSocket envelope
   to the size of arbitrary event payloads (which, per ADR-0016, may
   themselves grow to hold large data before a claim-check pattern is
   introduced) and avoids ever pushing a payload that a future event type
   should not expose over this channel (e.g. content generation records,
   ADR-0014). A client that needs the full payload calls
   `GET /sessions/{id}/timeline` — the same source of truth used on
   reload/resume (`docs/UX.md` Persistence).

### Idempotency and correlation

Every message carries `correlation_id` and `idempotency_key` in the
envelope (always present, nullable — mirroring
`contracts/schemas/events/envelope/fields.json`'s treatment of
`causation_id`/`correlation_id`/`idempotency_key` rather than making them
optional-by-omission).

- `idempotency_key` is optional/nullable for every message type in v0.1;
  `dispatch.json` has no branch that forces it non-null. In particular,
  neither `terminal.input` nor `terminal.output` map 1:1 onto a stored
  event, so forcing a key at the WebSocket layer would be wrong:
  - `terminal.input` carries raw keystrokes. Many `terminal.input`
    messages (buffered on Enter, say) can precede one `terminal.command`
    event; the terminal bridge, not the client, decides when a command is
    complete and assigns that event's `idempotency_key` itself (ADR-0008).
    A per-keystroke key on the WS message would not correspond to
    anything.
  - `terminal.output` is the server->client stream push, not the stored
    event. The underlying `terminal.output` event always carries its own
    server-assigned `idempotency_key` independently of this push. A sender
    may echo that key here so the client can dedupe a chunk redelivered
    after a reconnect, but the contract does not require it.
  - Other message types either cause no stored event (`terminal.resize`,
    `ping`/`pong`) or are already self-identifying via `event_id`
    (`event.appended`) or not yet backed by an event at all
    (`terminal.exit`, `lab.status`, `error`).
  A future message type that does map 1:1 onto a stored event may still
  add a `dispatch.json` branch forcing `idempotency_key` non-null, the way
  `contracts/schemas/events/dispatch.json` does for learner/terminal
  events; v0.1 just has no such type yet.
- `correlation_id` is left to the caller's judgement: a client may set it
  on `ping` and expect it echoed on the matching `pong`; a server push may
  echo the correlation_id of the event or REST call that caused it (e.g. a
  `lab.status` push following a `POST` that reset the lab).

### `type` is a flat enum, not a required dotted pattern

`envelope/fields.json` only requires `type` to be a non-empty string; the
closed vocabulary lives in `dispatch.json`'s `enum`, matching
`contracts/schemas/events/dispatch.json`'s approach for `event_type`. Most
message types reuse the event catalog's dotted convention
(`terminal.input`, `lab.status`, `event.appended`) so a reader can
recognize the analogous event type, but `ping`/`pong`/`error` are plain
words: they are connection-level, not modeled after any event type, and
forcing a dot into them would not add information.
