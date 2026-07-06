/**
 * Markdown rendering component wrapping ``@assistant-ui/react-markdown``.
 *
 * Provides a convenient component for rendering assistant text parts with
 * markdown formatting, code blocks, and Shiki v4 syntax highlighting.
 */

import { MarkdownTextPrimitive } from "@assistant-ui/react-markdown";
import { ShikiHighlighter } from "./ShikiHighlighter";

interface MarkdownTextProps {
  className?: string;
}

/**
 * Default markdown text component for assistant messages.
 *
 * Uses ``MarkdownTextPrimitive`` from ``@assistant-ui/react-markdown`` which
 * integrates ``react-markdown`` with smooth streaming-aware rendering.
 *
 * Code blocks are highlighted with Shiki v4 via ``SyntaxHighlighter``.
 */
export function MarkdownText({ className }: MarkdownTextProps) {
  return (
    <MarkdownTextPrimitive
      className={className}
      defer={true}
      components={{
        SyntaxHighlighter: ShikiHighlighter,
      }}
    />
  );
}
