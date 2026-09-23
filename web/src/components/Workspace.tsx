"use client";

import dynamic from "next/dynamic";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as api from "@/lib/api";
import { ApiError } from "@/lib/api";
import { newHighlightId, newIdempotencyKey, nowTimestamp } from "@/lib/ids";
import { interpretLayout, modeView, type InterpretedLayout, type TocItem } from "@/lib/layout";
import { readSelection, type SelectionTarget } from "@/lib/selection";
import type {
  ActivityView,
  AttemptState,
  ChatEvent,
  ClientEventRequest,
  ContentDocument,
  ContentSummary,
  HighlightEvent,
  ReferenceDocument,
  SessionState,
  StoredEvent,
  TerminalCommandPayload,
  TerminalOutputPayload,
  VisualizationDocument,
} from "@/lib/types";
import type { LabStatusMessage, ServerMessage } from "@/lib/ws";
import { ChatPanel } from "./ChatPanel";
import { ConceptPane } from "./ConceptPane";
import { MissionPanel } from "./MissionPanel";
import type { TerminalHandle } from "./TerminalPane";
import { TimelineView } from "./TimelineView";
import { TocPane } from "./TocPane";
import { VisualizationView } from "./VisualizationView";

// xterm touches `window` at import time: client-only.
const TerminalPane = dynamic(() => import("./TerminalPane"), {
  ssr: false,
  loading: () => <div className="terminal muted">Loading terminal…</div>,
});

const SIDE_PANE = "side";

// Bottom (chat) pane resize (drag handle on its top edge). Height is remembered
// per viewer in localStorage; falls back to the CSS default (240px) on read failure.
const BOTTOM_H_KEY = "morphloop.bottomPaneHeight";
const BOTTOM_H_MIN = 140;
const BOTTOM_H_MAX = 640;

function errText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

interface Loaded {
  state: SessionState;
  layout: InterpretedLayout;
  activities: ActivityView[];
  catalog: ContentSummary[];
}

export function Workspace({ sessionId, onLeave }: { sessionId: string; onLeave: () => void }) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState<AttemptState | null>(null);
  const [highlights, setHighlights] = useState<HighlightEvent[]>([]);
  const [chat, setChat] = useState<ChatEvent[]>([]);
  const [timeline, setTimeline] = useState<StoredEvent[]>([]);
  const [mode, setMode] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<ContentDocument | null>(null);
  const [vizDoc, setVizDoc] = useState<ContentDocument<VisualizationDocument> | null>(null);
  const [steps, setSteps] = useState<Record<string, string>>({});
  const [quoted, setQuoted] = useState<HighlightEvent[]>([]);
  const [selection, setSelection] = useState<SelectionTarget | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [chatError, setChatError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // Workspace only ever mounts client-side (see page.tsx), so reading localStorage
  // in the initializer is safe — no SSR/hydration mismatch.
  const [bottomHeight, setBottomHeight] = useState(() => {
    try {
      const stored = Number(window.localStorage.getItem(BOTTOM_H_KEY));
      if (Number.isFinite(stored) && stored >= BOTTOM_H_MIN && stored <= BOTTOM_H_MAX) return stored;
    } catch {
      // storage unavailable: keep the CSS default
    }
    return 240;
  });

  const terminalRef = useRef<TerminalHandle | null>(null);
  const lastPos = useRef(0);
  const refreshing = useRef<Promise<void> | null>(null);
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refDocs = useRef(new Map<string, ContentDocument>());
  const pendingChat = useRef<{ body: string; key: string } | null>(null);
  const toolbarRef = useRef<HTMLDivElement>(null);

  // ---------- load / resume (AC-F4) ----------

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [state, layoutRes, acts, content, hls, chatRes, tl] = await Promise.all([
          api.getSession(sessionId),
          api.getSessionLayout(sessionId),
          api.listSessionActivities(sessionId),
          api.listSessionContent(sessionId),
          api.getSessionHighlights(sessionId),
          api.getSessionChat(sessionId),
          api.getTimelineSince(sessionId, 0),
        ]);
        if (cancelled) return;
        const layout = interpretLayout(layoutRes.layout);
        const catalog = content.items;
        lastPos.current = tl.last_position;
        setTimeline(tl.events);
        setHighlights(hls.events);
        setChat(chatRes.events);
        setAttempt(state.active_attempt);
        setMode(layout.defaultMode);
        const restoredSteps: Record<string, string> = {};
        for (const s of state.ui_state.visualization_steps) restoredSteps[s.visualization_id] = s.step_id;
        setSteps(restoredSteps);

        const find = (id: string, kind?: string) =>
          catalog.find((c) => c.definition_id === id && (!kind || c.kind === kind));
        const open = state.ui_state.open_content;
        const openSummary = open ? find(open.content_id) : undefined;
        const lastViz = state.ui_state.visualization_steps.at(-1)?.visualization_id;
        const vizSummary =
          (lastViz ? find(lastViz, "visualization") : undefined) ??
          catalog.find((c) => c.kind === "visualization");
        const [od, vd] = await Promise.all([
          openSummary
            ? api.getSessionContent(sessionId, openSummary.kind, openSummary.definition_id)
            : null,
          vizSummary
            ? api.getSessionContent<VisualizationDocument>(sessionId, "visualization", vizSummary.definition_id)
            : null,
        ]);
        if (cancelled) return;
        setOpenDoc(od);
        setVizDoc(vd);
        setLoaded({ state, layout, activities: acts.activities, catalog });
      } catch (e) {
        if (!cancelled) setLoadError(errText(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  // ---------- timeline ----------

  const refreshTimeline = useCallback(async () => {
    if (refreshing.current) return refreshing.current;
    const run = (async () => {
      try {
        const page = await api.getTimelineSince(sessionId, lastPos.current);
        if (page.events.length > 0) {
          setTimeline((prev) => {
            const seen = new Set(prev.map((e) => e.position));
            return [...prev, ...page.events.filter((e) => !seen.has(e.position))];
          });
        }
        lastPos.current = Math.max(lastPos.current, page.last_position);
      } catch {
        // transient; next notification retries
      } finally {
        refreshing.current = null;
      }
    })();
    refreshing.current = run;
    return run;
  }, [sessionId]);

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => void refreshTimeline(), 300);
  }, [refreshTimeline]);

  useEffect(
    () => () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    },
    [],
  );

  // ---------- attempt polling while evaluating ----------

  const attemptId = attempt?.attempt_id ?? null;
  const attemptStatus = attempt?.status ?? null;
  const refetchAttempt = useCallback(async () => {
    if (!attemptId) return;
    try {
      setAttempt(await api.getAttempt(attemptId));
    } catch (e) {
      setActionError(errText(e));
    }
  }, [attemptId]);

  useEffect(() => {
    if (attemptStatus !== "evaluating") return;
    const t = setInterval(() => void refetchAttempt(), 2000);
    return () => clearInterval(t);
  }, [attemptStatus, refetchAttempt]);

  // ---------- selection toolbar (AC-D1) ----------

  useEffect(() => {
    const onChange = () => {
      if (toolbarRef.current?.contains(document.activeElement)) return;
      setSelection(readSelection());
    };
    document.addEventListener("selectionchange", onChange);
    return () => document.removeEventListener("selectionchange", onChange);
  }, []);

  // ---------- bottom pane resize ----------

  const startBottomResize = useCallback((ev: React.MouseEvent) => {
    ev.preventDefault();
    const onMove = (e: MouseEvent) => {
      const h = Math.min(BOTTOM_H_MAX, Math.max(BOTTOM_H_MIN, window.innerHeight - e.clientY));
      setBottomHeight(h);
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      setBottomHeight((h) => {
        try {
          window.localStorage.setItem(BOTTOM_H_KEY, String(h));
        } catch {
          // storage unavailable: size lives until reload
        }
        return h;
      });
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  }, []);

  // ---------- client events ----------

  const appendEvent = useCallback(
    async (req: Omit<ClientEventRequest, "occurred_at" | "idempotency_key" | "attempt_id" | "event_version">) => {
      const body = {
        ...req,
        event_version: 1,
        occurred_at: nowTimestamp(),
        idempotency_key: newIdempotencyKey(),
        attempt_id: attempt && attempt.status !== "completed" ? attempt.attempt_id : null,
      } as ClientEventRequest;
      const res = await api.appendClientEvent(sessionId, body);
      scheduleRefresh();
      return res.body.event;
    },
    [attempt, sessionId, scheduleRefresh],
  );

  const selectStep = useCallback(
    (stepId: string) => {
      if (!vizDoc) return;
      const vizId = vizDoc.definition_id;
      setSteps((s) => ({ ...s, [vizId]: stepId }));
      appendEvent({
        event_type: "visualization.step_selected",
        payload: { visualization_id: vizId, content_version: vizDoc.content_version, step_id: stepId },
      }).catch((e) => setActionError(errText(e)));
    },
    [vizDoc, appendEvent],
  );

  const openContent = useCallback(
    async (summary: ContentSummary, sourceHighlightId: string | null = null) => {
      try {
        const doc =
          refDocs.current.get(summary.definition_id) ??
          (await api.getSessionContent(sessionId, summary.kind, summary.definition_id));
        setOpenDoc(doc);
        await appendEvent({
          event_type: "content.opened",
          payload: {
            content_id: summary.definition_id,
            content_version: summary.content_version,
            pane: SIDE_PANE,
            source_highlight_id: sourceHighlightId,
          },
        });
      } catch (e) {
        setActionError(errText(e));
      }
    },
    [sessionId, appendEvent],
  );

  const saveHighlight = useCallback(
    async (t: SelectionTarget): Promise<HighlightEvent | null> => {
      try {
        const ev = (await appendEvent({
          event_type: "content.highlighted",
          payload: {
            highlight_id: newHighlightId(),
            content_id: t.contentId,
            content_version: t.contentVersion,
            selected_text: t.text,
            start_offset: t.start,
            end_offset: t.end,
            semantic_anchor: t.anchor,
            context_before: t.contextBefore,
            context_after: t.contextAfter,
          },
        })) as unknown as HighlightEvent;
        setHighlights((h) => [...h, ev]);
        window.getSelection()?.removeAllRanges();
        setSelection(null);
        return ev;
      } catch (e) {
        setActionError(errText(e));
        return null;
      }
    },
    [appendEvent],
  );

  const askAi = useCallback(
    async (t: SelectionTarget) => {
      const h = await saveHighlight(t);
      if (h) setQuoted((q) => [...q, h]);
    },
    [saveHighlight],
  );

  /** Finds a reference whose title or aliases occur in the selection, then opens it. */
  const openConcept = useCallback(
    async (t: SelectionTarget) => {
      if (!loaded) return;
      const needle = t.text.trim().toLowerCase();
      let match: ContentSummary | null = null;
      for (const s of loaded.catalog.filter((c) => c.kind === "reference")) {
        let doc = refDocs.current.get(s.definition_id);
        if (!doc) {
          doc = await api.getSessionContent(sessionId, "reference", s.definition_id);
          refDocs.current.set(s.definition_id, doc);
        }
        const ref = doc.document as unknown as ReferenceDocument;
        const terms = [ref.title, ...(ref.aliases ?? [])].map((x) => x.toLowerCase());
        if (terms.some((term) => needle.includes(term) || term.includes(needle))) {
          match = s;
          break;
        }
      }
      const h = await saveHighlight(t);
      if (!match) {
        setNotice("No concept in this pack matches the selection. Try “Ask AI”.");
        return;
      }
      setNotice(null);
      await openContent(match, h?.payload.highlight_id ?? null);
    },
    [loaded, sessionId, saveHighlight, openContent],
  );

  // ---------- chat (AC-D3..D5) ----------

  const sendChat = useCallback(
    async (text: string) => {
      setChatError(null);
      const threadId = chat.at(-1)?.payload.thread_id ?? null;
      const base = {
        occurred_at: nowTimestamp(),
        thread_id: threadId,
        attempt_id: attempt && attempt.status !== "completed" ? attempt.attempt_id : null,
        text,
        references: quoted.map((h) => ({ type: "highlight" as const, id: h.payload.highlight_id })),
      };
      // Retry after a 502 reuses the key so no second request event is appended.
      const fingerprint = JSON.stringify([base.thread_id, base.attempt_id, text, base.references]);
      const key =
        pendingChat.current?.body === fingerprint ? pendingChat.current.key : newIdempotencyKey();
      pendingChat.current = { body: fingerprint, key };
      try {
        const res = await api.sendChatMessage(sessionId, { ...base, idempotency_key: key });
        pendingChat.current = null;
        setChat((c) => {
          const ids = new Set(c.map((e) => e.event_id));
          return [...c, ...[res.body.request, res.body.reply].filter((e) => !ids.has(e.event_id))];
        });
        setQuoted([]);
        scheduleRefresh();
      } catch (e) {
        setChatError(`${errText(e)} — send again to retry.`);
        throw e;
      }
    },
    [chat, attempt, quoted, sessionId, scheduleRefresh],
  );

  // ---------- attempt commands ----------

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
    } catch (e) {
      setActionError(errText(e));
    } finally {
      setBusy(false);
    }
  };

  const startActivity = (a: ActivityView) =>
    run(async () => {
      const res = await api.startAttempt(sessionId, {
        idempotency_key: newIdempotencyKey(),
        activity_definition_id: a.activity_definition_id,
      });
      setAttempt(res.body);
      scheduleRefresh();
    });

  const submit = () =>
    run(async () => {
      if (!attempt) return;
      const res = await api.submitAttempt(attempt.attempt_id, newIdempotencyKey());
      setAttempt(res.body);
      scheduleRefresh();
    });

  const resetLab = () =>
    run(async () => {
      const lab = attempt?.lab;
      if (!attempt || !lab) return;
      const res = await api.resetLab(lab.lab_instance_id, newIdempotencyKey());
      setAttempt({ ...attempt, lab: res.body });
      scheduleRefresh();
    });

  const onLabStatus = useCallback((p: LabStatusMessage["payload"]) => {
    setAttempt((a) =>
      a?.lab && a.lab.lab_instance_id === p.lab_instance_id
        ? { ...a, lab: { ...a.lab, status: p.status, terminal_id: p.terminal_id } }
        : a,
    );
  }, []);

  const onServerMessage = useCallback(
    (m: ServerMessage) => {
      if (m.type === "event.appended") {
        scheduleRefresh();
        if (["activity.completed", "evaluation.completed", "lab.started"].includes(m.payload.event_type)) {
          void refetchAttempt();
        }
      } else if (m.type === "error") {
        setActionError(`${m.payload.code}: ${m.payload.message}`);
      }
    },
    [scheduleRefresh, refetchAttempt],
  );

  // ---------- derived ----------

  const highlightsById = useMemo(
    () => new Map(highlights.map((h) => [h.payload.highlight_id, h])),
    [highlights],
  );
  const storedOutput = useMemo(
    () =>
      timeline
        .filter((e) => e.event_type === "terminal.output")
        .map((e) => e.payload as unknown as TerminalOutputPayload),
    [timeline],
  );
  const commands = useMemo(
    () =>
      timeline.filter((e) => e.event_type === "terminal.command") as unknown as StoredEvent<
        "terminal.command",
        TerminalCommandPayload
      >[],
    [timeline],
  );

  if (loadError) {
    return (
      <div className="center">
        <p className="error">Could not load session: {loadError}</p>
        <button onClick={onLeave}>Back</button>
      </div>
    );
  }
  if (!loaded) return <div className="center muted">Loading session…</div>;

  const { layout, state } = loaded;
  const view = modeView(mode);
  const lab = attempt?.lab ?? null;
  const labReady = lab?.status === "ready";
  const mainComponents =
    attempt?.activity.lab_backed && !layout.mainComponents.includes("terminal")
      ? [...layout.mainComponents, "terminal"]
      : layout.mainComponents;

  const renderMainComponent = (c: string) => {
    if (c === "terminal") {
      if (!attempt?.activity.lab_backed) return null;
      if (!lab) return <div key={c} className="terminal muted">No lab for this attempt.</div>;
      return (
        <TerminalPane
          key={c}
          lab={lab}
          storedOutput={storedOutput}
          onLabStatus={onLabStatus}
          onServerMessage={onServerMessage}
          handleRef={terminalRef}
        />
      );
    }
    return (
      <div key={c} className="muted">
        Component “{c}” is not provided by the standard UI.
      </div>
    );
  };

  const visualizeMode = layout.modes.find((m) => modeView(m) === "visualize") ?? null;
  const activeActivityId =
    attempt && attempt.status !== "completed" ? attempt.activity.activity_definition_id : null;
  const summaryOf = (item: TocItem) =>
    loaded.catalog.find((c) => c.kind === item.kind && c.definition_id === item.id);

  const tocTitle = (item: TocItem) =>
    (item.kind === "activity"
      ? loaded.activities.find((a) => a.activity_definition_id === item.id)?.title
      : summaryOf(item)?.title) ?? item.id;

  const tocCurrent = (item: TocItem) =>
    item.kind === "activity"
      ? activeActivityId === item.id
      : item.kind === "visualization"
        ? view === "visualize" && vizDoc?.definition_id === item.id
        : openDoc?.kind === item.kind && openDoc.definition_id === item.id;

  const tocSelect = (item: TocItem) => {
    if (item.kind === "activity") {
      const a = loaded.activities.find((x) => x.activity_definition_id === item.id);
      if (activeActivityId === item.id) setMode(layout.defaultMode);
      else if (a) void startActivity(a);
      return;
    }
    const summary = summaryOf(item);
    if (!summary) return;
    if (item.kind === "reference") {
      void openContent(summary);
      return;
    }
    void run(async () => {
      if (vizDoc?.definition_id !== item.id) {
        setVizDoc(
          await api.getSessionContent<VisualizationDocument>(sessionId, "visualization", item.id),
        );
      }
      setMode(visualizeMode);
    });
  };

  const renderSide = () => {
    if (layout.sideComponent === "concept_pane") {
      return (
        <ConceptPane
          open={openDoc}
          catalog={loaded.catalog}
          highlights={highlights}
          onOpen={(s) => void openContent(s)}
          onAskAboutHighlight={(h) =>
            setQuoted((q) => (q.some((x) => x.event_id === h.event_id) ? q : [...q, h]))
          }
        />
      );
    }
    return <p className="muted">Component “{layout.sideComponent}” is not provided by the standard UI.</p>;
  };

  const renderBottom = () => {
    if (layout.bottomComponent === "ai_chat") {
      return (
        <ChatPanel
          events={chat}
          highlightsById={highlightsById}
          quoted={quoted}
          onRemoveQuote={(id) => setQuoted((q) => q.filter((h) => h.payload.highlight_id !== id))}
          onSend={sendChat}
          error={chatError}
        />
      );
    }
    return <p className="muted">Component “{layout.bottomComponent}” is not provided by the standard UI.</p>;
  };

  return (
    <div
      className={`app${layout.leftComponent ? " with-left" : ""}${layout.sideComponent ? " with-side" : ""}${layout.bottomComponent ? " with-bottom" : ""}`}
      style={layout.bottomComponent ? ({ "--bottom-h": `${bottomHeight}px` } as React.CSSProperties) : undefined}
    >
      <header className="app-header">
        <strong>{state.session.pack.pack_id}</strong>
        <span className="muted">
          v{state.session.pack.pack_version} · {state.session.session_id}
        </span>
        {layout.modes.length > 0 && (
          <nav className="modes" role="tablist" aria-label="Main pane mode">
            {layout.modes.map((m) => (
              <button key={m} role="tab" aria-selected={m === mode} onClick={() => setMode(m)}>
                {m}
              </button>
            ))}
          </nav>
        )}
        <button className="link" onClick={onLeave}>
          Leave session
        </button>
      </header>

      {layout.leftComponent && (
        <nav className="left-pane" aria-label="Table of contents">
          {layout.leftComponent === "toc" ? (
            <TocPane chapters={layout.toc} titleOf={tocTitle} isCurrent={tocCurrent} onSelect={tocSelect} />
          ) : (
            <p className="muted">Component “{layout.leftComponent}” is not provided by the standard UI.</p>
          )}
        </nav>
      )}

      <main className="main-pane">
        <MissionPanel
          attempt={attempt}
          activities={loaded.activities}
          contentVersion={state.session.pack.pack_version}
          highlights={highlights}
          busy={busy}
          onStart={startActivity}
          onSubmit={submit}
          onReset={resetLab}
          onNext={() => setAttempt(null)}
        />
        {!layout.declared && <p className="muted">This pack declares no layout.</p>}
        {actionError && (
          <p className="error" role="alert">
            {actionError}{" "}
            <button className="link" onClick={() => setActionError(null)}>
              dismiss
            </button>
          </p>
        )}
        {notice && <p className="muted">{notice}</p>}

        {view === "visualize" && (
          <section className="mode-body">
            {vizDoc ? (
              <VisualizationView
                content={vizDoc}
                selectedStepId={steps[vizDoc.definition_id] ?? null}
                onSelectStep={selectStep}
                onRun={labReady ? (line) => terminalRef.current?.runLine(line) : null}
                commands={commands}
                highlights={highlights}
              />
            ) : (
              <p className="muted">This pack has no visualization.</p>
            )}
          </section>
        )}
        {view === "review" && (
          <section className="mode-body">
            <TimelineView events={timeline} />
          </section>
        )}

        <div className={`main-components${view === "practice" ? " grow" : ""}`}>
          {mainComponents.map(renderMainComponent)}
        </div>
      </main>

      {layout.sideComponent && <aside className="side-pane">{renderSide()}</aside>}
      {layout.bottomComponent && (
        <footer className="bottom-pane">
          <div
            className="bottom-resize-handle"
            role="separator"
            aria-orientation="horizontal"
            aria-label="Resize chat panel"
            onMouseDown={startBottomResize}
          />
          {renderBottom()}
        </footer>
      )}

      {selection && (
        <div className="selection-toolbar" ref={toolbarRef} role="toolbar" aria-label="Selection actions">
          <q>{selection.text.length > 40 ? selection.text.slice(0, 40) + "…" : selection.text}</q>
          <button onMouseDown={(e) => e.preventDefault()} onClick={() => void saveHighlight(selection)}>
            Save highlight
          </button>
          <button onMouseDown={(e) => e.preventDefault()} onClick={() => void askAi(selection)}>
            Ask AI
          </button>
          {layout.sideComponent && (
            <button onMouseDown={(e) => e.preventDefault()} onClick={() => void openConcept(selection)}>
              Open concept
            </button>
          )}
        </div>
      )}
    </div>
  );
}
