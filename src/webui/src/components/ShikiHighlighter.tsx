/**
 * Shiki v4 syntax highlighter for assistant-ui's ``MarkdownTextPrimitive``.
 *
 * Implements the ``SyntaxHighlighter`` component contract from
 * ``@assistant-ui/react-markdown``, using Shiki v4's ``createHighlighterCore``
 * for oniguruma-backed syntax highlighting.
 *
 * The highlighter is created lazily as a module-level singleton so it is
 * initialised only once and shared across all code block renders.
 */

import { useEffect, useRef, useState, type ComponentType } from "react";
import { createHighlighterCore, type HighlighterCore } from "shiki/core";
import { createOnigurumaEngine } from "shiki/engine/oniguruma";
import { getWasmInstance } from "shiki/wasm";
import { bundledThemes } from "shiki/themes";
import { bundledLanguages } from "shiki/langs";
import type { SyntaxHighlighterProps } from "@assistant-ui/react-markdown";

// ── Lazy singleton highlighter ─────────────────────────────────────────────

let highlighterPromise: Promise<HighlighterCore> | null = null;

function getHighlighter(): Promise<HighlighterCore> {
  if (!highlighterPromise) {
    highlighterPromise = (async () => {
      return createHighlighterCore({
        themes: [
          await bundledThemes["github-dark-dimmed"](),
          await bundledThemes["github-light-default"](),
        ],
        langs: [
          await bundledLanguages.typescript(),
          await bundledLanguages.javascript(),
          await bundledLanguages.tsx(),
          await bundledLanguages.jsx(),
          await bundledLanguages.python(),
          await bundledLanguages.html(),
          await bundledLanguages.css(),
          await bundledLanguages.json(),
          await bundledLanguages.bash(),
          await bundledLanguages.markdown(),
          await bundledLanguages.yaml(),
          await bundledLanguages.rust(),
          await bundledLanguages.go(),
        ],
        engine: createOnigurumaEngine(getWasmInstance),
      });
    })();
  }
  return highlighterPromise;
}

// ── Try to load a language on the fly ───────────────────────────────────────

const HIGH_LANG = /^[a-z]\w*$/;

async function ensureLanguage(
  highlighter: HighlighterCore,
  lang: string,
): Promise<void> {
  // If empty / text, skip
  if (!lang || lang === "text") return;
  const loaded = highlighter.getLoadedLanguages();
  if (loaded.includes(lang as never)) return;

  // Attempt dynamic load from bundledLanguages
  const loader = (bundledLanguages as Record<string, () => Promise<unknown>>)[
    lang
  ];
  if (loader) {
    await highlighter.loadLanguage(await loader());
  } else if (HIGH_LANG.test(lang)) {
    // try lowercased alias
    const alt = lang.toLowerCase();
    const altLoader = (
      bundledLanguages as Record<string, () => Promise<unknown>>
    )[alt];
    if (altLoader) {
      await highlighter.loadLanguage(await altLoader());
    }
  }
}

// ── Theme name helpers ──────────────────────────────────────────────────────

function currentTheme(): string {
  return document.documentElement.classList.contains("dark")
    ? "github-dark-dimmed"
    : "github-light-default";
}

// ── SyntaxHighlighter component ─────────────────────────────────────────────

/**
 * Custom code-block syntax highlighter using Shiki v4.
 *
 * Renders highlighted code as static HTML via ``dangerouslySetInnerHTML``.
 * Falls back to the default ``Pre`` / ``Code`` wrapper components while
 * Shiki is loading or if highlighting fails.
 */
export const ShikiHighlighter: ComponentType<SyntaxHighlighterProps> = ({
  language,
  code,
  components: { Pre, Code },
}) => {
  const [html, setHtml] = useState<string | null>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    setHtml(null);

    const lang = language && language !== "" ? language : "text";

    (async () => {
      try {
        const highlighter = await getHighlighter();
        await ensureLanguage(highlighter, lang);

        if (!mountedRef.current) return;

        const highlighted = highlighter.codeToHtml(code, {
          lang,
          theme: currentTheme(),
        });
        if (mountedRef.current) {
          setHtml(highlighted);
        }
      } catch {
        // Fallback to default Pre/Code rendering
        if (mountedRef.current) {
          setHtml(null);
        }
      }
    })();

    return () => {
      mountedRef.current = false;
    };
  }, [language, code]);

  // While loading or on error, render the default Pre > Code fallback
  if (!html) {
    return (
      <Pre>
        <Code>{code}</Code>
      </Pre>
    );
  }

  // Render the fully-highlighted HTML from Shiki
  return <div className="not-prose" dangerouslySetInnerHTML={{ __html: html }} />;
};
