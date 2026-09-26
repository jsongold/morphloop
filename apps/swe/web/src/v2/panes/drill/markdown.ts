import MarkdownIt from "markdown-it";

const markdown = new MarkdownIt({ html: false });

export const renderMarkdown = (source: string): string => markdown.render(source);
