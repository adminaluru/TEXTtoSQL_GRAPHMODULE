/**
 * Limitations the server attached to an answer: date periods applied,
 * records without a value, groups not listed, truncated results.
 * Always shown with the answer; never hidden.
 */
export function Notices({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <>
      {items.map((text, i) => (
        <p className="notice" key={i}>
          <span aria-hidden="true">ⓘ</span>
          <span>{text}</span>
        </p>
      ))}
    </>
  );
}
