import type { ReactNode } from "react";
import type { Provenance } from "../types";

export function Empty({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-[2px] border border-dashed border-rule px-4 py-8 text-center text-muted">
      {children}
    </p>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return <p className="px-4 py-8 text-center text-muted">{label}…</p>;
}

/**
 * A request failed and there is nothing to show in its place.
 *
 * One of the two places DESIGN.md lets red render, and it means what the other
 * one means: this is not recoverable. `onRetry` does not soften that — the box
 * still states a failure whose content the app does not have — it just stops the
 * only way out being a browser reload. It renders inside the existing red
 * surface rather than beside it, so the render count stays at two.
 *
 * **Reusing this box adds a meaning without adding a render site.**
 * `ErrorBoundary` does exactly that for a render throw, and DESIGN.md counts it
 * as red's third meaning. The token count does not move when you reach for this
 * component, so check the meaning against DESIGN.md rather than trusting a grep.
 *
 * When a request fails but the previous data survives, this is the WRONG
 * component: nothing was lost, so nothing is unrecoverable, and red would be a
 * severity signal. Use `Caveat`.
 */
export function ErrorBox({
  error,
  onRetry,
}: {
  error: string;
  onRetry?: () => void;
}) {
  return (
    <p className="rounded-[2px] border border-danger/40 px-4 py-3 text-danger">
      {error}
      {onRetry && (
        <>
          {" "}
          <button
            onClick={onRetry}
            className="underline decoration-dotted underline-offset-2"
          >
            Try again
          </button>
        </>
      )}
    </p>
  );
}

/**
 * A caveat about the reading, in the app's own voice.
 *
 * Hairline and whitespace, no box and no colour — see DESIGN.md: this is a
 * qualification, not a failure, and colour here would be sentiment. The same
 * setting `StaleReading` uses, factored out once a second thing needed to say
 * "what you are looking at is still true, but here is what you should know".
 */
export function Caveat({
  children,
  action,
}: {
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <p className="mt-4 max-w-[62ch] border-l-2 border-dotted border-line pl-3 text-muted">
      {children}
      {action && <> {action}</>}
    </p>
  );
}

/**
 * The spine: content holds a reading measure, surplus width becomes a margin
 * with a job. See DESIGN.md — footnotes belong in the margin, not crammed onto
 * the end of a row. Below `lg` the margin collapses and its content is expected
 * to appear inline instead.
 */
export function Spine({
  margin,
  marginFirst,
  children,
}: {
  margin?: ReactNode;
  /** Put the margin ahead of the content in the DOM, and therefore ahead of it
   *  in the tab order, while it still renders on the right.
   *
   *  Only worth setting when the margin holds something focusable. It holds a
   *  jump rail on the product index, and rendering that after the content put
   *  it 400 tab stops past the entries it exists to skip — the navigation
   *  behind the thing being navigated. Explicit column placement does the job
   *  without a positive `tabindex`, which would be a worse cure. */
  marginFirst?: boolean;
  children: ReactNode;
}) {
  const content = (
    <div
      className={`min-w-0 ${marginFirst ? "lg:col-start-1 lg:row-start-1" : ""}`}
    >
      {children}
    </div>
  );
  const aside = (
    <div
      className={`hidden lg:block ${marginFirst ? "lg:col-start-2 lg:row-start-1" : ""}`}
    >
      {margin}
    </div>
  );
  return (
    <div className="grid gap-x-12 gap-y-3 lg:grid-cols-[minmax(0,var(--measure-read))_var(--spacing-margin)]">
      {marginFirst ? (
        <>
          {aside}
          {content}
        </>
      ) : (
        <>
          {content}
          {aside}
        </>
      )}
    </div>
  );
}

/** Marginalia. Small, mono, quiet. */
export function Aside({ children }: { children: ReactNode }) {
  return <div className="num pt-2 text-[11.5px] text-faint">{children}</div>;
}

/** A page reference, set as a citation rather than a badge.
 *
 *  Falls back to the locator when the format has no pages. A spreadsheet has
 *  none, and returning null there would retire the footnote apparatus for a
 *  whole class of response — the apparatus this design is built on, per the
 *  2026-09-03 row in DESIGN.md's decisions log. A cell reference is what a
 *  person sees when they open the file, so it is a citation in exactly the
 *  sense a page number is. */
export function Cite({ provenance }: { provenance?: Provenance | null }) {
  if (provenance?.page) {
    return (
      <span
        className="num shrink-0 text-[11.5px] text-faint"
        title={provenance.locator ?? undefined}
      >
        p.{provenance.page}
      </span>
    );
  }
  if (provenance?.locator) {
    return (
      <span className="num shrink-0 text-[11.5px] text-faint">
        {provenance.locator}
      </span>
    );
  }
  return null;
}
