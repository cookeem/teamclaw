import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";

const markdown = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

const defaultLinkRenderer =
  markdown.renderer.rules.link_open ??
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));

markdown.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet("target", "_blank");
  tokens[idx].attrSet("rel", "noopener noreferrer nofollow");
  return defaultLinkRenderer(tokens, idx, options, env, self);
};

export function renderMarkdown(rawText: string): string {
  const html = markdown.render(rawText);
  return DOMPurify.sanitize(html);
}
