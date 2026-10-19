// What the chat remembers, and the rules that keep it safe:
//
// - one conversation list per company (tenant); switching company never shows
//   or reuses another company's conversations or pending choices;
// - only one request at a time per screen (double clicks are ignored);
// - an answer that arrives after the company or conversation changed is
//   dropped, never shown in the wrong place;
// - a clarification can be answered once; its buttons then lock.
//
// Conversations live in memory for Phase 1 (saved history is Step 12).

import { useCallback, useRef, useState } from "react";

import type { ApiClient, Caller } from "../api/client";
import { ApiError, fallbackMessage } from "../api/errors";
import type { AnswerResponse } from "../api/types";

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
}

export interface ChatHandlers {
  onUnauthenticated: () => void;
  onForbidden: () => void;
}

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

export function useChat(api: ApiClient, caller: Caller | null, handlers: ChatHandlers) {
  const [byTenant, setByTenant] = useState<Record<string, Conversation[]>>({});
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  // Changes whenever the company or conversation changes; late answers
  // carrying an older value are dropped.
  const epoch = useRef(0);
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

  const selectConversation = useCallback((id: string) => {
    abandonInFlight();
    setActiveId(id);
  }, [abandonInFlight]);

  /** Call when the active company changes. */
  const resetForTenantChange = useCallback(() => {
    abandonInFlight();
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
        if (chatError.kind === "unauthenticated") handlers.onUnauthenticated();
        if (chatError.kind === "forbidden") handlers.onForbidden();
      } finally {
        if (requestSeq.current === myRequest) {
          busyRef.current = false;
          setBusy(false);
        }
      }
    },
    [caller, handlers, update],
  );

  const send = useCallback(
    async (question: string) => {
      const text = question.trim();
      if (!text || busyRef.current || !caller?.tenant) return;
      const forTenant = caller.tenant;
      let conversationId = activeId;
      const userMessage: Message = { id: newId(), role: "user", at: new Date(), text };
      if (!conversationId || !(byTenant[forTenant] ?? []).some((c) => c.id === conversationId)) {
        conversationId = newId();
        const created: Conversation = { id: conversationId, title: titleFrom(text), messages: [userMessage] };
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
    send,
    choose,
    startConversation,
    selectConversation,
    resetForTenantChange,
  };
}
