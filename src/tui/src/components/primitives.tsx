/**
 * PascalCase wrappers around OpenTUI's lowercase JSX primitives.
 *
 * OpenTUI registers <box>, <text>, <input>, <scrollbox> as lowercase
 * JSX tags. The linter (oxlint / react-doctor) treats any lowercase
 * JSX tag as an HTML element and flags props that aren't in the HTML
 * spec — even though OpenTUI is a terminal UI library, not HTML.
 *
 * PascalCase tags are skipped by the rule (treated as React components).
 * We re-export each primitive as a thin wrapper that calls
 * React.createElement with the lowercase tag string — no JSX literal
 * appears in this file, so the linter has nothing to misclassify.
 */
import { createElement } from "react";
import type {
  BoxProps,
  InputProps,
  ScrollBoxProps,
  TextProps,
} from "@opentui/react";

export function Box(props: BoxProps) {
  return createElement("box", props);
}

export function Text(props: TextProps) {
  return createElement("text", props);
}

export function Input(props: InputProps) {
  return createElement("input", props);
}

export function ScrollBox(props: ScrollBoxProps) {
  return createElement("scrollbox", props);
}
