import type { OssiaEvent, Config } from "./types";

/**
 * Parse an SSE response body into an async generator of OssiaEvent.
 * Handles the standard SSE format:
 * ```
 * event: message_delta
 * id: 42
 * data: {"seq":42,"type":"message_delta",...}
 * ```
 */
export async function* parseSSEStream(
  response: Response,
): AsyncGenerator<OssiaEvent> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      if (buffer.trim()) {
        const parsed = parseSSEBuffer(buffer);
        for (const event of parsed) {
          yield event;
        }
      }
      break;
    }

    buffer += decoder.decode(value, { stream: true });

    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";

    for (const part of parts) {
      if (!part.trim()) continue;
      const parsed = parseSSEBuffer(part);
      for (const event of parsed) {
        yield event;
      }
    }
  }
}

function parseSSEBuffer(block: string): OssiaEvent[] {
  const lines = block.split("\n");
  let jsonData = "";

  for (const line of lines) {
    if (line.startsWith("data: ")) {
      jsonData = line.slice(6);
    }
  }

  if (!jsonData) return [];

  try {
    const parsed = JSON.parse(jsonData) as OssiaEvent;
    return [parsed];
  } catch {
    return [];
  }
}

/**
 * Check if the backend server is reachable.
 */
export async function checkHealth(config: Config): Promise<boolean> {
  try {
    const response = await fetch(`${config.apiUrl}/health`, {
      signal: AbortSignal.timeout(3000),
    });
    return response.ok;
  } catch {
    return false;
  }
}
