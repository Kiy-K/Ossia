/**
 * Global type declarations for OpenTUI TUI.
 *
 * OpenTUI's component props don't include convenience style
 * attributes like `bold`/`dim` or event handlers like `onClick`.
 * These props work at runtime but need type suppression.
 *
 * Since @opentui/core and @opentui/react use restrictive exports
 * maps in their package.json, module augmentation on subpaths
 * doesn't work. Instead, component files use the SGR `attributes`
 * prop (1=bold, 2=dim) and @ts-expect-error for onClick.
 */

// ponytail: react-test-renderer is deprecated but still the only way to
// render hook-based OpenTUI components in unit tests (no @testing-library
// equivalent for terminal UIs). Minimal stub — migrate when a terminal
// testing-library exists.
declare module "react-test-renderer" {
  const TestRenderer: {
    create: (node: unknown) => { toJSON: () => unknown };
  };
  export default TestRenderer;
}
