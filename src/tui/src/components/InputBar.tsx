/**
 * InputBar — chat message input.
 *
 * Bottom-fixed input bar. User types a message and presses Enter to
 * submit. Clears on submit. Disabled while the backend is processing.
 */

import { useRef } from "react";
import { Box, Input, Text } from "./primitives";

interface InputBarProps {
  /** Called when the user submits a message. */
  onSubmit: (message: string) => void;
  /** Whether the backend is currently processing a request. */
  disabled: boolean;
  /** Active session topic. */
  sessionTopic: string;
}

export function InputBar({ onSubmit, disabled, sessionTopic }: InputBarProps) {
  const valueRef = useRef("");

  const handleSubmit = () => {
    const trimmed = valueRef.current.trim();
    if (!trimmed || disabled) return;
    valueRef.current = "";
    onSubmit(trimmed);
  };

  return (
    <Box height={1} width="100%" flexDirection="row">
      {/* Session topic badge */}
      <Text attributes={2}>[{sessionTopic}]</Text>
      <Text> </Text>
      <Text>{">"}</Text>
      <Text> </Text>
      {disabled ? (
        <Text attributes={2}>Thinking...</Text>
      ) : (
        <Input
          width={160}
          placeholder="Type a message"
          aria-label="Type a message"
          onInput={(v: string) => { valueRef.current = v; }}
          onSubmit={handleSubmit}
          focused={true}
        />
      )}
    </Box>
  );
}
