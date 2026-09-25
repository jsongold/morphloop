// Entry point of the v0.2 UI (issue #106). The v0.1 UI at `/` is untouched.

import { WorkspaceProvider } from "@/v2/state";
import { Workspace } from "@/v2/Workspace";

export default function V2Page() {
  return (
    <WorkspaceProvider>
      <Workspace />
    </WorkspaceProvider>
  );
}
