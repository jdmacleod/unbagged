import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Aside, Caveat, ErrorBox, Spine, Spinner } from "../components/ui";
import { day, money, number, paidLabel } from "../format";
import type { CompareRow } from "../types";

type Metric = {
  key: keyof CompareRow;
  label: string;
  format: (value: number | null | undefined) => string;
  /** Marks a row that is about provenance rather than quantity. */
  foreign?: boolean;
};

/** The rows, given the columns that will sit beside them.
 *
 *  Only the money label varies, and only on the word. The invariant this view
 *  protects is one label per ROW — not a refusal to qualify per column, which
 *  the column head already does ("disclosed no data", "totals only, no items").
 *  Deriving the word from the columns present keeps the row single-valued and
 *  stops Timeline and Compare naming one figure two ways.
 *
 *  Undisclosed columns are excluded from the question: every cell in them is an
 *  em dash, so they have no quantity to name. With no disclosed column at all
 *  `every` is vacuously true and the label falls back to "Total paid", which is
 *  what this row said before and sits over a row of dashes either way.
 */
export function rowsFor(requests: CompareRow[]): Metric[] {
  const disclosed = requests.filter((r) => r.disclosed);
  const label = paidLabel(disclosed.every((r) => r.lines_disclosed));
  return ROWS.map((row) =>
    row.key === "total_paid" ? { ...row, label } : row,
  );
}

const ROWS: Metric[] = [
  { key: "visits", label: "Visits", format: number },
  // Paid, not shelf. This row read "Total spend" over the summed pre-discount
  // amounts, which ranks two retailers by whose shelf prices are higher rather
  // than by which one actually cost more. The word now follows the columns —
  // see rowsFor — but the KEY does not: it stays total_paid either way.
  { key: "total_paid", label: "Total paid", format: money },
  { key: "total_saved", label: "…after loyalty savings of", format: money },
  { key: "distinct_products", label: "Distinct products", format: number },
  {
    key: "identifier_count",
    label: "Identifiers held for you",
    format: number,
  },
  { key: "inference_count", label: "Inferred attributes", format: number },
  {
    key: "appended_inference_count",
    label: "…of those, bought from elsewhere",
    format: number,
    // The one row where colour is allowed, because it is the one row about
    // provenance: these attributes came from a third party the response does
    // not name. See DESIGN.md on what colour means.
    foreign: true,
  },
  {
    key: "absent_disclosures",
    label: "Categories not addressed",
    format: number,
  },
];

/**
 * Two retailers side by side. See DESIGN.md.
 *
 * Usually there is only one, because most people only ever get one response.
 * That used to render a dashed box saying "comparison needs a second retailer",
 * stacked directly above the dashed upload box, which was two rectangles in the
 * same visual language saying nearly the same thing.
 *
 * It is a ruled sheet with the second column left blank now — a form awaiting a
 * response, which is exactly the true state of the world. The blank rule is the
 * same mark Compliance uses for a question that was not answered, and it means
 * the same thing here: nothing has been filled in yet. Worth distinguishing
 * from an em dash, which means a response arrived and disclosed nothing.
 */
export function Compare() {
  const compare = useAsync(() => api.compare(), []);

  if (compare.error) return <ErrorBox error={compare.error} />;
  if (!compare.data) return <Spinner label="Comparing" />;

  const { requests, comparable } = compare.data;
  // Asked of the marks, not of the columns. This read `requests.some(r =>
  // !r.disclosed)`, which is a question about whether a whole column disclosed
  // nothing — and a column can be disclosed and still carry a dash, which is
  // exactly what a response graded `partial` now produces. The sentence
  // explaining the mark was therefore hidden on the one case that needed it.
  // ROWS, not rowsFor: this asks which cells are dashes, and only the money
  // label varies between them.
  const anyDash = ROWS.some((row) =>
    requests.some((r) => r[row.key] === null || r[row.key] === undefined),
  );

  return (
    <div className="space-y-6">
      <Spine margin={<Aside>{requests.length} loaded</Aside>}>
        <div className="flex items-baseline gap-5">
          <span className="font-serif text-[42px] leading-none font-semibold text-faint tabular-nums">
            {requests.length}
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="font-serif text-[17px] font-semibold">
              {comparable ? "responses, side by side" : "response so far"}
            </h2>
            <p className="mt-0.5 max-w-[62ch] text-muted">
              {comparable
                ? "What each retailer holds, and how much of it each of them declined to explain."
                : "This view fills in as responses arrive. The second column is what a second retailer would look like beside the one you have."}
            </p>
          </div>
        </div>
      </Spine>

      <Spine margin={<Aside>per retailer</Aside>}>
        {/* The key goes before the marks it describes. It used to sit below the
            whole sheet, which put the nearest explanation of a dash in the
            "Categories not addressed" row — a different fact, and the wrong
            inference to hand someone. Caveat is the app's existing apparatus
            for "what you are looking at is still true, here is what you should
            know". */}
        {anyDash && (
          <Caveat>
            An em dash means the response did not disclose that, which is not
            the same as a zero. A figure is only written as zero where the
            retailer answered the category in full.
          </Caveat>
        )}

        <Sheet requests={requests} pending={!comparable} />

        <p className="mt-4 max-w-[62ch] text-[11.5px] text-muted">
          {!comparable && (
            <>A blank rule means no response has arrived to fill it in yet. </>
          )}
          Categories not addressed is counted for every retailer either way,
          because what a retailer failed to answer is a finding about that
          retailer.
        </p>
      </Spine>
    </div>
  );
}

function Sheet({
  requests,
  pending,
}: {
  requests: CompareRow[];
  pending: boolean;
}) {
  // One column per retailer, plus a blank one while there is only the first.
  const cols = `minmax(0,1fr) repeat(${requests.length + (pending ? 1 : 0)}, minmax(7rem, 11rem))`;

  return (
    <div className="scroll-x">
      <div className="min-w-[30rem]">
        <div
          className="grid items-baseline gap-4 border-b border-line pb-2"
          style={{ gridTemplateColumns: cols }}
        >
          <span />
          {requests.map((r) => (
            <div key={r.id} className="text-right">
              <div className="font-serif text-[15px] font-semibold">
                {r.display_name}
              </div>
              <div className="num mt-0.5 whitespace-nowrap text-[11px] text-faint">
                {r.disclosed ? (
                  <>
                    {day(r.first_visit)} → {day(r.last_visit)}
                  </>
                ) : (
                  // Said plainly at the head of the column, because a column of
                  // em dashes on its own is ambiguous: it could read as zero.
                  "disclosed no data"
                )}
              </div>
              {/* The qualification goes in the head, not on the cells.
                  "Total paid" is a summed line amount for one retailer and a
                  stated basket total for another — two quantities under one
                  label — and this file already carries a decision about that
                  row meaning one thing. A per-cell mark would have to be
                  invented; the head can just say it, which is the same move
                  the line above makes for the empty case. */}
              {r.disclosed && !r.lines_disclosed && (
                <div className="num mt-0.5 whitespace-nowrap text-[11px] text-faint">
                  totals only, no items
                </div>
              )}
            </div>
          ))}
          {pending && (
            <div className="text-right">
              <div className="font-serif text-[15px] font-semibold text-faint">
                Awaiting
              </div>
              <div className="num mt-0.5 text-[11px] text-faint">
                no response yet
              </div>
            </div>
          )}
        </div>

        {rowsFor(requests).map((row) => (
          <div
            key={row.key}
            className="grid items-baseline gap-4 border-b border-rule py-2"
            style={{ gridTemplateColumns: cols }}
          >
            <span className={row.foreign ? "text-foreign" : "text-muted"}>
              {row.label}
            </span>
            {requests.map((r) => {
              const value = r[row.key] as number | null;
              const dash = value === null || value === undefined;
              return (
                <span
                  key={r.id}
                  className={`num text-right ${row.foreign ? "text-foreign" : ""}`}
                  // Keyed on the cell, not the column: a disclosed column can
                  // still carry a dash, and that cell used to get no title at
                  // all. The wording splits the two reasons, because they are
                  // different findings about the retailer.
                  title={
                    dash
                      ? r.disclosed
                        ? `${r.display_name} did not address this. A dash means not disclosed, not zero.`
                        : `${r.display_name} disclosed no data. A dash means not disclosed, not zero.`
                      : undefined
                  }
                >
                  {row.format(value)}
                  {/* A cell holding only an em dash is announced as "em dash"
                      or skipped outright, so the mark says nothing to a screen
                      reader. Short here; the sentence is in the Caveat above.
                      Same pattern as the pending column below. */}
                  {dash && <span className="sr-only">not disclosed</span>}
                </span>
              );
            })}
            {pending && (
              <span className="flex justify-end">
                {/* The blank waiting to be filled in. Same mark as an unanswered
                    disclosure category, same meaning: nothing here yet. */}
                <span
                  aria-hidden
                  className="mt-2 inline-block h-px w-14 bg-line"
                />
                <span className="sr-only">no response yet</span>
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
