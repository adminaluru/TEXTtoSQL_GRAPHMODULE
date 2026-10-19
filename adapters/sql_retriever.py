"""Text-to-SQL engine behind the portable Retriever contract (Step 4).

A host (the hybrid router, or an existing RAG application) calls
`SQLRetriever.retrieve(RetrievalRequest)` and gets a `RetrievalResult`
without importing FlowOne internals. Only this adapter knows the engine.

Honest scope:
- The adapter implements the portable contract; the engine behind it is
  FlowOne application code, not a portable package.
- Identity is taken from `request.access` as given. It is NOT verified here;
  the caller must have authenticated it (Step 5).
- The request deadline is the engine's deadline: every LLM call, Neo4j
  query and PostgreSQL statement inside gets at most the time left, and
  PostgreSQL cancels a statement that would run past it (Step 7).
- Source provenance identifies the read-only database query by request; the
  SQL text is never exposed.

Status mapping:
    scalar / tabular answer        -> complete (evidence, plus a table for tabular)
    empty, or no aggregate value   -> empty
    ambiguous record or value,
    unclear question               -> clarification (with choices when offered)
    capability limit               -> unsupported
    caller not permitted           -> denied
    dependency down / timed out    -> unavailable
    deadline already passed        -> failed (deadline exceeded)
    anything else                  -> failed (safe message, details logged)
"""
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from clarification_state import ClarificationStateError
from observability.deadline import DeadlineExceeded, request_deadline
from hybrid_contracts import (ClarificationPrompt, EvidenceItem, Money, ResultTable,
                              RetrievalRequest, RetrievalResult, Route, SourceReference,
                              TableCell, TableColumn, TableRow)

logger = logging.getLogger(__name__)

UNSUPPORTED_OUTCOMES = {"clarification_unsupported", "clarification_fanout"}

DEADLINE_EXCEEDED = "The question could not be answered within the requested time."
NOT_PERMITTED = "You are not permitted to retrieve this data."
UNAVAILABLE = "The structured data service is temporarily unavailable. Please try again later."
FAILED = "The question could not be answered because of an internal error."
NO_VALUE = "No value was available for the requested criteria."

SOURCE_LOCATION = "FlowOne structured data (read-only query)"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SQLRetriever:
    route = Route.SQL

    def __init__(self, agent_for: Callable[[Any], Any], clock: Callable[[], datetime] = _utcnow):
        """
        agent_for(access) returns a ResearchAgentOrchestrator scoped to that
        caller (e.g. with the caller's tenant scope policy). The composition
        root owns it, including any caching of agents per tenant.
        """
        self._agent_for = agent_for
        self._clock = clock

    def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        if request.route != self.route:
            raise ValueError("Request was sent to the wrong retriever")
        if self._clock() >= request.deadline_at:
            return self._result(request, "failed", warnings=(DEADLINE_EXCEEDED,))
        try:
            seconds_left = (request.deadline_at - self._clock()).total_seconds()
            with request_deadline(seconds_left):
                answer = self._run(request)
        except DeadlineExceeded:
            return self._result(request, "failed", warnings=(DEADLINE_EXCEEDED,))
        except PermissionError:
            logger.info("FLOWONE_SQL_RETRIEVER_DENIED request_id=%s", request.request_id)
            return self._result(request, "denied", warnings=(NOT_PERMITTED,))
        except (ConnectionError, TimeoutError) as exc:
            logger.warning("FLOWONE_SQL_RETRIEVER_UNAVAILABLE request_id=%s error=%s",
                           request.request_id, type(exc).__name__)
            return self._result(request, "unavailable", warnings=(UNAVAILABLE,))
        except ClarificationStateError as exc:
            # Expired, replayed or someone else's choice: safe, user-facing text.
            return self._result(request, "failed", warnings=(str(exc),))
        except ValueError as exc:
            logger.info("FLOWONE_SQL_RETRIEVER_REJECTED request_id=%s error=%s",
                        request.request_id, type(exc).__name__)
            return self._result(request, "failed", warnings=(FAILED,))
        except Exception:
            logger.exception("FLOWONE_SQL_RETRIEVER_FAILED request_id=%s", request.request_id)
            return self._result(request, "failed", warnings=(FAILED,))
        if self._clock() >= request.deadline_at:
            return self._result(request, "failed", warnings=(DEADLINE_EXCEEDED,))
        result = self._map(request, answer)
        result.validate_for(request)
        return result

    # ------------------------------------------------------------------

    def _run(self, request: RetrievalRequest):
        from agent.orchestrator import CallerContext

        agent = self._agent_for(request.access)
        caller = (CallerContext(access=request.access, conversation_id=request.conversation_id)
                  if request.conversation_id else None)
        if request.clarification_response is not None:
            if caller is None:
                raise PermissionError("A clarification reply requires its conversation.")
            return agent.resume(request.clarification_response, caller=caller)
        return agent.ask(request.question, caller=caller)

    def _map(self, request: RetrievalRequest, answer) -> RetrievalResult:
        warnings = tuple(answer.warnings)
        if answer.result_type == "clarification":
            if answer.outcome in UNSUPPORTED_OUTCOMES:
                return self._result(request, "unsupported", warnings=(answer.answer,) + warnings)
            prompt = (ClarificationPrompt.model_validate(answer.data)
                      if isinstance(answer.data, dict) else None)
            return self._result(request, "clarification", clarification=prompt,
                                warnings=(answer.answer,) + warnings)
        if answer.result_type == "empty" or (answer.result_type == "scalar" and answer.data is None):
            return self._result(request, "empty",
                                warnings=warnings or ((NO_VALUE,) if answer.result_type == "scalar" else ()))
        if answer.result_type == "scalar":
            return self._result(request, "complete", warnings=warnings,
                                evidence=(self._evidence(request, answer, answer.data),))
        evidence = self._evidence(request, answer, None)
        return self._result(request, "complete", warnings=warnings, evidence=(evidence,),
                            tables=(self._table(answer, evidence.evidence_id),))

    def _evidence(self, request, answer, scalar: dict | None) -> EvidenceItem:
        now = self._clock()
        money = None
        if scalar and scalar.get("currency") and isinstance(scalar.get("value"), Decimal):
            money = Money(amount=scalar["value"], currency=scalar["currency"])
        return EvidenceItem(
            evidence_id=f"{request.request_id}-sql-1",
            tenant_id=request.access.tenant_id,
            kind="structured_fact",
            content=answer.answer,
            sources=(SourceReference(
                source_id=f"flowone-sql/{request.request_id}",
                tenant_id=request.access.tenant_id,
                # As-of version: no database snapshot ID exists yet, so the
                # retrieval time is the most precise truthful version.
                version=now.isoformat(),
                location=SOURCE_LOCATION,
                kind="database"),),
            retrieved_at=now,
            monetary_value=money,
        )

    @staticmethod
    def _table(answer, evidence_id: str) -> ResultTable:
        rows = answer.data or []
        keys = list(rows[0]) if rows else []
        columns = []
        for key in keys:
            values = [r.get(key) for r in rows]
            columns.append(TableColumn(key=key, label=key.replace("_", " ").capitalize(),
                                       data_type=_data_type(values),
                                       nullable=any(v is None for v in values)))
        return ResultTable(
            table_id="sql-result",
            title=answer.answer,
            columns=tuple(columns) or (TableColumn(key="result", label="Result", data_type="text"),),
            rows=tuple(TableRow(cells=tuple(TableCell(value=_cell(r.get(c.key), c.data_type))
                                            for c in columns),
                                evidence_ids=(evidence_id,)) for r in rows),
            truncated=any("truncated" in w.lower() for w in answer.warnings),
        )

    @staticmethod
    def _result(request, status, **kwargs) -> RetrievalResult:
        return RetrievalResult(request_id=request.request_id, tenant_id=request.access.tenant_id,
                               route=Route.SQL, status=status, **kwargs)


def _data_type(values: list) -> str:
    present = [v for v in values if v is not None]
    if not present:
        return "text"
    kinds = {type(v) for v in present}
    if kinds <= {bool}:
        return "boolean"
    if kinds <= {int}:
        return "integer"
    if kinds <= {int, Decimal, float}:
        return "decimal"
    if kinds <= {datetime}:
        return "datetime"
    if kinds <= {date}:
        return "date"
    return "text"


def _cell(value, data_type: str):
    if value is None:
        return None
    if data_type == "decimal" and isinstance(value, (int, float)) and not isinstance(value, bool):
        return Decimal(str(value))
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, datetime) and value.tzinfo is None:
        # Stored timestamps are US Central local time (D-007).
        from planning.date_period import BUSINESS_TIME_ZONE
        return value.replace(tzinfo=BUSINESS_TIME_ZONE)
    if isinstance(value, (str, int, Decimal, bool, date, datetime)):
        return value
    return str(value)
