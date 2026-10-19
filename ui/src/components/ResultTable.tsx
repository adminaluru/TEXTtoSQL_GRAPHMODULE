import type { CellValue } from "../api/types";
import { formatValue, humanize, isNumeric } from "../format/values";

interface Props {
  rows: Record<string, CellValue>[];
  /** Columns the server marks as internal detail (e.g. missing-value counts). */
  hide?: string[];
}

/** Shows rows exactly as returned. Never sums or re-sorts across currencies. */
export function ResultTable({ rows, hide = [] }: Props) {
  if (rows.length === 0) return null;
  const columns = Object.keys(rows[0]).filter((c) => !hide.includes(c));
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c} scope="col">{humanize(c)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              {columns.map((c) => (
                <td key={c} className={isNumeric(row[c]) ? "num" : undefined}>
                  {c === "currency" && row[c] === null ? "(currency unknown)" : formatValue(row[c])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
