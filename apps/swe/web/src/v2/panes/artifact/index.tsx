"use client";

// Owned by the artifact pane PR; replace this directory. Keep the exports
// below (src/v2/panes/types.ts): the reader mounts <ArtifactSlot/> for every
// `::artifact{type ref}` directive, and this module registers a renderer per
// artifact type (`registerArtifactRenderer("lab", LabView)`) at module scope.

import { useState, type ReactNode } from "react";
import type { ArtifactDirective } from "../types";
import { useWorkspace } from "@/v2/state";
import { ApiError, post } from "@/v2/api";

/** Returns the element for one directive, e.g. `(d) => <LabView directive={d} />`. */
export type ArtifactRenderer = (directive: ArtifactDirective) => ReactNode;

const renderers = new Map<string, ArtifactRenderer>();

export function registerArtifactRenderer(type: string, renderer: ArtifactRenderer): void {
  renderers.set(type, renderer);
}

/** Render slot for one artifact directive; a placeholder until its type has a renderer. */
export function ArtifactSlot({ directive }: { directive: ArtifactDirective }) {
  const render = renderers.get(directive.type);
  if (render) return render(directive);
  return (
    <div className="muted" data-artifact-type={directive.type} data-artifact-ref={directive.ref}>
      artifact {directive.type}:{directive.ref} (no renderer registered)
    </div>
  );
}

interface StartedArtifact {
  artifact_id: string;
  status: "running" | "stopped";
}

type LabState =
  | { status: "idle" }
  | { status: "starting" }
  | { status: "started"; artifact: StartedArtifact }
  | { status: "error"; message: string };

/**
 * Minimal `lab` renderer: a button that starts the pack artifact spec via
 * `POST /ws/{ws_id}/artifacts` (contracts/openapi/v0.2/paths/artifact.yaml).
 * The lab terminal itself is the bottom `ArtifactPane` slot, owned by the
 * artifact pane PR and not yet implemented.
 */
function LabView({ directive }: { directive: ArtifactDirective }) {
  const { ws } = useWorkspace();
  const [state, setState] = useState<LabState>({ status: "idle" });
  const start = () => {
    if (!ws) return;
    setState({ status: "starting" });
    post<StartedArtifact>(`/ws/${ws.ws_id}/artifacts`, { spec_id: directive.ref }).then(
      ({ body }) => setState({ status: "started", artifact: body }),
      (e: unknown) => setState({ status: "error", message: e instanceof ApiError ? e.message : String(e) }),
    );
  };
  if (state.status === "started") {
    return (
      <p className="muted" data-artifact-id={state.artifact.artifact_id}>
        Lab started ({state.artifact.status}).
      </p>
    );
  }
  return (
    <div data-artifact-type={directive.type} data-artifact-ref={directive.ref}>
      <button type="button" onClick={start} disabled={state.status === "starting" || !ws}>
        {state.status === "starting" ? "Starting…" : "Start lab"}
      </button>
      {state.status === "error" && (
        <p className="error" role="alert">
          {state.message}
        </p>
      )}
    </div>
  );
}

registerArtifactRenderer("lab", (directive) => <LabView directive={directive} />);

/** Bottom slot: the lab terminal area. */
export default function ArtifactPane() {
  return <p className="muted">artifact pane: not implemented</p>;
}
