/**
 * v3 SSE stream parser — one yield per channel-keyed event.
 *
 * Replaces the ``OssiaEvent``-style parser. Each SSE message carries
 * a ``channel`` key in its data dict and an ``event:`` field that
 * matches. The parser yields ``{channel, data}`` dicts so downstream
 * subscribers handle only their projection.
 */

export interface StreamChunk {
  channel: string;
  data: Record<string, unknown>;
}

export async function* parseV3SSEStream(
  response: Response,
): AsyncGenerator<StreamChunk> {
  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let currentChannel = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) {
      if (buffer.trim()) {
        const parsed = parseSSEBlock(buffer, currentChannel);
        for (const chunk of parsed) yield chunk;
      }
      break;
    }

    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split("\n\n");
    buffer = parts.pop() ?? "";

    for (const part of parts) {
      if (!part.trim()) continue;
      const { channel, data } = parseSSEMessage(part);
      if (channel && data) {
        currentChannel = channel;
        yield { channel, data };
      }
    }
  }
}

function parseSSEMessage(block: string): { channel: string; data: Record<string, unknown> | null } {
  const lines = block.split("\n");
  let channel = "";
  let jsonData = "";

  for (const line of lines) {
    if (line.startsWith("event: ")) {
      channel = line.slice(7).trim();
    } else if (line.startsWith("data: ")) {
      jsonData = line.slice(6);
    }
  }

  if (!channel || !jsonData) return { channel: "", data: null };

  try {
    const data = JSON.parse(jsonData) as Record<string, unknown>;
    return { channel, data };
  } catch {
    return { channel: "", data: null };
  }
}

export async function checkHealth(config: { apiUrl: string }): Promise<boolean> {
  try {
    const response = await fetch(`${config.apiUrl}/health`, {
      signal: AbortSignal.timeout(3000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

function parseSSEBlock(block: string, defaultChannel: string): StreamChunk[] {
  const { channel, data } = parseSSEMessage(block);
  if (!channel || !data) return [];
  return [{ channel: channel || defaultChannel, data }];
}
