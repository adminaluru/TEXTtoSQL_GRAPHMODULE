// What the chat remembers, and the rules that keep it safe:
//
// - one conversation list per company (tenant); switching company never shows
//   or reuses another company's conversations or pending choices;
// - only one request at a time per screen (double clicks are ignored);
// - an answer, list or saved conversation that arrives after the company or
//   conversation changed is dropped, never shown in the wrong place;
// - a clarification can be answered once; its buttons then lock.
//
// Conversations are saved by the server (Step 12c): the list is loaded when a
// company is chosen, and a conversation's messages when it is opened. Saved
// clarification choices are shown locked: they expire on the server, so the
// question is asked again instead.

import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiClient, Caller } from "../api/client";
import { ApiError, fallbackMessage } from "../api/errors";
import type { AnswerResponse, ConversationDetail, ConversationInfo } from "../api/types";

export interface ChatError {
  kind: ApiError["kind"];
  message: string;
  requestId: string | null;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "error";
  at: Date;
  text?: string;
  answer?: AnswerResponse;
  error?: ChatError;
  choicesLocked?: boolean;
}

export interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  // false until the saved messages have been fetched (always true for one
  // started in this session).
  loaded: boolean;
  loadError?: ChatError;
}

export interface ChatHandlers {
  onUnauthenticated: () => void;
  onForbidden: () => void;
}

export type ListStatus = "idle" | "loading" | "loaded" | "error";

const newId = () => crypto.randomUUID();

function toChatError(error: unknown): ChatError {
  if (error instanceof ApiError) {
    return { kind: error.kind, message: error.message, requestId: error.requestId };
  }
  return { kind: "server", message: fallbackMessage("server"), requestId: null };
}

function titleFrom(question: string): string {
  const clean = question.trim().replace(/\s+/g, " ");
  return clean.length > 40 ? `${clean.slice(0, 40)}…` : clean;
}

function fromSummary(info: ConversationInfo): Conversation {
  return { id: info.conversation_id, title: info.title, messages: [], loaded: false };
}

/** Saved messages as chat messages; saved choices stay locked. */
export function toMessages(detail: ConversationDetail): Message[] {
  return detail.messages.map((saved) => {
    const at = new Date(saved.created_at);
    if (saved.kind === "question") return { id: newId(), role: "user", at, text: saved.content };
    if (saved.kind === "choice") {
      return { id: newId(), role: "user", at, text: saved.content.label ?? "Chose an option" };
    }
    return { id: newId(), role: "assistant", at, answer: saved.content, choicesLocked: true };
  });
}

export function useChat(api: ApiClient, caller: Caller | null, handlers: ChatHandlers) {
  const [byTenant, setByTenant] = useState<Record<string, Conversation[]>>({});
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [listStatus, setListStatus] = useState<ListStatus>("idle");
  const [listError, setListError] = useState<ChatError | null>(null);
  const busyRef = useRef(false);
  // Changes whenever the company or conversation changes; late answers
  // carrying an older value are dropped.
  const epoch = useRef(0);
  // Changes whenever the company changes; a late conversation list is dropped.
  const listEpoch = useRef(0);
  // Numbers each request; only the newest one may clear "busy".
  const requestSeq = useRef(0);

  const tenant = caller?.tenant ?? null;
  const conversations = tenant ? byTenant[tenant] ?? [] : [];
  const active = conversations.find((c) => c.id === activeId) ?? null;

  const update = useCallback(
    (forTenant: string, conversationId: string, change: (c: Conversation) => Conversation) => {
      setByTenant((all) => ({
        ...all,
        [forTenant]: (all[forTenant] ?? []).map((c) => (c.id === conversationId ? change(c) : c)),
      }));
    },
    [],
  );

  const reportAuth = useCallback(
    (error: ChatError) => {
      if (error.kind === "unauthenticated") handlers.onUnauthenticated();
      if (error.kind === "forbidden") handlers.onForbidden();
    },
    [handlers],
  );

  // Saved conversations of the active company. Ones started in this session
  // and not yet in the server's list stay at the top.
  useEffect(() => {
    if (!caller?.tenant) {
      setListStatus("idle");
      return;
    }
    const forTenant = caller.tenant;
    const myEpoch = ++listEpoch.current;
    setListStatus("loading");
    setListError(null);
    api.listConversations(caller).then(
      (list) => {
        if (listEpoch.current !== myEpoch) return;
        setByTenant((all) => {
          const local = all[forTenant] ?? [];
          const saved = list.conversations.map(
            (info) => local.find((c) => c.id === info.conversation_id) ?? fromSummary(info),
          );
          const unsaved = local.filter((c) => !list.conversations.some((i) => i.conversation_id === c.id));
          return { ...all, [forTenant]: [...unsaved, ...saved] };
        });
        setListStatus("loaded");
      },
      (error) => {
        if (listEpoch.current !== myEpoch) return;
        const chatError = toChatError(error);
        setListError(chatError);
        setListStatus("error");
        reportAuth(chatError);
      },
    );
    // Only the identity and the company decide which list is shown.
  }, [api, caller?.token, caller?.tenant]);

  /** Leave the current view: a request still running is abandoned and its answer dropped. */
  const abandonInFlight = useCallback(() => {
    epoch.current += 1;
    requestSeq.current += 1;
    busyRef.current = false;
    setBusy(false);
  }, []);

  const startConversation = useCallback(() => {
    abandonInFlight();
    setActiveId(null);
  }, [abandonInFlight]);

  const selectConversation = useCallback(
    (id: string) => {
      abandonInFlight();
      setActiveId(id);
      if (!caller?.tenant) return;
      const forTenant = caller.tenant;
      const target = (byTenant[forTenant] ?? []).find((c) => c.id === id);
      if (!target || target.loaded) return;
      const myEpoch = epoch.current;
      update(forTenant, id, (c) => ({ ...c, loadError: undefined }));
      api.getConversation(caller, id).then(
        (detail) => {
          if (epoch.current !== myEpoch) return;
          update(forTenant, id, (c) => ({ ...c, title: detail.title, messages: toMessages(detail), loaded: true }));
        },
        (error) => {
          if (epoch.current !== myEpoch) return;
          const chatError = toChatError(error);
          update(forTenant, id, (c) => ({ ...c, loadError: chatError }));
          reportAuth(chatError);
        },
      );
    },
    [abandonInFlight, api, byTenant, caller, reportAuth, update],
  );

  const removeConversation = useCallback(
    async (id: string) => {
      if (!caller?.tenant) return;
      const forTenant = caller.tenant;
      try {
        await api.deleteConversation(caller, id);
      } catch (error) {
        const chatError = toChatError(error);
        // Already gone on the server: remove it here too.
        if (chatError.kind !== "not_available") {
          setListError(chatError);
          reportAuth(chatError);
          return;
        }
      }
      setByTenant((all) => ({ ...all, [forTenant]: (all[forTenant] ?? []).filter((c) => c.id !== id) }));
      if (activeId === id) {
        abandonInFlight();
        setActiveId(null);
      }
    },
    [abandonInFlight, activeId, api, caller, reportAuth],
  );

  /** Call when the active company changes. */
  const resetForTenantChange = useCallback(() => {
    abandonInFlight();
    listEpoch.current += 1;
    setActiveId(null);
  }, [abandonInFlight]);

  const run = useCallback(
    async (conversationId: string, call: () => Promise<AnswerResponse>, after?: (ok: boolean) => void) => {
      if (!caller?.tenant) return;
      const forTenant = caller.tenant;
      const myEpoch = epoch.current;
      const myRequest = ++requestSeq.current;
      busyRef.current = true;
      setBusy(true);
      try {
        const answer = await call();
        if (epoch.current !== myEpoch) return; // company/conversation changed meanwhile
        update(forTenant, conversationId, (c) => ({
          ...c,
          messages: [...c.messages, { id: newId(), role: "assistant", at: new Date(), answer }],
        }));
        after?.(true);
      } catch (error) {
        if (epoch.current !== myEpoch) return;
        const chatError = toChatError(error);
        update(forTenant, conversationId, (c) => ({
          ...c,
          messages: [...c.messages, { id: newId(), role: "error", at: new Date(), error: chatError }],
        }));
        after?.(false);
        reportAuth(chatError);
      } finally {
        if (requestSeq.current === myRequest) {
          busyRef.current = false;
          setBusy(false);
        }
      }
    },
    [caller, reportAuth, update],
  );

  const send = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || busyRef.current || !caller?.tenant) return;
      const forTenant = caller.tenant;
      let conversationId = activeId;
      const userMessage: Message = { id: newId(), role: "user", at: new Date(), text };
      const existing = (byTenant[forTenant] ?? []).find((c) => c.id === conversationId);
      // A saved conversation still loading cannot take new messages yet.
      if (existing && !existing.loaded) return;
      if (!conversationId || !existing) {
        conversationId = newId();
        const created: Conversation = { id: conversationId, title: titleFrom(text), messages: [userMessage], loaded: true };
        setByTenant((all) => ({ ...all, [forTenant]: [created, ...(all[forTenant] ?? [])] }));
        setActiveId(conversationId);
      } else {
        update(forTenant, conversationId, (c) => ({ ...c, messages: [...c.messages, userMessage] }));
      }
      const id = conversationId;
      await run(id, () => api.ask(caller, text, id));
    },
    [activeId, api, byTenant, caller, run, update],
  );

  const choose = useCallback(
    async (messageId: string, clarificationId: string, optionId: string, label: string) => {
      if (busyRef.current || !caller?.tenant || !active) return;
      const forTenant = caller.tenant;
      const conversationId = active.id;
      update(forTenant, conversationId, (c) => ({
        ...c,
        messages: [
          ...c.messages.map((m) => (m.id === messageId ? { ...m, choicesLocked: true } : m)),
          { id: newId(), role: "user" as const, at: new Date(), text: label },
        ],
      }));
      await run(conversationId, () => api.clarify(caller, conversationId, clarificationId, optionId));
    },
    [active, api, caller, run, update],
  );

  return {
    conversations,
    active,
    busy,
    listStatus,
    listError,
    send,
    choose,
    startConversation,
    selectConversation,
    removeConversation,
    resetForTenantChange,
  };
}
