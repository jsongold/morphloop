"use client";

// Owned by the artifact pane PR; replace this directory. Keep the exports
// below (src/v2/panes/types.ts): the reader mounts <ArtifactSlot/> for every
// `::artifact{type ref}` directive, and this module registers a renderer per
// artifact type (`registerArtifactRenderer("lab", LabView)`) at module scope.

import type { ReactNode } from "react";
import type { ArtifactDirective } from "../types";

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

/** Bottom slot: the lab terminal area. */
export default function ArtifactPane() {
  return <p className="muted">artifact pane: not implemented</p>;
}
