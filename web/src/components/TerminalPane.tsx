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
} from "@/lib/ws";

export interface TerminalHandle {
  /** Types a line into the lab PTY and presses Enter (AC-C3). */
  runLine: (line: string) => void;
  focus: () => void;
}

interface Props {
  lab: LabState;
  /** Stored output of this lab from the timeline, replayed before live output (resume). */
  storedOutput: TerminalOutputPayload[];
  onLabStatus: (p: LabStatusMessage["payload"]) => void;
  onServerMessage: (m: ServerMessage) => void;
  /** Filled with the imperative handle (a plain prop so it survives next/dynamic). */
  handleRef: RefObject<TerminalHandle | null>;
}

/** xterm.js wired to the lab terminal WebSocket (AC-B1). One socket per LabInstance. */
export default function TerminalPane({
  lab,
  storedOutput,
  onLabStatus,
  onServerMessage,
  handleRef,
}: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const socketRef = useRef<LabSocket | null>(null);
  const termRef = useRef<Terminal | null>(null);
  const [socketState, setSocketState] = useState<SocketState>("connecting");
  const [exited, setExited] = useState<string | null>(null);

  // Latest callbacks without reconnecting the socket.
  const cb = useRef({ onLabStatus, onServerMessage, storedOutput });
  useEffect(() => {
    cb.current = { onLabStatus, onServerMessage, storedOutput };
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

    // Replay what this lab already printed (stored terminal.output events),
    // then dedupe live chunks by `sequence` (ws/payloads/terminal.output.json).
    let lastSeq = -1;
    const replay = cb.current.storedOutput
      .filter((o) => o.lab_instance_id === labId)
      .sort((a, b) => a.sequence - b.sequence);
    for (const chunk of replay) {
      term.write(decodeOutput(chunk));
      lastSeq = Math.max(lastSeq, chunk.sequence);
    }

    const socket = new LabSocket(terminalPath, {
      onStateChange: (s) => setSocketState(s),
      onMessage: (m) => {
        if (m.type === "terminal.output") {
          if (m.payload.sequence <= lastSeq) return;
          lastSeq = m.payload.sequence;
          term.write(decodeOutput(m.payload));
        } else if (m.type === "terminal.exit") {
          socket.stopReconnect();
          setExited(
            m.payload.signal ?? (m.payload.exit_code === null ? "exited" : `exit ${m.payload.exit_code}`),
          );
        } else if (m.type === "lab.status") {
          if (m.payload.status === "resetting" || m.payload.status === "error") socket.stopReconnect();
          cb.current.onLabStatus(m.payload);
        }
        cb.current.onServerMessage(m);
      },
    });
    socketRef.current = socket;
    socket.connect();

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
