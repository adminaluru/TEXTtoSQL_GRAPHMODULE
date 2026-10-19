import { useState } from "react";

interface Props {
  busy: boolean;
  disabled: boolean;
  onSend: (question: string) => void;
}

/** Question box. Enter sends, Shift+Enter adds a line; blocked while an answer is on its way. */
export function Composer({ busy, disabled, onSend }: Props) {
  const [text, setText] = useState("");
  const canSend = !busy && !disabled && text.trim().length > 0;

  const send = () => {
    if (!canSend) return;
    onSend(text);
    setText("");
  };

  return (
    <form
      className="composer"
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
    >
      <label htmlFor="question" className="visually-hidden">Ask about your business data</label>
      <textarea
        id="question"
        rows={1}
        maxLength={5000}
        placeholder="Ask about your business data…"
        value={text}
        disabled={disabled}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            send();
          }
        }}
      />
      <button type="submit" className="primary" disabled={!canSend} aria-label="Send question">
        Send
      </button>
    </form>
  );
}
