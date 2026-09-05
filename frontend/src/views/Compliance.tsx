import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Aside, Empty, ErrorBox, Spine, Spinner } from "../components/ui";
import { humanise } from "../format";
import type { ComplianceRow, DisclosureCell } from "../types";

/**
 * What each retailer answered, and what it did not. See DESIGN.md.
 *
 * This used to be a matrix of coloured glyph buttons: eight squares in a row,
 * seven red and one green, with the evidence hidden behind a click. That is a
 * debug table standing in for the one thing this product does that nothing else
 * does, and with a single retailer loaded — the normal case — a one-row matrix
 * is a spreadsheet with nothing to compare.
 *
 * It reads as a document now. Eight categories down the page, and where an
 * answer should be there is a blank rule, the way an unfilled form carries a
 * blank. Seven rules in a column state the finding without a single pill, and
 * the evidence sits on the page instead of behind a click.
 *
 * A deliberate departure from docs/handoff.md section 8, which specified retailers as
 * rows and categories as columns. Cross-retailer comparison lives in Compare,
 * which already carries the unanswered count per retailer.
 */
export function Compliance() {
  const compliance = useAsync(() => api.compliance(), []);

  if (compliance.error) return <ErrorBox error={compliance.error} />;
  if (!compliance.data) return <Spinner label="Reading the disclosures" />;
  const { categories, rows } = compliance.data;
  if (rows.length === 0) return <Empty>No responses loaded yet.</Empty>;

  return (
    <div className="space-y-12">
      {rows.map((row) => (
        <Retailer key={row.id} row={row} categories={categories} />
      ))}

      <Spine>
        <p className="border-t border-rule pt-4 text-[11.5px] text-muted">
          This records what a response contained and what it did not. It is not a
          finding that anyone broke the law, and a retailer may lawfully decline a
          request it cannot verify. The reasoning, and where the eight categories
          come from, is in <code className="num">docs/legal-basis.md</code>.
        </p>
      </Spine>
    </div>
  );
}

function Retailer({ row, categories }: { row: ComplianceRow; categories: string[] }) {
  const [showLetter, setShowLetter] = useState(false);
  const answered = categories.length - row.absent_count;

  // missing_category follow-ups restate the absent categories one for one, and
  // the list above already is that list. Showing both says everything twice,
  // and the letter says it a third time. These carry something the categories
  // do not.
  const extra = row.follow_ups.filter((f) => f.kind !== "missing_category");

  // The adapter attaches the same explanatory note to every category it found
  // missing, because structurally it is the same fact each time: the report is
  // numbered as though those sections exist and they do not. Rendered per row
  // that printed one identical paragraph seven times, which is the failing this
  // view was rebuilt to fix rather than to relocate.
  //
  // Not the same as the appended-attributes block in Profile, where the
  // repeated line is each item's own field value. This is one sentence about
  // the document, so it is said once.
  const sharedNote = onlyNote(categories.map((c) => row.cells[c]));

  return (
    <section className="space-y-6">
      <Spine
        margin={
          <Aside>
            {row.statute}
            {row.period_start && (
              <div className="mt-1">
                {row.period_start} → {row.period_end ?? "?"}
              </div>
            )}
          </Aside>
        }
      >
        <div className="flex items-baseline gap-5">
          <span className="font-serif text-[42px] leading-none font-semibold text-faint tabular-nums">
            {row.absent_count}
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="font-serif text-[17px] font-semibold">
              {row.absent_count === 0
                ? `${row.display_name} addressed every category`
                : `of ${categories.length} categories went unanswered`}
            </h2>
            <p className="mt-0.5 max-w-[62ch] text-muted">
              {row.display_name} answered {answered} of {categories.length}. An
              absence here is not a blank: it is a record that the category was
              asked about and not addressed.
            </p>
          </div>
        </div>
      </Spine>

      <Spine margin={<Aside>eight categories</Aside>}>
        <div className="border-t border-rule">
          {categories.map((category) => (
            <Category
              key={category}
              category={category}
              cell={row.cells[category]}
              suppressNote={sharedNote}
            />
          ))}
        </div>
        {sharedNote && (
          <p className="mt-3 max-w-[62ch] text-[11.5px] text-faint">
            Every unanswered category above carries the same note: {sharedNote}
          </p>
        )}
      </Spine>

      {extra.length > 0 && (
        <Spine margin={<Aside>also worth asking</Aside>}>
          <ul className="space-y-2">
            {extra.map((f) => (
              <li key={f.id} className="max-w-[62ch] text-muted">
                {f.description}
              </li>
            ))}
          </ul>
        </Spine>
      )}

      <Spine>
        <button
          onClick={() => setShowLetter((v) => !v)}
          aria-expanded={showLetter}
          className="rounded-[2px] border border-line px-3 py-1.5 hover:bg-sunken"
        >
          {showLetter ? "Hide the draft" : `Draft a follow-up to ${row.display_name}`}
        </button>
        {showLetter && <Letter requestId={row.id} />}
      </Spine>
    </section>
  );
}

/**
 * The one note every unanswered category shares, if there is exactly one.
 *
 * Returns null when the notes differ, in which case each row keeps its own and
 * nothing is hidden. Hoisting only ever removes repetition, never information.
 */
function onlyNote(cells: DisclosureCell[]): string | null {
  const notes = cells
    .filter((c) => c.status !== "provided" && c.status !== "partial")
    .map((c) => c.notes)
    .filter((n): n is string => Boolean(n));
  if (notes.length < 2) return null;
  const distinct = new Set(notes);
  return distinct.size === 1 ? notes[0] : null;
}

/**
 * One category, with its answer or the absence of one.
 *
 * No colour. `provided` and `partial` show what the response actually said;
 * `absent` shows a blank rule where the answer belongs. Status is a word, not a
 * pill — see DESIGN.md on what colour is allowed to mean.
 */
function Category({
  category,
  cell,
  suppressNote,
}: {
  category: string;
  cell: DisclosureCell;
  /** The note hoisted out of this list, if any. Only a note identical to it is
   *  hidden; a row with something else to say still says it. */
  suppressNote?: string | null;
}) {
  const status = cell.status;
  const answered = status === "provided" || status === "partial";

  return (
    <div className="border-b border-rule py-3">
      {/* A grid, not flex: the blanks have to land in a column. Flex put each
          rule at a different x depending on how long the category name was,
          which destroys the one effect this layout exists for. */}
      <div className="grid grid-cols-[minmax(0,1fr)_11rem_2.5rem] items-baseline gap-4">
        <span className={answered ? "" : "text-muted"}>{humanise(category)}</span>

        {answered ? (
          <span>{status === "provided" ? "answered" : "answered in part"}</span>
        ) : (
          // The blank where an answer should be, the way an unfilled form
          // carries one. A column of these is the finding, in no colour at all.
          <span className="flex items-baseline gap-3">
            <span aria-hidden className="inline-block h-px w-16 bg-line" />
            <span className="text-muted">
              {status === "absent" ? "not addressed" : "not assessed"}
            </span>
          </span>
        )}

        <span className="num text-[11.5px] text-faint">
          {cell.provenance?.page ? `p.${cell.provenance.page}` : ""}
        </span>
      </div>

      {/* The retailer's own words, where there are any. Quoted rather than
          summarised: a keyword match can show a topic came up, never that the
          question was answered, so the reader gets the sentence and judges. */}
      {cell.evidence && (
        <p className="mt-1.5 max-w-[62ch] border-l-2 border-rule pl-3 text-muted italic">
          {cell.evidence}
        </p>
      )}
      {cell.notes && cell.notes !== suppressNote && (
        <p className="mt-1.5 max-w-[62ch] text-[11.5px] text-faint">{cell.notes}</p>
      )}
    </div>
  );
}

/** Rows for the collapsed draft: as tall as it needs, up to a cap.
 *
 * A blanket `rows={8}` is wrong in both directions. Most follow-ups name one or
 * two unanswered categories and run shorter than eight lines, so a fixed height
 * pads them with empty box; a response that went unanswered across the board
 * runs to thirty and needs the cap. Three is the floor because below that the
 * field stops reading as a document and starts reading as an input.
 *
 * Counts newlines only. Wrapping is what the measured overflow check handles —
 * this decides the height, that decides whether to offer the control.
 */
export function draftRows(text: string, cap = 8): number {
  const lines = text ? text.split("\n").length : 1;
  return Math.min(Math.max(lines, 3), cap);
}

/** True when the collapsed field is hiding something, wrapping included. */
function useOverflows(ref: React.RefObject<HTMLTextAreaElement | null>, deps: unknown[]) {
  const [overflows, setOverflows] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => setOverflows(el.scrollHeight - el.clientHeight > 2);
    read();
    window.addEventListener("resize", read);
    return () => window.removeEventListener("resize", read);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return overflows;
}

function Letter({ requestId }: { requestId: number }) {
  const letter = useAsync(() => api.followUpLetter(requestId), [requestId]);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const draft = useRef<HTMLTextAreaElement>(null);
  const text = letter.data?.letter ?? "";
  const collapsedRows = draftRows(text);
  const overflows = useOverflows(draft, [text, expanded]);

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
    <div className="mt-4">
      <p className="mb-2 max-w-[62ch] text-muted">{letter.data.note}</p>
      {/* Shortened, not hidden. This view was rebuilt to take evidence out from
          behind a click, and a disclosure that concealed the draft would walk
          that back — so the field still scrolls to every word without touching
          the control, and the control only changes how much you see at once.
          The letter is also the one thing here you are about to send in your own
          name, which is the argument for showing it whole; `rows={18}` answered
          that by spending ~350px of a ~700px section on a preview of a document
          that gets read in a mail client. */}
      <textarea
        ref={draft}
        readOnly
        value={letter.data.letter}
        rows={expanded ? draftRows(text, 40) : collapsedRows}
        className="num w-full rounded-[2px] border border-rule bg-sunken p-3 text-[12.5px] focus:border-accent focus:outline-2 focus:outline-offset-1 focus:outline-accent"
      />
      {/* Offered only when the collapsed field is actually holding something
          back. A short follow-up names one category and fits, and a control that
          expands nothing is furniture. */}
      {(overflows || expanded) && (
        <button
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="mt-1 text-accent underline underline-offset-2 hover:no-underline"
        >
          {expanded ? "Shorten the draft" : "Show the whole draft"}
        </button>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button
          onClick={copy}
          className="rounded-[2px] border border-line px-3 py-1.5 hover:bg-sunken"
        >
          {copied ? "Copied" : "Copy"}
        </button>
        <span className="max-w-[52ch] text-muted">
          {copyFailed
            ? "Your browser would not let the page copy for you. The draft is selected — copy it yourself."
            : "You send this yourself. unbagged never contacts anyone on your behalf."}
        </span>
      </div>
    </div>
  );
}
