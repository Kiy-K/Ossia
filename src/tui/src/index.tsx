/**
 * Ossia TUI — Entry point.
 *
 * Creates the OpenTUI CLI renderer, creates a React root, and renders
 * the App component.
 */

import { createCliRenderer } from "@opentui/core";
import { createRoot } from "@opentui/react";

import { App } from "./App";

const renderer = await createCliRenderer({
  exitOnCtrlC: true,
  targetFps: 30,
});

createRoot(renderer).render(<App />);
