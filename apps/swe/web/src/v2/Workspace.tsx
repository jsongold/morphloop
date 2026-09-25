"use client";

// The /v2 workspace: fixed slots (same grid and CSS as the v0.1 workspace),
// each filled by a pane from a fixed path. Slot -> pane mapping and the pane
// contract are documented in src/v2/panes/types.ts. This file is final: a
// pane PR only touches its own src/v2/panes/<name>/ directory.

import { useWorkspace } from "./state";
import ArtifactPane from "./panes/artifact";
import ChatPane from "./panes/chat";
import DrillPane from "./panes/drill";
import HighlightPane from "./panes/highlight";
import MemoPane from "./panes/memo";
import SessionPane from "./panes/session";
import { TextbookReader, TextbookToc } from "./panes/textbook";

export function Workspace() {
  const { session } = useWorkspace();

  if (!session) {
    return (
      <div className="picker">
        <SessionPane />
      </div>
    );
  }
  return (
    <div className="app with-left with-side with-bottom">
      <header className="app-header">
        <SessionPane />
      </header>
      <nav className="left-pane" aria-label="Table of contents">
        <TextbookToc />
      </nav>
      <main className="main-pane">
        <TextbookReader />
        <HighlightPane />
        <DrillPane />
      </main>
      <aside className="side-pane">
        <ChatPane />
        <div className="memo-region">
          <MemoPane />
        </div>
      </aside>
      <footer className="bottom-pane">
        <ArtifactPane />
      </footer>
    </div>
  );
}
