// What the Documents panel remembers, and the rules that keep it safe:
//
// - the list always belongs to the active workspace; switching workspace
//   clears it, and a reply that arrives after the switch is dropped;
// - while a document is queued or processing, the list refreshes every few
//   seconds and stops once nothing is left in progress;
// - the server decides what is accepted; the quick checks here (type, size)
//   only save the user a pointless upload, and the server checks again;
// - one upload at a time; actions on a document lock its buttons until done.

import { useCallback, useEffect, useRef, useState } from "react";

import type { ApiClient, Caller } from "../api/client";
import { ApiError, fallbackMessage } from "../api/errors";
import type { DocumentInfo, DocumentSearchResponse } from "../api/types";

export const ACCEPTED_EXTENSIONS = [".docx", ".pdf", ".xlsx"];
export const MAX_FILE_MB = 25; // D-010; the server enforces it
export const POLL_MS = 2000;

export interface DocumentsError {
  message: string;
  requestId: string | null;
}

export interface DocumentHandlers {
  onUnauthenticated: () => void;
  onForbidden: () => void;
}

const IN_PROGRESS = new Set(["queued", "processing", "deleting"]);

export function checkFile(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext))) {
    return "Only Word (.docx), PDF (.pdf) and Excel (.xlsx) files are supported.";
  }
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_FILE_MB * 1024 * 1024) return `The file is larger than ${MAX_FILE_MB} MB.`;
  return null;
}

export function useDocuments(api: ApiClient, caller: Caller | null, handlers: DocumentHandlers) {
  const [documents, setDocuments] = useState<DocumentInfo[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [busyIds, setBusyIds] = useState<Set<string>>(new Set());
  const [error, setError] = useState<DocumentsError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [search, setSearch] = useState<{ query: string; response: DocumentSearchResponse } | null>(null);
  const [searching, setSearching] = useState(false);

  // Changes whenever the workspace or token changes; late replies compare it.
  const epoch = useRef(0);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const fail = useCallback((err: unknown) => {
    if (err instanceof ApiError) {
      if (err.kind === "unauthenticated") return handlersRef.current.onUnauthenticated();
      if (err.kind === "forbidden") handlersRef.current.onForbidden();
      setError({ message: err.message, requestId: err.requestId });
    } else {
      setError({ message: fallbackMessage("server"), requestId: null });
    }
  }, []);

  const refresh = useCallback(async () => {
    if (!caller?.tenant) return;
    const mine = epoch.current;
    try {
      const list = await api.listDocuments(caller);
      if (mine !== epoch.current) return;
      setDocuments(list.documents);
      setLoaded(true);
    } catch (err) {
      if (mine === epoch.current) fail(err);
    }
  }, [api, caller, fail]);

  // New workspace (or token): start from an empty list and load it.
  useEffect(() => {
    epoch.current += 1;
    setDocuments([]);
    setLoaded(false);
    setError(null);
    setNotice(null);
    setBusyIds(new Set());
    setUploading(false);
    setSearch(null);
    setSearching(false);
    if (caller?.tenant) void refresh();
  }, [caller?.tenant, caller?.token]); // eslint-disable-line react-hooks/exhaustive-deps

  // Keep refreshing while anything is still being processed.
  const inProgress = documents.some((d) => IN_PROGRESS.has(d.latest.status));
  useEffect(() => {
    if (!inProgress) return;
    const timer = setInterval(() => void refresh(), POLL_MS);
    return () => clearInterval(timer);
  }, [inProgress, refresh]);

  const upload = useCallback(async (file: File) => {
    if (!caller?.tenant || uploading) return;
    setError(null);
    setNotice(null);
    const problem = checkFile(file);
    if (problem) {
      setError({ message: problem, requestId: null });
      return;
    }
    const mine = epoch.current;
    setUploading(true);
    try {
      const result = await api.uploadDocument(caller, file);
      if (mine !== epoch.current) return;
      if (result.duplicate) {
        setNotice(`“${file.name}” is already uploaded as “${result.filename}”. Nothing new was stored.`);
      }
      await refresh();
    } catch (err) {
      if (mine === epoch.current) fail(err);
    } finally {
      if (mine === epoch.current) setUploading(false);
    }
  }, [api, caller, fail, refresh, uploading]);

  const act = useCallback(async (documentId: string, action: (c: Caller) => Promise<unknown>) => {
    if (!caller?.tenant || busyIds.has(documentId)) return;
    const mine = epoch.current;
    setError(null);
    setNotice(null);
    setBusyIds((ids) => new Set(ids).add(documentId));
    try {
      await action(caller);
      if (mine === epoch.current) await refresh();
    } catch (err) {
      if (mine === epoch.current) {
        fail(err);
        await refresh();
      }
    } finally {
      if (mine === epoch.current) {
        setBusyIds((ids) => {
          const next = new Set(ids);
          next.delete(documentId);
          return next;
        });
      }
    }
  }, [busyIds, caller, fail, refresh]);

  // Step 9: quoted passages from this workspace's documents.
  const find = useCallback(async (query: string) => {
    const text = query.trim();
    if (!caller?.tenant || !text || searching) return;
    const mine = epoch.current;
    setError(null);
    setSearching(true);
    try {
      const response = await api.searchDocuments(caller, text);
      if (mine === epoch.current) setSearch({ query: text, response });
    } catch (err) {
      if (mine === epoch.current) fail(err);
    } finally {
      if (mine === epoch.current) setSearching(false);
    }
  }, [api, caller, fail, searching]);

  return {
    documents,
    search,
    searching,
    find,
    clearSearch: () => setSearch(null),
    loaded,
    uploading,
    busyIds,
    error,
    notice,
    upload,
    retry: (id: string) => act(id, (c) => api.retryDocument(c, id)),
    remove: (id: string) => act(id, (c) => api.deleteDocument(c, id)),
    dismissError: () => setError(null),
  };
}
