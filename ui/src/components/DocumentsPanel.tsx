import { useRef, useState } from "react";

import type { DocumentInfo, DocumentSearchResponse } from "../api/types";
import { ACCEPTED_EXTENSIONS, MAX_FILE_MB } from "../state/useDocuments";

interface Props {
  enabled: boolean;
  documents: DocumentInfo[];
  loaded: boolean;
  uploading: boolean;
  busyIds: Set<string>;
  error: { message: string; requestId: string | null } | null;
  notice: string | null;
  search: { query: string; response: DocumentSearchResponse } | null;
  searching: boolean;
  onSearch: (query: string) => void;
  onClearSearch: () => void;
  onUpload: (file: File) => void;
  onRetry: (documentId: string) => void;
  onDelete: (documentId: string) => void;
}

const STATUS_LABEL: Record<string, string> = {
  queued: "Waiting",
  processing: "Reading",
  ready: "Ready",
  failed: "Failed",
  deleting: "Deleting",
  deleted: "Deleted",
};

/** Marks the searched words inside a passage. Text stays text: no HTML is built. */
export function highlight(text: string, terms: string[]) {
  const words = terms.filter((t) => /^[\p{L}\p{N}]+$/u.test(t));
  if (words.length === 0) return text;
  // A word and its endings: "payment" also marks "payments".
  const pattern = new RegExp(`(?<![\\p{L}\\p{N}])((?:${words.join("|")})[\\p{L}\\p{N}]*)`, "giu");
  return text.split(pattern).map((part, i) => (i % 2 === 1 ? <mark key={i}>{part}</mark> : part));
}

function size(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Upload and manage the workspace's documents (Step 8). File names, notes and
 * messages are shown as plain text: content from files is never rendered as HTML.
 */
export function DocumentsPanel(props: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const hasReady = props.documents.some((d) => d.searchable);

  return (
    <section className="evidence-card documents" aria-label="Documents">
      <div className="documents-head">
        <h3>Documents</h3>
        <button
          type="button"
          className="secondary small"
          disabled={!props.enabled || props.uploading}
          onClick={() => input.current?.click()}
        >
          {props.uploading ? "Uploading…" : "Upload document"}
        </button>
        <input
          ref={input}
          id="document-upload"
          type="file"
          accept={ACCEPTED_EXTENSIONS.join(",")}
          aria-label="Choose a document to upload"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0];
            e.target.value = ""; // choosing the same file again still triggers
            if (file) props.onUpload(file);
          }}
        />
      </div>

      <p className="muted small-print">
        Word, PDF or Excel, up to {MAX_FILE_MB} MB. Search shows passages exactly as written in your documents,
        with where they come from.
      </p>

      <form
        className="document-search"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          props.onSearch(query);
        }}
      >
        <input
          id="document-search"
          type="search"
          value={query}
          maxLength={500}
          placeholder={hasReady ? "Search documents, e.g. late payment fee" : "Upload a document to search it"}
          aria-label="Search documents"
          disabled={!props.enabled || !hasReady}
          onChange={(e) => {
            setQuery(e.target.value);
            if (!e.target.value) props.onClearSearch();
          }}
        />
        <button type="submit" className="secondary small"
          disabled={!props.enabled || !hasReady || !query.trim() || props.searching}>
          {props.searching ? "Searching…" : "Search"}
        </button>
      </form>

      {props.search && (
        <section className="search-results" aria-label="Search results">
          {props.search.response.message && (
            <p className="notice" role="status">
              <span aria-hidden="true">ⓘ</span>
              <span>{props.search.response.message}</span>
            </p>
          )}
          {props.search.response.results.map((passage) => (
            <figure className="passage" key={`${passage.document_id}:${passage.version}:${passage.citation}:${passage.text.slice(0, 20)}`}>
              <blockquote>{highlight(passage.text, props.search!.response.terms)}</blockquote>
              <figcaption>{passage.citation}{passage.version > 1 && ` · version ${passage.version}`}</figcaption>
            </figure>
          ))}
        </section>
      )}

      {!props.enabled && <p className="muted">Choose a workspace to see its documents.</p>}

      {props.error && (
        <div className="notice error" role="alert">
          <span aria-hidden="true">!</span>
          <span>
            {props.error.message}
            {props.error.requestId && <span className="request-id"> (reference {props.error.requestId})</span>}
          </span>
        </div>
      )}
      {props.notice && (
        <p className="notice" role="status">
          <span aria-hidden="true">ⓘ</span>
          <span>{props.notice}</span>
        </p>
      )}

      {props.enabled && props.loaded && props.documents.length === 0 && (
        <p className="muted">No documents in this workspace yet.</p>
      )}

      <ul className="document-list">
        {props.documents.map((doc) => {
          const latest = doc.latest;
          const busy = props.busyIds.has(doc.document_id);
          return (
            <li key={doc.document_id} className="document-row">
              <div className="document-title">
                <span className="document-name" title={doc.filename}>{doc.filename}</span>
                <span className={`status status-${latest.status}`}>{STATUS_LABEL[latest.status] ?? latest.status}</span>
              </div>
              <div className="muted document-meta">
                {latest.file_type.toUpperCase()} · {size(latest.size_bytes)}
                {latest.version > 1 && <> · version {latest.version}</>}
                {latest.status === "ready" && <> · {latest.passage_count} passage{latest.passage_count === 1 ? "" : "s"}</>}
                {latest.status !== "ready" && doc.searchable && <> · version {doc.active_version} still in use</>}
              </div>
              {latest.message && <p className={latest.status === "failed" ? "document-message failed" : "document-message"}>{latest.message}</p>}
              {latest.notes.map((note, i) => (
                <p className="document-note" key={i}><span aria-hidden="true">ⓘ</span> {note}</p>
              ))}

              <div className="document-actions">
                {latest.can_retry && (
                  <button type="button" className="link" disabled={busy} onClick={() => props.onRetry(doc.document_id)}>
                    Try again
                  </button>
                )}
                {confirming === doc.document_id ? (
                  <span className="confirm">
                    Delete “{doc.filename}”?
                    <button type="button" className="link danger" disabled={busy}
                      onClick={() => { setConfirming(null); props.onDelete(doc.document_id); }}>
                      Delete
                    </button>
                    <button type="button" className="link" onClick={() => setConfirming(null)}>Cancel</button>
                  </span>
                ) : (
                  <button type="button" className="link" disabled={busy || latest.status === "deleting"}
                    onClick={() => setConfirming(doc.document_id)}>
                    Delete
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
