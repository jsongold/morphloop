"use client";

// Entry point for the v0.2.0 resource-centric UI (ADR-0018, issue #47). This
// shell has no layout spec (v0.1's declarative layout is a v0.1-only
// concept, ADR-0012); it just lists whatever resources have registered
// themselves in `src/resources/index.ts`. The v0.1 UI at `/` is untouched.

import { listResources } from "@/resources";

export default function V2Home() {
  const resources = listResources();

  if (resources.length === 0) {
    return (
      <div className="center muted">
        No resources registered yet. Add one under{" "}
        <code>web/src/resources/&lt;resource&gt;/</code> and register it in{" "}
        <code>web/src/resources/index.ts</code>.
      </div>
    );
  }

  return (
    <div>
      {resources.map(({ id, label, View }) => (
        <section key={id}>
          <h2>{label}</h2>
          <View />
        </section>
      ))}
    </div>
  );
}
