// Shapes returned by the FlowOne HTTP API. They mirror the backend models
// (answering/models.py AnswerResponse, api/models.py MeResponse, and the
// hybrid_contracts ClarificationPrompt carried in `data` for choices).
// The UI only displays these values; it never recalculates them.

export type ResultType = "empty" | "scalar" | "tabular" | "clarification" | "documents";

export type CellValue = string | number | boolean | null;

export interface ScalarData {
  alias: string;
  value: CellValue;
  currency?: string | null;
  missing_count?: number;
}

export interface ClarificationOption {
  option_id: string;
  label: string;
}

export interface ClarificationPrompt {
  clarification_id: string;
  question: string;
  reason: string;
  options: ClarificationOption[];
  allows_text: boolean;
  expires_at: string;
}

export interface AnswerResponse {
  answer: string;
  result_type: ResultType;
  row_count: number;
  data: ScalarData | Record<string, CellValue>[] | ClarificationPrompt | null;
  warnings: string[];
  // Step 11: which sources answered, and document passages quoted exactly.
  route?: "database" | "documents" | "both" | null;
  passages?: QuotedPassage[];
  // Step 12 (D-012): "written" when the text was written from the sources;
  // each sentence then ends with markers such as "[1]" (passage 1 in
  // `passages`) or "[database]". "quoted" when the passages are the answer.
  answer_basis?: "written" | "quoted";
}

export interface QuotedPassage {
  document_id: string;
  version: number;
  citation: string;
  text: string;
}

export interface MeResponse {
  subject_id: string;
  kind: string;
  roles: string[];
  tenants: string[];
}

export function isClarificationPrompt(data: AnswerResponse["data"]): data is ClarificationPrompt {
  return !!data && !Array.isArray(data) && "clarification_id" in data && "options" in data;
}

export function isScalarData(data: AnswerResponse["data"]): data is ScalarData {
  return !!data && !Array.isArray(data) && "alias" in data && "value" in data;
}

// Documents (Step 8): mirrors api/models.py. Status and messages come from
// the server; the UI never decides whether a file is acceptable.

export type DocumentStatus = "queued" | "processing" | "ready" | "failed" | "deleting" | "deleted";

export interface DocumentVersionInfo {
  version: number;
  status: DocumentStatus;
  message: string | null;
  progress_percent: number;
  file_type: "pdf" | "docx" | "xlsx";
  size_bytes: number;
  passage_count: number;
  notes: string[];
  can_retry: boolean;
  uploaded_at: string;
  updated_at: string;
}

export interface DocumentInfo {
  document_id: string;
  filename: string;
  searchable: boolean;
  active_version: number | null;
  latest: DocumentVersionInfo;
  duplicate: boolean;
}

export interface DocumentList {
  documents: DocumentInfo[];
}

// Step 9: passages quoted exactly as stored, with where they come from.
export interface DocumentPassage {
  document_id: string;
  version: number;
  citation: string; // e.g. "terms.pdf · page 3"
  text: string;
}

export interface DocumentSearchResponse {
  status: "complete" | "empty" | "clarification";
  message: string | null;
  terms: string[];
  results: DocumentPassage[];
}

// Saved conversations (Step 12c): mirrors api/models.py. Only the caller's
// own, in the active company; the server enforces both.

export interface ConversationInfo {
  conversation_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface ConversationList {
  conversations: ConversationInfo[];
}

export type SavedMessage =
  | { kind: "question"; content: string; created_at: string }
  | { kind: "choice"; content: { option_id: string; label: string | null }; created_at: string }
  | { kind: "answer"; content: AnswerResponse; created_at: string };

export interface ConversationDetail extends ConversationInfo {
  messages: SavedMessage[];
}
