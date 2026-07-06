/**
 * Ossia TUI — Session cache module.
 *
 * Reads and writes ``.kilocode/active_session.json`` via Bun's filesystem API
 * so that the TUI persists and re-joins sessions across restarts.
 *
 * Schema matches ``SessionMetadata`` in ``src/core/utils/session.py``.
 */

import { readdirSync, unlinkSync } from "fs";

/** Mirrors the Python SessionMetadata dataclass. */
export interface SessionMetadata {
  session_id: string;
  topic: string;
  project_context: string;
  created_at: string;
  is_random: boolean;
}

/** Expected location of the active session cache, relative to the repo root. */
const KILOCODE_REL = ".kilocode/active_session.json";

/**
 * Full path to the ``.kilocode/active_session.json`` cache file.
 * Searches upward from CWD for a ``.git`` directory to find the repo root.
 */
function cachePath(): string {
  const cwd = process.cwd();
  let dir = cwd;
  while (dir !== "/") {
    try {
      if (readdirSync(dir).includes(".git")) return `${dir}/${KILOCODE_REL}`;
    } catch {
      break;
    }
    const next = dir.substring(0, dir.lastIndexOf("/")) || "/";
    if (next === dir) break;
    dir = next;
  }
  return `${cwd}/${KILOCODE_REL}`;
}

/**
 * Read the active session from ``.kilocode/active_session.json``.
 * Returns ``null`` when the file does not exist or is malformed.
 */
export async function readActiveSession(): Promise<SessionMetadata | null> {
  try {
    const path = cachePath();
    const file = Bun.file(path);
    const exists = await file.exists();
    if (!exists) return null;
    const text = await file.text();
    return JSON.parse(text) as SessionMetadata;
  } catch {
    return null;
  }
}

/**
 * Write session metadata to ``.kilocode/active_session.json``.
 * Creates the ``.kilocode/`` directory if it does not exist.
 */
export async function writeActiveSession(meta: SessionMetadata): Promise<void> {
  const path = cachePath();
  const dir = path.substring(0, path.lastIndexOf("/"));
  // Ensure .kilocode/ directory exists
  await Bun.$`mkdir -p ${dir}`.quiet();
  await Bun.write(Bun.file(path), JSON.stringify(meta, null, 2));
}

/**
 * Remove ``.kilocode/active_session.json`` from disk.
 * Returns ``true`` if the file existed and was removed, ``false`` otherwise.
 */
export function clearActiveSession(): boolean {
  try {
    const path = cachePath();
    unlinkSync(path);
    return true;
  } catch {
    return false;
  }
}
