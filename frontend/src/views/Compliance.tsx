import { useRef, useState } from "react";
import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Card, Empty, ErrorBox, Spinner } from "../components/ui";
import { humanise } from "../format";
import type { ComplianceRow, DisclosureCell } from "../types";

// A glyph as well as a colour. The matrix has to be readable without colour
// vision, and "red means bad" is exactly the kind of thing that fails silently.
const STATUS: Record<string, { glyph: string; className: string; label: string }> = {
  provided: {
    glyph: "✓",
    className: "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200",
    label: "Answered",
  },
  partial: {
    glyph: "◐",
    className: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
    label: "Answered in part",
  },
  absent: {
    glyph: "✕",
    className: "bg-red-100 text-red-900 dark:bg-red-950 dark:text-red-200",
    label: "Not addressed",
  },
};

const UNASSESSED = {
  glyph: "?",
  className: "bg-stone-100 text-stone-500 dark:bg-stone-800 dark:text-stone-400",
  label: "Not assessed by this adapter",
};

export function Compliance() {
  const compliance = useAsync(() => api.compliance(), []);
  const [cell, setCell] = useState<{ row: ComplianceRow; cell: DisclosureCell } | null>(
    null,
  );
  const [letterFor, setLetterFor] = useState<number | null>(null);

  if (compliance.error) return <ErrorBox error={compliance.error} />;
  if (!compliance.data) return <Spinner label="Building the matrix" />;
  const { categories, rows } = compliance.data;
  if (rows.length === 0) return <Empty>No responses loaded yet.</Empty>;

  return (
    <div className="space-y-4">
      <Card title="What each retailer disclosed">
        <div className="scroll-x">
          <table className="w-full min-w-[52rem] border-separate border-spacing-1 text-xs">
            <thead>
              <tr>
                <th className="text-left font-medium">Retailer</th>
                {categories.map((category) => (
                  <th
                    key={category}
                    className="w-24 align-bottom text-left font-medium text-stone-600 dark:text-stone-400"
                  >
                    {humanise(category)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <th className="text-left align-middle text-sm font-medium">
                    {row.display_name}
                    <span className="ml-1 font-normal text-stone-500 dark:text-stone-400">
                      {row.absent_count}/{categories.length} unanswered
                    </span>
                  </th>
                  {categories.map((category) => {
                    const c = row.cells[category];
                    const look = (c.status && STATUS[c.status]) || UNASSESSED;
                    return (
                      <td key={category}>
                        <button
                          onClick={() => setCell({ row, cell: c })}
                          title={`${humanise(category)}: ${look.label}`}
                          className={`h-9 w-full rounded text-center text-sm transition hover:opacity-80 ${look.className}`}
                        >
                          {look.glyph}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <p className="mt-3 text-xs text-stone-500 dark:text-stone-400">
          ✓ answered · ◐ answered in part · ✕ not addressed · ? not assessed. This
          records what a response contained. It is not a finding that anyone broke the
          law — see <code>docs/legal-basis.md</code>.
        </p>
      </Card>

      {cell && (
        <Card
          title={`${cell.row.display_name} — ${humanise(cell.cell.category)}`}
          actions={
            <button
              className="text-xs text-stone-500 hover:underline dark:text-stone-400"
              onClick={() => setCell(null)}
            >
              close
            </button>
          }
        >
          <p className="text-sm">
            <strong>{(cell.cell.status && STATUS[cell.cell.status]?.label) ?? UNASSESSED.label}</strong>
          </p>
          {cell.cell.evidence && (
            <p className="mt-2 border-l-2 border-stone-300 pl-3 text-sm italic dark:border-stone-700">
              {cell.cell.evidence}
            </p>
          )}
          {cell.cell.notes && (
            <p className="mt-2 text-sm text-stone-600 dark:text-stone-400">
              {cell.cell.notes}
            </p>
          )}
          {cell.cell.provenance?.locator && (
            <p className="mt-2 font-mono text-[11px] text-stone-400">
              {cell.cell.provenance.page ? `page ${cell.cell.provenance.page} · ` : ""}
              {cell.cell.provenance.locator}
            </p>
          )}
        </Card>
      )}

      {rows.map((row) => (
        <Card
          key={row.id}
          title={`Next steps for ${row.display_name}`}
          actions={
            <button
              onClick={() => setLetterFor(letterFor === row.id ? null : row.id)}
              className="rounded bg-stone-900 px-3 py-1 text-xs text-white dark:bg-stone-100 dark:text-stone-900"
            >
              {letterFor === row.id ? "Hide draft" : "Draft a follow-up"}
            </button>
          }
        >
          <ul className="space-y-1 text-sm">
            {row.follow_ups.map((f) => (
              <li key={f.id} className="text-stone-700 dark:text-stone-300">
                {f.description}
              </li>
            ))}
          </ul>
          {letterFor === row.id && <Letter requestId={row.id} />}
        </Card>
      ))}
    </div>
  );
}

function Letter({ requestId }: { requestId: number }) {
  const letter = useAsync(() => api.followUpLetter(requestId), [requestId]);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const draft = useRef<HTMLTextAreaElement>(null);

  // The Clipboard API rejects more often than it looks: permission denied, the
  // page not focused, Firefox's stricter policy. And over plain http to a LAN
  // address — which the README documents as a supported override — the whole
  // API is undefined, so `navigator.clipboard?.writeText(...)` short-circuits
  // and nothing happens at all. Both paths used to leave the button reading
  // "Copy" with no error, no feedback, and an unhandled rejection in the
  // console. Now the failure selects the draft so it can be copied by hand.
  function copy() {
    const text = letter.data?.letter;
    if (!text) return;
    const fallback = () => {
      setCopyFailed(true);
      draft.current?.focus();
      draft.current?.select();
    };
    if (!navigator.clipboard) {
      fallback();
      return;
    }
    navigator.clipboard.writeText(text).then(
      () => {
        setCopyFailed(false);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
      },
      fallback,
    );
  }

  if (letter.error) return <ErrorBox error={letter.error} />;
  if (!letter.data) return <Spinner label="Drafting" />;

  return (
    <div className="mt-3">
      <p className="mb-2 text-xs text-amber-800 dark:text-amber-300">{letter.data.note}</p>
      <textarea
        ref={draft}
        readOnly
        value={letter.data.letter}
        rows={18}
        className="w-full rounded border border-stone-300 bg-stone-50 p-3 font-mono text-xs dark:border-stone-700 dark:bg-stone-950"
      />
      <div className="mt-2 flex items-center gap-3">
        <button
          onClick={copy}
          className="rounded border border-stone-300 px-3 py-1 text-xs dark:border-stone-700"
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <span className="text-xs text-stone-500 dark:text-stone-400">
          {copyFailed
            ? "Your browser would not let the page copy for you. The draft is selected — copy it yourself."
            : "You send this yourself. unbagged never contacts anyone on your behalf."}
        </span>
      </div>
    </div>
  );
}
