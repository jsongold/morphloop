// Pane contract for the /v2 workspace shell (src/v2/Workspace.tsx).
//
// Each pane lives in its own directory and is imported from a fixed path;
// a pane PR replaces only `src/v2/panes/<name>/` and keeps these exports:
//
//   slot     module (index.tsx)   export
//   header   session              default SessionPane   (also the picker when no session is selected)
//   left     textbook             TextbookToc           (topic.docs[] order; picks a doc via setDoc)
//   main     textbook             TextbookReader        (renders the selected doc)
//   main     highlight            default HighlightPane (selection toolbar / popup chat over the reader)
//   main     drill                default DrillPane
//   side     chat                 default ChatPane      (the main / active thread)
//   side     memo                 default MemoPane
//   bottom   artifact             default ArtifactPane  (lab terminal area)
//   inline   artifact             ArtifactSlot, registerArtifactRenderer
//
// Panes take no props: shared state (session, ws, docId, threadId and their
// setters) comes from `useWorkspace()` in src/v2/state.tsx, and each pane
// fetches its own resource with src/v2/api.ts.

/**
 * DOM contract of the textbook reader. Every rendered block is an element
 * carrying `data-doc-id` and `data-block-id`; its `textContent` is exactly the
 * block's `plaintext` (contracts/fixtures/plaintext/), so a highlight's
 * TextPositionSelector offsets count into it. Other panes locate a block with
 * `el.closest("[data-block-id]")` and read the ids from `dataset`.
 */
export const DOC_ATTR = "data-doc-id";
export const BLOCK_ATTR = "data-block-id";

/**
 * A `::artifact{type=<type> ref=<spec_id>}` directive inside a block body.
 * The reader renders each one as `<ArtifactSlot directive={...} />`, resolved
 * by the registry in src/v2/panes/artifact/ keyed by `type` ("lab", "diagram").
 */
export interface ArtifactDirective {
  type: string;
  /** The pack artifact spec id (`spec_id` of `POST /ws/{ws_id}/artifacts`). */
  ref: string;
}
