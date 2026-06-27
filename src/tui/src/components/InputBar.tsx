/**
 * InputBar — chat message input.
 *
 * Bottom-fixed input bar. User types a message and presses Enter to
 * submit. Clears on submit. Disabled while the backend is processing.
 */

import { useState } from "react";

interface InputBarProps {
  /** Called when the user submits a message. */
  onSubmit: (message: string) => void;
  /** Whether the backend is currently processing a request. */
  disabled: boolean;
}

export function InputBar({ onSubmit, disabled }: InputBarProps) {
  const [value, setValue] = useState("");

  const handleSubmit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    setValue("");
    onSubmit(trimmed);
  };

  return (
    <box height={1} width="100%" flexDirection="row">
      <text>{">"}</text>
      <text> </text>
      {disabled ? (
        <text dim>Thinking...</text>
      ) : (
        <input
          width={200}
          placeholder="Type a message"
          onInput={(v: string) => setValue(v)}
          onSubmit={handleSubmit}
          focused={true}
        />
      )}
    </box>
  );
}
