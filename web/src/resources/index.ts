// Registration point for per-resource UI on the /v2 shell (ADR-0018).
//
// Every planned resource is already imported below for its self-registering
// side effect (`registerResource(...)` at module scope in its own
// `<resource>/index.ts`). This file is final: a resource PR never edits it —
// parallel resource PRs would otherwise all append adjacent import lines
// here and conflict on merge. Instead, a resource PR only replaces its own
// stub at `web/src/resources/<resource>/index.ts` (created by issue #47)
// with real registration; nothing outside that directory changes.
//
// `web/src/app/v2/page.tsx` renders `listResources()`; with every resource
// still a no-op stub (as of issue #47) it shows an empty state.

import type { ComponentType } from "react";

// All planned resources, imported up front for their registration side
// effect (ADR-0018). Order is alphabetical and irrelevant to behavior.
import "./artifact";
import "./chat";
import "./drill";
import "./highlight";
import "./memo";
import "./session";
import "./textbook";
import "./ws";

export interface ResourceModule {
  /** Resource name (ADR-0018), e.g. "session", "ws", "memo". */
  id: string;
  /** Short label for the shell's resource list. */
  label: string;
  /** Root UI for this resource, mounted on the /v2 shell. */
  View: ComponentType;
}

const registry = new Map<string, ResourceModule>();

export function registerResource(mod: ResourceModule): void {
  registry.set(mod.id, mod);
}

export function listResources(): ResourceModule[] {
  return [...registry.values()];
}
