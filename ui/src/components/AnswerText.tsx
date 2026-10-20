import type { ReactNode } from "react";

const MARKER = /\[(\d+|database)\]/g;

interface Props {
  text: string;
  passageCount: number;
  onCite: (passage: number) => void;
}

/**
 * A written answer (Step 12): "[1]" after a sentence becomes a button that
 * shows passage 1 below; "[database]" becomes a tag for business-data facts.
 * A marker naming a passage that is not shown stays as plain text. Everything
 * is rendered as text: nothing from the answer can run as HTML.
 */
export function AnswerText({ text, passageCount, onCite }: Props) {
  const parts: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(MARKER)) {
    const index = match.index ?? 0;
    parts.push(text.slice(last, index));
    const ref = match[1];
    const n = Number(ref);
    if (ref === "database") {
      parts.push(
        <span key={index} className="cite database" title="From your business data">database</span>,
      );
    } else if (Number.isInteger(n) && n >= 1 && n <= passageCount) {
      parts.push(
        <button
          key={index}
          type="button"
          className="cite"
          aria-label={`Show passage ${n}`}
          onClick={(e) => {
            e.stopPropagation();
            onCite(n);
          }}
        >
          {n}
        </button>,
      );
    } else {
      parts.push(match[0]);
    }
    last = index + match[0].length;
  }
  parts.push(text.slice(last));
  return <p className="answer-text">{parts}</p>;
}
