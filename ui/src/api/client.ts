// The only place that talks HTTP. Screens call these functions; they never
// call fetch themselves. Identity travels only as the bearer token, and the
// active company only as the X-FlowOne-Tenant header (the server verifies both).

import { ApiError, fallbackMessage, kindForStatus } from "./errors";
import type { AnswerResponse, DocumentInfo, DocumentList, DocumentSearchResponse, MeResponse } from "./types";

export const API_BASE = "/api";

export interface Caller {
  token: string;
  tenant: string | null;
}

type Fetch = typeof fetch;

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE";
  json?: unknown;
  // A file upload; the browser sets the multipart Content-Type and boundary.
  form?: FormData;
}

async function request<T>(fetchImpl: Fetch, path: string, caller: Caller, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${caller.token}` };
  if (caller.tenant) headers["X-FlowOne-Tenant"] = caller.tenant;
  if (options.json !== undefined) headers["Content-Type"] = "application/json";

  const hasBody = options.json !== undefined || options.form !== undefined;
  let response: Response;
  try {
    response = await fetchImpl(`${API_BASE}${path}`, {
      method: options.method ?? (hasBody ? "POST" : "GET"),
      headers,
      body: options.form ?? (options.json === undefined ? undefined : JSON.stringify(options.json)),
    });
  } catch {
    throw new ApiError("network", fallbackMessage("network"));
  }

  if (response.status === 204) return undefined as T;
  if (response.ok) return (await response.json()) as T;

  const kind = kindForStatus(response.status);
  let detail: unknown;
  try {
    detail = (await response.json())?.detail;
  } catch {
    detail = undefined;
  }
  // FastAPI validation errors (422) return a list, and a missing route says
  // only "Not Found"; show our own text for both.
  const useful = typeof detail === "string" && detail.trim() && detail !== "Not Found";
  const message = useful ? (detail as string) : fallbackMessage(kind);
  throw new ApiError(kind, message, response.headers.get("X-Request-ID"));
}

export function createClient(fetchImpl: Fetch = (...args) => fetch(...args)) {
  return {
    me: (caller: Caller) => request<MeResponse>(fetchImpl, "/me", caller),
    ask: (caller: Caller, question: string, conversationId: string) =>
      request<AnswerResponse>(fetchImpl, "/ask", caller, { json: { question, conversation_id: conversationId } }),
    clarify: (caller: Caller, conversationId: string, clarificationId: string, optionId: string) =>
      request<AnswerResponse>(fetchImpl, "/clarify", caller, {
        json: { conversation_id: conversationId, clarification_id: clarificationId, option_id: optionId },
      }),

    // Documents (Step 8): always in the caller's active workspace.
    listDocuments: (caller: Caller) => request<DocumentList>(fetchImpl, "/documents", caller),
    uploadDocument: (caller: Caller, file: File) => {
      const form = new FormData();
      form.append("file", file, file.name);
      return request<DocumentInfo>(fetchImpl, "/documents", caller, { form });
    },
    retryDocument: (caller: Caller, documentId: string) =>
      request<DocumentInfo>(fetchImpl, `/documents/${encodeURIComponent(documentId)}/retry`, caller, { method: "POST" }),
    deleteDocument: (caller: Caller, documentId: string) =>
      request<void>(fetchImpl, `/documents/${encodeURIComponent(documentId)}`, caller, { method: "DELETE" }),
    searchDocuments: (caller: Caller, query: string, limit = 5) =>
      request<DocumentSearchResponse>(fetchImpl, "/documents/search", caller, { json: { query, limit } }),
  };
}

export type ApiClient = ReturnType<typeof createClient>;
