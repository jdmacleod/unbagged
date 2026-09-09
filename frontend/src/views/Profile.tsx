import { api } from "../api";
import { useAsync } from "../components/useAsync";
import { Empty, ErrorBox, Spinner } from "../components/ui";
import { humanise } from "../format";
import type { Identity, Inference, Provenance } from "../types";

/**
 * Profile, read as a document rather than a dashboard. See DESIGN.md.
 *
 * Three movements down one spine: what you handed them, what they worked out by
 * watching you, and what they bought about you. Two equal columns used to hold
 * five cards on the left and sixteen on the right, which left roughly a
 * thousand pixels of dead space and made the most important finding in the
 * product look like a rendering bug.
 *
 * The imbalance is the finding. The layout's job is to make it legible as a
 * ratio, so the counts hang in the margin at display size and you read "5"
 * against "16" before you read a word. Where the material changes source, the
 * page changes stock.
 */
export function Profile({ requestId }: { requestId: number }) {
  const profile = useAsync(() => api.profile(requestId), [requestId]);

  if (profile.error) return <ErrorBox error={profile.error} />;
  if (!profile.data) return <Spinner label="Reading the profile" />;

  const { identities, inferences_by_origin, household_scoped_count } =
    profile.data;
  const mine = inferences_by_origin.first_party_model ?? [];
  const bought = inferences_by_origin.appended_third_party ?? [];
  const unclear = inferences_by_origin.unknown ?? [];

  if (identities.length === 0 && mine.length === 0 && bought.length === 0) {
    return (
      <Empty>
        This response contained no identifiers or inferred attributes.
      </Empty>
    );
  }

  return (
    <div className="space-y-10">
      <Movement
        heading="What you handed them"
        blurb="The separate keys this retailer holds for you. Each one is another way
               to find you in their systems."
        count={identities.length}
        marginalia={<Aside>{scopeNote(identities)}</Aside>}
      >
        {identities.length === 0 ? (
          <Empty>No identifiers were found in this response.</Empty>
        ) : (
          <ul>
            {identities.map((identity) => (
              <IdentityRow key={identity.id} identity={identity} />
            ))}
          </ul>
        )}
      </Movement>

      {/* Said out loud rather than left as an absence.
          Every inference section below is gated on having something to show, so
          a response with none of them rendered one identity row and then simply
          stopped — a page that ends early reads as a bug, and the reader has no
          way to tell "they disclosed nothing" from "we could not read it". That
          distinction is the one this whole app refuses to blur. */}
      {mine.length === 0 && bought.length === 0 && unclear.length === 0 && (
        <Movement
          heading="What they worked out about you"
          blurb="Nothing. This response carried no inferred attributes at all —
                 no scores computed from your baskets, and nothing bought in
                 from anywhere else."
          count={0}
          marginalia={<Aside>none disclosed</Aside>}
        >
          <Empty>
            That is what the response contained, not what this tool could read.
            A retailer that holds inferences and does not disclose them looks
            exactly like this from here, which is itself worth knowing.
          </Empty>
        </Movement>
      )}

      {mine.length > 0 && (
        <Movement
          heading="What they worked out by watching you"
          blurb="Computed from the baskets in this same report, which means you can
                 check every one of them against the timeline."
          count={mine.length}
          marginalia={<Aside>derived · first-party</Aside>}
        >
          <ul>
            {mine.map((inference) => (
              <Derived key={inference.id} inference={inference} />
            ))}
          </ul>
        </Movement>
      )}

      {bought.length > 0 && (
        <Bought inferences={bought} householdCount={household_scoped_count} />
      )}

      {unclear.length > 0 && (
        <Movement
          heading="Origin unclear"
          blurb="The adapter could not tell whether these were derived or obtained."
          count={unclear.length}
          marginalia={<Aside>unclassified</Aside>}
        >
          <ul>
            {unclear.map((inference) => (
              <Derived key={inference.id} inference={inference} />
            ))}
          </ul>
        </Movement>
      )}
    </div>
  );
}

/** How many identifiers cover the household rather than the person. */
/** What the margin says about who these identifiers describe.
 *
 *  "All individual" is a claim, and it used to be made by default whenever no
 *  identity was household-scoped — including when the response never said. An
 *  adapter that deliberately records `scope: null` rather than inventing one is
 *  making the honest choice, and the margin must not undo it. Silence and a
 *  stated fact are different, which is the distinction this whole app exists to
 *  keep. */
function scopeNote(identities: Identity[]): string {
  const household = identities.filter((i) => i.scope === "household").length;
  if (household > 0) return `${household} household-scoped`;
  const unstated = identities.filter((i) => !i.scope).length;
  if (unstated === identities.length) return "scope not stated";
  if (unstated > 0) return `${unstated} with no scope stated`;
  return "all individual";
}

/**
 * One movement: a hanging count, a heading, and the margin.
 *
 * The count is set at display size in the serif and hangs to the left of the
 * heading, so the ratio between movements is readable at a glance without
 * anyone having to count cards.
 */
function Movement({
  heading,
  blurb,
  count,
  marginalia,
  children,
}: {
  heading: string;
  blurb: string;
  count: number;
  marginalia?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="grid gap-x-12 gap-y-4 lg:grid-cols-[minmax(0,var(--measure-read))_var(--spacing-margin)]">
      <div className="min-w-0">
        <div className="flex items-baseline gap-5">
          <span className="font-serif text-[42px] leading-none font-semibold text-faint tabular-nums">
            {count}
          </span>
          <div className="min-w-0 flex-1">
            <h2 className="font-serif text-[17px] font-semibold">{heading}</h2>
            <p className="mt-0.5 max-w-[62ch] text-muted">{blurb}</p>
          </div>
        </div>
        <div className="mt-5">{children}</div>
      </div>
      <div className="hidden lg:block">{marginalia}</div>
    </section>
  );
}

/** Marginalia. Footnotes belong in the margin, not crammed onto the row. */
function Aside({ children }: { children: React.ReactNode }) {
  return <div className="num pt-2 text-[11.5px] text-faint">{children}</div>;
}

/** A page reference, set as a citation rather than a badge. */
function Cite({ provenance }: { provenance: Provenance }) {
  if (!provenance?.page) return null;
  return (
    <span
      className="num shrink-0 text-[11.5px] text-faint"
      title={provenance.locator ?? undefined}
    >
      p.{provenance.page}
    </span>
  );
}

function IdentityRow({ identity }: { identity: Identity }) {
  return (
    <li className="flex items-baseline gap-4 border-b border-rule py-2.5">
      <span className="w-40 shrink-0 text-muted">
        {humanise(identity.id_type)}
      </span>
      <span
        className="num min-w-0 flex-1 truncate text-[12.5px]"
        title={identity.value}
      >
        {identity.value}
      </span>
      {/* A word, not a coloured pill. Colour is reserved for provenance. */}
      {identity.scope === "household" && (
        <span
          className="shrink-0 text-muted italic"
          title="Covers everyone at the address, not only you."
        >
          household
        </span>
      )}
      <Cite provenance={identity.provenance} />
    </li>
  );
}

/**
 * Something they computed from your own baskets.
 *
 * Given real vertical room and a rule whose length encodes the value, because
 * each of these is defensible and traceable. Position, not a pill.
 */
function Derived({ inference }: { inference: Inference }) {
  const pct = gaugeWidth(inference);
  return (
    <li className="flex items-baseline gap-4 border-b border-rule py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span>{humanise(inference.label)}</span>
          {inference.subject === "household" && (
            <span
              className="text-faint italic"
              title="Describes your household, not you."
            >
              household
            </span>
          )}
        </div>
        {inference.scale && (
          <div className="mt-0.5 text-[11.5px] text-faint">
            {humanise(inference.scale)}
          </div>
        )}
        {pct !== null && (
          <div className="mt-1.5 h-0.5 max-w-md bg-rule">
            <div className="h-0.5 bg-ink/55" style={{ width: `${pct}%` }} />
          </div>
        )}
      </div>
      <span className="num shrink-0 text-[12.5px]">{inference.value_raw}</span>
      <Cite provenance={inference.provenance} />
    </li>
  );
}

/** Only ordinal scales carry a position worth drawing.
 *  Exported for `gaugeWidth.test.ts`. */
export function gaugeWidth(inference: Inference): number | null {
  if (inference.value_num === null || !inference.scale) return null;
  const match = /(\d+)[–-](\d+)/.exec(inference.scale);
  if (!match) return null;
  const [lo, hi] = [Number(match[1]), Number(match[2])];
  if (!(hi > lo)) return null;
  const pct = ((inference.value_num - lo) / (hi - lo)) * 100;
  return Math.max(0, Math.min(100, Math.round(pct)));
}

/**
 * The third movement, where the page changes stock.
 *
 * A full-bleed panel on visibly different paper, because this material has a
 * different source and saying so in a caption is weaker than saying it in the
 * material. Every entry repeats the same line: source not disclosed. The
 * repetition is the argument, and it is also just the honest field value.
 * Reading it sixteen times does what a warning badge cannot.
 */
function Bought({
  inferences,
  householdCount,
}: {
  inferences: Inference[];
  householdCount: number;
}) {
  // The pull has to match the shell's padding exactly or this is not a bleed.
  // It was `sm:-mx-8` against the shell's `sm:px-12`, so above the `sm`
  // breakpoint the panel floated 16px inside the page edge on both sides — a
  // tinted card with two of its borders removed, which is the one thing
  // DESIGN.md says this panel must not be.
  return (
    <section className="-mx-6 border-y border-foreign bg-foreign-paper px-6 py-7 sm:-mx-12 sm:px-12">
      <div className="grid gap-x-12 gap-y-4 lg:grid-cols-[minmax(0,var(--measure-read))_var(--spacing-margin)]">
        <div className="min-w-0">
          <div className="flex items-baseline gap-5">
            <span className="font-serif text-[42px] leading-none font-semibold text-foreign tabular-nums">
              {inferences.length}
            </span>
            <div className="min-w-0 flex-1">
              <h2 className="font-serif text-[17px] font-semibold text-foreign">
                What they bought about you
              </h2>
              <p className="mt-0.5 max-w-[62ch] text-foreign/85">
                Nothing in a grocery basket says how long you have lived at your
                address or whether you will take a cruise. These were obtained
                elsewhere, and the response does not say from whom.
              </p>
            </div>
          </div>

          <ul className="mt-5 grid gap-x-8 sm:grid-cols-2 xl:grid-cols-3">
            {inferences.map((inference) => (
              <BoughtEntry key={inference.id} inference={inference} />
            ))}
          </ul>

          {householdCount > 0 && (
            <p className="mt-5 max-w-[62ch] text-foreign/85">
              {householdCount} of these describe your <strong>household</strong>{" "}
              rather than you, which means they describe people who never signed
              up for anything.
            </p>
          )}
        </div>
        <div className="num hidden pt-2 text-[11.5px] text-foreign/70 lg:block">
          <div>appended</div>
          <div>third party</div>
          <div className="mt-4">not named</div>
        </div>
      </div>
    </section>
  );
}

function BoughtEntry({ inference }: { inference: Inference }) {
  return (
    <li className="border-b border-foreign-rule py-2">
      <div className="flex items-baseline gap-2">
        <span className="min-w-0 flex-1 text-[12px]">
          {humanise(inference.label)}
        </span>
        <Cite provenance={inference.provenance} />
      </div>
      <div className="num text-[12.5px] text-foreign">
        {inference.value_raw}
      </div>
      {/* The honest field value, and the whole point of the view. Repeated on
          purpose: an inventory that keeps saying the same thing lands harder
          than one badge would. */}
      <div className="num text-[10.5px] text-foreign/75">
        {inference.derivable_from_txns === true
          ? "source not disclosed · your baskets could explain this"
          : "source not disclosed"}
      </div>
    </li>
  );
}
