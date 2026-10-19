import type { Conversation } from "../state/useChat";

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  roleLabel: string;
  subject: string;
  onNew: () => void;
  onSelect: (id: string) => void;
}

export function Sidebar({ conversations, activeId, roleLabel, subject, onNew, onSelect }: Props) {
  return (
    <nav className="panel sidebar" aria-label="Conversations">
      <button type="button" className="primary" onClick={onNew}>＋ New conversation</button>

      <p className="section-label">Conversations (this session)</p>
      <ul className="conversation-list">
        {conversations.length === 0 && <li className="muted">No conversations yet.</li>}
        {conversations.map((c) => (
          <li key={c.id}>
            <button type="button" aria-current={c.id === activeId} onClick={() => onSelect(c.id)}>
              {c.title}
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
