"use client";

import { createElement, useEffect, useState, type ReactNode } from "react";
import MarkdownIt from "markdown-it";
import type { Token } from "markdown-it";
import { get } from "@/v2/api";
import { useWorkspace } from "@/v2/state";
import { ArtifactSlot } from "../artifact";
import type { ArtifactDirective } from "../types";

type Summary = { id: string; title: string; labels: string[] };
type Doc = Summary & { blocks: { id: string; body: string; labels: string[]; plaintext: string }[] };
const md = new MarkdownIt("commonmark");
const directive = /^::artifact\{type=([^\s{}]+) ref=([^\s{}]+)\}$/;
const prefix = /^(?:>[ \t]?|#{1,6}[ \t]+)/;
const marker = /^(?:[-*+][ \t]+|\d{1,9}[.)][ \t]+)/;
const message = (e: unknown) => e instanceof Error ? e.message : String(e);

export function TextbookToc() {
  const { session, docId, setDoc } = useWorkspace();
  const topicId = session?.topic_id;
  const [loaded, setLoaded] = useState<{ topicId: string; docs: Summary[] } | null>(null);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  useEffect(() => {
    if (!topicId) return;
    let active = true;
    get<{ docs: Summary[] }>("/textbook/docs", { topic_id: topicId }).then(
      ({ docs }) => { if (active) { setLoaded({ topicId, docs }); setError(null); } },
      (e: unknown) => { if (active) setError({ id: topicId, message: message(e) }); },
    );
    return () => { active = false; };
  }, [topicId]);
  const docs = loaded && loaded.topicId === topicId ? loaded.docs : null;
  return <details className="toc" open>
    <summary>Contents</summary>
    {error && error.id === topicId && <p className="error" role="alert">{error.message}</p>}
    {!docs ? <p className="muted">Loading textbook…</p> : docs.length === 0 ? <p className="muted">No textbook docs.</p> :
      <ol>{docs.map((doc) => <li key={doc.id}><button className="link" aria-current={docId === doc.id ? "true" : undefined} onClick={() => setDoc(doc.id)}>{doc.title}</button></li>)}</ol>}
  </details>;
}

function rawLines(token: Token, source: string[], listStarts: Set<number>): (string | null)[] {
  const count = (token.children ?? []).filter((t) => t.type === "softbreak" || t.type === "hardbreak").length + 1;
  const [start, end] = token.map ?? [0, 0];
  const strip = (line: number) => {
    let text = source[line]?.trim() ?? "";
    while (true) {
      const match = prefix.exec(text) ?? (listStarts.has(line) ? marker.exec(text) : null);
      if (!match) return text;
      text = text.slice(match[0].length);
    }
  };
  if (end - start === count) return Array.from({ length: count }, (_, i) => strip(start + i));
  const lines: Token[][] = [[]];
  for (const child of token.children ?? []) {
    if (child.type === "softbreak" || child.type === "hardbreak") lines.push([]);
    else lines[lines.length - 1].push(child);
  }
  const complex = lines.map((line) => line.some((t) => t.type === "code_inline" || t.type === "html_inline"));
  const first = complex.indexOf(true), last = complex.lastIndexOf(true);
  if (first < 0) return Array(count).fill(null);
  return lines.map((_, i) => i < first ? strip(start + i) : i > last ? strip(end - count + i) : null);
}

function inline(tokens: Token[]): ReactNode[] {
  const out: ReactNode[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.nesting === 1) {
      let depth = 1, end = i + 1;
      while (depth && end < tokens.length) depth += tokens[end++].nesting;
      const href = t.attrGet("href");
      out.push(createElement(t.tag, t.tag === "a" && href && /^(https?:|mailto:|\/|#|\.)/i.test(href) ? { key: i, href } : { key: i }, inline(tokens.slice(i + 1, end - 1))));
      i = end - 1;
    } else if (t.type === "text") out.push(t.content);
    else if (t.type === "code_inline") out.push(<code key={i}>{t.content}</code>);
    else if (t.type === "image") {
      const alt = t.children?.map((child) => child.content).join("") ?? t.content;
      out.push(<span key={i}><img src={t.attrGet("src") ?? ""} alt={alt} />{alt}</span>);
    } else if (t.type === "html_inline") out.push(md.utils.unescapeAll(t.content.replace(/<[^>]*>/g, "")));
    else if (t.type === "softbreak" || t.type === "hardbreak") out.push("\n");
  }
  return out;
}

export function renderBlock(body: string): { content: ReactNode[]; artifacts: ArtifactDirective[] } {
  const tokens = md.parse(body, {}), source = body.split(/\r?\n/);
  const listStarts = new Set(tokens.filter((t) => t.type === "list_item_open" && t.map).map((t) => t.map![0]));
  const artifacts: ArtifactDirective[] = [];
  let hasText = false;
  function render(start: number, stop: number): ReactNode[] {
    const out: ReactNode[] = [];
    for (let i = start; i < stop; i++) {
      const t = tokens[i];
      if (t.nesting === 1) {
        let depth = 1, end = i + 1;
        while (depth && end < stop) depth += tokens[end++].nesting;
        out.push(createElement(t.tag, { key: i, start: t.tag === "ol" ? t.attrGet("start") ?? undefined : undefined }, render(i + 1, end - 1)));
        i = end - 1;
      } else if (t.type === "inline") {
        const lines: Token[][] = [[]];
        for (const child of t.children ?? []) {
          if (child.type === "softbreak" || child.type === "hardbreak") lines.push([]);
          else lines[lines.length - 1].push(child);
        }
        const raws = rawLines(t, source, listStarts), kept: Token[] = [];
        const separator = (t.children ?? []).find((child) => child.type === "softbreak" || child.type === "hardbreak");
        lines.forEach((line, index) => {
          const match = raws[index]?.match(directive);
          if (match) artifacts.push({ type: match[1], ref: match[2] });
          else { if (kept.length && separator) kept.push(separator); kept.push(...line); }
        });
        if (kept.length) { if (hasText) out.push("\n"); out.push(...inline(kept)); hasText = true; }
      } else if (t.type === "fence" || t.type === "code_block" || t.type === "html_block") {
        const value = t.type === "html_block" ? md.utils.unescapeAll(t.content.replace(/<[^>]*>/g, "")).trim() : t.content.replace(/\n$/, "");
        if (value) { if (hasText) out.push("\n"); out.push(t.type === "html_block" ? value : <pre key={i}><code>{value}</code></pre>); hasText = true; }
      } else if (t.type === "hr") out.push(<hr key={i} />);
    }
    return out;
  }
  return { content: render(0, tokens.length), artifacts };
}

export function TextbookReader() {
  const { docId } = useWorkspace();
  const [loaded, setLoaded] = useState<Doc | null>(null);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  useEffect(() => {
    if (!docId) return;
    let active = true;
    get<Doc>(`/textbook/docs/${encodeURIComponent(docId)}`).then(
      (doc) => { if (active) { setLoaded(doc); setError(null); } },
      (e: unknown) => { if (active) setError({ id: docId, message: message(e) }); },
    );
    return () => { active = false; };
  }, [docId]);
  if (!docId) return <p className="muted">Select a textbook doc.</p>;
  if (error?.id === docId) return <p className="error" role="alert">{error.message}</p>;
  if (!loaded || loaded.id !== docId) return <p className="muted">Loading textbook…</p>;
  return <article><h1>{loaded.title}</h1>{loaded.blocks.map((item) => {
    const rendered = renderBlock(item.body);
    return <div key={item.id}>
      <div data-doc-id={loaded.id} data-block-id={item.id}>{rendered.content}</div>
      {rendered.artifacts.map((artifact, i) => <ArtifactSlot key={i} directive={artifact} />)}
    </div>;
  })}</article>;
}
