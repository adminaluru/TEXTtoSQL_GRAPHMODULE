"""Saved conversations in one local SQLite file (Step 12c, decision D-012).

A conversation belongs to one user in one company: every read and write is
keyed by (tenant, subject, conversation id), so another user's or another
company's conversation is simply not found, even with the same id.
Conversations are kept until their owner deletes them (D-012).

What is saved, in order, per conversation:
    question   the text the user asked
    choice     a clarification option the user picked (id and its label)
    answer     the answer exactly as returned by the API (JSON)

Earlier messages are stored and shown, never used to answer a later question
(follow-up questions are a later step, D-012).
"""
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

TITLE_CHARS = 80

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    tenant_id       TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    title           TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    message_count   INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, subject_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS conversations_recent ON conversations (tenant_id, subject_id, updated_at);

CREATE TABLE IF NOT EXISTS messages (
    tenant_id       TEXT NOT NULL,
    subject_id      TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    ordinal         INTEGER NOT NULL,
    kind            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, subject_id, conversation_id, ordinal),
    FOREIGN KEY (tenant_id, subject_id, conversation_id)
        REFERENCES conversations (tenant_id, subject_id, conversation_id) ON DELETE CASCADE
);
"""

MessageKind = Literal["question", "choice", "answer"]


@dataclass(frozen=True)
class ConversationSummary:
    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int


@dataclass(frozen=True)
class SavedMessage:
    kind: MessageKind
    content: Any  # question: str; choice: {"option_id", "label"}; answer: the answer JSON
    created_at: datetime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SqliteConversationStore:
    def __init__(self, path: str | Path, clock=_utcnow):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._clock = clock
        with self._lock:
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(_SCHEMA)

    # ------------------------------------------------------------ writes

    def add_question(self, tenant_id: str, subject_id: str, conversation_id: str, question: str) -> None:
        self._append(tenant_id, subject_id, conversation_id, "question", question, title=question)

    def add_answer(self, tenant_id: str, subject_id: str, conversation_id: str, answer: dict) -> None:
        self._append(tenant_id, subject_id, conversation_id, "answer", answer)

    def add_choice(self, tenant_id: str, subject_id: str, conversation_id: str, option_id: str) -> None:
        """The option picked, with its label from this conversation's latest clarification (if found)."""
        label = None
        for message in reversed(self.messages(tenant_id, subject_id, conversation_id) or []):
            if message.kind != "answer":
                continue
            data = message.content.get("data") if isinstance(message.content, dict) else None
            options = data.get("options") if isinstance(data, dict) else None
            if isinstance(options, list):
                label = next((o.get("label") for o in options
                              if isinstance(o, dict) and o.get("option_id") == option_id), None)
                break
        self._append(tenant_id, subject_id, conversation_id, "choice", {"option_id": option_id, "label": label})

    def delete(self, tenant_id: str, subject_id: str, conversation_id: str) -> bool:
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM conversations WHERE tenant_id = ? AND subject_id = ? AND conversation_id = ?",
                (tenant_id, subject_id, conversation_id))
            return cursor.rowcount > 0

    # ------------------------------------------------------------ reads

    def recent(self, tenant_id: str, subject_id: str, limit: int = 100) -> list[ConversationSummary]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT conversation_id, title, created_at, updated_at, message_count FROM conversations "
                "WHERE tenant_id = ? AND subject_id = ? ORDER BY updated_at DESC, conversation_id LIMIT ?",
                (tenant_id, subject_id, limit)).fetchall()
        return [ConversationSummary(r["conversation_id"], r["title"], datetime.fromisoformat(r["created_at"]),
                                    datetime.fromisoformat(r["updated_at"]), r["message_count"]) for r in rows]

    def summary(self, tenant_id: str, subject_id: str, conversation_id: str) -> ConversationSummary | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT conversation_id, title, created_at, updated_at, message_count FROM conversations "
                "WHERE tenant_id = ? AND subject_id = ? AND conversation_id = ?",
                (tenant_id, subject_id, conversation_id)).fetchone()
        if row is None:
            return None
        return ConversationSummary(row["conversation_id"], row["title"], datetime.fromisoformat(row["created_at"]),
                                   datetime.fromisoformat(row["updated_at"]), row["message_count"])

    def messages(self, tenant_id: str, subject_id: str, conversation_id: str) -> list[SavedMessage] | None:
        """The conversation's messages in order, or None when it does not exist for this owner."""
        if self.summary(tenant_id, subject_id, conversation_id) is None:
            return None
        with self._lock:
            rows = self._connection.execute(
                "SELECT kind, content, created_at FROM messages "
                "WHERE tenant_id = ? AND subject_id = ? AND conversation_id = ? ORDER BY ordinal",
                (tenant_id, subject_id, conversation_id)).fetchall()
        return [SavedMessage(r["kind"], json.loads(r["content"]), datetime.fromisoformat(r["created_at"]))
                for r in rows]

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    # ------------------------------------------------------------ internals

    def _append(self, tenant_id, subject_id, conversation_id, kind: MessageKind, content, title: str | None = None):
        now = self._clock().isoformat()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                # The first message creates the conversation; its title is the first question.
                self._connection.execute(
                    "INSERT INTO conversations (tenant_id, subject_id, conversation_id, title, created_at, "
                    "updated_at, message_count) VALUES (?, ?, ?, ?, ?, ?, 0) "
                    "ON CONFLICT (tenant_id, subject_id, conversation_id) DO NOTHING",
                    (tenant_id, subject_id, conversation_id, _title(title), now, now))
                row = self._connection.execute(
                    "SELECT message_count FROM conversations "
                    "WHERE tenant_id = ? AND subject_id = ? AND conversation_id = ?",
                    (tenant_id, subject_id, conversation_id)).fetchone()
                ordinal = row["message_count"]
                self._connection.execute(
                    "INSERT INTO messages (tenant_id, subject_id, conversation_id, ordinal, kind, content, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (tenant_id, subject_id, conversation_id, ordinal, kind,
                     json.dumps(content, ensure_ascii=False, default=str), now))
                self._connection.execute(
                    "UPDATE conversations SET message_count = ?, updated_at = ? "
                    "WHERE tenant_id = ? AND subject_id = ? AND conversation_id = ?",
                    (ordinal + 1, now, tenant_id, subject_id, conversation_id))
                self._connection.execute("COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise


def _title(text: str | None) -> str:
    text = " ".join((text or "").split()) or "Conversation"
    return text if len(text) <= TITLE_CHARS else text[:TITLE_CHARS - 1].rstrip() + "…"
