// TOC over a session's pinned topic subtree (#106). The session resource
// carries a copy of the selected topic's subtree (`tree`), so the TOC must
// walk it: a topic's own docs live in `docs[]`, but a parent topic's docs can
// live in its subtopics. The per-topic reading list (`GET /textbook/docs?
// topic_id=`) is already in `topic.docs[]` order (generated docs last).

export type TocTopic = {
  id: string;
  title: string;
  docs?: string[];
  topics?: TocTopic[];
};

export type TocDoc = { id: string; title: string; labels: string[] };

export type TocSection = {
  id: string;
  title: string;
  /** 0 is the selected topic itself; deeper is a subtopic (rendered as a heading). */
  depth: number;
  docs: TocDoc[];
};

/** Every topic id in the subtree, pre-order, so the component fetches each reading list once. */
export function topicIds(tree: TocTopic): string[] {
  return [tree.id, ...(tree.topics ?? []).flatMap(topicIds)];
}

/**
 * One section per topic that has docs, in depth-first tree order; each section
 * holds the topic's fetched reading list unchanged. Topics without docs are
 * skipped, so a parent whose docs live in its subtopics yields only those
 * subtopic sections instead of an empty heading.
 */
export function tocSections(tree: TocTopic, docs: Map<string, TocDoc[]>): TocSection[] {
  const sections: TocSection[] = [];
  const walk = (topic: TocTopic, depth: number) => {
    const topicDocs = docs.get(topic.id) ?? [];
    if (topicDocs.length) sections.push({ id: topic.id, title: topic.title, depth, docs: topicDocs });
    for (const child of topic.topics ?? []) walk(child, depth + 1);
  };
  walk(tree, 0);
  return sections;
}
