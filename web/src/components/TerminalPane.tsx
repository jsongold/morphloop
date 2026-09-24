"use client";

import "@xterm/xterm/css/xterm.css";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { useEffect, useImperativeHandle, useRef, useState, type RefObject } from "react";
import type { LabState, TerminalOutputPayload } from "@/lib/types";
import {
  decodeOutput,
  LabSocket,
  type LabStatusMessage,
  type ServerMessage,
  type SocketState,
} from "@/lib/websocket";

export interface TerminalHandle {
  /** Types a line into the lab PTY and presses Enter (AC-C3). */
  runLine: (line: string) => void;
  focus: () => void;
}

/** A terminal selection reported to the parent for highlighting (content.highlighted v2, kind terminal). */
export interface TerminalSelection {
  text: string;
  terminal_id: string;
  first_sequence: number;
  last_sequence: number;
  anchorRect: { top: number; right: number };
}

interface Props {
  lab: LabState;
  /** Stored output of this lab from the timeline, replayed before live output (resume). */
  storedOutput: TerminalOutputPayload[];
  onLabStatus: (p: LabStatusMessage["payload"]) => void;
  onServerMessage: (m: ServerMessage) => void;
  /** Filled with the imperative handle (a plain prop so it survives next/dynamic). */
  handleRef: RefObject<TerminalHandle | null>;
  /** Fired with the current terminal selection, or null when it collapses. */
  onSelection?: (sel: TerminalSelection | null) => void;
}

/** xterm.js wired to the lab terminal WebSocket (AC-B1). One socket per LabInstance. */
export default function TerminalPane({
  lab,
  storedOutput,
  onLabStatus,
  onServerMessage,
  handleRef,
  onSelection,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const socketRef = useRef<LabSocket | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const [socketState, setSocketState] = useState<SocketState>("connecting");
  const [exited, setExited] = useState<string | null>(null);

  // Latest callbacks without reconnecting the socket.
  const cb = useRef({ onLabStatus, onServerMessage, storedOutput, onSelection });
  useEffect(() => {
    cb.current = { onLabStatus, onServerMessage, storedOutput, onSelection };
  });
  // Latest lab for fallback reads inside the socket effect (no re-connect).
  const labRef = useRef(lab);
  useEffect(() => {
    labRef.current = lab;
  });

  useImperativeHandle(handleRef, () => ({
    runLine: (line: string) => {
      socketRef.current?.sendInput(line + "\r");
      termRef.current?.focus();
    },
    focus: () => termRef.current?.focus(),
  }));

  const labId = lab.lab_instance_id;
  const terminalPath = lab.terminal_path;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const term = new Terminal({
      cursorBlink: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      fontSize: 13,
      theme: { background: "#1a3a1a", foreground: "#fff9a0", cursor: "#b8cd92", selectionBackground: "#557744" },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    termRef.current = term;
    setExited(null);

    // Map absolute buffer rows to the terminal.output `sequence` that wrote
    // them, so a selection can be reported as an inclusive sequence range.
    const rowSeq = new Map<number, number>();
    let lastWritten = -1;
    const writeChunk = (chunk: { data: string; encoding: "utf-8" | "base64"; sequence: number }) => {
      const seq = chunk.sequence;
      lastWritten = seq;
      // xterm writes asynchronously, so capture the cursor row before the
      // write and assign the range once the chunk has actually been parsed
      // (its callback fires after this chunk is processed).
      const start = term.buffer.active.baseY + term.buffer.active.cursorY;
      term.write(decodeOutput(chunk), () => {
        try {
          const buf = term.buffer.active;
          const end = Math.min(buf.baseY + buf.cursorY, Math.max(start, buf.length - 1));
          for (let r = start; r <= end; r++) rowSeq.set(r, seq);
        } catch {
          rowSeq.set(start, seq);
        }
      });
    };

    // Replay what this lab already printed (stored terminal.output events),
    // then dedupe live chunks by `sequence` (websocket/payloads/terminal.output.json).
    // A sequence counts within one terminal: every connection opens a new
    // terminal whose sequence restarts at 0, named by lab.status.terminal_id.
    const lastSeq = new Map<string, number>();
    let terminalId = labRef.current.terminal_id ?? "";
    const replay = cb.current.storedOutput
      .filter((o) => o.lab_instance_id === labId)
      .sort((a, b) => a.sequence - b.sequence);
    for (const chunk of replay) {
      writeChunk(chunk);
      lastSeq.set(chunk.terminal_id, Math.max(lastSeq.get(chunk.terminal_id) ?? -1, chunk.sequence));
    }

    const socket = new LabSocket(terminalPath, {
      onStateChange: (s) => setSocketState(s),
      onMessage: (m) => {
        if (m.type === "terminal.output") {
          if (m.payload.sequence <= (lastSeq.get(terminalId) ?? -1)) return;
          lastSeq.set(terminalId, m.payload.sequence);
          writeChunk(m.payload);
        } else if (m.type === "terminal.exit") {
          socket.stopReconnect();
          setExited(
            m.payload.signal ?? (m.payload.exit_code === null ? "exited" : `exit ${m.payload.exit_code}`),
          );
        } else if (m.type === "lab.status") {
          if (m.payload.terminal_id) terminalId = m.payload.terminal_id;
          if (m.payload.status === "resetting" || m.payload.status === "error") socket.stopReconnect();
          cb.current.onLabStatus(m.payload);
        }
        cb.current.onServerMessage(m);
      },
    });
    socketRef.current = socket;
    socket.connect();

    // Report the current selection (or its collapse) so the workspace can
    // offer the same highlight toolbar/chat as DOM text. xterm selection
    // internals vary by version; every internal read is guarded.
    const emitSelection = () => {
      const report = cb.current.onSelection;
      if (!report) return;
      const anchorRectOf = (): { top: number; right: number } => {
        const rect = hostRef.current?.getBoundingClientRect();
        return rect ? { top: rect.top, right: rect.right } : { top: 0, right: 0 };
      };
      let text: string;
      try {
        text = term.getSelection().trim();
      } catch {
        report(null);
        return;
      }
      if (!text) {
        report(null);
        return;
      }
      try {
        const buf = term.buffer.active;
        let startRow: number;
        let endRow: number;
        try {
          const pos = term.getSelectionPosition();
          if (pos) {
            startRow = pos.start.y - 1;
            endRow = pos.end.y - 1;
          } else {
            startRow = endRow = buf.baseY + buf.cursorY;
          }
        } catch {
          startRow = endRow = buf.baseY + buf.cursorY;
        }
        const seqs: number[] = [];
        for (let r = startRow; r <= endRow; r++) {
          const s = rowSeq.get(r);
          if (s !== undefined) seqs.push(s);
        }
        const first = seqs.length > 0 ? Math.min(...seqs) : (lastWritten >= 0 ? lastWritten : 0);
        const last = seqs.length > 0 ? Math.max(...seqs) : (lastWritten >= 0 ? lastWritten : 0);
        report({
          text,
          terminal_id: terminalId || labRef.current.terminal_id || "",
          first_sequence: first,
          last_sequence: last,
          anchorRect: anchorRectOf(),
        });
      } catch {
        // xterm internals changed shape: degrade to the last written sequence.
        report({
          text,
          terminal_id: terminalId || labRef.current.terminal_id || "",
          first_sequence: lastWritten >= 0 ? lastWritten : 0,
          last_sequence: lastWritten >= 0 ? lastWritten : 0,
          anchorRect: anchorRectOf(),
        });
      }
    };
    const selChange = term.onSelectionChange(emitSelection);

    const input = term.onData((d) => socket.sendInput(d));
    const resize = term.onResize(({ cols, rows }) => socket.sendResize(cols, rows));
    const doFit = () => {
      try {
        fit.fit();
      } catch {
        // host not measurable yet
      }
    };
    const ro = new ResizeObserver(doFit);
    ro.observe(host);
    doFit();

    return () => {
      ro.disconnect();
      input.dispose();
      resize.dispose();
      selChange.dispose();
      socket.close();
      socketRef.current = null;
      term.dispose();
      termRef.current = null;
    };
  }, [labId, terminalPath]);

  // Send the current size once the socket opens.
  useEffect(() => {
    const t = termRef.current;
    if (socketState === "open" && t) socketRef.current?.sendResize(t.cols, t.rows);
  }, [socketState]);

  return (
    <div className="terminal">
      <div className="terminal-status">
        <span>lab {lab.status}</span>
        <span>socket {socketState}</span>
        {exited && <span>shell {exited}</span>}
      </div>
      {socketState === "reconnecting" && (
        <div className="terminal-banner" role="status">
          Connection to the lab was lost. Reconnecting…
        </div>
      )}
      {socketState === "closed" && !exited && (
        <div className="terminal-banner error" role="alert">
          Disconnected from the lab. Reload the page to reconnect.
        </div>
      )}
      <div
        className="terminal-host"
        ref={hostRef}
        aria-label="Lab terminal"
        onClick={() => termRef.current?.focus()}
      />
    </div>
  );
}
