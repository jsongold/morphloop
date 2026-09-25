"use client";

import { useEffect, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { del, get, newIdempotencyKey, post } from "@/v2/api";
import { useWorkspace } from "@/v2/state";
import type { StoredEvent } from "@/v2/types";
import { anchorFor, chars, type Anchor } from "./anchor";
import { highlightKey, keepsDraft, mergeById, nearBottom, popupTop, reuseKey, shouldClearDraft, threadTarget, type PendingHighlight } from "./behavior";

type Highlight = { highlight_id: string; anchor: Anchor };
type Message = { message_id: string; role: "learner" | "assistant"; text: string };
type Selection = { anchor: Anchor; x: number; y: number };
type Popup = { highlight: Highlight; x: number; y: number; threadId: string; messages: Message[] };

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const blockOf = (node: Node | null) =>
  (node instanceof Element ? node : node?.parentElement)?.closest<HTMLElement>("[data-doc-id][data-block-id]") ?? null;

function offset(block: HTMLElement, node: Node, at: number): number {
  const range = document.createRange();
  range.selectNodeContents(block);
  range.setEnd(node, at);
  return chars(range.toString()).length;
}

function readSelection(): Selection | null {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  const block = blockOf(range.startContainer);
  if (!block || block !== blockOf(range.endContainer)) return null;
  const full = block.textContent ?? "";
  const start = offset(block, range.startContainer, range.startOffset);
  const end = offset(block, range.endContainer, range.endOffset);
  if (!block.dataset.docId || !block.dataset.blockId) return null;
  const anchor = anchorFor(block.dataset.docId, block.dataset.blockId, full, start, end);
  if (!anchor) return null;
  const rect = range.getBoundingClientRect();
  return {
    anchor,
    x: rect.right,
    y: rect.bottom,
  };
}

function rangeFor(highlight: Highlight): Range | null {
  const { doc_id, block_id, selector: [quote, position] } = highlight.anchor;
  const block = [...document.querySelectorAll<HTMLElement>("[data-doc-id][data-block-id]")]
    .find((el) => el.dataset.docId === doc_id && el.dataset.blockId === block_id);
  if (!block || chars(block.textContent ?? "").slice(position.start, position.end).join("") !== quote.exact) return null;
  const walker = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
  let node: Node | null;
  let cursor = 0;
  let first: { node: Node; at: number } | null = null;
  let last: { node: Node; at: number } | null = null;
  while ((node = walker.nextNode())) {
    const text = chars(node.textContent ?? "");
    const next = cursor + text.length;
    if (!first && position.start >= cursor && position.start < next) first = { node, at: text.slice(0, position.start - cursor).join("").length };
    if (position.end > cursor && position.end <= next) {
      last = { node, at: text.slice(0, position.end - cursor).join("").length };
      break;
    }
    cursor = next;
  }
  if (!first || !last) return null;
  const range = document.createRange();
  range.setStart(first.node, first.at);
  range.setEnd(last.node, last.at);
  return range;
}

export default function HighlightPane() {
  const { ws } = useWorkspace();
  const wsId = ws?.ws_id;
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [popup, setPopup] = useState<Popup | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ranges = useRef(new Map<string, Range>());
  const highlightsRef = useRef(highlights);
  const openRequest = useRef(0);
  const activeThread = useRef<string | null>(null);
  const pendingHighlight = useRef<PendingHighlight | null>(null);
  const pendingMessage = useRef<{ threadId: string; text: string; key: string } | null>(null);
  const deleteKeys = useRef(new Map<string, string>());
  const openingHighlight = useRef<string | null>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const followMessages = useRef(true);

  useEffect(() => { highlightsRef.current = highlights; }, [highlights]);

  useEffect(() => {
    if (!wsId) return;
    let cancelled = false;
    const requests = openRequest;
    get<{ highlights: Highlight[] }>(`/ws/${wsId}/highlights`).then(
      ({ highlights: loaded }) => { if (!cancelled) setHighlights((items) => mergeById(loaded, items, (item) => item.highlight_id)); },
      (error: unknown) => { if (!cancelled) setError(errorText(error)); },
    );
    return () => { cancelled = true; requests.current++; activeThread.current = null; setOpening(false); setHighlights([]); setSelection(null); setPopup(null); };
  }, [wsId]);

  useEffect(() => {
    const draw = () => {
      ranges.current = new Map(highlights.map((h) => [h.highlight_id, rangeFor(h)] as const).filter((entry): entry is readonly [string, Range] => entry[1] !== null));
      if (typeof CSS !== "undefined" && CSS.highlights) {
        CSS.highlights.set("morphloop-v2-highlight", new window.Highlight(...ranges.current.values()));
      }
    };
    draw();
    const reader = document.querySelector(".main-pane");
    const observer = new MutationObserver(draw);
    if (reader) observer.observe(reader, { childList: true, subtree: true, characterData: true });
    return () => { observer.disconnect(); CSS.highlights?.delete("morphloop-v2-highlight"); };
  }, [highlights]);

  async function open(highlight: Highlight, x: number, y: number) {
    if (!wsId) return;
    const request = ++openRequest.current;
    openingHighlight.current = highlight.highlight_id;
    setSelection(null);
    window.getSelection()?.removeAllRanges();
    setError(null);
    setOpening(true);
    try {
      // No v2 thread-list endpoint: retain the creation key so POST replays on reopen.
      const storageKey = `morphloop:v2:thread:${wsId}:${highlight.highlight_id}`;
      const key = localStorage.getItem(storageKey) ?? newIdempotencyKey();
      localStorage.setItem(storageKey, key);
      const { body } = await post<StoredEvent<{ thread_id: string }>>(`/ws/${wsId}/threads`, {
        target: threadTarget(highlight.highlight_id, highlight.anchor),
      }, key);
      const threadId = body.payload.thread_id;
      const { messages } = await get<{ messages: Message[] }>(`/ws/${wsId}/threads/${threadId}/messages`);
      if (request !== openRequest.current) return;
      if (!keepsDraft(popup?.threadId ?? null, threadId)) setDraft("");
      followMessages.current = true;
      activeThread.current = threadId;
      setPopup({ highlight, x, y, threadId, messages });
    } catch (error) { if (request === openRequest.current) setError(errorText(error)); }
    finally { if (request === openRequest.current) { setOpening(false); openingHighlight.current = null; } }
  }

  useLayoutEffect(() => {
    if (!popup) return;
    const place = () => {
      if (popupRef.current) popupRef.current.style.top = `${popupTop(popup.y, window.innerHeight, popupRef.current.offsetHeight)}px`;
    };
    place();
    window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [popup]);

  useLayoutEffect(() => {
    if (popup && followMessages.current && logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [popup]);

  useEffect(() => {
    const onSelection = () => setSelection(readSelection());
    const onClick = (event: MouseEvent) => {
      if ((event.target as Element).closest(".popup-chat, .selection-toolbar, [aria-label='Saved highlights']")) return;
      if (window.getSelection()?.toString()) return;
      for (const [id, range] of ranges.current) {
        if (![...range.getClientRects()].some((rect) => event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom)) continue;
        const highlight = highlightsRef.current.find((h) => h.highlight_id === id);
        if (highlight) void open(highlight, event.clientX, event.clientY);
        break;
      }
    };
    document.addEventListener("mouseup", onSelection);
    document.addEventListener("keyup", onSelection);
    document.addEventListener("click", onClick);
    return () => {
      document.removeEventListener("mouseup", onSelection);
      document.removeEventListener("keyup", onSelection);
      document.removeEventListener("click", onClick);
    };
  });

  async function save(ask: boolean) {
    if (!wsId || !selection || busy) return;
    const selected = selection;
    setBusy(true);
    setError(null);
    try {
      const pending = highlightKey(pendingHighlight.current, wsId, selected.anchor, newIdempotencyKey);
      pendingHighlight.current = pending;
      const { body } = await post<Highlight>(`/ws/${wsId}/highlights`, { anchor: selected.anchor }, pending.key);
      pendingHighlight.current = null;
      setHighlights((items) => [...items, body]);
      setSelection(null);
      window.getSelection()?.removeAllRanges();
      if (ask) void open(body, selected.x, selected.y);
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  async function remove(highlight: Highlight) {
    if (!wsId || busy) return;
    if (openingHighlight.current === highlight.highlight_id) {
      openRequest.current++;
      openingHighlight.current = null;
      setOpening(false);
    }
    setBusy(true);
    setError(null);
    const key = reuseKey(deleteKeys.current, highlight.highlight_id, newIdempotencyKey);
    try {
      await del(`/ws/${wsId}/highlights/${highlight.highlight_id}`, key);
      deleteKeys.current.delete(highlight.highlight_id);
      setHighlights((items) => items.filter((item) => item.highlight_id !== highlight.highlight_id));
      if (popup?.highlight.highlight_id === highlight.highlight_id) setPopup(null);
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!wsId || !popup || !draft.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      const text = draft.trim();
      const key = pendingMessage.current?.threadId === popup.threadId && pendingMessage.current.text === text
        ? pendingMessage.current.key : newIdempotencyKey();
      pendingMessage.current = { threadId: popup.threadId, text, key };
      const { body } = await post<{ sent: Message; reply: Message }>(`/ws/${wsId}/threads/${popup.threadId}/messages`, { text }, key);
      pendingMessage.current = null;
      setPopup((current) => current && current.threadId === popup.threadId
        ? { ...current, messages: mergeById(current.messages, [body.sent, body.reply], (message) => message.message_id) } : current);
      if (activeThread.current === popup.threadId) setDraft((current) => (shouldClearDraft(current, text) ? "" : current));
    } catch (error) { setError(errorText(error)); }
    finally { setBusy(false); }
  }

  return (
    <>
      <style>{"::highlight(morphloop-v2-highlight) { background: var(--mark); color: inherit; }"}</style>
      {selection && wsId && <div className="selection-toolbar" role="toolbar" aria-label="Selection actions">
        <q>{selection.anchor.selector[0].exact.slice(0, 40)}</q>
        <button disabled={busy} onMouseDown={(e) => e.preventDefault()} onClick={() => void save(false)}>Save highlight</button>
        <button disabled={busy} onMouseDown={(e) => e.preventDefault()} onClick={() => void save(true)}>Ask AI / chat</button>
      </div>}
      {highlights.length > 0 && <section aria-label="Saved highlights">
        <h3>Highlights</h3>
        <ul className="highlights">{highlights.map((highlight) => <li key={highlight.highlight_id}>
          <button className="link" onClick={() => void open(highlight, window.innerWidth / 2, 100)}>{highlight.anchor.selector[0].exact}</button>{" "}
          <button className="link" disabled={busy} aria-label={`Delete highlight: ${highlight.anchor.selector[0].exact}`} onClick={() => void remove(highlight)}>Delete</button>
        </li>)}</ul>
      </section>}
      {opening && <p className="muted" role="status">Opening chat…</p>}
      {error && <p className="error" role="alert">{error}</p>}
      {popup && <div ref={popupRef} className="popup-chat" role="dialog" aria-label="Chat about this highlight" style={{ left: Math.min(Math.max(8, popup.x), Math.max(8, window.innerWidth - 328)), top: 8, overflowY: "auto" }}>
        <div className="popup-chat-head"><blockquote>{popup.highlight.anchor.selector[0].exact}</blockquote><button className="popup-chat-close" aria-label="Close" onClick={() => setPopup(null)}>×</button></div>
        <div ref={logRef} className="chat-log" onScroll={(event) => { const el = event.currentTarget; followMessages.current = nearBottom(el.scrollTop, el.clientHeight, el.scrollHeight); }}>{popup.messages.map((message) => <p className={`chat-msg ${message.role}`} key={message.message_id}>{message.text}</p>)}</div>
        <form className="chat-form" onSubmit={(event) => void send(event)}><textarea autoFocus aria-label="Message" value={draft} onChange={(event) => setDraft(event.target.value)} onKeyDown={(event) => { if (event.key === "Escape") setPopup(null); }} /><button disabled={busy || !draft.trim()}>Send</button></form>
      </div>}
    </>
  );
}
