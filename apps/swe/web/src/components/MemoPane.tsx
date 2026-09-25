"use client";

import { useState } from "react";
import type { MemoView } from "@/lib/types";

interface Props {
  memos: MemoView[];
  onOpenThread: (highlightId: string, anchor?: { x: number; y: number }) => void;
  onEdit: (memoId: string, title: string, body: string) => Promise<void>;
}

/**
 * Learning notes written from highlight threads (memo.recorded) and editable
 * by the learner (memo.edited). One card per memo; "Chat" reopens the popup
 * thread the memo summarizes.
 */
export function MemoPane({ memos, onOpenThread, onEdit }: Props) {
  const [editing, setEditing] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [saving, setSaving] = useState(false);

  return (
    <section className="memo-pane" aria-label="Learning notes">
      <h4>Notes</h4>
      {memos.length === 0 ? (
        <p className="muted">Highlight text, then “Ask AI / chat” to write a learning note.</p>
      ) : (
        memos.map((m) => (
          <div key={m.memo_id} className={`memo-card${editing === m.memo_id ? " editing" : ""}`}>
            {editing === m.memo_id ? (
              <>
                <input
                  value={title}
                  placeholder="Title"
                  onChange={(ev) => setTitle(ev.target.value)}
                />
                <textarea
                  value={body}
                  placeholder="Note (Markdown)"
                  rows={6}
                  onChange={(ev) => setBody(ev.target.value)}
                />
                <div className="row">
                  <button
                    disabled={saving || !title.trim()}
                    onClick={() => {
                      setSaving(true);
                      void onEdit(m.memo_id, title.trim(), body).finally(() => {
                        setSaving(false);
                        setEditing(null);
                      });
                    }}
                  >
                    Save
                  </button>
                  <button onClick={() => setEditing(null)}>Cancel</button>
                </div>
              </>
            ) : (
              <>
                <h5>{m.title}</h5>
                <p className="memo-body">{m.body}</p>
                <div className="row">
                  <button
                    className="link"
                    onClick={() => {
                      setEditing(m.memo_id);
                      setTitle(m.title);
                      setBody(m.body);
                    }}
                  >
                    Edit
                  </button>
                  <button
                    className="link"
                    onClick={(ev) => {
                      const rect = (ev.currentTarget as HTMLElement).getBoundingClientRect();
                      onOpenThread(m.highlight_id, { x: rect.right + 8, y: rect.top });
                    }}
                  >
                    Chat
                  </button>
                </div>
              </>
            )}
          </div>
        ))
      )}
    </section>
  );
}