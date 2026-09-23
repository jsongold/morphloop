// Minimal interpreter for the pack layout document (GET /sessions/{id}/layout).
//
// v0.1 fixes no generic layout schema (ADR-0003, ADR-0012; contracts/schemas/
// pack/layout.json is deliberately permissive). This reads only the keys the
// first pack declares and ignores everything else. Generalize in Slice 4
// (AC-H1), not here.
//
// Understood shape (all keys optional; unknown keys ignored):
//   { toc: { chapters: { title: string, items: { kind, id }[] }[] },
//     layout: {
//       left:   { component: string },
//       main:   { modes: string[], default_mode: string, components: string[] },
//       side:   { component: string },
//       bottom: { component: string, persistent: boolean } } }
//
// No harness defaults (ADR-0002): a region the pack does not declare is not
// rendered, with one exception — the practice environment of a lab-backed
// activity is always mounted, because "practice first" is a product principle
// (docs/PRODUCT.md), not a tunable.

export interface TocItem {
  kind: "activity" | "reference" | "visualization";
  id: string;
}

export interface TocChapter {
  title: string;
  items: TocItem[];
}

export interface InterpretedLayout {
  leftComponent: string | null;
  /** Navigation only: never locks items or forces an order. */
  toc: TocChapter[];
  modes: string[];
  defaultMode: string | null;
  mainComponents: string[];
  sideComponent: string | null;
  bottomComponent: string | null;
  bottomPersistent: boolean;
  declared: boolean;
}

function obj(v: unknown): Record<string, unknown> | null {
  return typeof v === "object" && v !== null && !Array.isArray(v)
    ? (v as Record<string, unknown>)
    : null;
}

function strings(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.length > 0 ? v : null;
}

export function interpretLayout(doc: Record<string, unknown> | null): InterpretedLayout {
  const root = obj(doc?.layout) ?? doc;
  const left = obj(root?.left);
  const main = obj(root?.main);
  const side = obj(root?.side);
  const bottom = obj(root?.bottom);
  const modes = strings(main?.modes);
  const defaultMode = str(main?.default_mode);
  // The Importer validated the toc against contracts/schemas/pack/layout.json.
  const toc = (obj(doc?.toc)?.chapters ?? []) as TocChapter[];
  return {
    leftComponent: str(left?.component),
    toc,
    modes,
    defaultMode: defaultMode && modes.includes(defaultMode) ? defaultMode : (modes[0] ?? null),
    mainComponents: strings(main?.components),
    sideComponent: str(side?.component),
    bottomComponent: str(bottom?.component),
    bottomPersistent: bottom?.persistent === true,
    declared: root !== null && root !== undefined,
  };
}

/**
 * What a main-pane mode token renders. Mode tokens are pack data; the
 * standard UI maps the ones it has components for and shows the practice
 * environment for any other token.
 */
export type ModeView = "practice" | "visualize" | "review";

export function modeView(mode: string | null): ModeView {
  if (mode === "visualize") return "visualize";
  if (mode === "review") return "review";
  return "practice";
}
