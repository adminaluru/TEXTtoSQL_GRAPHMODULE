import type { ReactNode } from "react";

import type { AnswerResponse } from "../api/types";
import { Notices } from "./Notices";

interface Props {
  answer: AnswerResponse | null;
  answeredAt: Date | null;
  documents: ReactNode;
}

/**
 * What supports the selected answer (database answers today), and the
 * workspace's documents underneath.
 */
export function EvidencePanel({ answer, answeredAt, documents }: Props) {
  const hasFacts = answer && (answer.result_type === "scalar" || answer.result_type === "tabular");

  return (
    <aside className="panel evidence" aria-label="Supporting evidence">
      <h2>Supporting evidence</h2>

      {!answer && <p className="muted">Select an answer to see what it is based on.</p>}

      {answer && answer.passages && answer.passages.length > 0 && (
        <section className="evidence-card">
          <h3>Document passages</h3>
          <ul className="citations">
            {answer.passages.map((p, i) => <li key={i}>{p.citation}</li>)}
          </ul>
          <p className="muted">Quoted exactly; open the Documents list below to see each file's status.</p>
        </section>
      )}

      {answer && answer.result_type !== "documents" && (
        <section className="evidence-card">
          <h3>Structured data</h3>
          {hasFacts ? (
            <>
              <p>Calculated by the database from FlowOne records (read-only query).</p>
              {answeredAt && <p className="muted">Retrieved {answeredAt.toLocaleString()}</p>}
            </>
          ) : (
            <p className="muted">This reply contains no figures from the database.</p>
          )}
          <Notices items={answer.warnings} />
        </section>
      )}

      {documents}
    </aside>
  );
}
