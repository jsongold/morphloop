// Hand-written client for the lab terminal WebSocket
// (`/labs/{lab_instance_id}/terminal`, contracts/schemas/ws/).
//
// One connection per LabInstance. After a lab reset the server closes the
// socket; the caller connects a new LabSocket to the replacement instance.

import { terminalSocketUrl } from "./api";
import type { LabInstanceId, LabStatus, TerminalId } from "./types";

export const WS_PROTOCOL_VERSION = 1;

interface Envelope<T extends string, P> {
  type: T;
  protocol_version: number;
  correlation_id: string | null;
  idempotency_key: string | null;
  payload: P;
}

// client -> server
export type TerminalInputMessage = Envelope<"terminal.input", { data: string }>;
export type TerminalResizeMessage = Envelope<"terminal.resize", { cols: number; rows: number }>;
export type PingMessage = Envelope<"ping", Record<string, never>>;
export type ClientMessage = TerminalInputMessage | TerminalResizeMessage | PingMessage;

// server -> client
export type TerminalOutputMessage = Envelope<
  "terminal.output",
  { data: string; encoding: "utf-8" | "base64"; sequence: number }
>;
export type TerminalExitMessage = Envelope<
  "terminal.exit",
  { exit_code: number | null; signal: string | null }
>;
export type LabStatusMessage = Envelope<
  "lab.status",
  {
    lab_instance_id: LabInstanceId;
    status: LabStatus;
    terminal_id: TerminalId | null;
    reason?: string;
  }
>;
export type EventAppendedMessage = Envelope<
  "event.appended",
  {
    event_id: string;
    event_type: string;
    event_version: number;
    position: number;
    occurred_at: string;
    attempt_id: string | null;
    activity_definition_id: string | null;
  }
>;
export type ErrorMessage = Envelope<
  "error",
  { code: string; message: string; details?: Record<string, unknown> }
>;
export type PongMessage = Envelope<"pong", Record<string, never>>;
export type ServerMessage =
  | TerminalOutputMessage
  | TerminalExitMessage
  | LabStatusMessage
  | EventAppendedMessage
  | ErrorMessage
  | PongMessage;

export type SocketState = "connecting" | "open" | "reconnecting" | "closed";

export interface LabSocketHandlers {
  onMessage?: (msg: ServerMessage) => void;
  onStateChange?: (state: SocketState, closeCode?: number) => void;
}

const SERVER_TYPES = new Set<string>([
  "terminal.output",
  "terminal.exit",
  "lab.status",
  "event.appended",
  "error",
  "pong",
]);

function envelope<T extends string, P>(type: T, payload: P): Envelope<T, P> {
  return {
    type,
    protocol_version: WS_PROTOCOL_VERSION,
    correlation_id: null,
    idempotency_key: null,
    payload,
  };
}

/** Decodes a terminal.output chunk to bytes for xterm (handles base64 chunks). */
export function decodeOutput(p: { data: string; encoding: "utf-8" | "base64" }): string | Uint8Array {
  if (p.encoding === "utf-8") return p.data;
  const bin = atob(p.data);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/** Reconnect delays after an unexpected close; the socket gives up after the last one. */
const RECONNECT_DELAYS_MS = [500, 1000, 2000, 4000, 8000, 8000, 8000, 8000];

export class LabSocket {
  private ws: WebSocket | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private retries = 0;
  private reconnect = true;

  constructor(
    readonly terminalPath: string,
    private readonly handlers: LabSocketHandlers,
  ) {}

  connect(): void {
    this.handlers.onStateChange?.("connecting");
    const ws = new WebSocket(terminalSocketUrl(this.terminalPath));
    this.ws = ws;
    ws.onopen = () => {
      this.retries = 0;
      this.handlers.onStateChange?.("open");
      this.pingTimer = setInterval(() => this.send(envelope("ping", {})), 25_000);
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string") return;
      let msg: unknown;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (
        typeof msg === "object" &&
        msg !== null &&
        SERVER_TYPES.has((msg as { type?: string }).type ?? "")
      ) {
        this.handlers.onMessage?.(msg as ServerMessage);
      }
    };
    ws.onclose = (ev) => {
      this.clearPing();
      this.ws = null;
      // An api restart drops the socket; the lab survives it, so reattach.
      const delay = this.reconnect ? RECONNECT_DELAYS_MS[this.retries] : undefined;
      if (delay === undefined) {
        this.handlers.onStateChange?.("closed", ev.code);
        return;
      }
      this.retries += 1;
      this.handlers.onStateChange?.("reconnecting", ev.code);
      this.retryTimer = setTimeout(() => this.connect(), delay);
    };
  }

  /** Stops reconnecting, for a lab that is gone (reset, error, shell exited). */
  stopReconnect(): void {
    this.reconnect = false;
  }

  sendInput(data: string): void {
    if (data.length > 0) this.send(envelope("terminal.input", { data }));
  }

  sendResize(cols: number, rows: number): void {
    this.send(envelope("terminal.resize", { cols, rows }));
  }

  close(): void {
    this.reconnect = false;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    this.clearPing();
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      ws.onmessage = null;
      ws.onclose = null;
      ws.close();
    }
  }

  private send(msg: ClientMessage): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  private clearPing(): void {
    if (this.pingTimer) clearInterval(this.pingTimer);
    this.pingTimer = null;
  }
}

/**
 * Renders a pack argv as one terminal line. The line is typed into the
 * learner's sandbox PTY (never a host shell, ADR-0009); each argument is
 * single-quoted when it contains anything but safe characters.
 */
export function argvToCommandLine(argv: string[]): string {
  return argv
    .map((a) => (/^[A-Za-z0-9_@%+=:,./-]+$/.test(a) ? a : `'${a.replace(/'/g, `'\\''`)}'`))
    .join(" ");
}
