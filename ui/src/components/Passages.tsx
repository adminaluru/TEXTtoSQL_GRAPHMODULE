import type { QuotedPassage } from "../api/types";

/**
 * Document passages under an answer: quoted exactly as stored, each with the
 * file and place it comes from. Plain text only: content from documents is
 * never rendered as HTML.
 */
export function Passages({ passages }: { passages: QuotedPassage[] }) {
  return (
    <section className="answer-passages" aria-label="From your documents">
      {passages.map((p, i) => (
        <figure className="passage" key={`${p.document_id}:${p.version}:${i}`}>
          <blockquote>{p.text}</blockquote>
          <figcaption>{p.citation}{p.version > 1 && ` · version ${p.version}`}</figcaption>
        </figure>
      ))}
    </section>
  );
}
