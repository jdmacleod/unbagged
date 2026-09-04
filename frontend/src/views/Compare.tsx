import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Aside, ErrorBox, Spine, Spinner } from "../components/ui";
import { day, money, number } from "../format";
import type { CompareRow } from "../types";

type Metric = {
  key: keyof CompareRow;
  label: string;
  format: (value: number | null | undefined) => string;
  /** Marks a row that is about provenance rather than quantity. */
  foreign?: boolean;
};

const ROWS: Metric[] = [
  { key: "visits", label: "Visits", format: number },
  // Paid, not shelf. This row read "Total spend" over the summed pre-discount
  // amounts, which ranks two retailers by whose shelf prices are higher rather
  // than by which one actually cost more.
  { key: "total_paid", label: "Total paid", format: money },
  { key: "total_saved", label: "…after loyalty savings of", format: money },
  { key: "distinct_products", label: "Distinct products", format: number },
  { key: "identifier_count", label: "Identifiers held for you", format: number },
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
  { key: "absent_disclosures", label: "Categories not addressed", format: number },
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
  const undisclosed = requests.some((r) => !r.disclosed);

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
        <Sheet requests={requests} pending={!comparable} />

        <p className="mt-4 max-w-[62ch] text-[11.5px] text-muted">
          {undisclosed && (
            <>
              An em dash means the retailer disclosed nothing of that kind, which is
              not the same as a zero.{" "}
            </>
          )}
          {!comparable && (
            <>A blank rule means no response has arrived to fill it in yet. </>
          )}
          Categories not addressed is counted for every retailer either way, because
          what a retailer failed to answer is a finding about that retailer.
        </p>
      </Spine>
    </div>
  );
}

function Sheet({ requests, pending }: { requests: CompareRow[]; pending: boolean }) {
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
            </div>
          ))}
          {pending && (
            <div className="text-right">
              <div className="font-serif text-[15px] font-semibold text-faint">
                Awaiting
              </div>
              <div className="num mt-0.5 text-[11px] text-faint">no response yet</div>
            </div>
          )}
        </div>

        {ROWS.map((row) => (
          <div
            key={row.key}
            className="grid items-baseline gap-4 border-b border-rule py-2"
            style={{ gridTemplateColumns: cols }}
          >
            <span className={row.foreign ? "text-foreign" : "text-muted"}>
              {row.label}
            </span>
            {requests.map((r) => (
              <span
                key={r.id}
                className={`num text-right ${row.foreign ? "text-foreign" : ""}`}
                title={
                  r.disclosed
                    ? undefined
                    : `${r.display_name} did not disclose this. A dash means not disclosed, not zero.`
                }
              >
                {row.format(r[row.key] as number | null)}
              </span>
            ))}
            {pending && (
              <span className="flex justify-end">
                {/* The blank waiting to be filled in. Same mark as an unanswered
                    disclosure category, same meaning: nothing here yet. */}
                <span aria-hidden className="mt-2 inline-block h-px w-14 bg-line" />
                <span className="sr-only">no response yet</span>
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
