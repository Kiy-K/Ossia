/**
 * localStorage key constants for the Ossia Web UI.
 *
 * All keys use the `ossia:` prefix to avoid collisions with other apps.
 * Import these constants instead of hardcoding key strings.
 *
 * ⚠️ If you add a key here, update the blocking script in `index.html`
 *    which duplicates the `ossia:darkMode` key for the flash-free startup.
 */

export const STORAGE_KEYS = {
  /** Active panel tab: "chat" | "subagents" | "tools" | "react" */
  ACTIVE_PANEL: "ossia:activePanel",

  /** Dark mode toggle: "true" | "false" */
  DARK_MODE: "ossia:darkMode",

  /** Backend API base URL */
  API_URL: "ossia:apiUrl",

  /** Backend API key */
  API_KEY: "ossia:apiKey",
} as const;
