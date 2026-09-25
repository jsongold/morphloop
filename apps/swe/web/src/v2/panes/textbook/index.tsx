"use client";

import { createElement, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import MarkdownIt from "markdown-it";
import type { Token } from "markdown-it";
import { get } from "@/v2/api";
import { useWorkspace } from "@/v2/state";
import { ArtifactSlot } from "../artifact";
import type { ArtifactDirective } from "../types";
import { tocSections, topicIds, type TocDoc, type TocSection } from "./toc";

type Doc = TocDoc & { blocks: { id: string; body: string; labels: string[]; plaintext: string }[] };
const md = new MarkdownIt("commonmark");
const directive = /^::artifact\{type=([^\s{}]+) ref=([^\s{}]+)\}$/;
const prefix = /^(?:>[ \t]?|#{1,6}[ \t]+)/;
const marker = /^(?:[-*+][ \t]+|\d{1,9}[.)][ \t]+)/;
const message = (e: unknown) => e instanceof Error ? e.message : String(e);

export function TextbookToc() {
  const { session, docId, setDoc } = useWorkspace();
  const sessionId = session?.id;
  const tree = session?.tree;
  const [loaded, setLoaded] = useState<{ sessionId: string; sections: TocSection[] } | null>(null);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  useEffect(() => {
    if (!sessionId || !tree) return;
    let active = true;
    const lists = topicIds(tree).map((id) =>
      get<{ docs: TocDoc[] }>("/textbook/docs", { topic_id: id }).then(({ docs }) => [id, docs] as const));
    Promise.all(lists).then(
      (entries) => { if (active) { setLoaded({ sessionId, sections: tocSections(tree, new Map(entries)) }); setError(null); } },
      (e: unknown) => { if (active) setError({ id: sessionId, message: message(e) }); },
    );
    return () => { active = false; };
  }, [sessionId, tree]);
  const sections = loaded && loaded.sessionId === sessionId ? loaded.sections : null;
  const doc = (item: TocDoc) =>
    <li key={item.id}><button className="link" aria-current={docId === item.id ? "true" : undefined} onClick={() => setDoc(item.id)}>{item.title}</button></li>;
  return <details className="toc" open>
    <summary>Contents</summary>
    {error && error.id === sessionId && <p className="error" role="alert">{error.message}</p>}
    {!sections ? <p className="muted">Loading textbook…</p> : sections.length === 0 ? <p className="muted">No textbook docs.</p> :
      <ol>{sections.map((section) => <li key={section.id}>
        {section.depth > 0 && <strong>{section.title}</strong>}
        <ul>{section.docs.map(doc)}</ul>
      </li>)}</ol>}
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

/** No scheme (relative path, `#frag`, `//host`) or an explicitly safe one; rejects `javascript:` etc. */
function safeHref(href: string): boolean {
  const scheme = /^([a-z][a-z0-9+.-]*):/i.exec(href);
  return !scheme || /^(https?|mailto)$/i.test(scheme[1]);
}

/** Strips tags and comments, keeping the text between them; a `<` that cannot open a tag
 * (e.g. the comparison in `a < b`) is data, like an HTML parser — not a delimiter to scan to `>`. */
function stripTags(html: string): string {
  let out = "", i = 0;
  while (i < html.length) {
    if (html[i] !== "<") { out += html[i]; i++; continue; }
    if (html.startsWith("<!--", i)) {
      const end = html.indexOf("-->", i + 4);
      i = end < 0 ? html.length : end + 3;
      continue;
    }
    const next = html[i + 1] ?? "";
    if (!(next === "!" || next === "?" || next === "/" || /[a-zA-Z]/.test(next))) {
      out += html[i]; i++; continue;
    }
    let j = i + 1, quote = "";
    while (j < html.length && (html[j] !== ">" || quote)) {
      if (quote) { if (html[j] === quote) quote = ""; }
      else if (html[j] === '"' || html[j] === "'") quote = html[j];
      j++;
    }
    i = j + 1;
  }
  return out;
}

/** Renders an artifact directive in a shadow tree so its text never joins the block's `textContent`. */
function ArtifactHost({ directive }: { directive: ArtifactDirective }) {
  const ref = useRef<HTMLSpanElement>(null);
  const [root, setRoot] = useState<ShadowRoot | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) setRoot(el.shadowRoot ?? el.attachShadow({ mode: "open" }));
  }, []);
  return <span ref={ref} className="artifact-slot">{root && createPortal(<ArtifactSlot directive={directive} />, root)}</span>;
}

/** Plaintext of inline tokens, matching the API's recursive rule (breaks are "\n", images recurse). */
function plainText(tokens: Token[]): string {
  let out = "";
  for (const t of tokens) {
    if (t.type === "text" || t.type === "text_special" || t.type === "code_inline") out += t.content;
    else if (t.type === "softbreak" || t.type === "hardbreak") out += "\n";
    else if (t.type === "image") out += plainText(t.children ?? []);
  }
  return out;
}

function inline(tokens: Token[]): ReactNode[] {
  const out: ReactNode[] = [];
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t.nesting === 1) {
      let depth = 1, end = i + 1;
      while (depth && end < tokens.length) depth += tokens[end++].nesting;
      const href = t.attrGet("href");
      out.push(createElement(t.tag, t.tag === "a" && href && safeHref(href) ? { key: i, href } : { key: i }, inline(tokens.slice(i + 1, end - 1))));
      i = end - 1;
    } else if (t.type === "text") out.push(t.content);
    else if (t.type === "code_inline") out.push(<code key={i}>{t.content}</code>);
    else if (t.type === "image") {
      const alt = plainText(t.children ?? []);
      out.push(<span key={i}><img src={t.attrGet("src") ?? ""} alt={alt} /><span hidden>{alt}</span></span>);
    } else if (t.type === "softbreak" || t.type === "hardbreak") out.push("\n");
    // html_inline: dropped, same as the plaintext rule (its content is only the tag; any text
    // around it is a separate "text" token already handled above).
  }
  return out;
}

export function renderBlock(body: string): { content: ReactNode[]; artifacts: ArtifactDirective[] } {
  const tokens = md.parse(body, {}), source = body.split(/\r\n|\r|\n/);
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
        const lines: Token[][] = [[]], delims: Token[] = [];
        for (const child of t.children ?? []) {
          if (child.type === "softbreak" || child.type === "hardbreak") { delims.push(child); lines.push([]); }
          else lines[lines.length - 1].push(child);
        }
        const raws = rawLines(t, source, listStarts);
        const nodes: ReactNode[] = [];
        let hadText = false, pendingHard = false;
        lines.forEach((line, index) => {
          if (index > 0 && delims[index - 1].type === "hardbreak") pendingHard = true;
          const match = raws[index]?.match(directive);
          if (match) {
            const found: ArtifactDirective = { type: match[1], ref: match[2] };
            artifacts.push(found);
            nodes.push(<ArtifactHost key={`${i}-${index}`} directive={found} />);
            return;
          }
          if (hadText) { if (pendingHard) nodes.push(<br key={`${i}-${index}-br`} />); nodes.push("\n"); }
          pendingHard = false;
          nodes.push(...inline(line));
          hadText = true;
        });
        // Keep artifact widgets at their directive position even when the whole line/block is
        // otherwise empty; only join with the surrounding blocks when this one has real text.
        if (nodes.length) { if (hadText && hasText) out.push("\n"); out.push(...nodes); if (hadText) hasText = true; }
      } else if (t.type === "fence" || t.type === "code_block") {
        const value = t.content.replace(/\n$/, "");
        if (hasText) out.push("\n");
        out.push(<pre key={i}><code>{value}</code></pre>);
        hasText = true;
      } else if (t.type === "html_block") {
        const value = md.utils.unescapeAll(stripTags(t.content)).trim();
        if (value) { if (hasText) out.push("\n"); out.push(value); hasText = true; }
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
  return <article><h1>{loaded.title}</h1>{loaded.blocks.map((item) =>
    <div key={item.id} data-doc-id={loaded.id} data-block-id={item.id}>{renderBlock(item.body).content}</div>,
  )}</article>;
}
