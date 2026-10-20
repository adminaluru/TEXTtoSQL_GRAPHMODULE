import { useEffect, useRef } from "react";

import type { QuotedPassage } from "../api/types";

interface Props {
  passages: QuotedPassage[];
  // Written answers cite passages by number (Step 12): show the numbers.
  numbered?: boolean;
  highlighted?: number | null;
}

/**
 * Document passages under an answer: quoted exactly as stored, each with the
 * file and place it comes from. Plain text only: content from documents is
 * never rendered as HTML.
 */
export function Passages({ passages, numbered = false, highlighted = null }: Props) {
  const refs = useRef<(HTMLElement | null)[]>([]);

  useEffect(() => {
    if (highlighted) refs.current[highlighted - 1]?.scrollIntoView?.({ block: "nearest", behavior: "smooth" });
  }, [highlighted]);

  return (
    <section className="answer-passages" aria-label="From your documents">
      {passages.map((p, i) => (
        <figure
          className={`passage${highlighted === i + 1 ? " highlighted" : ""}`}
          key={`${p.document_id}:${p.version}:${i}`}
          ref={(el) => {
            refs.current[i] = el;
          }}
          aria-label={numbered ? `Passage ${i + 1}` : undefined}
        >
          <blockquote>
            {numbered && <span className="passage-number">{i + 1}</span>}
            {p.text}
          </blockquote>
          <figcaption>{p.citation}{p.version > 1 && ` · version ${p.version}`}</figcaption>
        </figure>
      ))}
    </section>
  );
}
