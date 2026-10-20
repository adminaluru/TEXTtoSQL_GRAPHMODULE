import type { Conversation } from "../state/useChat";
import { AnswerCard } from "./AnswerCard";
import { Composer } from "./Composer";
import { ErrorNotice } from "./ErrorNotice";

interface Props {
  conversation: Conversation | null;
  busy: boolean;
  canAsk: boolean;
  selectedMessageId: string | null;
  onSelectMessage: (id: string) => void;
  onSend: (question: string) => void;
  onChoose: (messageId: string, clarificationId: string, optionId: string, label: string) => void;
}

const time = (d: Date) => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

export function ChatPanel(props: Props) {
  const { conversation, busy, canAsk, selectedMessageId } = props;

  return (
    <main className="panel" aria-label="Conversation">
      <header className="chat-header">
        <h1>{conversation?.title ?? "New conversation"}</h1>
        <p>Ask about customers, invoices, payments and claims in the selected workspace.</p>
      </header>

      <div className="messages" aria-live="polite">
        {!conversation && (
          <div className="empty-state">
            <p><strong>Ask a question to start.</strong></p>
            <p>For example: “What is the total claim amount for Blue Prism?” or “How many open invoices are there?”</p>
          </div>
        )}

        {conversation && !conversation.loaded && !conversation.loadError && (
          <p className="loading-state" role="status">Loading conversation…</p>
        )}
        {conversation?.loadError && <ErrorNotice error={conversation.loadError} />}

        {conversation?.messages.map((m) => (
          <div key={m.id}>
            {m.role === "user" && <div className="bubble-user">{m.text}</div>}
            {m.role === "assistant" && m.answer && (
              <AnswerCard
                answer={m.answer}
                busy={busy}
                choicesLocked={!!m.choicesLocked}
                selected={selectedMessageId === m.id}
                onSelect={() => props.onSelectMessage(m.id)}
                onChoose={(cid, oid, label) => props.onChoose(m.id, cid, oid, label)}
              />
            )}
            {m.role === "error" && m.error && <ErrorNotice error={m.error} />}
            <div className="time">{time(m.at)}</div>
          </div>
        ))}

        {busy && <p className="thinking" role="status">Working on it…</p>}
      </div>

      <Composer busy={busy} disabled={!canAsk || (!!conversation && !conversation.loaded)} onSend={props.onSend} />
    </main>
  );
}
