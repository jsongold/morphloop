// Entry point of the UI (issue #106); `/` redirects here.

import { WorkspaceProvider } from "@/v2/state";
import { Workspace } from "@/v2/Workspace";

export default function V2Page() {
  return (
    <WorkspaceProvider>
      <Workspace />
    </WorkspaceProvider>
  );
}
