# Hybrid contracts

Independent Pydantic v2 package. No FastAPI, database, provider or domain imports. Existing application contracts and /ask are unchanged.

Install: python -m pip install -e '.[test]'
Test: python -m pytest tests

QuestionRequest is public and rejects tenant, role and permission injection. AccessContext is internal, constructed by authentication/policy. Constructing it does not authenticate users. Roles are configurable strings; initial roles are sales_user, credit_manager and company_admin.

Orchestration must call result.validate_for(request). Tenant checks enforce data consistency, not database access. Authentication, row/field permissions and source access must be enforced separately before retrieval.

Routing dependencies reference earlier steps. Deadlines are timezone-aware; enforcement belongs to execution. Decimal money serializes as text. Evidence requires versioned source references. Citation validation checks references, not claim truth or accessibility.

This is an initial foundation, not completed hybrid integration. Table and upload/job contracts are now included. Runtime pagination, persisted clarification and access enforcement remain future work. Existing AnswerResponse stays intact.

## Version 0.2.0 additions

ResultTable provides typed columns, positional cells, evidence IDs, truncation and optional opaque continuation cursors. Decimal/date/datetime values round-trip through JSON using column types. Tables in AnswerResult must reference evidence included in the answer.

ClarificationPrompt and ClarificationResponse define options or text input. The server must verify pending-state ownership, expiration and option membership; the response itself supplies no trusted tenant or role. The optional prompt field preserves compatibility with earlier clarification answers.

ErrorResponse carries a request ID, safe message, error code and retry hint. Do not place raw exception text, credentials or SQL parameters in messages.

UploadMetadata initially supports docx, pdf and xlsx. It checks metadata shape and filename, not actual content, malware, file size or parser safety. Legacy doc/xls and OCR are not implemented. DocumentJob represents versioned processing and deletion states; can_transition_to is a helper that execution code must enforce atomically. READY does not itself prove search/graph publication succeeded. Reprocessing a ready source creates a new version/job.

These are data contracts only. API integration, jobs, pagination implementation, clarification persistence and permission enforcement remain separate action items. Replace the full package when upgrading and reinstall its editable package to update version metadata.

## Version 0.3.0 additions (backward compatible)

RetrievalResult statuses now distinguish every outcome a host must handle differently:
complete, partial, empty, clarification, unsupported, denied, disabled, unavailable, failed.
Only complete and partial carry evidence; every status except complete and empty must explain
itself in warnings. Results may carry typed ResultTables whose rows cite included evidence, and
a ClarificationPrompt (only with status clarification).

RetrievalRequest adds conversation_id (needed before a retriever may offer selectable choices)
and clarification_response (to continue an earlier clarification; the retriever must re-check
ownership).

DisabledRetriever answers truthfully with status disabled for a switched-off capability; never
evidence or a placeholder success. Dependent packages pin `flowone-hybrid-contracts>=0.3,<0.4`.
