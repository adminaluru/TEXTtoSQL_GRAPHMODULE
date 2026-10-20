import { useState } from "react";

import type { AnswerResponse } from "../api/types";
import { isClarificationPrompt, isScalarData } from "../api/types";
import { formatAmount } from "../format/values";
import { AnswerText } from "./AnswerText";
import { Notices } from "./Notices";
import { Passages } from "./Passages";
import { ResultTable } from "./ResultTable";

const ROUTE_TEXT: Record<string, string> = {
  database: "business data",
  documents: "your documents",
  both: "business data and your documents",
};

interface Props {
  answer: AnswerResponse;
  choicesLocked: boolean;
  busy: boolean;
  selected: boolean;
  onSelect: () => void;
  onChoose: (clarificationId: string, optionId: string, label: string) => void;
}

/**
 * One answer from the server, shown as returned. All text is rendered as
 * plain text (React escapes it), so content from data or documents can never
 * run as HTML or script.
 */
export function AnswerCard({ answer, choicesLocked, busy, selected, onSelect, onChoose }: Props) {
  const { data } = answer;
  const written = answer.answer_basis === "written";
  const passageCount = answer.passages?.length ?? 0;
  const [highlighted, setHighlighted] = useState<number | null>(null);

  return (
    <article
      className="answer"
      aria-pressed={selected}
      tabIndex={0}
      onClick={onSelect}
      onKeyDown={(e) => (e.key === "Enter" ? onSelect() : undefined)}
      aria-label="Answer. Select to show its supporting evidence."
    >
      {answer.result_type === "scalar" && isScalarData(data) && (
        <p className="answer-value">{formatAmount(data.value, "currency" in data ? data.currency : undefined)}</p>
      )}

      {written ? (
        <AnswerText text={answer.answer} passageCount={passageCount} onCite={setHighlighted} />
      ) : (
        <p className="answer-text">{answer.answer}</p>
      )}

      {answer.result_type === "tabular" && Array.isArray(data) && (
        <ResultTable rows={data} hide={Object.keys(data[0] ?? {}).filter((k) => k.endsWith("_missing"))} />
      )}

      {answer.result_type === "clarification" && isClarificationPrompt(data) && (
        <div className="choices" role="group" aria-label="Choose one">
          {data.options.map((option) => (
            <button
              key={option.option_id}
              type="button"
              disabled={choicesLocked || busy}
              onClick={(e) => {
                e.stopPropagation();
                onChoose(data.clarification_id, option.option_id, option.label);
              }}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}

      {answer.passages && answer.passages.length > 0 && (
        <Passages passages={answer.passages} numbered={written} highlighted={highlighted} />
      )}

      <Notices items={answer.warnings} />

      {answer.route && <p className="route-label">Answered from {ROUTE_TEXT[answer.route]}</p>}
    </article>
  );
}
