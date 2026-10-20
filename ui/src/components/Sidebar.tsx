import type { ChatError, Conversation, ListStatus } from "../state/useChat";

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  listStatus: ListStatus;
  listError: ChatError | null;
  roleLabel: string;
  subject: string;
  onNew: () => void;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}

export function Sidebar(props: Props) {
  const { conversations, activeId, listStatus, listError, roleLabel, subject } = props;

  return (
    <nav className="panel sidebar" aria-label="Conversations">
      <button type="button" className="primary" onClick={props.onNew}>＋ New conversation</button>

      <p className="section-label">Your conversations</p>
      {listStatus === "loading" && conversations.length === 0 && <p className="muted" role="status">Loading…</p>}
      {listError && <p className="notice" role="alert">{listError.message}</p>}
      <ul className="conversation-list">
        {listStatus !== "loading" && conversations.length === 0 && <li className="muted">No conversations yet.</li>}
        {conversations.map((c) => (
          <li key={c.id} className="conversation-item">
            <button type="button" aria-current={c.id === activeId} onClick={() => props.onSelect(c.id)}>
              {c.title}
            </button>
            <button
              type="button"
              className="icon-button"
              aria-label={`Delete conversation: ${c.title}`}
              title="Delete conversation"
              onClick={() => {
                if (window.confirm(`Delete “${c.title}”? This cannot be undone.`)) props.onDelete(c.id);
              }}
            >
              ×
            </button>
          </li>
        ))}
      </ul>

      <div className="sidebar-footer">
        {/* Shown for information only: what a role may see is enforced by the server. */}
        <strong>{roleLabel}</strong>
        <span className="muted">{subject}</span>
      </div>
    </nav>
  );
}
